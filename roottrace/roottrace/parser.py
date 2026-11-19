"""Normalise raw Loki lines into structured events; mask PII / payloads; stitch stack traces."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .loki import LogEntry

LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|SEVERE)\b")
EXC_RE = re.compile(r"\b((?:[a-zA-Z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Fault))\b")
CAUSED_RE = re.compile(r"Caused by:\s*([\w$.]+)(?::\s*(.*))?")
# MOSIP-style error codes, e.g. IDA-MLC-019, KER-ATH-401, RES-SER-410, PRG_PAM_APP_005
ERRCODE_RE = re.compile(r"\b([A-Z]{2,6}[-_][A-Z]{2,5}[-_](?:[A-Z]{2,5}[-_])?\d{3,4})\b")
TRACE_RE = re.compile(r"(?i)\b(?:trace[-_]?id|transaction[-_]?id|txn[-_]?id|requestid|X-B3-TraceId)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9-]{6,64})")
CONT_RE = re.compile(r"^(\s+at\s|\s*\.\.\. \d+ (more|common frames)|Caused by:|\s+Suppressed:|\t)")
# MOSIP logback pattern: 2026-11-19 08:55:42,518 [thread] ERROR [o.s.b.c.s.AbstractStep].execute.259 : msg
MOSIP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ][\d:.,]+\s+\[(?P<thread>[^\]]*)\]\s+(?P<level>[A-Z]+)\s+\[(?P<logger>[^\]]*)\]\S*\s*:\s?(?P<msg>.*)$")
LEAD_TS_RE = re.compile(r"^\[?\d{4}-\d{2}-\d{2}[T ][\d:.,]+(?:Z|[+-]\d{2}:?\d{2})?\]?\s*")
EXC_HEADER_RE = re.compile(r"^(?:[a-zA-Z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error)(?::|\s*$)")
SPRING_RE = re.compile(
    r"^\S+\s+\S+\s+(?P<level>[A-Z]+)\s+\d*\s*---\s+\[(?P<thread>[^\]]*)\]\s+(?P<logger>\S+)\s*:\s*(?P<msg>.*)$")

MASKS = [
    (re.compile(r"[A-Za-z0-9+/_-]{120,}={0,2}"), "<BLOB>"),                 # base64 biometrics, JWTs
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "<JWT>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),
    (re.compile(r"(?<![\d.:-])\d{10,16}(?![\d.:-])"), "<ID>"),                # UIN / VID / phone
]


def mask(text: str) -> str:
    for rx, repl in MASKS:
        text = rx.sub(repl, text)
    return text


@dataclass
class Event:
    ts_ns: int
    namespace: str
    service: str
    pod: str
    level: str
    message: str           # masked first line / message
    exception: str | None = None
    root_cause: str | None = None
    error_codes: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    raw: str = ""          # masked full text incl. stack, truncated


def _service(labels: dict, service_label: str) -> str:
    for k in (service_label, "app", "app_kubernetes_io_name", "container", "job"):
        if labels.get(k):
            v = labels[k]
            return v.split("/")[-1]
    pod = labels.get("pod", "unknown")
    return re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$|-\d+$", "", pod)


def _from_json(line: str):
    if not line.startswith("{"):
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    msg = d.get("message") or d.get("msg") or ""
    stack = d.get("stack_trace") or d.get("exception") or d.get("stacktrace") or ""
    level = str(d.get("level") or d.get("severity") or d.get("log.level") or "").upper()
    trace = d.get("traceId") or d.get("trace_id") or d.get("transactionId")
    return level, f"{msg}\n{stack}".strip(), trace


def parse(entries: list[LogEntry], service_label: str = "app") -> list[Event]:
    events: list[Event] = []
    last_by_stream: dict[tuple, Event] = {}
    for e in entries:
        key = tuple(sorted(e.labels.items()))
        line = e.line.rstrip("\n")
        prev = last_by_stream.get(key)
        # an exception header logged as its own line right after an ERROR/WARN line -> same event
        if (prev is not None and EXC_HEADER_RE.match(line) and prev.level in ("ERROR", "WARN", "FATAL")
                and e.ts_ns - prev.ts_ns < 2_000_000_000 and not prev.exception):
            prev.raw += "\n" + mask(line)[:2000]
            prev.exception = EXC_HEADER_RE.match(line).group(0).rstrip(": ").strip()
            continue
        if prev is not None and CONT_RE.match(line):
            if len(prev.raw) < 6000:
                prev.raw += "\n" + mask(line)
            m = CAUSED_RE.search(line)
            if m:
                prev.root_cause = m.group(1)
            continue

        trace_from_json = None
        js = _from_json(line)
        if js:
            level, text, trace_from_json = js
        else:
            text = line
            sm = MOSIP_RE.match(line) or SPRING_RE.match(line)
            if sm:
                level, text = sm.group("level"), f"{sm.group('logger')}: {sm.group('msg')}"
            else:
                lm = LEVEL_RE.search(line[:200])
                level = lm.group(1) if lm else ""
                text = LEAD_TS_RE.sub("", line)
        level = {"WARNING": "WARN", "SEVERE": "ERROR"}.get(level, level) or "ERROR"

        first, *rest = text.split("\n")
        ev = Event(
            ts_ns=e.ts_ns,
            namespace=e.labels.get("namespace", "-"),
            service=_service(e.labels, service_label),
            pod=e.labels.get("pod", "-"),
            level=level,
            message=mask(first)[:1000],
            raw=mask(text)[:6000],
        )
        exc = EXC_RE.search(text)
        ev.exception = exc.group(1) if exc else None
        causes = CAUSED_RE.findall(text)
        if causes:
            ev.root_cause = causes[-1][0]
        ev.error_codes = sorted(set(ERRCODE_RE.findall(text)))
        tids = set(TRACE_RE.findall(text))
        if trace_from_json:
            tids.add(str(trace_from_json))
        ev.trace_ids = sorted(tids)
        events.append(ev)
        last_by_stream[key] = ev
    return events
