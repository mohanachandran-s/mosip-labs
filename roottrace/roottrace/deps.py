"""Dependency-aware root-cause analysis.

Combines a static dependency map with dependencies *observed* in error text, works out which
unhealthy components are roots and which are symptoms, and produces failure chains with
troubleshooting details.
"""
from __future__ import annotations

import re
from collections import defaultdict
from importlib import resources
from pathlib import Path

import yaml

URL_HOST_RE = re.compile(r"https?://([a-z0-9][a-z0-9-]*)(?:\.([a-z0-9-]+))?", re.I)
HARD_K8S = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
            "CreateContainerError", "OOMKilled", "Evicted", "Failed", "NotReady", "RunContainerError"}


class Node:
    def __init__(self, d: dict):
        self.name = d["name"]
        self.kind = d.get("kind", "service")
        m = d.get("match", {})
        self.ns_rx = re.compile(f"(?:{m['namespace']})$", re.I) if m.get("namespace") else None
        self.app_rx = re.compile(m["app"], re.I) if m.get("app") else None
        self.mention_rx = re.compile(d["mentions"], re.I) if d.get("mentions") else None
        self.depends_on = list(d.get("depends_on", []))
        self.checks = list(d.get("checks", []))

    def matches(self, namespace: str, name: str) -> bool:
        if self.ns_rx and not self.ns_rx.match(namespace or ""):
            return False
        if self.app_rx and not self.app_rx.search(name or ""):
            return False
        return bool(self.ns_rx or self.app_rx)


class DepMap:
    def __init__(self, extra_paths: list[str] | None = None):
        texts = [resources.files("roottrace").joinpath("dependencies.yaml").read_text()]
        texts += [Path(p).read_text() for p in (extra_paths or [])]
        by_name: dict[str, dict] = {}
        order: list[str] = []
        for t in texts:
            for d in (yaml.safe_load(t) or {}).get("nodes", []):
                if d["name"] in by_name:          # later files override / extend
                    by_name[d["name"]].update(d)
                else:
                    by_name[d["name"]] = d
                    if t is texts[0]:
                        order.append(d["name"])
                    else:                         # team-added nodes are matched first (more specific)
                        order.insert(0, d["name"])
        self.nodes = [Node(by_name[n]) for n in order]
        self.by_name = {n.name: n for n in self.nodes}

    def resolve(self, namespace: str, name: str) -> str:
        for n in self.nodes:
            if n.matches(namespace, name):
                return n.name
        return name or namespace

    def host_to_node(self, host: str, second: str | None) -> str | None:
        for cand in (host, second):
            if not cand:
                continue
            for n in self.nodes:
                if n.name == cand or (n.app_rx and n.app_rx.search(cand)) or (n.ns_rx and n.ns_rx.match(cand)):
                    return n.name
        return None


def _status_rank(s: str) -> int:
    return {"down": 3, "suspected": 2, "degraded": 1}.get(s, 0)


