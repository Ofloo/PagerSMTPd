#!/usr/bin/env python3
"""Mock PagerMCP (/notify) and mock share.h4ck.io (PUT) for local tests.

No external network: both listen on 127.0.0.1 ephemeral ports.
Writes a env fragment to MOCK_ENV_FILE: PAGER_URL=http://127.0.0.1:P1, SHARE_URL=http://127.0.0.1:P2
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"notify": [], "share": {}}


class PagerHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        auth = self.headers.get("Authorization", "")
        if self.path == "/notify":
            if not auth.startswith("Bearer "):
                self.send_response(401)
                self.end_headers()
                return
            mailbox = auth.removeprefix("Bearer ").strip()
            payload = json.loads(body.decode("utf-8"))
            STATE["notify"].append({"mailbox": mailbox, "payload": payload})
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b"accepted")
        else:
            self.send_response(404)
            self.end_headers()


class ShareHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        name = self.path.lstrip("/")
        STATE["share"][name] = body
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"http://127.0.0.1:{self.server.server_port}/{name}".encode())


def main():
    pager = ThreadingHTTPServer(("127.0.0.1", 0), PagerHandler)
    share = ThreadingHTTPServer(("127.0.0.1", 0), ShareHandler)
    threading.Thread(target=pager.serve_forever, daemon=True).start()
    threading.Thread(target=share.serve_forever, daemon=True).start()

    env_file = sys.argv[1]
    with open(env_file, "w") as fh:
        fh.write(f"PAGER_URL=http://127.0.0.1:{pager.server_port}\n")
        fh.write(f"SHARE_URL=http://127.0.0.1:{share.server_port}\n")

    state_file = sys.argv[2]
    print(json.dumps({"pager_port": pager.server_port, "share_port": share.server_port, "state_file": state_file}))
    # Keep running; dump state periodically for the test runner.
    import time
    while True:
        time.sleep(0.2)
        snapshot = {
            "notify": STATE["notify"],
            "share": {k: {"size": len(v), "sha_prefix": __import__("hashlib").sha256(v).hexdigest()[:12]}
                      for k, v in STATE["share"].items()},
            "share_bodies": {k: v.decode("latin-1") for k, v in STATE["share"].items()},
        }
        with open(state_file, "w") as fh:
            json.dump(snapshot, fh)


if __name__ == "__main__":
    main()
