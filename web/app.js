let report;
const $=id=>document.getElementById(id);
const money=n=>new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(n);
const pct=n=>(n*100).toFixed(2)+'%';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=s=>s?new Date(s).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):'Not started';
function table(headers,rows,empty){return rows.length?'<div class="table-wrap"><table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>':'<div class="empty">'+esc(empty)+'</div>';}
function chart(curve){
 if(!curve.length){$('chart').innerHTML='<div class="empty">Connect market data to begin the forward record.</div>';return;}
 const W=650,H=250,L=68,R=18,T=20,B=40;
 const values=curve.flatMap(p=>[p.equity,p.benchmark]);let lo=Math.min(...values),hi=Math.max(...values);let pad=Math.max((hi-lo)*.15,100);lo-=pad;hi+=pad;
 const x=i=>L+(W-L-R)*(curve.length===1?.5:i/(curve.length-1)); const y=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo));
 let svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Virtual account value in rupees by session date">';
 for(let i=0;i<4;i++){let v=lo+(hi-lo)*i/3;svg+='<line class="grid" x1="'+L+'" x2="'+(W-R)+'" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="'+(L-8)+'" y="'+(y(v)+3)+'" text-anchor="end">'+esc(money(v))+'</text>';}
 for(const key of ['benchmark','equity']){let points=curve.map((p,i)=>x(i)+','+y(p[key])).join(' ');svg+='<polyline class="'+(key==='equity'?'strategy':'benchmark')+'" points="'+points+'"/>';if(curve.length===1)svg+='<circle cx="'+x(0)+'" cy="'+y(curve[0][key])+'" r="3"/>';}
 svg+='<text x="'+L+'" y="'+(H-17)+'">'+esc(curve[0].day)+'</text><text x="'+(W-R)+'" y="'+(H-17)+'" text-anchor="end">'+esc(curve.at(-1).day)+'</text><text x="'+(W/2)+'" y="'+(H-1)+'" text-anchor="middle">Session date</text></svg>';
 $('chart').innerHTML=svg;
}
async function refresh(){
 try{
  const res=await fetch('/api/report');if(!res.ok)throw Error('Account unavailable');report=await res.json();const r=report,l=r.latest;
  $('mode').textContent=r.mode==='demo'?'SYNTHETIC DEMO':(r.provider==='nse'?'NSE DAILY REPORTS':r.provider==='csv'?'IMPORTED DATA':'UPSTOX')+' · PAPER ONLY';
  let notice=r.mode==='demo'?'Synthetic data: these prices and returns are generated for software testing. They are not market results.':'Paper simulation using completed market sessions. Orders are simulated locally; no money is invested.';
  if(r.last_error)notice=r.last_error;else if(r.trial_complete)notice='Trial complete. The account is frozen at its last recorded marks. Review the report before starting another experiment.';else if(r.stale)notice='Market data is behind the expected session. Check the feed, token, or exchange calendar.';else if(r.risk_halted)notice='Loss limit reached. New entries are halted for this trial. Existing holdings remain exposed.';else if(r.paused)notice='New entries are paused. Accounting and previously planned exits continue.';
  $('notice').textContent=notice;$('notice').className='notice'+(r.last_error||r.stale?' error':'');
  $('equity').textContent=money(l?l.equity:r.initial);$('cash').textContent=money(r.cash);
  const gain=(l?l.equity:r.initial)-r.initial;$('pnl').textContent=(gain>=0?'+':'')+money(gain)+' · '+pct(gain/r.initial)+' since start';
  $('drawdown').textContent=pct(Math.max(0,...r.curve.map(p=>p.drawdown)));
  $('drawdown-hint').textContent=pct(r.config.max_drawdown)+' limit pauses new entries';
  $('strategy-description').textContent='Positive '+r.config.lookback+'-session momentum, above the '+r.config.trend_window+'-session price average. Rebalance every '+r.config.rebalance_sessions+' observed sessions.';
  $('limits').innerHTML=['Up to '+r.config.max_positions+' positions',pct(r.config.max_position_weight)+' per position at entry',pct(r.config.max_gross_weight)+' total exposure at entry',pct(r.config.max_sector_weight)+' sector exposure at entry',pct(r.config.max_daily_loss)+' daily loss pauses new entries'].map(x=>'<li>'+esc(x)+'</li>').join('');
  $('sessions').textContent=r.sessions;$('deadline').textContent=r.expires_at?'Ends '+time(r.expires_at)+' IST':'Starts after the first successful data check';
  $('date').textContent=l?'Last completed session · '+l.day:'Waiting for a data connection';
  chart(r.curve);
  $('positions').innerHTML=table(['Symbol','Shares','Average cost incl. fees','Last price','Market value','Unrealized P&L'],r.positions.map(p=>[esc(p.symbol),p.qty,money(p.average),money(p.last),money(p.value),'<span class="'+(p.unrealized>=0?'good':'bad')+'">'+money(p.unrealized)+'</span>']),'No open positions. This is valid when no candidates qualify or orders await the next session.');
  $('decision-table').innerHTML=table(['Session','Symbol','Decision','Momentum','Reason'],r.decisions.slice(0,18).map(d=>[esc(d.day),esc(d.symbol),'<span class="badge '+(d.action==='SELL'?'sell':'')+'">'+esc(d.action)+'</span>',pct(JSON.parse(d.features).momentum),esc(d.reason)]),'No decisions yet. Warm-up data will be used to create the first signals.');
  const fills=Object.fromEntries(r.fills.map(f=>[f.order_id,f]));
  $('orders').innerHTML=table(['Signal date','Symbol','Side','Shares','State','Fill / fee','Reason'],r.orders.map(o=>{let f=fills[o.id];return[esc(o.signal_day),esc(o.symbol),esc(o.side),o.qty,esc(o.status),f?esc(f.day)+' · '+money(f.price)+' / '+money(f.fee):'—',esc(o.reason)]}),'No orders yet. The system does not trade to fill a quota.');
  $('costs').textContent='Fees '+money(r.fees)+' · modelled slippage '+money(r.slippage);
  $('sync').textContent=r.mode==='demo'?'Advance one demo session':'Check market data';$('sync').disabled=r.trial_complete||r.busy;
  $('pause').textContent=r.paused?'Resume new entries':'Pause new entries';$('pause').disabled=r.trial_complete||r.busy;
  $('fingerprint').textContent=r.strategy+' · '+r.fingerprint;$('health').textContent='Last check '+time(r.last_check)+' IST';
  $('events').innerHTML=r.events.map(e=>'<div class="event"><time>'+esc(time(e.at))+'</time><strong>'+esc(e.level)+'</strong><span>'+esc(e.message)+'</span></div>').join('')||'<p class="caption">The first data check will appear here.</p>';
 }catch(e){$('notice').textContent='Cannot reach the local app. Start the application or check its terminal.';$('notice').className='notice error';}
}
async function action(name){$('action-status').textContent='Working…';$('sync').disabled=true;$('pause').disabled=true;try{const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Paper-CSRF':report.csrf},body:JSON.stringify({action:name})});const result=await response.json();$('action-status').textContent=result.message;}catch(e){$('action-status').textContent='Request failed. Check local app status.';}await refresh();}
$('sync').onclick=()=>action('sync');$('pause').onclick=()=>action(report.paused?'resume':'pause');refresh();setInterval(refresh,15000);
