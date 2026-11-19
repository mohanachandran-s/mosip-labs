"""Tiny in-process Loki stand-in: supports query_range (namespace selector, |~, |=, limit, forward)
and the count_over_time instant vector used by the silent-service check. Test use only."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeLoki:
    def __init__(self, lines):  # lines: list of (ts_ns, labels, line)
        self.lines = sorted(lines, key=lambda x: x[0])
        self.queries = []

    def _filter(self, q, start, end):
        sel = re.search(r"\{(.*?)\}", q).group(1)
        inc = re.search(r'namespace=~"([^"]*)"', sel)
        exc = re.search(r'namespace!~"([^"]*)"', sel)
        rx = re.search(r"\|~ `(.*?)`", q)
        eq = re.search(r"\|= `(.*?)`", q)
        out = []
        for ts, lb, line in self.lines:
            if not (start <= ts < end):
                continue
            if inc and not re.fullmatch(inc.group(1), lb["namespace"]):
                continue
            if exc and re.fullmatch(exc.group(1), lb["namespace"]):
                continue
            if rx and not re.search(rx.group(1), line):
                continue
            if eq and eq.group(1) not in line:
                continue
            out.append((ts, lb, line))
        return out

    def handle(self, path, qs):
        q = qs.get("query", "")
        self.queries.append(q)
        if path.endswith("/query_range"):
            rows = self._filter(q, int(qs["start"]), int(qs["end"]))[: int(qs.get("limit", 100))]
            streams = {}
            for ts, lb, line in rows:
                streams.setdefault(tuple(sorted(lb.items())), []).append([str(ts), line])
            return {"status": "success", "data": {"resultType": "streams", "result": [
                {"stream": dict(k), "values": v} for k, v in streams.items()]}}
        if path.endswith("/query"):
            m = re.search(r"by \(namespace, (\w+)\) \(count_over_time\((.*)\[(\d+)s\]\)\)", q)
            lbl, sel, rng = m.group(1), m.group(2), int(m.group(3))
            t = int(qs["time"])
            counts = {}
            for ts, lb, _ in self._filter(sel, t - rng * 10**9, t):
                k = (lb["namespace"], lb.get(lbl))
                counts[k] = counts.get(k, 0) + 1
            return {"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {"namespace": k[0], lbl: k[1]}, "value": [t / 1e9, str(v)]} for k, v in counts.items()]}}
        return {"status": "error"}

    def start(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                body = json.dumps(fake.handle(u.path, {k: v[0] for k, v in parse_qs(u.query).items()})).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def stop(self):
        self.srv.shutdown()
