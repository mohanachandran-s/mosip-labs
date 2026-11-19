"""Kubernetes health signals: restarts, bad pod states, warning events, recent rollouts."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import K8sConfig

log = logging.getLogger(__name__)
BAD_WAITING = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
               "CreateContainerError", "InvalidImageName", "RunContainerError"}


def _ns_of(t) -> int:
    if t is None:
        return 0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int(t.timestamp() * 1e9)


def collect(cfg: K8sConfig, namespaces: list[str], exclude: list[str], start_ns: int, end_ns: int) -> dict:
    """Returns {"findings": [...], "rollouts": [...], "error": str|None}. Never raises."""
    result = {"findings": [], "rollouts": [], "error": None}
    if not cfg.enabled:
        return result
    try:
        from kubernetes import client, config as kcfg
        try:
            if cfg.kubeconfig or not _in_cluster():
                kcfg.load_kube_config(config_file=cfg.kubeconfig, context=cfg.context)
            else:
                kcfg.load_incluster_config()
        except Exception:
            kcfg.load_incluster_config()
        core, apps = client.CoreV1Api(), client.AppsV1Api()
        ns_list = namespaces or [n.metadata.name for n in core.list_namespace().items]
        ns_list = [n for n in ns_list if n not in exclude]
        for ns in ns_list:
            for pod in core.list_namespaced_pod(ns).items:
                for cs in (pod.status.container_statuses or []):
                    w = cs.state.waiting if cs.state else None
                    if w and w.reason in BAD_WAITING:
                        result["findings"].append(dict(kind="pod-state", namespace=ns, pod=pod.metadata.name,
                                                       container=cs.name, reason=w.reason,
                                                       message=(w.message or "")[:400], restarts=cs.restart_count))
                    term = cs.last_state.terminated if cs.last_state else None
                    if cs.restart_count and term and start_ns <= _ns_of(term.finished_at) <= end_ns:
                        result["findings"].append(dict(kind="restart", namespace=ns, pod=pod.metadata.name,
                                                       container=cs.name, reason=term.reason or "Restarted",
                                                       message=f"exit code {term.exit_code}", restarts=cs.restart_count,
                                                       ts_ns=_ns_of(term.finished_at)))
                    if pod.status.phase == "Running" and not cs.ready and not (w and w.reason in BAD_WAITING):
                        result["findings"].append(dict(kind="not-ready", namespace=ns, pod=pod.metadata.name,
                                                       container=cs.name, reason="NotReady", message="",
                                                       restarts=cs.restart_count))
                if pod.status.phase == "Failed" or pod.status.reason == "Evicted":
                    ends = [_ns_of(cs.state.terminated.finished_at) for cs in (pod.status.container_statuses or [])
                            if cs.state and cs.state.terminated]
                    fin = max(ends) if ends else _ns_of(pod.status.start_time)
                    if start_ns <= fin <= end_ns:
                        is_job = any(o.kind == "Job" for o in (pod.metadata.owner_references or []))
                        result["findings"].append(dict(kind="job-failed" if is_job else "pod-state", namespace=ns,
                                                       pod=pod.metadata.name, container="-",
                                                       reason=pod.status.reason or ("JobFailed" if is_job else "Failed"),
                                                       message=(pod.status.message or "")[:400], restarts=0, ts_ns=fin))
            for ev in core.list_namespaced_event(ns).items:
                ts = _ns_of(ev.last_timestamp or ev.event_time or ev.first_timestamp)
                if ev.type == "Warning" and start_ns <= ts <= end_ns:
                    result["findings"].append(dict(kind="event", namespace=ns, pod=ev.involved_object.name,
                                                   container=ev.involved_object.kind, reason=ev.reason,
                                                   message=(ev.message or "")[:400], restarts=ev.count or 1, ts_ns=ts))
            for rs in apps.list_namespaced_replica_set(ns).items:
                ts = _ns_of(rs.metadata.creation_timestamp)
                if start_ns <= ts <= end_ns and (rs.spec.replicas or 0) > 0:
                    owner = (rs.metadata.owner_references or [None])[0]
                    img = ", ".join(c.image.split("/")[-1] for c in rs.spec.template.spec.containers)
                    result["rollouts"].append(dict(namespace=ns, name=owner.name if owner else rs.metadata.name,
                                                   ts_ns=ts, images=img))
    except Exception as e:  # k8s is optional — the report still works on logs alone
        log.warning("Kubernetes collection skipped: %s", e)
        result["error"] = str(e)
    return result


def _in_cluster() -> bool:
    import os
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
