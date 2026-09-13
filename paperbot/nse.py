"""NSE final daily reports. No brokerage login, intraday data or live orders."""
import csv
import hashlib
import io
import json
import math
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from .core import SafetyError, expected_session
from .data import NoRedirect

BASE = 'https://nsearchives.nseindia.com/content/cm/'
ISINS = {'RELIANCE':'INE002A01018','HDFCBANK':'INE040A01034','INFY':'INE009A01021',
         'ITC':'INE154A01025','LT':'INE018A01030','TCS':'INE467B01029','NIFTYBEES':'INF204KB14I2'}


def filename(day):
    return 'BhavCopy_NSE_CM_0_0_0_'+date.fromisoformat(day).strftime('%Y%m%d')+'_F_0000.csv.zip'


def parse_report(payload, day, symbols):
    try:
        archive=zipfile.ZipFile(io.BytesIO(payload))
        entries=archive.infolist()
        if len(entries)!=1 or entries[0].filename != filename(day)[:-4] or entries[0].file_size>20_000_000:
            raise ValueError('Unexpected archive layout')
        reader=csv.DictReader(io.StringIO(archive.read(entries[0]).decode('utf-8-sig')))
        required={'TradDt','TckrSymb','SctySrs','ISIN','Sgmt','Src','SsnId','OpnPric','HghPric','LwPric','ClsPric','TtlTradgVol'}
        if not required.issubset(reader.fieldnames or []): raise ValueError('Schema mismatch')
        found={}
        for r in reader:
            symbol=r['TckrSymb']
            if symbol not in symbols or r['SctySrs']!='EQ': continue
            if symbol in found or r['TradDt']!=day or r['ISIN']!=ISINS[symbol] or r['Sgmt']!='CM' or r['Src']!='NSE' or r['SsnId']!='F1':
                raise ValueError('Identity, session or date mismatch')
            o,h,l,c,v=[float(r[k]) for k in ('OpnPric','HghPric','LwPric','ClsPric','TtlTradgVol')]
            if not all(math.isfinite(x) and x>0 for x in (o,h,l,c,v)) or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError('Invalid price or volume')
            found[symbol]=dict(symbol=symbol,date=day,open=o,high=h,low=l,close=c,volume=v)
        if set(found)!=set(symbols): raise ValueError('Incomplete universe')
        return [found[s] for s in symbols]
    except (ValueError,KeyError,UnicodeError,zipfile.BadZipFile,RuntimeError):
        raise SafetyError('NSE report validation failed for '+day+'; ledger not advanced') from None


def download(day):
    for attempt in range(3):
        try:
            request=Request(BASE+filename(day),headers={'User-Agent':'IndianPaperTrial/1.0','Accept':'application/zip'})
            with build_opener(NoRedirect()).open(request,timeout=20) as response:
                payload=response.read(5_000_001)
                if len(payload)>5_000_000: raise SafetyError('NSE download exceeded size limit')
                return payload
        except HTTPError as exc:
            if exc.code in (429,500,502,503,504) and attempt<2:
                time.sleep(attempt+1); continue
            raise SafetyError('NSE report unavailable for '+day+' (HTTP '+str(exc.code)+'); no substitute prices used') from None
        except (URLError,OSError,TimeoutError):
            if attempt<2:
                time.sleep(attempt+1); continue
            raise SafetyError('NSE download failed for '+day+'; retry later') from None


def nse_rows(config, state, now, offline=False):
    if now.year!=2026: raise SafetyError('NSE calendar requires review for the new year')
    symbols=config['symbols']+[config['benchmark']]
    if any(s not in ISINS for s in symbols): raise SafetyError('NSE instrument identity mapping requires review')
    cache=Path(state)/'nse'; cache.mkdir(parents=True,exist_ok=True)
    days=[]; d=date.fromisoformat(expected_session(now,config['market_holidays']))
    for _ in range(200):
        if d.weekday()<5 and d.isoformat() not in config['market_holidays']: days.append(d.isoformat())
        if len(days)>=max(60,config['trend_window'],config['lookback']+1): break
        d-=timedelta(days=1)
    else: raise SafetyError('Unable to assemble warm-up sessions')
    rows=[]
    for day in reversed(days):
        target=cache/filename(day)
        checksum=target.with_suffix('.sha256')
        if target.exists():
            payload=target.read_bytes()
            if not checksum.exists() or checksum.read_text()!=hashlib.sha256(payload).hexdigest():
                raise SafetyError('NSE cache integrity check failed for '+day)
        elif offline:
            raise SafetyError('Missing offline NSE report '+day)
        else:
            payload=download(day)
            parse_report(payload,day,symbols)
            temp=target.with_suffix('.tmp'); temp.write_bytes(payload); temp.replace(target)
            checksum.write_text(hashlib.sha256(payload).hexdigest())
            time.sleep(.15)
        rows.extend(parse_report(payload,day,symbols))
    # Unadjusted bhavcopy must not silently feed a split/bonus discontinuity into momentum.
    for symbol in symbols:
        history=[r for r in rows if r['symbol']==symbol]
        for previous,current in zip(history,history[1:]):
            if abs(current['open']/previous['close']-1)>config['max_gap']:
                raise SafetyError('Price discontinuity in NSE history for '+symbol+'; corporate action review required')
    return rows
