"""End-to-end: synthetic MOSIP-like logs -> fake Loki -> scan/trace -> assertions + HTML report."""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fake_loki import FakeLoki  # noqa: E402

from roottrace import engine, report  # noqa: E402
from roottrace.config import Config  # noqa: E402

H = 3600 * 10**9
NOW = time.time_ns()
random.seed(7)


def L(ns, app, pod):
    return {"namespace": ns, "app": app, "pod": pod, "container": app}


def spring(level, logger, msg):
    return f"2026-11-19 10:00:00.000 {level} 1 --- [nio-8090-exec-3] {logger} : {msg}"


def build_lines():
    rows = []
    ida, kms, reg, res = (L("ida", "ida-auth-service", "ida-auth-service-7d9f8c6b5-x2k4p"),
                          L("keymanager", "keymanager", "keymanager-5c7d8f9b6-qwe12"),
                          L("regproc", "regproc-group1", "regproc-group1-0"),
                          L("resident", "resident", "resident-6b8c9d7f5-abc12"))
    for window_start, is_now in ((NOW - H - 24 * H, False), (NOW - H, True)):
        def t():
            return window_start + random.randint(0, H - 1)
        # steady background noise, present in both windows
        for _ in range(40):
            rows.append((t(), res, spring("ERROR", "i.m.r.s.impl.ResidentServiceImpl",
                                          f"RES-SER-410 Invalid input for individualId {random.randint(10**9, 10**10-1)}")))
            rows.append((t(), res, spring("INFO", "i.m.r.c.ResidentController", "request processed ok")))
        for _ in range(30):
            rows.append((t(), reg, spring("INFO", "i.m.r.s.Stage", "packet processed")))
        if is_now:
            # NEW: keymanager HSM failure + IDA failing downstream with trace ids
            for k in range(25):
                ts = t()
                rows.append((ts, kms, spring("ERROR", "i.m.k.k.s.impl.KeymanagerServiceImpl",
                                             "KER-KMS-012 Key generation failed for appId IDA refId SIGN")))
                rows.append((ts + 1, kms, "Caused by: sun.security.pkcs11.wrapper.PKCS11Exception: CKR_TOKEN_NOT_PRESENT"))
                rows.append((ts + 5, ida, json.dumps({
                    "level": "ERROR", "logger_name": "i.m.a.c.s.i.BioAuthServiceImpl",
                    "message": f"IDA-MLC-019 Unable to sign response transactionId=TXN{1000 + k} biometric=" + "A" * 300,
                    "stack_trace": "io.mosip.authentication.core.exception.IdAuthenticationBusinessException: sign failed\n"
                                   "\tat io.mosip.X.y(X.java:10)\nCaused by: org.springframework.web.client.HttpServerErrorException: 500"})))
                rows.append((ts - 10, ida, spring("INFO", "i.m.a.AuthFilter", f"auth request received transactionId=TXN{1000 + k}")))
            # spike of DB errors in resident
            for _ in range(15):
                rows.append((t(), res, spring("ERROR", "c.z.h.p.HikariPool",
                                              "HikariPool-1 - Connection is not available, request timed out after 30000ms.")))
        else:
            # regproc used to log in baseline (will look "silent" now: no lines at all this hour)
            pass
    # make regproc silent now: remove its current-window lines
    rows = [r for r in rows if not (r[1]["namespace"] == "regproc" and r[0] >= NOW - H)]
    return rows


def main():
    fake = FakeLoki(build_lines())
    url = fake.start()
    cfg = Config()
    cfg.loki.url = url
    cfg.loki.page_size = 50          # force pagination
    cfg.k8s.enabled = False
    cfg.environment_name = "dev-synthetic"

    r = engine.scan(cfg, NOW - H, NOW)
    out = Path(__file__).parent.parent / "sample-out"
    hp, jp = report.write(r, str(out))

    issues = r["issues"]
    print(r["verdict"])
    for i in issues:
        print(f"{i['score']:6.1f} new={i['is_new']!s:5} {i['service']:18} x{i['count']:<4} base={i['baseline_count']:<4} "
              f"known={[k['id'] for k in i['known']]} | {i['template'][:80]}")
    print("silent:", r["silent"])

    by_svc = {i["service"]: i for i in issues}
    assert issues[0]["service"] in ("keymanager", "ida-auth-service"), "HSM/ida failure should rank top"
    km = by_svc["keymanager"]
    assert km["is_new"] and km["count"] == 25
    assert "sun.security.pkcs11.wrapper.PKCS11Exception" in km["root_causes"], km["root_causes"]
    assert any(k["id"] == "KEYM-001" for k in km["known"])
    ida = by_svc["ida-auth-service"]
    assert "IDA-MLC-019" in ida["error_codes"] and ida["trace_ids"], ida
    assert "AAAAAAAAAA" not in json.dumps(r), "biometric blob must be masked"
    res_new = [i for i in issues if i["service"] == "resident" and i["is_new"]]
    assert res_new and any(k["id"] == "DB-001" for k in res_new[0]["known"])
    res_old = [i for i in issues if i["service"] == "resident" and not i["is_new"]]
    assert res_old and res_old[0]["count"] == 40 and res_old[0]["baseline_count"] == 40, "numbers templated -> one cluster"
    assert any(s["service"] == "regproc-group1" for s in r["silent"])
    assert r["critical"]

    tr = engine.trace(cfg, "TXN1003", NOW - H, NOW)
    print("trace:", tr["count"], tr["services"], tr["first_error"]["message"][:80])
    assert tr["count"] == 2 and tr["first_error"]["service"] == "ida-auth-service"
    assert tr["timeline"][0]["level"] == "INFO"

    print(f"\nALL CHECKS PASSED\nreport: {hp}")
    fake.stop()


if __name__ == "__main__":
    main()
