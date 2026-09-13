"""A bounded scheduled worker; durable state is restored/persisted by the host."""
import hashlib
import html
import json
from datetime import datetime,timezone
from pathlib import Path

from .core import Engine, IST, SafetyError, expected_session
from .mail import initialize, queue
from .nse import nse_rows


def phase(now, config):
    local=now.astimezone(IST)
    if local.year!=2026: raise SafetyError('Calendar expired: review the NSE calendar before resuming')
    if local.weekday()>=5 or local.date().isoformat() in config['market_holidays']: return 'closed'
    minute=local.hour*60+local.minute
    if 555<=minute<930: return 'open'
    if 1020<=minute<1260: return 'evening'
    return 'closed'


def export(engine, state):
    report=engine.report()
    report.update(provider='nse',execution='Daily-report paper simulation; fills observed after publication')
    state=Path(state)
    (state/'trial-report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    last=report['latest']
    text=[f"# Indian paper trial",f"Status: {'COMPLETE' if report['trial_complete'] else 'HALTED' if report['risk_halted'] else 'PAUSED' if report['paused'] else 'ACTIVE' if last else 'AWAITING FIRST EVENING DATA CHECK'}",
          f"Last completed session: {last['day'] if last else 'None'}",f"Virtual cash: INR {report['cash']:,.2f}",
          f"Virtual equity: INR {last['equity'] if last else report['initial']:,.2f}",
          f"Last error: {report['last_error'] or 'None'}",'','## Simulated fills (not live transactions)']
    text += [f"- {f['day']}: {f['side']} {f['qty']} {f['symbol']} at INR {f['price']:.4f}; fees INR {f['fee']:.2f}; observed {f['observed_at']}" for f in report['fills']]
    text += ['','## Pending paper proposals']
    text += [f"- {o['side']} {o['qty']} {o['symbol']}: {o['reason']}" for o in report['orders'] if o['status']=='PENDING']
    text += ['','Daily reports cannot confirm live opening fills. Return figures exclude personal income tax and dividends. No guarantee of profit.']
    (state/'REPORT.md').write_text('\n\n'.join(text)+'\n')
    return report


def run(config, state, now=None, fetcher=nse_rows, action='tick'):
    now=now or datetime.now(timezone.utc); stamp=now.isoformat()
    day=now.astimezone(IST).date().isoformat()
    e=Engine(Path(state)/'ledger.sqlite3',config,'paper')
    initialize(e.db)
    failed=False
    try:
        if action in ('pause','resume'):
            e.pause(action=='pause')
            queue(e.db,'control:'+stamp,'Entries '+('paused' if action=='pause' else 'resumed'),
                  'Risk halts remain binding. Existing holdings remain exposed.',stamp)
        elif action=='setup':
            queue(e.db,'setup','Deployment configured',
                  'The worker and durable paper account are initialized. First signals follow a successful evening data check. Scheduled starts may be delayed by the free host.',stamp)
        else:
            e.reconcile()
            if e.meta('expires_at') and now>=datetime.fromisoformat(e.meta('expires_at')):
                e.cycle([],now)
            elif phase(now,config)=='open':
                last=e.meta('last_day')
                expected=expected_session(now,config['market_holidays'])
                detail=('Prior-session data ready through '+last if last==expected else 'NOT READY: prior-session data not yet validated; no confirmed fills available.')
                queue(e.db,'open:'+day,'Market-session worker started',
                      'Actual worker start: '+now.astimezone(IST).isoformat()+'\n'+detail+'\nThis is a daily strategy. Pending paper proposals are not executed broker orders.',stamp)
                if last!=expected:
                    queue(e.db,'stale:'+day,'Data readiness needs attention',
                          'Expected last session '+expected+'. Last recorded '+str(last)+'. Evening checks will retry; never substitute synthetic prices.',stamp)
            elif phase(now,config)=='evening':
                result=e.cycle(fetcher(config,state,now),now)
                with e.db: e.set_meta('last_error','')
                r=e.report()
                if r['latest']:
                    pending=[f"{o['side']} {o['qty']} {o['symbol']} — {o['reason']}" for o in r['orders'] if o['status']=='PENDING']
                    queue(e.db,'summary:'+day,'Daily paper summary',
                          result+f"\nVirtual equity INR {r['latest']['equity']:,.2f}; cash INR {r['cash']:,.2f}."
                          +'\nPending proposals:\n'+('\n'.join(pending) or 'None')
                          +'\nBenchmark is a price-only comparison; dividends and personal income tax are excluded.',stamp)
            with e.db: e.set_meta('hosted_last_run',stamp)
        # Reconstruct notices from durable fills, including after a crash between cycle and enqueue.
        for f in e.db.execute('SELECT * FROM fills ORDER BY day,order_id').fetchall():
            queue(e.db,'fill:'+f['order_id'],'Simulated '+f['side']+' '+f['symbol'],
                  f"{f['side']} {f['qty']} {f['symbol']} at INR {f['price']:.4f}. Fees INR {f['fee']:.2f}.\n"
                  +f"Modelled session: {f['day']}; observed after daily report publication: {f['observed_at']}.\n"
                  +'Assumes next-session opening price plus adverse slippage, not an actual broker fill.',stamp)
        for o in e.db.execute("SELECT * FROM orders WHERE status IN ('REJECTED','CANCELLED','EXPIRED')").fetchall():
            queue(e.db,'order:'+o['id']+':'+o['status'],'Paper proposal '+o['status'].lower(),
                  f"{o['side']} {o['qty']} {o['symbol']}: {o['reason']}",stamp)
        if e.meta('risk_halted')=='1':
            queue(e.db,'risk-halt','Loss limit reached',
                  'New buys are halted for this trial. Open positions remain exposed; this does not cap losses or liquidate holdings.',stamp)
        if e.meta('trial_complete')=='1':
            queue(e.db,'complete','Paper trial complete',
                  'No new proposals. Last recorded marks retained; positions are not liquidated. Review the report before another trial.',stamp)
    except Exception as exc:
        failed=True
        message=str(exc) if isinstance(exc,SafetyError) else 'Processing failed ('+type(exc).__name__+'); inspect worker logs'
        e.failure(message)
        queue(e.db,'error:'+day+':'+hashlib.sha256(message.encode()).hexdigest()[:16],
              'Worker needs attention',message+'\nAccount was not advanced by a failed data cycle.',stamp)
    finally:
        report=export(e,state)
        e.close()
    return report,failed
