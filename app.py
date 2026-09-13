#!/usr/bin/env python3
import argparse
import getpass
import json
import os
from pathlib import Path

from paperbot.core import SafetyError, load_config
from paperbot.service import Service
from paperbot.server import serve

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description="Autonomous local paper-trading trial. Cannot place real trades.")
    parser.add_argument("command",choices=["demo","serve","once","report","token"])
    parser.add_argument("--provider",choices=["demo","upstox","csv","nse"],default="upstox")
    parser.add_argument("--config",type=Path,default=ROOT/"config.json")
    parser.add_argument("--state",type=Path)
    parser.add_argument("--csv",type=Path)
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    provider="demo" if args.command=="demo" else args.provider
    state=args.state or ROOT/"state"/("demo" if provider=="demo" else "nse-paper" if provider=="nse" else "paper")
    state.mkdir(parents=True,exist_ok=True)
    if args.command=="token":
        value=getpass.getpass("Paste your Upstox access token locally (hidden): ").strip()
        if not value: raise SafetyError("No token provided")
        path=state/"upstox.token"
        fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,"w") as f: f.write(value)
        os.chmod(path,0o600)
        print("Token saved locally. It is not included in reports. Use Check market data or restart the paper app.")
        return
    if provider=="csv" and args.command!="report" and not args.csv:
        raise SafetyError("CSV provider requires --csv")
    service=Service(load_config(args.config),state,provider,csv_path=args.csv)
    if args.command in ("demo","serve"): serve(service,args.port)
    elif args.command=="once": print(service.sync(advance=provider=="demo"))
    elif args.command=="report": print(json.dumps(service.report(),indent=2))


if __name__=="__main__":
    try: main()
    except SafetyError as exc:
        print("Action needed: "+str(exc))
        raise SystemExit(1)
    except OSError:
        print("Could not open the local service or file. Check file permissions, network access, and whether the dashboard port is already in use.")
        raise SystemExit(1)