def analyze(raw: dict, dm: DepMap) -> dict:
    issues, k8s, silent = raw["issues"], raw["k8s"], raw["silent"]
    comp: dict[str, dict] = defaultdict(lambda: {"issues": [], "k8s": [], "silent": False, "namespaces": set(),
                                                 "first_ns": 0, "status": "healthy", "evidence": []})

    def first(c, ts):
        if ts and (not c["first_ns"] or ts < c["first_ns"]):
            c["first_ns"] = ts

    for i in issues:
        n = dm.resolve(i["namespace"], i["service"])
        i["component"] = n
        c = comp[n]; c["issues"].append(i); c["namespaces"].add(i["namespace"]); first(c, i["first_ns"])
    for f in k8s:
        n = dm.resolve(f["namespace"], f["pod"])
        f["component"] = n
        c = comp[n]; c["k8s"].append(f); c["namespaces"].add(f["namespace"]); first(c, f.get("ts_ns", 0))
    for s in silent:
        n = dm.resolve(s["namespace"], s["service"])
        c = comp[n]; c["silent"] = True; c["namespaces"].add(s["namespace"])

    # ---- health status per component
    for name, c in comp.items():
        hard = [f for f in c["k8s"] if f["kind"] != "job-failed"
                and (f["reason"] in HARD_K8S or f["kind"] in ("pod-state", "not-ready"))]
        if hard or c["silent"]:
            c["status"] = "down"
        elif c["issues"] or c["k8s"]:
            c["status"] = "degraded"
        for f in c["k8s"]:
            c["evidence"].append(f"{f['kind']}: {f['pod']} — {f['reason']} {f.get('message','')[:140]}".strip())
        if c["silent"]:
            c["evidence"].append("no log lines at all in the window (was logging in the baseline)")
        for i in sorted(c["issues"], key=lambda x: -x["score"])[:4]:
            tag = "NEW " if i["is_new"] else ""
            cause = (list(i["root_causes"]) or list(i["exceptions"]) or [""])[0]
            c["evidence"].append(f"{tag}{i['level']} ×{i['count']} in {i['service']}: {i['template'][:160]}"
                                 + (f"  [{cause}]" if cause else ""))
    unhealthy = {n for n, c in comp.items() if c["status"] != "healthy" and dm.by_name.get(n, Node({"name": n})).kind != "test"}

    # ---- edges: static + observed in error text
    edges: dict[str, dict[str, dict]] = defaultdict(dict)   # from -> to -> {source, evidence}
    for n in dm.nodes:
        for d in n.depends_on:
            edges[n.name][d] = {"source": "map", "evidence": None}
    for name in unhealthy:
        for i in comp[name]["issues"]:
            text = " ".join([i["template"], *(s["text"][:1500] for s in i["samples"])])
            for node in dm.nodes:
                if node.name == name or not node.mention_rx:
                    continue
                m = node.mention_rx.search(i["template"]) or node.mention_rx.search(text)
                if m:
                    edges[name][node.name] = {"source": "logs", "evidence": f"{i['service']}: …{_around(text, m)}…"}
            for host, second in URL_HOST_RE.findall(text):
                tgt = dm.host_to_node(host, second)
                if tgt and tgt != name:
                    edges[name][tgt] = {"source": "logs", "evidence": f"{i['service']} calls http://{host}"}

    # ---- suspects: healthy-looking component that ≥2 unhealthy components complain about in their logs
    complainers: dict[str, set] = defaultdict(set)
    for a, outs in edges.items():
        if a in unhealthy:
            for b, e in outs.items():
                if e["source"] == "logs" and b not in unhealthy:
                    complainers[b].add(a)
    suspects = {b for b, who in complainers.items() if len(who) >= 2}
    for b in suspects:
        c = comp[b]; c["status"] = "suspected"
        c["evidence"].insert(0, f"no errors or pod problems of its own, but {len(complainers[b])} components fail "
                                f"talking to it ({', '.join(sorted(complainers[b]))}) — check network, credentials, "
                                f"config, or whether its logs reach Loki")
    bad = unhealthy | suspects

    def blamed(n):
        """A dependency explains n's trouble only with evidence: n's own errors mention it, or it is
        hard-down (crashing / not ready / silent). Map-only edges to merely degraded components are ignored,
        so unrelated problems are not lumped under one root."""
        out = []
        for d, e in edges.get(n, {}).items():
            if d == n or d not in bad:
                continue
            ds, ns_ = comp[d]["status"], comp[n]["status"]
            if e["source"] == "logs" or ds == "down" or (ds == "suspected" and ns_ == "suspected"):
                out.append(d)
        return out

    # ---- roots: bad nodes with no bad dependencies (break cycles by strongest/earliest)
    roots = {n for n in bad if not blamed(n)}
    covered = set()
    for r in roots:
        covered |= _upstream_of(r, bad, blamed)
    for n in sorted(bad - covered - roots, key=lambda n: (-_status_rank(comp[n]["status"]), comp[n]["first_ns"] or 1 << 62)):
        if n not in covered:          # inside a cycle with no clean root
            roots.add(n)
            covered |= _upstream_of(n, bad, blamed)

    # ---- build chains
    rev: dict[str, list] = defaultdict(list)
    for a in bad:
        for d in blamed(a):
            rev[d].append(a)
    chains = []
    for r in roots:
        c = comp[r]
        node = dm.by_name.get(r)
        known = {k["id"]: k for i in c["issues"] for k in i["known"]}
        known.update({k["id"]: k for f in c["k8s"] for k in f.get("known", [])})
        tree = _tree(r, rev, comp, edges, c["first_ns"], set([r]))
        if c["status"] == "suspected":       # borrow the diagnosis from the components that complain about it
            for up in _flatten(tree):
                for i in comp[up]["issues"]:
                    for k in i["known"]:
                        known.setdefault(k["id"], k)
        impacted = _count(tree)
        dependents = [n.name for n in dm.nodes if r in n.depends_on]
        at_risk = [d for d in dependents if d not in bad]
        chains.append({
            "root": r, "kind": node.kind if node else "service", "status": c["status"],
            "namespaces": sorted(c["namespaces"]) or [r], "first_ns": c["first_ns"],
            "evidence": c["evidence"][:8], "known": list(known.values()),
            "checks": _checks(r, node, c), "impacted": tree, "impacted_count": impacted,
            "at_risk": at_risk[:10],
            "rank": (_status_rank(c["status"]) * 1000 + impacted * 50
                     + max([i["score"] for i in c["issues"]] or [0])),
        })
    chains.sort(key=lambda ch: -ch["rank"])
    graph_edges = sorted({(a, b, e["source"]) for a, outs in edges.items() for b, e in outs.items()
                          if a in bad or b in bad})
    return {"chains": chains, "components": {n: comp[n]["status"] for n in comp},
            "graph": [{"from": a, "to": b, "source": s, "blamed": b in bad and a in bad} for a, b, s in graph_edges]}


