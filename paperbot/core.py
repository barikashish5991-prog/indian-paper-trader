import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ENGINE_VERSION = "paper-engine-1.0"


class SafetyError(Exception):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def load_config(path):
    c = json.loads(Path(path).read_text())
    if not 0 < c["initial_cash"] <= 1e9:
        raise SafetyError("Invalid virtual capital")
    if not 0 < c["max_position_weight"] <= c["max_gross_weight"] <= 1:
        raise SafetyError("Invalid exposure limits")
    if not 0 < c["max_sector_weight"] <= 1:
        raise SafetyError("Invalid sector limit")
    if not c["symbols"] or len(set(c["symbols"])) != len(c["symbols"]):
        raise SafetyError("Universe must contain unique symbols")
    if c["benchmark"] in c["symbols"]:
        raise SafetyError("Benchmark must be separate from the trading universe")
    if any(s not in c["sectors"] for s in c["symbols"]):
        raise SafetyError("Missing sector classification")
    for key in ("max_positions", "lookback", "trend_window", "rebalance_sessions", "trial_calendar_days"):
        if not isinstance(c[key], int) or c[key] < 1:
            raise SafetyError("Invalid " + key)
    for key in ("slippage_bps", "brokerage_per_order", "stt_each_side", "exchange_rate", "sebi_rate", "stamp_buy", "gst", "dp_sell_including_gst", "monthly_operating_budget", "min_adv_rupees"):
        if not isinstance(c[key], (int, float)) or not math.isfinite(c[key]) or c[key] < 0:
            raise SafetyError("Invalid " + key)
    for key in ("max_drawdown", "max_daily_loss", "max_gap", "max_order_adv_fraction"):
        if not 0 < c[key] < 1:
            raise SafetyError("Invalid " + key)
    for d in c["market_holidays"] + c["corporate_action_dates"]:
        date.fromisoformat(d)
    return c


def fees(notional, side, c):
    brokerage = min(c["brokerage_per_order"], notional * 0.025)
    exchange = notional * c["exchange_rate"]
    sebi = notional * c["sebi_rate"]
    return round(brokerage + exchange + sebi + notional * c["stt_each_side"]
                 + (notional * c["stamp_buy"] if side == "BUY" else c["dp_sell_including_gst"])
                 + c["gst"] * (brokerage + exchange + sebi), 2)


def expected_session(now, holidays):
    local = now.astimezone(IST)
    d = local.date()
    if (local.hour, local.minute) < (17, 0):
        d -= timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d -= timedelta(days=1)
    return d.isoformat()


