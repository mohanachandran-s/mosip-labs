"""Scan + trace orchestration (used by the CLI, the HTTP server and the CronJob)."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from . import k8s as k8smod
from . import report
from .catalog import Catalog
from .deps import DepMap, analyze
from .config import Config
from .loki import LokiClient
from .miner import Miner, keep
from .parser import parse

log = logging.getLogger(__name__)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def dur_ns(s: str) -> int:
    m = re.fullmatch(r"(\d+)([smhdw])", s.strip())
    if not m:
        raise ValueError(f"bad duration '{s}' (use e.g. 30m, 2h, 1d)")
    return int(m.group(1)) * _UNITS[m.group(2)] * 1_000_000_000


def ts_ns(s: str) -> int:
    """ISO-8601 (2026-11-19T10:00:00Z) or epoch seconds/ms/ns."""
    if s.isdigit():
        n = int(s)
        return n * 10**9 if n < 10**11 else n * 10**6 if n < 10**14 else n
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1e9)


def window(since: str | None, frm: str | None, to: str | None) -> tuple[int, int]:
    end = ts_ns(to) if to else time.time_ns()
    start = ts_ns(frm) if frm else end - dur_ns(since or "1h")
    if start >= end:
        raise ValueError("window start must be before end")
    return start, end


def _lq(name: str) -> str:
    """Escape a literal for a regex inside a LogQL double-quoted string (backslashes doubled)."""
    return re.sub(r"([.\\+*?\[\](){}|^$])", r"\\\\\1", name)


def selector(cfg: Config, namespaces: list[str] | None) -> str:
    ns = namespaces or cfg.namespaces
    if ns:
        return '{namespace=~"%s"}' % "|".join(_lq(n) for n in ns)
    return '{namespace=~".+", namespace!~"%s"}' % "|".join(_lq(n) for n in cfg.exclude_namespaces)


def scan(cfg: Config, start: int, end: int, namespaces: list[str] | None = None) -> dict:
    loki = LokiClient(cfg.loki)
    sel = selector(cfg, namespaces)
    q = f'{sel} |~ `{cfg.error_filter}`'
    off = dur_ns(cfg.baseline_offset)
    span = end - start

    log.info("Loki query: %s", q)
    cur = [e for e in parse(loki.query_range(q, start, end), cfg.service_label) if keep(e, cfg.include_warn)]
    base = [e for e in parse(loki.query_range(q, start - off, end - off), cfg.service_label)
            if keep(e, cfg.include_warn)]
    log.info("events: current=%d baseline=%d", len(cur), len(base))

    miner = Miner()
    miner.add_baseline(base)
    miner.add_current(cur)
    cat = Catalog(cfg.catalog_paths)
    issues = miner.finalize(cfg.spike_factor)          # sets templates first
    cat.apply(issues)
    issues = miner.finalize(cfg.spike_factor)          # rescore with catalog severity

    # services that logged anything in baseline but nothing now
    silent = []
    try:
        lbl = cfg.service_label
        rng = f"{max(span // 1_000_000_000, 60)}s"
        vq = f"sum by (namespace, {lbl}) (count_over_time({sel}[{rng}]))"
        now_v = {(m.get("namespace"), m.get(lbl)) for m, v in loki.instant_vector(vq, end) if v > 0}
        for m, v in loki.instant_vector(vq, end - off):
            k = (m.get("namespace"), m.get(lbl))
            if v >= 10 and k not in now_v and k[1]:
                silent.append({"namespace": k[0], "service": k[1], "baseline": int(v)})
    except Exception as e:
        log.warning("silent-service check skipped: %s", e)

    kube = k8smod.collect(cfg.k8s, namespaces or cfg.namespaces, cfg.exclude_namespaces, start, end)
    cat.apply_k8s(kube["findings"])

    raw = {
        "environment": cfg.environment_name,
        "window_from": report.fmt_ts(start), "window_to": report.fmt_ts(end),
        "start_ns": start, "end_ns": end, "baseline_offset": cfg.baseline_offset,
        "namespaces": ", ".join(namespaces or cfg.namespaces),
        "generated": report.fmt_ts(time.time_ns()), "query": q,
        "issues": [i.to_dict() for i in issues],
        "resolved": [i.to_dict() for i in miner.resolved()[:20]],
        "silent": sorted(silent, key=lambda s: -s["baseline"]),
        "k8s": kube["findings"], "rollouts": kube["rollouts"], "k8s_error": kube["error"],
    }
    try:
        raw["deps"] = analyze(raw, DepMap(cfg.dependency_map_paths))
    except Exception as e:
        log.warning("dependency analysis skipped: %s", e)
        raw["deps"] = {"chains": [], "components": {}, "graph": [], "error": str(e)}
    r = report.build(raw)
    r["critical"] = r["unhealthy"] or any(i["score"] >= cfg.critical_score for i in r["issues"])
    return r


def trace(cfg: Config, txn_id: str, start: int, end: int, namespaces: list[str] | None = None) -> dict:
    """Every log line (any level) mentioning an ID, across all services, in time order."""
    loki = LokiClient(cfg.loki)
    q = f'{selector(cfg, namespaces)} |= `{txn_id}`'
    events = parse(loki.query_range(q, start, end, max_lines=5000), cfg.service_label)
    events.sort(key=lambda e: e.ts_ns)
    errors = [e for e in events if e.level in ("ERROR", "FATAL")]
    first_err = errors[0] if errors else None
    return {
        "id": txn_id, "query": q, "count": len(events),
        "services": sorted({e.service for e in events}),
        "first_error": None if not first_err else {
            "service": first_err.service, "message": first_err.message,
            "root_cause": first_err.root_cause or first_err.exception, "error_codes": first_err.error_codes,
            "ts": report.fmt_ts(first_err.ts_ns)},
        "timeline": [{"ts": report.fmt_ts(e.ts_ns), "service": e.service, "pod": e.pod, "level": e.level,
                      "message": e.message, "error_codes": e.error_codes} for e in events],
    }
