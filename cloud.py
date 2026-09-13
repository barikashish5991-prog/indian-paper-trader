"""CLI used by the private hosted workflow. Sending is a separate post-persist step."""
import argparse
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path

from paperbot.core import Engine, SafetyError, load_config
from paperbot.hosted import run
from paperbot.mail import deliver,settings

ROOT=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['tick','setup','pause','resume','send','check-email'])
    p.add_argument('--state',type=Path,required=True)
    args=p.parse_args()
    config=load_config(ROOT/'hosted-config.json')
    if args.command=='check-email':
        settings(); print('Email settings are present; delivery has not yet been verified.'); return
    if not (args.state/'ledger.sqlite3').exists() and args.command!='setup':
        raise SafetyError('Hosted ledger missing; refusing to create a replacement account')
    if args.command=='send':
        e=Engine(args.state/'ledger.sqlite3',config,'paper')
        try: print('Emails accepted by SMTP server:',deliver(e.db,datetime.now(timezone.utc).isoformat()))
        finally: e.close()
    else:
        report,failed=run(config,args.state,action=args.command)
        print('Paper worker:', 'NEEDS ATTENTION' if failed else 'completed', '; last session:',report['latest']['day'] if report['latest'] else 'not initialized')
        if failed: raise SystemExit(1)


if __name__=='__main__':
    try: main()
    except SafetyError as exc: raise SystemExit(str(exc))
