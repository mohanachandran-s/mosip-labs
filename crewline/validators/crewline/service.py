"""Long-running local services a test run needs (the DSL TestRig's packet creator), started, checked and stopped.

  crewline service start  --name <n> --cwd <dir> --ready-url <url> [--copy-from <dir>] [--ready-timeout <min>]
                          [--env KEY=VALUE] [--out <task>/evidence/service-<n>.json] -- <command ...>
  crewline service health --name <n> [--out <json>]
  crewline service stop   --name <n> [--out <json>]
  crewline service list

`crewline run` waits for its command to exit, so it cannot hold a server open for the next command. `service start`
launches the command in the background (its own process group, stdout+stderr to `<cwd>/crewline-service-<n>.log`),
then polls `--ready-url` until it answers below HTTP 500 (a redirect or 401 still means the server is up). It returns
as soon as the service is ready, as soon as the process dies, or at `--ready-timeout` (then it stops the process).
`--copy-from` first copies a folder (e.g. the packet creator's `dockersupport/centralized/mosip-packet-creator`) to
`--cwd` when `--cwd` does not exist yet, so `crewline creds merge --into <cwd>/config/application.properties` fills
values in a copy and never in a tracked file. `--cwd` may not be a tracked or unignored folder of a git checkout
(put it under `.crewline-work/` or outside the checkout): filled-in secrets there could be committed.

The registry (one JSON per name, pid and URL; never the environment values) lives in `~/.crewline/services/`
(CREWLINE_SERVICES_DIR overrides): a port is a machine resource, so a second task sees a service the first one left.

  SVC-001  --cwd is inside a git checkout and not ignored (secrets copied there could be committed)       BLOCK
  SVC-002  a service of that name is registered and its process is alive: stop it first                 BLOCK
  SVC-003  something already answers on --ready-url before start (another instance, another app)        BLOCK
  SVC-004  the process exited before it was ready (last log lines, secrets masked)                      BLOCK
  SVC-005  not ready within --ready-timeout: the process was stopped                                    BLOCK
  SVC-006  started and ready / healthy / stopped (pid, seconds)                                         INFO
  SVC-007  health: process dead or URL not answering                                                    BLOCK
  SVC-008  no service of that name is registered                                                        WARN (stop) / BLOCK (health)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .gitio import git
from .result import BLOCK, INFO, WARN, Result

SECRET = re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key|private)")


def registry_dir() -> Path:
    d = Path(os.environ.get("CREWLINE_SERVICES_DIR") or Path.home() / ".crewline" / "services")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entry(name: str) -> Path:
    return registry_dir() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.json"


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                       # a zombie child of this process is not alive
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not alive(pid):
            return
        time.sleep(0.25)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def answers(url: str, timeout: float = 3.0) -> int | None:
    """HTTP status of `url` (redirects followed), or None when nothing answers."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _up(code: int | None) -> bool:
    return code is not None and code < 500


def tail(log: Path, n: int = 15) -> str:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return ""
    return "\n".join("    [line masked: may hold a secret]" if SECRET.search(l) else "    " + l[:300] for l in lines)


def _unprotected(cwd: Path) -> bool:
    """True when `cwd` sits in a git checkout and git would track files written there."""
    probe = cwd if cwd.exists() else cwd.parent
    while not probe.exists():
        probe = probe.parent
    top = git(probe, "rev-parse", "--show-toplevel", check=False).strip()
    if not top:
        return False
    rel = os.path.relpath(cwd.resolve(), Path(top).resolve()).replace(os.sep, "/")
    if rel.startswith(".."):
        return False
    return not git(Path(top), "check-ignore", "--no-index", rel + "/crewline-probe", check=False).strip()


def _record(res: Result, out: str | None) -> Result:
    res = res.finish()
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(res.to_json() + "\n", encoding="utf-8")
    return res


