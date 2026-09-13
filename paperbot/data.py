import csv
import gzip
import json
import math
import os
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import IST, SafetyError, expected_session


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential to a redirect destination.
        return None


def read_url(url, token=None):
    allowed = ("https://api.upstox.com/v3/historical-candle/", "https://assets.upstox.com/market-quote/instruments/exchange/")
    if not url.startswith(allowed):
        raise SafetyError("Data client only allows Upstox historical data and instrument-list URLs")
    headers={"Accept":"application/json", "User-Agent":"LocalPaperTrial/1.0"}
    if token:
        if not url.startswith(allowed[0]): raise SafetyError("Credential destination refused")
        headers["Authorization"]="Bearer "+token
    for attempt in range(3):
        try:
            with build_opener(NoRedirect()).open(Request(url,headers=headers,method="GET"),timeout=20) as response:
                return response.read(40_000_000)
        except HTTPError as exc:
            if exc.code in (401,403):
                raise SafetyError("Upstox authorization failed. Generate a valid access token locally and check data entitlement.") from None
            if (exc.code==429 or exc.code>=500) and attempt<2:
                time.sleep(1+attempt); continue
            raise SafetyError("Upstox data request failed (HTTP %s); account state was not advanced" % exc.code) from None
        except (URLError, TimeoutError, OSError):
            if attempt<2:
                time.sleep(1+attempt); continue
            raise SafetyError("Cannot reach Upstox data service. Check internet connectivity.") from None


def resolve_instruments(config, state):
    cache = Path(state)/"instruments.json"
    wanted = config["symbols"]+[config["benchmark"]]
    if cache.exists():
        mapping=json.loads(cache.read_text())
        if set(mapping)==set(wanted): return mapping
    payload=read_url("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz")
    try:
        data=json.loads(gzip.decompress(payload))
    except (ValueError,OSError):
        raise SafetyError("Invalid Upstox instrument-list response") from None
    mapping={}
    for s in wanted:
        candidates=[r for r in data if r.get("segment")=="NSE_EQ" and r.get("trading_symbol")==s and r.get("instrument_type")=="EQ"]
        if len(candidates)!=1:
            raise SafetyError("Could not uniquely resolve NSE equity instrument "+s)
        mapping[s]=candidates[0]["instrument_key"]
    cache.write_text(json.dumps(mapping,indent=2))
    return mapping


def upstox_rows(config, state, token_path, now):
    # Reload each cycle, so replacing an expired token needs no restart.
    token=os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token and Path(token_path).exists(): token=Path(token_path).read_text().strip()
    if not token:
        raise SafetyError("Upstox is not connected. Run the local token setup and then check market data.")
    mapping=resolve_instruments(config,state)
    end=expected_session(now,config["market_holidays"])
    start=(date.fromisoformat(end)-timedelta(days=180)).isoformat()
    rows=[]
    for symbol,key in mapping.items():
        url="https://api.upstox.com/v3/historical-candle/"+quote(key,safe="")+"/days/1/"+end+"/"+start
        try:
            payload=json.loads(read_url(url,token))
            if payload.get("status")!="success": raise ValueError()
            for candle in payload["data"]["candles"]:
                day=datetime.fromisoformat(candle[0]).astimezone(IST).date().isoformat()
                if day>end: continue
                rows.append(dict(symbol=symbol,date=day,**dict(zip(("open","high","low","close","volume"),candle[1:6]))))
        except (ValueError,TypeError,KeyError,IndexError):
            raise SafetyError("Unexpected Upstox historical-data response for "+symbol) from None
        time.sleep(0.25)
    return rows


def csv_rows(path):
    with Path(path).open(newline="") as f:
        reader=csv.DictReader(f)
        required={"symbol","date","open","high","low","close","volume"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise SafetyError("CSV needs symbol,date,open,high,low,close,volume")
        return [{k:r[k] for k in required} for r in reader]


def demo_rows(config, extra_sessions=0):
    """Synthetic fixtures, not historical market prices and never performance evidence."""
    rng=random.Random(735)
    symbols=config["symbols"]+[config["benchmark"]]
    prices={s:250+250*i for i,s in enumerate(symbols)}
    rows=[]
    d=date(2026,5,4)
    count=0
    needed=80+extra_sessions
    while count<needed:
        if d.weekday()<5:
            market=rng.gauss(0.0003,0.004)
            for i,s in enumerate(symbols):
                drift=0.0012 if i%3==0 else (-0.0004 if i%3==1 else 0.0007)
                opened=prices[s]*math.exp(rng.gauss(0,0.0015))
                closed=opened*math.exp(market+drift+rng.gauss(0,0.003))
                rows.append({"symbol":s,"date":d.isoformat(),"open":round(opened,4),"high":round(max(opened,closed)*1.004,4),"low":round(min(opened,closed)*0.996,4),"close":round(closed,4),"volume":rng.randint(1000000,4000000)})
                prices[s]=closed
            count+=1
        d+=timedelta(days=1)
    return rows
