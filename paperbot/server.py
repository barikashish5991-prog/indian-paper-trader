import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import IST, SafetyError


def serve(service,port=8765):
    csrf=secrets.token_urlsafe(32)
    host="127.0.0.1:"+str(port)
    origin="http://"+host
    web=Path(__file__).resolve().parent.parent/"web"
    stopped=threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def send(self,body,kind="application/json",status=200):
            self.send_response(status)
            self.send_header("Content-Type",kind+"; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers(); self.wfile.write(body.encode())

        def allowed(self): return self.headers.get("Host")==host

        def do_GET(self):
            if not self.allowed(): return self.send("Forbidden",status=403)
            if self.path=="/api/report":
                r=service.report(); r["csrf"]=csrf
                return self.send(json.dumps(r,allow_nan=False))
            if self.path=="/api/export":
                return self.send(json.dumps(service.report(),indent=2,allow_nan=False))
            files={"/":("index.html","text/html"),"/app.js":("app.js","application/javascript"),"/style.css":("style.css","text/css")}
            if self.path in files:
                name,kind=files[self.path]
                return self.send((web/name).read_text(),kind)
            return self.send("Not found",status=404)

        def do_POST(self):
            if not self.allowed() or self.headers.get("Origin")!=origin or self.headers.get("X-Paper-CSRF")!=csrf:
                return self.send("Forbidden",status=403)
            if self.path!="/api/action": return self.send("Not found",status=404)
            try:
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<1000: raise ValueError()
                name=json.loads(self.rfile.read(size))["action"]
                result=service.action(name)
                self.send(json.dumps({"message":result}))
            except (ValueError,KeyError,TypeError): self.send(json.dumps({"message":"Invalid action"}),status=400)
            except SafetyError as exc: self.send(json.dumps({"message":str(exc)}),status=409)

    def scheduler():
        # One cycle at startup, then check every 30 minutes during the EOD window.
        # No synthetic time advancement in the background.
        try: service.sync()
        except SafetyError: pass
        while not stopped.wait(1800):
            if service.provider=="demo": continue
            local=datetime.now(timezone.utc).astimezone(IST)
            r=service.report()
            if r["trial_complete"]: continue
            expired=r["expires_at"] and datetime.now(timezone.utc)>=datetime.fromisoformat(r["expires_at"])
            if expired or (local.weekday()<5 and local.hour>=17):
                try: service.sync()
                except SafetyError: pass

    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    threading.Thread(target=scheduler,daemon=True).start()
    print("Paper trading dashboard: "+origin,flush=True)
    print("Keep this process running. Ctrl+C stops it. No real orders can be sent.",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stopped.set(); server.server_close()