def _around(text: str, m: re.Match, w: int = 60) -> str:
    return text[max(0, m.start() - w): m.end() + w].replace("\n", " ")


def _upstream_of(root, bad, blamed):
    seen, stack = {root}, [root]
    while stack:
        cur = stack.pop()
        for n in bad:
            if n not in seen and cur in blamed(n):
                seen.add(n); stack.append(n)
    return seen


def _tree(n, rev, comp, edges, root_first, seen):
    out = []
    for up in sorted(rev.get(n, [])):
        if up in seen:
            continue
        seen.add(up)
        c = comp[up]
        e = edges[up][n]
        out.append({
            "component": up, "status": c["status"], "via": e["source"], "via_evidence": e["evidence"],
            "symptoms": c["evidence"][:3],
            "before_root": bool(root_first and c["first_ns"] and c["first_ns"] < root_first - 60 * 10**9),
            "children": _tree(up, rev, comp, edges, root_first, seen),
        })
    return out


def _flatten(tree):
    for t in tree:
        yield t["component"]
        yield from _flatten(t["children"])


def _count(tree):
    return sum(1 + _count(t["children"]) for t in tree)


def _checks(name, node, c):
    ns = sorted(c["namespaces"])[0] if c["namespaces"] else name
    crashing = sorted({f["pod"] for f in c["k8s"] if f["kind"] in ("pod-state", "restart", "not-ready")})
    pods = crashing + sorted({p for i in c["issues"] for p in i["pods"]} - set(crashing))
    pod = pods[0] if pods else "<pod>"
    out = [f"kubectl -n {ns} get pods -o wide",
           f"kubectl -n {ns} describe pod {pod}",
           f"kubectl -n {ns} logs {pod} --since=1h | findstr /i \"error exception\"",
           f"kubectl -n {ns} get events --sort-by=.lastTimestamp"]
    if any(f["kind"] == "restart" or f["reason"] == "CrashLoopBackOff" for f in c["k8s"]):
        out.insert(2, f"kubectl -n {ns} logs {pod} --previous   (logs of the crashed container)")
    if c["status"] == "suspected":
        out.append(f"From an affected pod, test reachability: kubectl -n <ns> exec <pod> -- curl -sv http://{name}.{ns}")
    return out + (node.checks if node else [])