class Engine:
    def __init__(self, db_path, config, mode):
        if mode not in ("demo", "paper"):
            raise SafetyError("Only demo or paper mode is available")
        self.c, self.mode = config, mode
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bars(symbol TEXT, day TEXT, open REAL, high REAL, low REAL,
          close REAL, volume REAL, received_at TEXT, PRIMARY KEY(symbol,day));
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, signal_day TEXT, created_at TEXT,
          symbol TEXT, side TEXT, qty INTEGER, status TEXT, reason TEXT, adv REAL);
        CREATE TABLE IF NOT EXISTS fills(order_id TEXT PRIMARY KEY REFERENCES orders(id), day TEXT,
          symbol TEXT, side TEXT, qty INTEGER, price REAL, fee REAL, slippage REAL, observed_at TEXT);
        CREATE TABLE IF NOT EXISTS cashflows(id TEXT PRIMARY KEY, day TEXT, amount REAL, kind TEXT);
        CREATE TABLE IF NOT EXISTS decisions(day TEXT, symbol TEXT, action TEXT, reason TEXT,
          features TEXT, PRIMARY KEY(day,symbol));
        CREATE TABLE IF NOT EXISTS equity(day TEXT PRIMARY KEY, cash REAL, holdings REAL, equity REAL,
          benchmark REAL, gross REAL, drawdown REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, level TEXT, message TEXT);
        """)
        digest = hashlib.sha256(json.dumps({"config": config, "engine": ENGINE_VERSION}, sort_keys=True).encode()).hexdigest()
        old = self.meta("config_hash")
        if old and (old != digest or self.meta("mode") != mode):
            self.db.close()
            raise SafetyError("Configuration/version differs from this account. Use a new account directory; do not mix trial records.")
        if not old:
            with self.db:
                self.set_meta("config_hash", digest)
                self.set_meta("mode", mode)
                self.set_meta("paused", "0")
                self.db.execute("INSERT INTO cashflows VALUES(?,?,?,?)", ("initial", "initial", config["initial_cash"], "deposit"))

    def close(self):
        self.db.close()

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))

    def event(self, level, message):
        self.db.execute("INSERT INTO events(at,level,message) VALUES(?,?,?)", (timestamp(), level, message))

    def pause(self, paused):
        with self.db:
            self.set_meta("paused", "1" if paused else "0")
            if paused:
                self.db.execute("UPDATE orders SET status='CANCELLED', reason='User paused new entries' WHERE side='BUY' AND status='PENDING'")
            self.event("WARNING" if paused else "INFO", "New entries paused" if paused else "User enabled new entries; automatic risk limits still apply")

    def failure(self, message):
        with self.db:
            self.set_meta("last_error", message)
            self.set_meta("last_check", timestamp())
            self.event("ERROR", message)

    def cash(self):
        return round(self.db.execute("SELECT COALESCE(SUM(amount),0) FROM cashflows").fetchone()[0], 2)

    def positions(self):
        result = {}
        for f in self.db.execute("SELECT * FROM fills ORDER BY day, rowid"):
            p = result.setdefault(f["symbol"], {"qty": 0, "cost": 0.0})
            if f["side"] == "BUY":
                p["qty"] += f["qty"]
                p["cost"] += f["qty"] * f["price"] + f["fee"]
            else:
                if f["qty"] > p["qty"]:
                    raise SafetyError("Ledger contains a short position")
                p["cost"] *= (p["qty"] - f["qty"]) / p["qty"]
                p["qty"] -= f["qty"]
        return {s: p for s, p in result.items() if p["qty"]}

    def reconcile(self):
        if self.cash() < -0.01:
            raise SafetyError("Cash ledger is negative")
        fills = self.db.execute("SELECT * FROM fills").fetchall()
        for f in fills:
            expected = round((1 if f["side"] == "SELL" else -1) * f["qty"] * f["price"] - f["fee"], 2)
            row = self.db.execute("SELECT amount FROM cashflows WHERE id=?", (f["order_id"],)).fetchone()
            order = self.db.execute("SELECT status,qty,symbol,side FROM orders WHERE id=?", (f["order_id"],)).fetchone()
            if not row or abs(row[0] - expected) > 0.001 or not order or order[0] != "FILLED" or tuple(order)[1:] != (f["qty"], f["symbol"], f["side"]):
                raise SafetyError("Order, fill and cash ledger mismatch")
        if self.db.execute("SELECT COUNT(*) FROM orders WHERE status='FILLED'").fetchone()[0] != len(fills):
            raise SafetyError("Filled order is missing its fill")
        if self.db.execute("SELECT COUNT(*) FROM cashflows WHERE kind='trade'").fetchone()[0] != len(fills):
            raise SafetyError("Orphan trade cashflow")
        self.positions()

    def ingest(self, rows, now):
        universe = self.c["symbols"] + [self.c["benchmark"]]
        seen, days_by_symbol = set(), {s: set() for s in universe}
        for r in rows:
            s, d = r["symbol"], r["date"]
            if s not in days_by_symbol or (s, d) in seen:
                raise SafetyError("Unexpected or duplicate bar")
            date.fromisoformat(d)
            values = [float(r[k]) for k in ("open", "high", "low", "close", "volume")]
            o, h, l, cl, v = values
            if not all(math.isfinite(x) for x in values) or min(o, h, l, cl) <= 0 or v <= 0 or not l <= min(o, cl) <= max(o, cl) <= h:
                raise SafetyError("Invalid OHLCV for " + s)
            if self.mode == "paper" and d > expected_session(now, self.c["market_holidays"]):
                raise SafetyError("Incomplete or future daily bar")
            seen.add((s, d)); days_by_symbol[s].add(d)
        first = days_by_symbol[universe[0]]
        if len(first) < max(self.c["lookback"] + 1, self.c["trend_window"]):
            raise SafetyError("Not enough warm-up history")
        if any(ds != first for ds in days_by_symbol.values()):
            raise SafetyError("Inconsistent sessions across instruments; no trades processed")
        latest = max(first)
        if self.mode == "paper" and latest != expected_session(now, self.c["market_holidays"]):
            raise SafetyError("Daily data is stale, or the exchange holiday calendar needs updating")
        # Immutable prices prevent silent rewrites of the forward record.
        for r in rows:
            vals = tuple(float(r[k]) for k in ("open", "high", "low", "close", "volume"))
            old = self.db.execute("SELECT open,high,low,close,volume FROM bars WHERE symbol=? AND day=?", (r["symbol"], r["date"])).fetchone()
            if old and any(abs(a-b) > max(0.0001, abs(a)*1e-8) for a,b in zip(old, vals)):
                raise SafetyError("Previously stored data changed for " + r["symbol"] + "; investigate corrections/corporate actions")
            self.db.execute("INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?,?)", (r["symbol"], r["date"]) + vals + (now.isoformat(),))
        return sorted(first)

    def bar(self, symbol, day):
        row = self.db.execute("SELECT * FROM bars WHERE symbol=? AND day=?", (symbol, day)).fetchone()
        if not row:
            raise SafetyError("Missing valuation price for " + symbol)
        return row

    def value(self, day, field="close"):
        positions = self.positions()
        holdings = sum(p["qty"] * self.bar(s, day)[field] for s,p in positions.items())
        return self.cash() + holdings, holdings

    def risk_reject(self, order, day, price):
        c = self.c
        if order["side"] == "SELL":
            return None if self.positions().get(order["symbol"], {}).get("qty", 0) >= order["qty"] else "Insufficient position"
        if self.meta("paused") == "1" or self.meta("risk_halted") == "1":
            return "New entries paused"
        positions = self.positions()
        equity, gross = self.value(day, "open")
        notional = order["qty"] * price
        symbol = order["symbol"]
        current = positions.get(symbol, {}).get("qty", 0) * self.bar(symbol, day)["open"]
        sector = sum(p["qty"] * self.bar(s, day)["open"] for s,p in positions.items() if c["sectors"][s] == c["sectors"][symbol])
        cost = fees(notional, "BUY", c)
        if notional + cost > self.cash(): return "Insufficient cash including fees"
        if notional > order["adv"] * c["max_order_adv_fraction"]: return "Liquidity participation limit"
        if current + notional > equity * c["max_position_weight"]: return "Position limit at execution price"
        if gross + notional > equity * c["max_gross_weight"]: return "Gross exposure limit"
        if sector + notional > equity * c["max_sector_weight"]: return "Sector exposure limit"
        if symbol not in positions and len(positions) >= c["max_positions"]: return "Position count limit"
        return None

    def execute_pending(self, day, now):
        orders = self.db.execute("SELECT * FROM orders WHERE status='PENDING' AND signal_day<? ORDER BY CASE side WHEN 'SELL' THEN 0 ELSE 1 END,id", (day,)).fetchall()
        for order in orders:
            if self.mode == "paper":
                opened = datetime.fromisoformat(day + "T09:15:00+05:30")
                if datetime.fromisoformat(order["created_at"]) >= opened:
                    self.db.execute("UPDATE orders SET status='EXPIRED',reason='Signal did not exist before session open' WHERE id=?", (order["id"],))
                    continue
            # Orders get a single next-session fill attempt; no phantom repeated attempts.
            b = self.bar(order["symbol"], day)
            price = round(b["open"] * (1 + (1 if order["side"] == "BUY" else -1) * self.c["slippage_bps"] / 10000), 4)
            reason = self.risk_reject(order, day, price)
            if reason:
                self.db.execute("UPDATE orders SET status='REJECTED',reason=? WHERE id=?", (reason, order["id"]))
                continue
            notional = order["qty"] * price
            charge = fees(notional, order["side"], self.c)
            self.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)", (order["id"], day, order["symbol"], order["side"], order["qty"], price, charge, abs(price-b["open"])*order["qty"], now.isoformat()))
            cashflow = round((1 if order["side"] == "SELL" else -1) * notional - charge, 2)
            self.db.execute("INSERT INTO cashflows VALUES(?,?,?,?)", (order["id"], day, cashflow, "trade"))
            self.db.execute("UPDATE orders SET status='FILLED',reason='Simulated next-session open with adverse slippage' WHERE id=?", (order["id"],))

    def check_losses(self, day, field="close"):
        equity, _ = self.value(day, field)
        peak = float(self.meta("peak", self.c["initial_cash"]))
        prev = self.db.execute("SELECT equity FROM equity ORDER BY day DESC LIMIT 1").fetchone()
        prev = prev[0] if prev else self.c["initial_cash"]
        if equity <= peak * (1-self.c["max_drawdown"]) or equity <= prev*(1-self.c["max_daily_loss"]):
            if self.meta("risk_halted") != "1": self.event("WARNING", "Loss limit triggered: new entries halted for this trial; existing positions remain exposed")
            self.set_meta("risk_halted", "1")
            self.db.execute("UPDATE orders SET status='CANCELLED',reason='Loss limit triggered' WHERE status='PENDING' AND side='BUY'")

    def decide(self, day, now, index):
        c = self.c
        features = {}
        candidates = []
        for s in c["symbols"]:
            hist = self.db.execute("SELECT close,volume FROM bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT ?", (s, day, max(c["lookback"]+1,c["trend_window"]))).fetchall()
            closes = [r[0] for r in hist]
            momentum = closes[0]/closes[c["lookback"]]-1
            average = sum(closes[:c["trend_window"]])/c["trend_window"]
            adv = sum(r[0]*r[1] for r in hist[:20])/len(hist[:20])
            features[s] = {"momentum": momentum, "close": closes[0], "trend_average": average, "adv_rupees": adv, "version": c["version"], "available_at": now.isoformat()}
            if momentum > 0 and closes[0] > average and adv >= c["min_adv_rupees"]:
                candidates.append(s)
        candidates.sort(key=lambda s: (-features[s]["momentum"], s))
        selected, sector_weights = [], {}
        # Reserve 5% of each position allowance for next-open price uncertainty.
        weight = min(c["max_position_weight"], c["max_gross_weight"]/c["max_positions"])*0.95
        for s in candidates:
            sector = c["sectors"][s]
            if len(selected)<c["max_positions"] and sector_weights.get(sector,0)+weight <= c["max_sector_weight"]:
                selected.append(s); sector_weights[sector] = sector_weights.get(sector,0)+weight
        rebalance = index % c["rebalance_sessions"] == 0
        positions = self.positions()
        equity, _ = self.value(day)
        for s in c["symbols"]:
            held = positions.get(s, {}).get("qty", 0)
            action, reason, qty = "HOLD" if held else "SKIP", "Between scheduled rebalances", 0
            if rebalance:
                target = math.floor(equity*weight/features[s]["close"]) if s in selected else 0
                diff = target-held
                if diff>0 and (self.meta("paused")=="1" or self.meta("risk_halted")=="1"):
                    reason = "New entries paused by user or loss limit"
                elif diff:
                    action, qty = ("BUY" if diff>0 else "SELL"), abs(diff)
                    reason = "Positive momentum, above trend, liquidity and sector filters passed" if diff>0 else "Scheduled portfolio rebalance"
                else:
                    reason = "Already at target allocation" if held else "Not selected: trend, momentum, liquidity or sector filter"
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (day,s,action,reason,json.dumps(features[s],sort_keys=True)))
            if qty:
                order_id = hashlib.sha256((self.meta("config_hash")+day+s+action).encode()).hexdigest()[:24]
                self.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)", (order_id,day,now.isoformat(),s,action,qty,"PENDING",reason,features[s]["adv_rupees"]))

    def cycle(self, rows, now=None):
        now = now or datetime.now(timezone.utc)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.reconcile()
            if self.meta("trial_complete") == "1":
                self.db.commit()
                return "Trial complete; create a new account for another experiment"
            if self.meta("expires_at") and now >= datetime.fromisoformat(self.meta("expires_at")):
                self.set_meta("trial_complete", "1")
                self.db.execute("UPDATE orders SET status='EXPIRED',reason='Trial window ended' WHERE status='PENDING'")
                self.event("INFO", "Trial window ended. Last recorded marks are retained; open positions are not liquidated.")
                self.db.commit()
                return "Trial complete"
            sessions = self.ingest(rows, now)
            latest = sessions[-1]
            last = self.meta("last_day")
            if last and latest < last:
                raise SafetyError("Feed moved backwards")
            if last == latest:
                self.set_meta("last_check", now.isoformat()); self.set_meta("last_error", "")
                self.db.commit(); return "No new completed session"
            if not last:
                self.set_meta("start_day", latest)
                self.set_meta("started_at", now.isoformat())
                self.set_meta("expires_at", (now+timedelta(days=self.c["trial_calendar_days"])).isoformat())
                self.set_meta("benchmark_start", self.bar(self.c["benchmark"], latest)["close"])
                self.set_meta("peak", self.c["initial_cash"])
                self.set_meta("session_count", 0)
                new_days = [latest]
                self.event("INFO", "Trial initialized; historical data used only for warm-up, not reported as forward profit")
            else:
                if last not in sessions:
                    raise SafetyError("Feed lacks overlap with last processed session")
                new_days = [d for d in sessions if d > last]
                if self.mode == "paper":
                    expected = date.fromisoformat(last) + timedelta(days=1)
                    while expected <= date.fromisoformat(latest):
                        if expected.weekday() < 5 and expected.isoformat() not in self.c["market_holidays"] and expected.isoformat() not in new_days:
                            raise SafetyError("Missing intervening session; verify the exchange calendar and feed")
                        expected += timedelta(days=1)
            # Recover only orders that already existed. Never create backdated decisions.
            for day in new_days:
                prior = self.db.execute("SELECT MAX(day) FROM bars WHERE symbol=? AND day<?", (self.c["benchmark"],day)).fetchone()[0]
                if last:
                    if day in self.c["corporate_action_dates"]:
                        raise SafetyError("Declared corporate action: manual review required before processing")
                    for s in self.c["symbols"] + [self.c["benchmark"]]:
                        if abs(self.bar(s,day)["open"]/self.bar(s,prior)["close"]-1) > self.c["max_gap"]:
                            raise SafetyError("Large price discontinuity for "+s+": review corporate actions or bad data")
                    self.check_losses(day,"open")
                    self.execute_pending(day,now)
                    elapsed=(date.fromisoformat(day)-date.fromisoformat(self.meta("last_day"))).days
                    operating=round(self.c["monthly_operating_budget"]*12/365*elapsed,2)
                    if operating:
                        if operating > self.cash(): raise SafetyError("Operating budget exceeds available cash")
                        self.db.execute("INSERT INTO cashflows VALUES(?,?,?,?)",("operating:"+day,day,-operating,"operating"))
                self.check_losses(day)
                equity, holdings = self.value(day)
                peak=max(float(self.meta("peak")),equity)
                self.set_meta("peak",peak)
                benchmark=self.c["initial_cash"]*self.bar(self.c["benchmark"],day)["close"]/float(self.meta("benchmark_start"))
                self.db.execute("INSERT INTO equity VALUES(?,?,?,?,?,?,?)",(day,self.cash(),holdings,equity,benchmark,holdings/equity if equity else 0,1-equity/peak))
                count=int(self.meta("session_count"))+(1 if last else 0)
                self.set_meta("session_count",count)
                self.set_meta("last_day",day)
                if day==latest and now<datetime.fromisoformat(self.meta("expires_at")):
                    self.decide(day,now,count)
                elif day!=latest:
                    self.event("WARNING","Recovered accounting for "+day+"; no backdated signals created")
                last=day
            if now>=datetime.fromisoformat(self.meta("expires_at")):
                self.set_meta("trial_complete","1")
                self.db.execute("UPDATE orders SET status='EXPIRED',reason='Trial window ended' WHERE status='PENDING'")
                self.event("INFO","Trial window ended; no new decisions. Review results before a new trial.")
            self.reconcile()
            self.set_meta("last_check",now.isoformat()); self.set_meta("last_error","")
            self.event("INFO","Session "+latest+" processed and paper ledger reconciled")
            self.db.commit()
            return "Processed " + latest
        except Exception:
            self.db.rollback()
            raise

    def report(self):
        curve=[dict(r) for r in self.db.execute("SELECT * FROM equity ORDER BY day")]
        latest=curve[-1] if curve else None
        positions=[]
        if latest:
            for s,p in self.positions().items():
                price=self.bar(s,latest["day"])["close"]
                positions.append({"symbol":s,"qty":p["qty"],"average":p["cost"]/p["qty"],"last":price,"value":p["qty"]*price,"unrealized":p["qty"]*price-p["cost"]})
        def query(sql): return [dict(r) for r in self.db.execute(sql)]
        costs=self.db.execute("SELECT COALESCE(SUM(fee),0),COALESCE(SUM(slippage),0) FROM fills").fetchone()
        return {"mode":self.mode,"strategy":self.c["version"],"fingerprint":self.meta("config_hash")[:12],"cash":self.cash(),"initial":self.c["initial_cash"],"latest":latest,"curve":curve,"positions":positions,"orders":query("SELECT * FROM orders ORDER BY signal_day DESC,rowid DESC LIMIT 100"),"fills":query("SELECT * FROM fills ORDER BY day DESC,rowid DESC LIMIT 100"),"decisions":query("SELECT * FROM decisions ORDER BY day DESC,symbol LIMIT 150"),"events":query("SELECT * FROM events ORDER BY id DESC LIMIT 40"),"paused":self.meta("paused")=="1","risk_halted":self.meta("risk_halted")=="1","last_check":self.meta("last_check"),"last_error":self.meta("last_error",""),"started_at":self.meta("started_at"),"expires_at":self.meta("expires_at"),"trial_complete":self.meta("trial_complete")=="1","sessions":int(self.meta("session_count",0)),"fees":costs[0],"slippage":costs[1],"config":self.c}
