"""Cluster events into templates (Drain3), diff against a baseline, score issues."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from .parser import Event

LEVEL_WEIGHT = {"FATAL": 3.0, "ERROR": 2.0, "WARN": 1.0}


@dataclass
class Issue:
    key: str
    namespace: str
    service: str
    template: str
    level: str
    count: int = 0
    baseline_count: int = 0
    first_ns: int = 0
    last_ns: int = 0
    pods: set = field(default_factory=set)
    exceptions: Counter = field(default_factory=Counter)
    root_causes: Counter = field(default_factory=Counter)
    error_codes: Counter = field(default_factory=Counter)
    trace_ids: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    known: list = field(default_factory=list)      # catalog matches
    score: float = 0.0

    @property
    def is_new(self) -> bool:
        return self.baseline_count == 0

    @property
    def ratio(self) -> float:
        return self.count / max(self.baseline_count, 1)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "namespace": self.namespace, "service": self.service,
            "template": self.template, "level": self.level, "count": self.count,
            "baseline_count": self.baseline_count, "is_new": self.is_new,
            "first_ns": self.first_ns, "last_ns": self.last_ns, "pods": sorted(self.pods),
            "exceptions": dict(self.exceptions.most_common(5)),
            "root_causes": dict(self.root_causes.most_common(5)),
            "error_codes": dict(self.error_codes.most_common(10)),
            "trace_ids": self.trace_ids[:10], "samples": self.samples,
            "known": self.known, "score": round(self.score, 1),
        }


def keep(ev: Event, include_warn: bool) -> bool:
    if ev.level in ("ERROR", "FATAL"):
        return True
    if ev.level == "WARN":
        return include_warn or bool(ev.exception)
    return bool(ev.exception)        # INFO/DEBUG lines that still carry an exception


class Miner:
    def __init__(self):
        c = TemplateMinerConfig()
        c.drain_sim_th = 0.45
        c.drain_depth = 5
        c.parametrize_numeric_tokens = True
        c.masking_instructions = []
        self._miners: dict[str, TemplateMiner] = {}
        self._cfg = c
        self.issues: dict[str, Issue] = {}

    def _miner(self, service: str) -> TemplateMiner:
        if service not in self._miners:
            self._miners[service] = TemplateMiner(config=self._cfg)
        return self._miners[service]

    def _cluster(self, ev: Event) -> str:
        text = ev.message
        if ev.root_cause and ev.root_cause not in text:
            text += f" [cause {ev.root_cause}]"
        r = self._miner(f"{ev.namespace}/{ev.service}").add_log_message(text)
        return f"{ev.namespace}/{ev.service}#{r['cluster_id']}"

    def add_baseline(self, events: list[Event]):
        for ev in events:
            key = self._cluster(ev)
            iss = self._get(key, ev)
            iss.baseline_count += 1

    def add_current(self, events: list[Event]):
        for ev in events:
            key = self._cluster(ev)
            iss = self._get(key, ev)
            iss.count += 1
            iss.first_ns = ev.ts_ns if not iss.first_ns else min(iss.first_ns, ev.ts_ns)
            iss.last_ns = max(iss.last_ns, ev.ts_ns)
            iss.pods.add(ev.pod)
            if LEVEL_WEIGHT.get(ev.level, 0) > LEVEL_WEIGHT.get(iss.level, 0):
                iss.level = ev.level
            if ev.exception:
                iss.exceptions[ev.exception] += 1
            if ev.root_cause:
                iss.root_causes[ev.root_cause] += 1
            iss.error_codes.update(ev.error_codes)
            for t in ev.trace_ids:
                if len(iss.trace_ids) < 20 and t not in iss.trace_ids:
                    iss.trace_ids.append(t)
            if len(iss.samples) < 3:
                iss.samples.append({"ts_ns": ev.ts_ns, "pod": ev.pod, "text": ev.raw[:3000]})

    def _get(self, key: str, ev: Event) -> Issue:
        if key not in self.issues:
            self.issues[key] = Issue(key=key, namespace=ev.namespace, service=ev.service,
                                     template="", level=ev.level)
        return self.issues[key]

    def finalize(self, spike_factor: float) -> list[Issue]:
        # refresh templates (they evolve as Drain merges clusters)
        for key, iss in self.issues.items():
            svc, cid = key.rsplit("#", 1)
            for c in self._miner(svc).drain.clusters:
                if str(c.cluster_id) == cid:
                    iss.template = c.get_template()
        out = [i for i in self.issues.values() if i.count > 0]
        for i in out:
            s = LEVEL_WEIGHT.get(i.level, 1.0) * 5 * math.log10(1 + i.count)
            if i.is_new:
                s += 25
            elif i.ratio >= spike_factor:
                s += 10 * min(math.log2(i.ratio), 4)
            s += 3 * min(len(i.pods), 5)
            for k in i.known:
                s += {"critical": 30, "high": 20, "medium": 10}.get(k.get("severity"), 5)
            i.score = s
        return sorted(out, key=lambda i: i.score, reverse=True)

    def resolved(self) -> list[Issue]:
        """Templates present in baseline but gone now (possible fixes, or service down!)."""
        return sorted([i for i in self.issues.values() if i.count == 0 and i.baseline_count >= 5],
                      key=lambda i: i.baseline_count, reverse=True)
