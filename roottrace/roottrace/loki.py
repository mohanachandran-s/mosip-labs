"""Thin Loki HTTP API client with pagination."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from .config import LokiConfig

log = logging.getLogger(__name__)


@dataclass
class LogEntry:
    ts_ns: int
    labels: dict
    line: str


class LokiClient:
    def __init__(self, cfg: LokiConfig):
        self.cfg = cfg
        self.s = requests.Session()
        if cfg.username:
            self.s.auth = (cfg.username, cfg.password or "")
        if cfg.bearer_token:
            self.s.headers["Authorization"] = f"Bearer {cfg.bearer_token}"
        if cfg.org_id:
            self.s.headers["X-Scope-OrgID"] = cfg.org_id
        self.s.verify = cfg.verify_tls

    def _get(self, path: str, params: dict) -> dict:
        r = self.s.get(self.cfg.url.rstrip("/") + path, params=params, timeout=self.cfg.timeout_s)
        if r.status_code >= 400:
            raise RuntimeError(f"Loki {path} -> HTTP {r.status_code}: {r.text[:500]}")
        body = r.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Loki {path} error: {body}")
        return body["data"]

    def label_values(self, label: str, start_ns: int, end_ns: int) -> list[str]:
        r = self.s.get(self.cfg.url.rstrip("/") + f"/loki/api/v1/label/{label}/values",
                       params={"start": start_ns, "end": end_ns}, timeout=self.cfg.timeout_s)
        r.raise_for_status()
        return r.json().get("data", []) or []

    def query_range(self, query: str, start_ns: int, end_ns: int, max_lines: int | None = None) -> list[LogEntry]:
        """Fetch log lines oldest-first, paginating forward until end or cap."""
        cap = max_lines or self.cfg.max_lines
        out: list[LogEntry] = []
        cursor = start_ns
        while cursor < end_ns and len(out) < cap:
            data = self._get("/loki/api/v1/query_range", {
                "query": query, "start": cursor, "end": end_ns,
                "limit": min(self.cfg.page_size, cap - len(out)), "direction": "forward",
            })
            batch: list[LogEntry] = []
            for stream in data.get("result", []):
                labels = stream.get("stream", {})
                for ts, line in stream.get("values", []):
                    batch.append(LogEntry(int(ts), labels, line))
            if not batch:
                break
            batch.sort(key=lambda e: e.ts_ns)
            out.extend(batch)
            if len(batch) < self.cfg.page_size:
                break
            cursor = batch[-1].ts_ns + 1
        if len(out) >= cap:
            log.warning("Hit max_lines cap (%d) — narrow the window or namespaces for full coverage", cap)
        return out

    def instant_vector(self, query: str, at_ns: int) -> list[tuple[dict, float]]:
        data = self._get("/loki/api/v1/query", {"query": query, "time": at_ns})
        return [(r.get("metric", {}), float(r["value"][1])) for r in data.get("result", [])]
