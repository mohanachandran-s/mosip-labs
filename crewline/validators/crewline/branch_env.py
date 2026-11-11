"""Branch-environment gate (Q8): system tests of production code need an env running THIS branch.

Does not provision the environment. It refuses SYSTEM_TEST_EXECUTION when the plan has no
kind, names a released-only env as if it were the branch, or defers without an exception.

  BEV-001  code/test task: test_plan.environment_kind missing or not an allowed value              BLOCK
  BEV-002  post-merge-deferred without an attributed system_tests exception                        BLOCK
  BEV-003  environment_kind is branch-deployed-env or local-stack but test_plan.environment empty  BLOCK
  BEV-004  environment string names a released-only host (api-internal.released) as if it were the branch  BLOCK
           (an attributed `branch_environment` exception covers it: the finding is APPROVED and the gate PASSes)
  BEV-005  local-test-rig while a changed file is outside the test rig (api-test/, apitest-commons/, UI/DSL rigs),
           or no implementation.changed_files recorded                                                    BLOCK

environment_kind `local-test-rig`: the change is in the test rig itself, so the branch build IS the local test jar and
running it against a released environment is the correct system test (BEV-004 does not apply).

  python -m crewline branch-env --manifest <m>
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .result import BLOCK, Result

RELEASED_HOST = re.compile(r"api-internal\.released|\.released\b|released-only", re.I)

NEEDS_ENV = {"feature", "bug-fix", "refactor", "dependency-upgrade", "database-migration", "test-automation"}
ALLOWED = {"branch-deployed-env", "local-stack", "post-merge-deferred", "local-test-rig"}
TEST_RIG = re.compile(r"(^|/)(api-test|apitest-commons|ui-test|uitest[\w-]*|dsl-?test[\w-]*)/", re.I)
FIELDS = ("approver", "reason", "scope", "timestamp")


def _exception(doc: dict, gate: str) -> dict | None:
    return next((e for e in doc.get("exceptions") or [] if e.get("gate") == gate and all(e.get(k) for k in FIELDS)), None)


def run(manifest_path: str) -> Result:
    res = Result("branch-env", inputs={"manifest": manifest_path})
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result.not_run("branch-env", str(e))
    task_type = doc.get("task_type") or "test-automation"
    if task_type not in NEEDS_ENV:
        return res.finish()
    plan = doc.get("test_plan") or {}
    kind = plan.get("environment_kind") or ""
    env = (plan.get("environment") or "").strip()
    if kind not in ALLOWED:
        res.add("BEV-001", BLOCK, f"test_plan.environment_kind={kind!r}: need branch-deployed-env, local-stack, "
                "local-test-rig or post-merge-deferred (open-questions Q8: an env that runs the branch build)",
                remediation="name how the branch build gets onto a non-prod env, or a local-stack; never treat "
                "api-internal.released as the branch")
    if kind == "post-merge-deferred":
        if not _exception(doc, "system_tests"):
            res.add("BEV-002", BLOCK, "post-merge-deferred needs an attributed system_tests exception "
                    "(environment + date); results stay DEFERRED and become a follow-up task")
    if kind in {"branch-deployed-env", "local-stack", "local-test-rig"} and not env:
        res.add("BEV-003", BLOCK, "environment_kind set but test_plan.environment is empty")
    if kind == "local-test-rig":
        changed = [f.replace("\\", "/") for f in (doc.get("implementation") or {}).get("changed_files") or []]
        outside = [f for f in changed if not TEST_RIG.search("/" + f.lstrip("/"))]
        if not changed or outside:
            res.add("BEV-005", BLOCK, "local-test-rig is only for a change to the test rig itself: "
                    + (f"{', '.join(outside[:5])} is outside it" if outside else "record implementation.changed_files"),
                    remediation="production code needs branch-deployed-env or local-stack")
    if kind in {"branch-deployed-env", "local-stack"} and env and RELEASED_HOST.search(env):
        f = res.add("BEV-004", BLOCK, f"test_plan.environment={env!r} is a released-only host; Q8 needs the BRANCH build "
                    "(local-stack docker-compose / mvn spring-boot:run, or a named dev/qa env running this image)")
        exc = _exception(doc, "branch_environment")
        if exc:
            f.approved_by = exc["approver"]
            res.inputs["exception"] = {k: exc[k] for k in FIELDS}
    res.inputs["environment_kind"] = kind
    return res.finish()