def start(name: str, cwd: str, command: list[str], ready_url: str, copy_from: str | None = None,
          ready_timeout: float = 10.0, env: list[str] | None = None, out: str | None = None,
          tick: float = 2.0) -> Result:
    d = Path(cwd)
    res = Result("service", inputs={"action": "start", "name": name, "cwd": str(d.resolve()), "ready_url": ready_url,
                                    "command": command, "env": [e.split("=", 1)[0] for e in env or []]})
    if not command:
        return Result.not_run("service", "no command: service start ... -- <command>")
    if _unprotected(d):
        res.add("SVC-001", BLOCK, f"{d} is inside a git checkout and not ignored: config values filled there could be "
                "committed", remediation="use <clone>/.crewline-work/tasks/<id>/services/<name> or a folder outside the checkout")
        return _record(res, out)
    reg = _entry(name)
    if reg.is_file():
        old = json.loads(reg.read_text(encoding="utf-8"))
        if alive(int(old.get("pid", 0))):
            res.add("SVC-002", BLOCK, f"{name} is already running (pid {old['pid']}, started {old.get('started_at')})",
                    remediation=f"crewline service stop --name {name}")
            return _record(res, out)
    if _up(answers(ready_url)):
        res.add("SVC-003", BLOCK, f"{ready_url} already answers before start: another instance or another app holds the "
                "port", remediation="stop it (or point the run at it and skip start)")
        return _record(res, out)
    if copy_from and not d.exists():
        shutil.copytree(copy_from, d)
        res.inputs["copied_from"] = str(Path(copy_from).resolve())
    d.mkdir(parents=True, exist_ok=True)
    log = d / f"crewline-service-{name}.log"
    run_env = dict(os.environ)
    for e in env or []:
        k, _, v = e.partition("=")
        run_env[k] = v
        if k == "JAVA_HOME":
            run_env["PATH"] = str(Path(v) / "bin") + os.pathsep + run_env.get("PATH", "")
    exe = shutil.which(command[0], path=run_env.get("PATH")) or command[0]
    kw: dict = {"cwd": str(d), "env": run_env, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kw["start_new_session"] = True
    t0 = time.monotonic()
    with open(log, "ab") as fh:
        proc = subprocess.Popen([exe] + command[1:], stdout=fh, stderr=subprocess.STDOUT, **kw)
    rec = {"name": name, "pid": proc.pid, "cwd": str(d.resolve()), "ready_url": ready_url, "log": str(log.resolve()),
           "command": command, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    reg.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    res.inputs.update(pid=proc.pid, log=rec["log"], registry=str(reg))
    deadline = t0 + ready_timeout * 60
    while True:
        code = proc.poll()
        if code is not None:
            reg.unlink(missing_ok=True)
            res.add("SVC-004", BLOCK, f"{name} exited with {code} before {ready_url} answered; last log lines:\n"
                    f"{tail(log)}", evidence=rec["log"])
            return _record(res, out)
        status = answers(ready_url)
        if _up(status):
            secs = round(time.monotonic() - t0, 1)
            res.inputs.update(ready_s=secs, ready_status=status)
            res.add("SVC-006", INFO, f"{name} ready in {secs}s (pid {proc.pid}, HTTP {status}); log {rec['log']}; "
                    f"stop with `crewline service stop --name {name}`")
            return _record(res, out)
        if time.monotonic() >= deadline:
            kill(proc.pid)
            reg.unlink(missing_ok=True)
            res.add("SVC-005", BLOCK, f"{ready_url} did not answer within {ready_timeout:g} min; {name} stopped. Last "
                    f"log lines:\n{tail(log)}", evidence=rec["log"])
            return _record(res, out)
        time.sleep(tick)


def health(name: str, out: str | None = None) -> Result:
    res = Result("service", inputs={"action": "health", "name": name})
    reg = _entry(name)
    if not reg.is_file():
        res.add("SVC-008", BLOCK, f"no service {name} is registered: start it first")
        return _record(res, out)
    rec = json.loads(reg.read_text(encoding="utf-8"))
    res.inputs.update(pid=rec["pid"], ready_url=rec["ready_url"], log=rec["log"])
    is_alive, status = alive(int(rec["pid"])), answers(rec["ready_url"])
    if not is_alive or not _up(status):
        res.add("SVC-007", BLOCK, f"{name}: process {'alive' if is_alive else 'dead'}, {rec['ready_url']} "
                f"{'HTTP ' + str(status) if status else 'not answering'}; last log lines:\n{tail(Path(rec['log']))}",
                evidence=rec["log"])
    else:
        res.add("SVC-006", INFO, f"{name} healthy (pid {rec['pid']}, HTTP {status})")
    return _record(res, out)


def stop(name: str, out: str | None = None) -> Result:
    res = Result("service", inputs={"action": "stop", "name": name})
    reg = _entry(name)
    if not reg.is_file():
        res.add("SVC-008", WARN, f"no service {name} is registered: nothing to stop")
        return _record(res, out)
    rec = json.loads(reg.read_text(encoding="utf-8"))
    pid = int(rec["pid"])
    was = alive(pid)
    if was:
        kill(pid)
    reg.unlink(missing_ok=True)
    res.inputs.update(pid=pid, log=rec["log"])
    res.add("SVC-006", INFO, f"{name} {'stopped' if was else 'was not running'} (pid {pid}); log kept at {rec['log']}")
    return _record(res, out)


def listing() -> Result:
    res = Result("service", inputs={"action": "list"})
    for f in sorted(registry_dir().glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        state = "alive" if alive(int(rec.get("pid", 0))) else "dead"
        res.add("SVC-006", INFO, f"{rec['name']}: pid {rec['pid']} {state}, {rec['ready_url']}, since {rec['started_at']}")
    if not res.findings:
        res.summary = "no services registered"
    return res.finish()
