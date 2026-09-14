import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import state


class PersistenceTests(unittest.TestCase):
    def test_push_restore_checksum_and_secret_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); remote=root/'remote.git'; account=root/'account'; account.mkdir()
            def git(args,cwd=None,check=True):
                result=subprocess.run(['git']+args,cwd=cwd,capture_output=True,text=True)
                if check and result.returncode: raise RuntimeError('test git failed: '+result.stderr)
                return result
            git(['init','--bare',str(remote)])
            git(['init','--initial-branch=paper-state'],account)
            git(['config','user.name','Test'],account); git(['config','user.email','test@example.invalid'],account)
            git(['remote','add','origin',str(remote)],account)
            db=sqlite3.connect(account/'ledger.sqlite3'); db.execute('CREATE TABLE test(value TEXT)'); db.execute("INSERT INTO test VALUES('retained')"); db.commit(); db.close()
            (account/'upstox.token').write_text('must-not-be-persisted')
            (account/'REPORT.md').write_text('paper')
            with patch.object(state,'git',side_effect=git): state.persist(account)
            restored=root/'restored'; git(['clone','--branch','paper-state',str(remote),str(restored)])
            state.verify(restored)
            self.assertFalse((restored/'upstox.token').exists())
            (restored/'REPORT.md').write_text('tampered')
            with self.assertRaisesRegex(RuntimeError,'integrity'): state.verify(restored)

    def test_missing_state_never_silently_reinitialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.CompletedProcess([],2,'','')
            with patch.dict(os.environ,{'GITHUB_REPOSITORY':'example/private'}),patch.object(state,'git',return_value=result):
                with self.assertRaisesRegex(RuntimeError,'State branch missing'): state.restore(Path(tmp)/'missing',False)
            self.assertFalse((Path(tmp)/'missing').exists())

    def test_network_failure_is_not_a_missing_branch(self):
        result=subprocess.CompletedProcess([],128,'','')
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'example/private'}),patch.object(state,'git',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'Cannot determine'): state.restore(Path('/unused'),True)


if __name__=='__main__': unittest.main()
