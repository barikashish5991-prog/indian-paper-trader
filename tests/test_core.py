import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paperbot.core import Engine, SafetyError, expected_session, fees, load_config
from paperbot.data import demo_rows, upstox_rows, read_url
from paperbot.service import Service

ROOT=Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.c=load_config(ROOT/"config.json")
        self.path=Path(self.tmp.name)/"ledger.sqlite3"
        self.e=Engine(self.path,self.c,"demo")

    def tearDown(self):
        self.e.close(); self.tmp.cleanup()

    def rows(self,n=0): return demo_rows(self.c,n)
    def now(self,n=0): return datetime.fromisoformat(max(r["date"] for r in self.rows(n))+"T17:10:00+05:30")
    def cycle(self,n=0): return self.e.cycle(self.rows(n),self.now(n))

    def test_warmup_does_not_fabricate_forward_trades(self):
        self.cycle()
        self.assertEqual(self.e.cash(),100000)
        self.assertEqual(len(self.e.report()["curve"]),1)
        self.assertEqual(len(self.e.report()["fills"]),0)
        self.assertTrue(self.e.report()["orders"])

    def test_fills_only_after_signal_session_and_include_fees(self):
        self.cycle(); self.cycle(1)
        report=self.e.report()
        self.assertTrue(report["fills"])
        self.assertLess(report["cash"],100000)
        for f in report["fills"]:
            order=next(o for o in report["orders"] if o["id"]==f["order_id"])
            self.assertLess(order["signal_day"],f["day"])
            self.assertGreater(f["fee"],0)
            self.assertGreater(f["price"],self.e.bar(f["symbol"],f["day"])["open"])
        self.e.reconcile()

    def test_duplicate_run_is_idempotent(self):
        self.cycle(); self.cycle(1)
        before=self.e.report()
        self.cycle(1)
        after=self.e.report()
        for key in ("cash","fills","orders","curve","decisions"):
            self.assertEqual(before[key],after[key])

    def test_restart_preserves_account(self):
        self.cycle();self.cycle(1)
        old=self.e.report();self.e.close()
        self.e=Engine(self.path,self.c,"demo");self.cycle(1)
        self.assertEqual(old["fills"],self.e.report()["fills"])
        self.assertEqual(old["cash"],self.e.cash())

    def test_configuration_cannot_change_under_account(self):
        c=copy.deepcopy(self.c);c["lookback"]+=1
        with self.assertRaises(SafetyError): Engine(self.path,c,"demo")

    def test_paper_and_demo_cannot_share_account(self):
        with self.assertRaises(SafetyError): Engine(self.path,self.c,"paper")

    def test_missing_symbol_blocks_atomically(self):
        self.cycle()
        rows=self.rows(1)[:-1]
        with self.assertRaises(SafetyError): self.e.cycle(rows,self.now(1))
        self.assertFalse(self.e.report()["fills"])
        self.assertEqual(len(self.e.report()["curve"]),1)

    def test_bad_ohlc_blocks(self):
        rows=self.rows();rows[-1]["high"]=1
        with self.assertRaises(SafetyError): self.e.cycle(rows,self.now())
        self.assertEqual(self.e.db.execute("SELECT COUNT(*) FROM bars").fetchone()[0],0)

    def test_nan_blocks(self):
        rows=self.rows();rows[-1]["close"]=float("nan")
        with self.assertRaises(SafetyError): self.e.cycle(rows,self.now())

    def test_revised_history_blocks(self):
        self.cycle();rows=self.rows(1);rows[0]["volume"]+=100
        with self.assertRaises(SafetyError): self.e.cycle(rows,self.now(1))
        self.assertFalse(self.e.report()["fills"])

    def test_pause_cancels_queued_entries(self):
        self.cycle();self.e.pause(True);self.cycle(1)
        self.assertFalse(self.e.report()["fills"])
        self.assertTrue(all(o["status"]=="CANCELLED" for o in self.e.report()["orders"]))

    def test_loss_limit_latches(self):
        self.cycle()
        with self.e.db: self.e.db.execute("INSERT INTO cashflows VALUES('loss','test',-6000,'test')")
        self.cycle(1)
        self.assertTrue(self.e.report()["risk_halted"])
        self.e.pause(False)
        self.assertTrue(self.e.report()["risk_halted"])
        self.assertFalse(self.e.report()["fills"])

    def test_large_gap_blocks_without_partial_commit(self):
        self.cycle();rows=self.rows(1)
        last=max(r["date"] for r in rows)
        for r in rows:
            if r["date"]==last and r["symbol"]==self.c["symbols"][0]:
                for k in ("open","high","low","close"): r[k]*=0.5
        with self.assertRaises(SafetyError): self.e.cycle(rows,self.now(1))
        self.assertFalse(self.e.report()["fills"])
        self.assertEqual(self.e.cash(),100000)

    def test_trial_expiration_does_not_need_data(self):
        self.cycle()
        self.e.cycle([],self.now()+timedelta(days=7))
        self.assertTrue(self.e.report()["trial_complete"])
        self.assertTrue(all(o["status"]=="EXPIRED" for o in self.e.report()["orders"]))
        self.assertEqual(self.e.cycle([],self.now()+timedelta(days=8)),"Trial complete; create a new account for another experiment")

    def test_forged_cashflow_detected(self):
        self.cycle();self.cycle(1)
        with self.e.db: self.e.db.execute("UPDATE cashflows SET amount=amount+1 WHERE kind='trade'")
        with self.assertRaises(SafetyError): self.e.reconcile()

    def test_no_future_features(self):
        self.cycle()
        day=self.e.meta("last_day")
        decisions=self.e.report()["decisions"]
        for d in decisions:
            f=json.loads(d["features"])
            self.assertEqual(f["close"],self.e.bar(d["symbol"],day)["close"])

    def test_cash_limit_rejects_buy(self):
        self.cycle()
        order=dict(self.e.db.execute("SELECT * FROM orders LIMIT 1").fetchone())
        order["qty"]=10000000
        reason=self.e.risk_reject(order,order["signal_day"],100)
        self.assertEqual(reason,"Insufficient cash including fees")

    def test_sell_remains_allowed_during_entry_pause(self):
        self.cycle();self.cycle(1)
        symbol,p=next(iter(self.e.positions().items()))
        with self.e.db:
            self.e.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",("exit-test",self.e.meta("last_day"),self.now(1).isoformat(),symbol,"SELL",p["qty"],"PENDING","Test planned exit",1e9))
        cash=self.e.cash();self.e.pause(True);self.cycle(2)
        self.assertNotIn(symbol,self.e.positions())
        self.assertGreater(self.e.cash(),cash)
        self.e.reconcile()

    def test_mid_transaction_failure_rolls_back_every_fill(self):
        self.cycle()
        self.e.db.execute("CREATE TRIGGER fail_cash BEFORE INSERT ON cashflows WHEN NEW.kind='trade' BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
        with self.assertRaises(Exception):self.cycle(1)
        self.assertFalse(self.e.report()["fills"])
        self.assertTrue(all(o["status"]=="PENDING" for o in self.e.report()["orders"]))
        self.assertEqual(self.e.cash(),100000)
        self.e.db.execute("DROP TRIGGER fail_cash")
        self.cycle(1);self.e.reconcile()

    def test_sector_limit_at_execution(self):
        self.cycle()
        with self.e.db:
            self.e.db.execute("UPDATE orders SET status='CANCELLED' WHERE symbol!='TCS'")
        self.cycle(1)
        order={"side":"BUY","symbol":"INFY","qty":100,"adv":1e9}
        self.assertEqual(self.e.risk_reject(order,self.e.meta("last_day"),100),"Sector exposure limit")

    def test_paper_rejects_stale_or_future_data(self):
        paper=Engine(Path(self.tmp.name)/"paper.db",self.c,"paper")
        try:
            with self.assertRaises(SafetyError): paper.cycle(self.rows(),self.now()+timedelta(days=4))
            with self.assertRaises(SafetyError): paper.cycle(self.rows(),self.now()-timedelta(days=1))
            self.assertEqual(paper.cash(),100000)
        finally: paper.close()

    def test_no_backdated_decisions_after_downtime(self):
        self.cycle();self.cycle(3)
        decision_days={d["day"] for d in self.e.report()["decisions"]}
        self.assertEqual(decision_days,{self.now().date().isoformat(),self.now(3).date().isoformat()})
        self.assertEqual(len(self.e.report()["curve"]),4)

    def test_paper_signal_created_after_open_is_not_filled(self):
        self.cycle()
        with self.e.db:
            self.e.db.execute("UPDATE orders SET created_at=?",(self.now(1).isoformat(),))
        self.e.mode="paper"
        self.cycle(1)
        self.assertFalse(self.e.report()["fills"])
        self.assertTrue(all(o["status"]=="EXPIRED" for o in self.e.report()["orders"]))


class AdapterTests(unittest.TestCase):
    def test_weekends_holidays_and_incomplete_day(self):
        holidays=["2026-09-14"]
        for at in ("2026-09-12T18:00:00+05:30","2026-09-14T18:00:00+05:30","2026-09-15T10:00:00+05:30"):
            self.assertEqual(expected_session(datetime.fromisoformat(at),holidays),"2026-09-11")

    def test_cost_schedule(self):
        c=load_config(ROOT/"config.json")
        self.assertAlmostEqual(fees(10000,"BUY",c),35.47,places=2)
        self.assertAlmostEqual(fees(10000,"SELL",c),57.57,places=2)

    def test_upstox_endpoint_is_get_data_only(self):
        with self.assertRaises(SafetyError): read_url("https://api.upstox.com/v2/order/place", "secret")
        with self.assertRaises(SafetyError): read_url("https://example.org/", "secret")

    def test_missing_token_is_clear_and_no_network(self):
        c=load_config(ROOT/"config.json")
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ",{},clear=True), patch("paperbot.data.read_url") as get:
            with self.assertRaisesRegex(SafetyError,"not connected"):
                upstox_rows(c,tmp,Path(tmp)/"missing",datetime.now(timezone.utc))
            get.assert_not_called()

    def test_upstox_parser_and_routing(self):
        c=load_config(ROOT/"config.json");symbols=c["symbols"]+[c["benchmark"]]
        payload={"status":"success","data":{"candles":[["2026-09-11T00:00:00+05:30",100,102,99,101,200000,0]]}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ",{"UPSTOX_ACCESS_TOKEN":"test-token"}), patch("paperbot.data.resolve_instruments",return_value={s:"NSE_EQ|TEST"+s for s in symbols}), patch("paperbot.data.read_url",return_value=json.dumps(payload).encode()) as get, patch("paperbot.data.time.sleep"):
            rows=upstox_rows(c,tmp,Path(tmp)/"missing",datetime.fromisoformat("2026-09-12T18:00:00+05:30"))
            self.assertEqual(len(rows),len(symbols));self.assertEqual(rows[0]["close"],101)
            for call in get.call_args_list:
                self.assertIn("/v3/historical-candle/NSE_EQ%7C",call.args[0])
                self.assertIn("/days/1/2026-09-11/",call.args[0])

    def test_service_provider_cannot_change(self):
        c=load_config(ROOT/"config.json")
        with tempfile.TemporaryDirectory() as tmp:
            Service(c,tmp,"csv")
            with self.assertRaises(SafetyError): Service(c,tmp,"upstox")


if __name__=="__main__": unittest.main()
