"""roottrace CLI.

  roottrace scan  --since 1h [-n ida -n regproc] [--slack] [--out ./out]
  roottrace scan  --from 2026-11-19T09:00:00Z --to 2026-11-19T10:30:00Z      # a test-run window
  roottrace trace <transactionId> --since 2h
  roottrace serve --port 8080                                              # HTTP API for test rigs

Exit codes (scan): 0 = healthy/degraded, 2 = critical (use it to gate CI or a test run).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import engine, notify, report
from .config import load_config


def _common(p):
    p.add_argument("-c", "--config", help="config.yaml (env vars ROOTTRACE_* override)")
    p.add_argument("-n", "--namespace", action="append", help="namespace(s); repeatable")
    p.add_argument("--since", help="look-back window, e.g. 30m, 2h, 1d (default 1h)")
    p.add_argument("--from", dest="frm", help="window start (ISO-8601 or epoch)")
    p.add_argument("--to", help="window end (ISO-8601 or epoch; default now)")
    p.add_argument("-v", "--verbose", action="store_true")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="roottrace", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="mine errors in a window and produce a health report")
    _common(s)
    s.add_argument("--baseline", help="baseline offset, e.g. 24h or 7d (default from config)")
    s.add_argument("--out", default="./roottrace-out")
    s.add_argument("--slack", action="store_true", help="post digest to Slack webhook")
    s.add_argument("--report-url", help="link to include in Slack message")
    s.add_argument("--json", action="store_true", help="print JSON summary to stdout")
    t = sub.add_parser("trace", help="follow a transaction/trace id across services")
    t.add_argument("id")
    _common(t)
    t.add_argument("--json", action="store_true")
    v = sub.add_parser("serve", help="HTTP API: GET /scan, GET /trace/<id>, GET /report")
    v.add_argument("-c", "--config")
    v.add_argument("--port", type=int, default=8080)
    v.add_argument("--out", default="./roottrace-out")
    v.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(a.config)

    if a.cmd == "scan":
        if a.baseline:
            cfg.baseline_offset = a.baseline
        start, end = engine.window(a.since, a.frm, a.to)
        r = engine.scan(cfg, start, end, a.namespace)
        hp, jp = report.write(r, a.out)
        if a.slack and cfg.slack_webhook:
            notify.slack(cfg.slack_webhook, r, a.report_url)
        if a.json:
            print(json.dumps({k: r[k] for k in ("verdict", "stats", "critical", "known_summary")}, default=list, indent=2))
        else:
            print(r["verdict"])
            for i in r["issues"][:10]:
                tag = "NEW " if i["is_new"] else "    "
                print(f"  {i['score']:6.1f} {tag}{i['service'][:24]:24} x{i['count']:<6} {i['template'][:90]}")
            print(f"\nReport: {hp}\nJSON:   {jp}")
        return 2 if r["critical"] else 0

    if a.cmd == "trace":
        start, end = engine.window(a.since or "6h", a.frm, a.to)
        r = engine.trace(cfg, a.id, start, end, a.namespace)
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            print(f"{r['count']} lines across {', '.join(r['services']) or '-'}")
            if r["first_error"]:
                fe = r["first_error"]
                print(f"FIRST ERROR @ {fe['ts']} in {fe['service']}: {fe['message'][:160]}  [{fe['root_cause']}]")
            for e in r["timeline"]:
                print(f"{e['ts']}  {e['level']:5} {e['service'][:22]:22} {e['message'][:120]}")
        return 0

    if a.cmd == "serve":
        serve(cfg, a.port, a.out)
        return 0


def serve(cfg, port: int, out: str):
    """Minimal JSON API so Java test rigs (REST-assured) can call it after a failure."""
    state = {"last_html": None}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=list, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            ns = qs.get("namespace", "").split(",") if qs.get("namespace") else None
            try:
                if u.path == "/health":
                    return self._send(200, {"status": "ok"})
                if u.path == "/scan":
                    start, end = engine.window(qs.get("since"), qs.get("from"), qs.get("to"))
                    r = engine.scan(cfg, start, end, ns)
                    hp, _ = report.write(r, out)
                    state["last_html"] = hp
                    if qs.get("slack") in ("1", "true") and cfg.slack_webhook:
                        url = cfg.public_url.rstrip("/") + "/report" if cfg.public_url else None
                        if qs.get("only_if") != "unhealthy" or r["unhealthy"]:
                            notify.slack(cfg.slack_webhook, r, url)
                    return self._send(200, r)
                if u.path.startswith("/trace/"):
                    start, end = engine.window(qs.get("since", "6h"), qs.get("from"), qs.get("to"))
                    return self._send(200, engine.trace(cfg, u.path.split("/", 2)[2], start, end, ns))
                if u.path == "/report" and state["last_html"]:
                    return self._send(200, state["last_html"].read_bytes(), "text/html; charset=utf-8")
                return self._send(404, {"error": "use /scan, /trace/<id>, /report, /health"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                logging.exception("request failed")
                return self._send(500, {"error": str(e)})

        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

    print(f"roottrace API on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
