"""Configuration loading. Values in YAML can be overridden by ROOTTRACE_* env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class LokiConfig:
    url: str = "http://localhost:3100"
    username: str | None = None
    password: str | None = None
    bearer_token: str | None = None
    org_id: str | None = None          # X-Scope-OrgID for multi-tenant Loki
    verify_tls: bool = True
    page_size: int = 5000
    max_lines: int = 200_000           # safety cap per query window
    timeout_s: int = 60


@dataclass
class K8sConfig:
    enabled: bool = True
    kubeconfig: str | None = None      # Rancher-downloaded kubeconfig; None = default / in-cluster
    context: str | None = None


@dataclass
class Config:
    loki: LokiConfig = field(default_factory=LokiConfig)
    k8s: K8sConfig = field(default_factory=K8sConfig)
    namespaces: list[str] = field(default_factory=list)   # empty = all
    exclude_namespaces: list[str] = field(default_factory=lambda: [
        "kube-system", "cattle-system", "cattle-monitoring-system", "cattle-logging-system",
        "cattle-fleet-system", "fleet-system", "longhorn-system", "istio-system",
    ])
    service_label: str = "app"         # label used as "service" name; falls back to container/pod
    error_filter: str = r"(?i)(error|exception|fatal|severe|caused by|failed|refused|timed? ?out)"
    include_warn: bool = False
    baseline_offset: str = "24h"      # compare to same window this long ago
    spike_factor: float = 3.0          # current/baseline ratio considered a spike
    critical_score: float = 50.0       # score threshold -> exit code 2
    catalog_paths: list[str] = field(default_factory=list)
    dependency_map_paths: list[str] = field(default_factory=list)   # extra/override dependency maps
    slack_webhook: str | None = None
    environment_name: str = "env"
    public_url: str | None = None      # e.g. https://roottrace.dev.mosip.net (used for Slack links)


def _merge(dc, data: dict):
    for k, v in (data or {}).items():
        if hasattr(dc, k):
            cur = getattr(dc, k)
            if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
                _merge(cur, v)
            else:
                setattr(dc, k, v)


def load_config(path: str | None) -> Config:
    cfg = Config()
    if path:
        _merge(cfg, yaml.safe_load(Path(path).read_text()) or {})
    env = os.environ
    cfg.loki.url = env.get("ROOTTRACE_LOKI_URL", cfg.loki.url)
    cfg.loki.username = env.get("ROOTTRACE_LOKI_USER", cfg.loki.username)
    cfg.loki.password = env.get("ROOTTRACE_LOKI_PASSWORD", cfg.loki.password)
    cfg.loki.bearer_token = env.get("ROOTTRACE_LOKI_TOKEN", cfg.loki.bearer_token)
    cfg.loki.org_id = env.get("ROOTTRACE_LOKI_ORG", cfg.loki.org_id)
    cfg.slack_webhook = env.get("ROOTTRACE_SLACK_WEBHOOK", cfg.slack_webhook)
    cfg.environment_name = env.get("ROOTTRACE_ENV_NAME", cfg.environment_name)
    cfg.public_url = env.get("ROOTTRACE_PUBLIC_URL", cfg.public_url)
    return cfg
