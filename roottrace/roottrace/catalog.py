"""Known-issue catalog: YAML signatures -> probable cause + fix. Team-maintained tribal knowledge."""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml

from .miner import Issue


class Catalog:
    def __init__(self, paths: list[str]):
        self.entries = []
        texts = [resources.files("roottrace").joinpath("known_issues.yaml").read_text()]
        texts += [Path(p).read_text() for p in paths]
        for t in texts:
            for e in (yaml.safe_load(t) or {}).get("issues", []):
                m = e.get("match", {})
                e["_rx"] = re.compile(m["regex"], re.I) if m.get("regex") else None
                e["_svc"] = re.compile(m["service"], re.I) if m.get("service") else None
                self.entries.append(e)

    def apply(self, issues: list[Issue]):
        for iss in issues:
            hay = "\n".join([iss.template, *iss.exceptions, *iss.root_causes, *iss.error_codes,
                             *(s["text"][:1500] for s in iss.samples)])
            for e in self.entries:
                if e["_svc"] and not e["_svc"].search(iss.service):
                    continue
                if e["_rx"] and e["_rx"].search(hay):
                    iss.known.append({k: e.get(k) for k in ("id", "title", "severity", "cause", "fix", "owner")})

    def apply_k8s(self, findings: list[dict]):
        for f in findings:
            hay = f"{f.get('reason','')} {f.get('message','')}"
            for e in self.entries:
                if e.get("scope") == "k8s" and e["_rx"] and e["_rx"].search(hay):
                    f.setdefault("known", []).append({k: e.get(k) for k in ("id", "title", "cause", "fix")})
