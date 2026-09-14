import copy
import csv
import io
import json
import os
import smtplib
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from paperbot.core import Engine,SafetyError,load_config
from paperbot.data import demo_rows
from paperbot.hosted import phase,run
from paperbot.mail import deliver,initialize,queue,send_message,settings
from paperbot.nse import ISINS,filename,parse_report,nse_rows

ROOT=Path(__file__).resolve().parents[1]


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.c=load_config(ROOT/'hosted-config.json')
        self.now=datetime.fromisoformat('2026-08-21T17:30:00+05:30')
        self.fetch=lambda *args:demo_rows(self.c)

    def connection(self):
        db=sqlite3.connect(self.state/'ledger.sqlite3'); self.addCleanup(db.close); return db

    def test_calendar(self):
        for s,expected in [('2026-09-14T09:17:00+05:30','closed'),('2026-09-15T09:17:00+05:30','open'),
                           ('2026-09-15T17:17:00+05:30','evening'),('2026-09-15T08:00:00+05:30','closed'),
                           ('2026-09-13T09:17:00+05:30','closed')]:
            self.assertEqual(phase(datetime.fromisoformat(s),self.c),expected)
        with self.assertRaises(SafetyError): phase(datetime.fromisoformat('2027-01-04T09:17:00+05:30'),self.c)

    def test_open_notification_idempotent_without_fetch(self):
        now=datetime.fromisoformat('2026-09-15T09:17:00+05:30')
        def unexpected(*args): raise AssertionError('Morning must not create EOD signals')
        for _ in range(2): r,failed=run(self.c,self.state,now,unexpected)
        self.assertFalse(failed); self.assertIsNone(r['latest'])
        db=self.connection()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'open:%'").fetchone()[0],1)

    def test_forward_fills_report_and_email_ids(self):
        r,failed=run(self.c,self.state,self.now,self.fetch)
        self.assertFalse(failed); self.assertFalse(r['fills'])
        next_day=datetime.fromisoformat('2026-08-24T17:30:00+05:30')
        for _ in range(2): r,failed=run(self.c,self.state,next_day,lambda *args:demo_rows(self.c,1))
        self.assertFalse(failed); self.assertTrue(r['fills'])
        db=self.connection()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'fill:%'").fetchone()[0],len(r['fills']))
        self.assertIn('not live transactions',(self.state/'REPORT.md').read_text())

    def test_failed_data_does_not_advance_and_notifies_once(self):
        run(self.c,self.state,self.now,self.fetch)
        next_day=datetime.fromisoformat('2026-08-24T17:30:00+05:30')
        def broken(*args): raise SafetyError('Unavailable report')
        for _ in range(2): r,failed=run(self.c,self.state,next_day,broken)
        self.assertTrue(failed); self.assertEqual(r['latest']['day'],'2026-08-21')
        self.assertEqual(self.connection().execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'error:%'").fetchone()[0],1)

    def test_email_retry_and_sent_receipts(self):
        db=self.connection(); initialize(db)
        queue(db,'x','Started','Body',self.now.isoformat())
        def fail(*args): raise SafetyError('SMTP unavailable')
        with self.assertRaises(SafetyError): deliver(db,self.now.isoformat(),fail)
        self.assertIsNone(db.execute('SELECT delivered_at FROM outbox').fetchone()[0])
        sent=[]
        self.assertEqual(deliver(db,self.now.isoformat(),lambda *args:sent.append(args)),1)
        self.assertEqual(deliver(db,self.now.isoformat(),lambda *args:sent.append(args)),0)
        self.assertEqual(len(sent),1)

    def test_email_requires_tls_and_single_recipient(self):
        env=dict(SMTP_HOST='smtp.example.invalid',SMTP_USER='test',SMTP_PASSWORD='test-only',EMAIL_FROM='from@example.invalid',ALERT_EMAIL='to@example.invalid')
        with patch.dict(os.environ,env),patch('paperbot.mail.smtplib.SMTP') as transport:
            smtp=transport.return_value.__enter__.return_value
            smtp.send_message.return_value={}
            send_message('[PAPER ONLY] Test','test body','id')
            smtp.starttls.assert_called_once()
            smtp.login.assert_called_once_with('test','test-only')
            smtp.send_message.assert_called_once()
        with patch.dict(os.environ,dict(env,ALERT_EMAIL='a@example.invalid,b@example.invalid')):
            with self.assertRaises(SafetyError): settings()

    def test_gmail_password_spaces_and_safe_auth_error(self):
        env=dict(SMTP_HOST='smtp.gmail.com',SMTP_USER='test@example.invalid',
                 SMTP_PASSWORD='abcd efgh ijkl mnop',EMAIL_FROM='test@example.invalid',
                 ALERT_EMAIL='to@example.invalid')
        with patch.dict(os.environ,env),patch('paperbot.mail.smtplib.SMTP') as transport:
            smtp=transport.return_value.__enter__.return_value
            smtp.login.side_effect=smtplib.SMTPAuthenticationError(535,b'secret-provider-response')
            with self.assertRaisesRegex(SafetyError,'Email login rejected') as caught:
                send_message('Test','Body','test')
            smtp.login.assert_called_once_with('test@example.invalid','abcdefghijklmnop')
            self.assertNotIn('secret-provider-response',str(caught.exception))
            self.assertNotIn('abcdefghijklmnop',str(caught.exception))

    def test_email_secrets_not_in_export(self):
        with patch.dict(os.environ,{'SMTP_PASSWORD':'test-secret-marker'}):
            run(self.c,self.state,self.now,self.fetch)
        self.assertNotIn('test-secret-marker',(self.state/'trial-report.json').read_text())

    def test_expiry_without_network_even_on_holiday(self):
        c=copy.deepcopy(self.c); c['trial_calendar_days']=1
        run(c,self.state,self.now,self.fetch)
        def forbidden(*args): raise AssertionError('No download needed at expiry')
        r,failed=run(c,self.state,datetime.fromisoformat('2026-08-23T09:17:00+05:30'),forbidden)
        self.assertFalse(failed); self.assertTrue(r['trial_complete'])


class ReportTests(unittest.TestCase):
    def payload(self, **changes):
        row=dict(TradDt='2026-09-11',TckrSymb='INFY',SctySrs='EQ',ISIN=ISINS['INFY'],Sgmt='CM',Src='NSE',SsnId='F1',
                 OpnPric='100',HghPric='102',LwPric='99',ClsPric='101',TtlTradgVol='1000')
        row.update(changes)
        stream=io.StringIO(); w=csv.DictWriter(stream,fieldnames=list(row)); w.writeheader(); w.writerow(row)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr(filename('2026-09-11')[:-4],stream.getvalue())
        return out.getvalue()

    def test_schema_identity_dates_prices(self):
        self.assertEqual(parse_report(self.payload(),'2026-09-11',['INFY'])[0]['close'],101)
        for bad in [dict(ISIN='wrong'),dict(TradDt='2026-09-10'),dict(ClsPric='nan'),dict(SctySrs='BE'),dict(LwPric='110')]:
            with self.subTest(bad=bad),self.assertRaises(SafetyError): parse_report(self.payload(**bad),'2026-09-11',['INFY'])
        with self.assertRaises(SafetyError): parse_report(b'notzip','2026-09-11',['INFY'])

    def test_offline_missing_data_fails_closed(self):
        c=load_config(ROOT/'hosted-config.json')
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaisesRegex(SafetyError,'Missing offline'):
                nse_rows(c,state,datetime.fromisoformat('2026-09-13T12:00:00+05:30'),offline=True)


if __name__=='__main__': unittest.main()
