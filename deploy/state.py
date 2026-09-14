"""Private GitHub branch persistence. Never reset state on a missing branch."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def git(args,cwd=None,check=True):
    env=os.environ.copy()
    token=env.get('GH_STATE_TOKEN','')
    if not token: raise RuntimeError('Missing scoped repository token')
    # Per-process auth only. No credentials in URLs, files or command output.
    env.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
               GIT_CONFIG_VALUE_0='AUTHORIZATION: basic '+base64.b64encode(('x-access-token:'+token).encode()).decode())
    result=subprocess.run(['git']+args,cwd=cwd,env=env,capture_output=True,text=True)
    if check and result.returncode: raise RuntimeError('State repository operation failed; refusing to continue')
    return result


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(state):
    manifest=state/'manifest.json'
    if not manifest.exists(): raise RuntimeError('State manifest missing')
    contents=json.loads(manifest.read_text())
    if 'ledger.sqlite3' not in contents: raise RuntimeError('Ledger missing from state manifest')
    for name,expected in contents.items():
        path=state/name
        if not path.resolve().is_relative_to(state.resolve()):
            raise RuntimeError('Invalid manifest path')
        if not path.is_file() or digest(path)!=expected: raise RuntimeError('Persistent state failed integrity check')
    db=sqlite3.connect(state/'ledger.sqlite3')
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Ledger integrity check failed')
    finally: db.close()


def restore(state,bootstrap):
    repository=os.environ['GITHUB_REPOSITORY']
    if len(repository.split('/'))!=2 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./' for c in repository):
        raise RuntimeError('Invalid repository')
    remote='https://github.com/'+repository+'.git'
    result=git(['ls-remote','--exit-code','--heads',remote,'refs/heads/paper-state'],check=False)
    if result.returncode not in (0,2): raise RuntimeError('Cannot determine state branch; not initializing')
    if result.returncode==2:
        if not bootstrap: raise RuntimeError('State branch missing: run the explicit bootstrap once, never reset a lost account')
        if state.exists(): raise RuntimeError('Bootstrap target already exists')
        state.mkdir(parents=True)
        git(['init','--initial-branch=paper-state'],state)
        git(['remote','add','origin',remote],state)
    else:
        if bootstrap: raise RuntimeError('Paper state already exists; use tick rather than bootstrap')
        git(['clone','--depth','1','--branch','paper-state',remote,str(state)])
        verify(state)
    git(['config','user.name','Paper trial worker'],state)
    git(['config','user.email','paper-worker@users.noreply.github.com'],state)


def persist(state):
    ledger=state/'ledger.sqlite3'
    if not ledger.exists(): raise RuntimeError('No ledger to persist')
    db=sqlite3.connect(ledger)
    try:
        busy=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]
        if busy: raise RuntimeError('Ledger busy; persistence refused')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Ledger corrupted')
    finally: db.close()
    allowed=[]
    for p in state.rglob('*'):
        if '.git' in p.relative_to(state).parts or not p.is_file(): continue
        name=p.relative_to(state).as_posix()
        if name in ('ledger.sqlite3','REPORT.md','trial-report.json') or (name.startswith('nse/') and p.suffix in ('.zip','.sha256')):
            allowed.append(name)
    (state/'manifest.json').write_text(json.dumps({n:digest(state/n) for n in sorted(allowed)},indent=2)+'\n')
    git(['add','--']+allowed+['manifest.json'],state)
    if git(['diff','--cached','--quiet'],state,check=False).returncode==0: return
    git(['commit','-m','Persist paper ledger and notification outbox'],state)
    # No force push. A concurrency conflict blocks delivery rather than losing history.
    git(['push','origin','HEAD:refs/heads/paper-state'],state)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['restore','persist'])
    p.add_argument('--state',type=Path,required=True); p.add_argument('--bootstrap',action='store_true')
    args=p.parse_args()
    try:
        if args.command=='restore': restore(args.state,args.bootstrap)
        else: persist(args.state)
    except Exception as exc:
        # Never emit captured git output or credentials on failure.
        raise SystemExit(str(exc) if isinstance(exc,RuntimeError) else 'State operation failed ('+type(exc).__name__+')')
