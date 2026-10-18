"""Tiny localhost receiver: the Action Network tab POSTs bet rows here and
they are upserted (by id) into data/picks.json.

    python receiver.py            # listens on 127.0.0.1:8765
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "picks.json")


def load():
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            return {r["id"]: r for r in json.load(f)}
    return {}


def save(rows):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    out = sorted(rows.values(), key=lambda r: (r["t"] or "", r["id"]))
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, DATA)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "https://www.actionnetwork.com")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Serves collector.js so a run can load it with one fetch instead of pasting it.
        if self.path != "/collector.js":
            self.send_response(404)
            self.end_headers()
            return
        with open(os.path.join(HERE, "collector.js"), "rb") as f:
            body = f.read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/javascript")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        incoming = json.loads(body)
        rows = load()
        for r in incoming:
            rows[r["id"]] = r
        save(rows)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"received": len(incoming), "total": len(rows)}).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("receiver on http://127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
