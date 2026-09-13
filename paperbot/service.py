import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .core import Engine, SafetyError, expected_session
from .data import csv_rows, demo_rows, upstox_rows
from .nse import nse_rows


class Service:
    def __init__(self, config, state, provider, token_path=None, csv_path=None):
        if provider not in ("demo","upstox","csv","nse"):
            raise SafetyError("Unsupported data provider")
        self.config,self.state,self.provider=config,Path(state),provider
        self.state.mkdir(parents=True,exist_ok=True)
        self.token_path=Path(token_path or self.state/"upstox.token")
        self.csv_path=csv_path
        self.lock=threading.Lock()
        self.db_path=self.state/"ledger.sqlite3"
        e=self.engine()
        with e.db:
            old=e.meta("provider")
            if old and old!=provider: raise SafetyError("Cannot change the data provider of an existing account")
            e.set_meta("provider",provider)
        e.close()

    def engine(self):
        return Engine(self.db_path,self.config,"demo" if self.provider=="demo" else "paper")

    def sync(self, advance=False):
        if not self.lock.acquire(blocking=False):
            return "A data check is already running"
        e=None
        try:
            e=self.engine()
            now=datetime.now(timezone.utc)
            if self.provider=="demo":
                count=int(e.meta("session_count",0))
                rows=demo_rows(self.config,count+(1 if advance and e.meta("last_day") else 0))
                day=max(r["date"] for r in rows)
                now=datetime.fromisoformat(day+"T17:10:00+05:30")
            elif e.meta("expires_at") and now>=datetime.fromisoformat(e.meta("expires_at")):
                rows=[]  # Engine finalizes without requiring an expired credential.
            elif self.provider=="upstox":
                rows=upstox_rows(self.config,self.state,self.token_path,now)
            elif self.provider=="nse":
                rows=nse_rows(self.config,self.state,now)
            else:
                rows=csv_rows(self.csv_path)
            result=e.cycle(rows,now)
            self.export(e)
            return result
        except Exception as exc:
            message=str(exc) if isinstance(exc,SafetyError) else "Local processing failed ("+type(exc).__name__+"); inspect data and configuration"
            if e:
                e.failure(message)
                self.export(e)
            raise SafetyError(message) from None
        finally:
            if e: e.close()
            self.lock.release()

    def report(self):
        e=self.engine()
        try:
            r=e.report()
            r["provider"]=self.provider
            r["busy"]=self.lock.locked()
            r["expected_session"]=expected_session(datetime.now(timezone.utc),self.config["market_holidays"])
            r["stale"]=bool(self.provider!="demo" and r["latest"] and r["latest"]["day"]<r["expected_session"])
            return r
        finally: e.close()

    def action(self,name):
        if name=="sync": return self.sync(advance=self.provider=="demo")
        if name not in ("pause","resume"): raise SafetyError("Unknown action")
        with self.lock:
            e=self.engine()
            try:
                e.pause(name=="pause"); self.export(e)
            finally: e.close()
        return "New entries paused" if name=="pause" else "New entries enabled within risk limits"

    def export(self,e):
        path=self.state/"trial-report.json"
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps(e.report(),indent=2,allow_nan=False))
        temp.replace(path)
