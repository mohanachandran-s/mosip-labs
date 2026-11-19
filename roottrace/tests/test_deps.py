"""Dependency analysis on a scenario modelled on a real 131ga scan."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from roottrace.deps import DepMap, analyze  # noqa: E402


def issue(ns, svc, tpl, count=5, new=False, known=()):
    return {"namespace": ns, "service": svc, "template": tpl, "level": "ERROR", "count": count,
            "is_new": new, "first_ns": 10**18, "score": 30, "root_causes": [], "exceptions": [],
            "error_codes": [], "pods": [f"{svc}-abc"], "samples": [{"text": tpl}], "known": list(known)}


OIDC = "Error connecting to OIDC service (WebClient) Problem in connecting to auth service or UNKNOWN Error."
raw = {
    "issues": [
        issue("pms", "pms-partner", OIDC, new=True, known=[{"id": "AUTH-002", "title": "t", "cause": "c", "fix": "f"}]),
        issue("pms", "pms-policy", OIDC, new=True),
        issue("kernel", "syncdata", OIDC),
        issue("admin", "admin-hotlist", OIDC),
        issue("websub", "websub", "error: Publisher is not authorized", 45),
        issue("resident", "resident", "Error registering topic to hub :- hub.mode=denied&hub.reason=Publisher is not authorized", 45),
        issue("idrepo", "credentialrequest", "Encountered an error executing step credentialReProcessJob [java.lang.ClassCastException]", 1),
        issue("regproc", "regproc-group1", "Failed to send message to queue", 3),
    ],
    "k8s": [{"kind": "pod-state", "namespace": "activemq", "pod": "activemq-activemq-artemis-slave-0",
             "container": "x", "reason": "CrashLoopBackOff", "message": "", "restarts": 9},
            {"kind": "job-failed", "namespace": "apitestrig", "pod": "cronjob-apitestrig-auth-1",
             "container": "-", "reason": "JobFailed", "message": "", "restarts": 0}],
    "silent": [],
}
d = analyze(raw, DepMap())
chains = {c["root"]: c for c in d["chains"]}
print({r: (c["status"], c["impacted_count"]) for r, c in chains.items()})

assert set(chains) == {"activemq", "keycloak", "websub", "idrepo-credentialrequest"}, chains.keys()
assert d["chains"][0]["root"] == "activemq"                                   # hard-down ranks first
assert [t["component"] for t in chains["activemq"]["impacted"]] == ["regproc"]  # down dep blamed via map
kc = chains["keycloak"]
assert kc["status"] == "suspected"
flat = str(kc["impacted"])
assert all(x in flat for x in ("pms", "syncdata", "admin")) and "websub" not in flat and "credentialrequest" not in flat
assert any(k["id"] == "AUTH-002" for k in kc["known"])                       # borrowed diagnosis
assert [t["component"] for t in chains["websub"]["impacted"]] == ["resident"]
assert chains["idrepo-credentialrequest"]["impacted_count"] == 0
assert "testrigs" not in chains
print("DEPS CHECKS PASSED")
