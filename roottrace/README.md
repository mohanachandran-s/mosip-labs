# RootTrace

**RootTrace finds root causes in MOSIP environments running on Rancher-managed Kubernetes.**
It reads error logs from Loki, checks pod health in Kubernetes, and follows the dependencies between services. That lets it answer three questions:

1. **What is broken?** Error patterns are grouped and ranked, and anything new compared with yesterday is flagged.
2. **Where did it start?** It identifies the root-cause component.
3. **What else is affected?** It shows the services impacted by that root cause and the ones still at risk.

The result is a single HTML report, plus JSON output, a Slack digest, an exit code for CI, and an HTTP API for test rigs.

> **Is AI involved?** No. RootTrace has no AI or LLM at runtime. The "cause → fix" text comes from a YAML
> catalog (`roottrace/known_issues.yaml`) that the tool matches against your error lines with regular expressions.
> The starter catalog and dependency map are **suggestions to be reviewed by your team**. See
> [Extending RootTrace](#9-extending-roottrace). No log data leaves your machine or cluster.

---

## Contents
1. [What you get](#1-what-you-get)
2. [How it works](#2-how-it-works)
3. [Quick start (Windows, step by step)](#3-quick-start-windows-step-by-step)
4. [Connect to your cluster](#4-connect-to-your-cluster)
5. [Run your first scan](#5-run-your-first-scan)
6. [Reading the report](#6-reading-the-report)
7. [All commands](#7-all-commands)
8. [Configuration reference](#8-configuration-reference)
9. [Extending RootTrace](#9-extending-roottrace)
10. [Deploy in Rancher (continuous mode)](#10-deploy-in-rancher-continuous-mode)
11. [Test-rig integration (Java / TestNG)](#11-test-rig-integration-java--testng)
12. [Troubleshooting](#12-troubleshooting)
13. [Project structure](#13-project-structure)
14. [Linux / macOS notes](#14-linux--macos-notes)
15. [Roadmap](#15-roadmap)

---

## 1. What you get

| Feature | What it does |
|---|---|
| **Error mining** | Groups thousands of error lines into a few dozen patterns per service (Drain3 clustering) |
| **Baseline diff** | Compares the window with the same window 24 hours earlier, and flags **NEW** patterns, spikes, patterns that disappeared, and services that **went silent** |
| **Failure chains** | Finds the **root cause**, an **impact tree** of the services it affects, and the services **at risk** |
| **Known-issue catalog** | Maps a log signature to a probable cause, a fix and an owner |
| **Kubernetes health** | Flags CrashLoopBackOff, OOMKilled, image pull errors, restarts, NotReady pods, warning events and rollouts |
| **Masking** | Hides biometric/base64 blobs, JWTs, e-mails and 10–16 digit IDs before anything is shown or stored |
| **Transaction trace** | Shows every log line for one transaction ID across all services, with the first error highlighted |
| **Outputs** | HTML report, JSON, Slack, exit code (2 = critical), HTTP API |

## 2. How it works

```
 Loki ──(errors in window + same window yesterday)──┐
                                                     ├─► parse ─► mask ─► cluster ─► diff vs baseline ─► score
 Kubernetes API ──(pods, restarts, events)───────────┘                                    │
                                                                                          ▼
                               known_issues.yaml ──► match cause/fix      dependencies.yaml + deps seen in logs
                                                                                          │
                                                                                          ▼
                                                              root cause ─► impact tree ─► at-risk services
                                                                                          │
                                         HTML report · JSON · Slack · exit code · HTTP API (test rigs)
```

**How root causes are decided:**
- A component is a **root** when it is unhealthy and none of its dependencies explain its trouble.
- A dependency taken from the map is blamed only when it is clearly **down**, meaning crashing, not ready, or silent.
- A dependency that is merely degraded is blamed only when the service's **own errors mention it**. For example, "Error connecting to OIDC service" points at keycloak.
- When several services fail talking to a component that looks healthy, that component is marked **suspected**. The usual causes are network, credentials, config, or its logs not reaching Loki.
- When a "symptom" started failing *before* its root, the report warns that the two may be unrelated.

---

## 3. Quick start (Windows, step by step)

> You don't need to know Python. You'll only run commands and edit one config file.
> Use **IntelliJ** (or VS Code). Open the project folder and use its built-in **Terminal**, which runs PowerShell.

### Prerequisites
| Tool | Check | Install |
|---|---|---|
| Python **3.10+** | `python --version` | python.org, and **tick "Add python.exe to PATH"** in the installer |
| kubectl | `kubectl version --client` | `winget install -e --id Kubernetes.kubectl` (then reopen the terminal) |
| Rancher access | You can open your cluster in the Rancher UI | Ask your Rancher admin |

### Step 1: Get the code
Unzip or clone into a short path, for example `D:\Work\roottrace`. Make sure the folder isn't nested twice (`roottrace\roottrace`).
In IntelliJ: **File → Open → select the folder → Open as Project**.

### Step 2: Create an isolated Python environment
In the IntelliJ terminal, inside the project folder:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
- If you get *"running scripts is disabled"*, run this once and then try again:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```
- When it works, your prompt starts with `(.venv)`.
- ⚠️ **Every new terminal** needs `.\.venv\Scripts\Activate.ps1` again.

### Step 3: Install
```powershell
pip install -e .
roottrace --help
```
You should see a usage message listing `scan`, `trace` and `serve`.

### Step 4: Self-test (no cluster needed)
```powershell
$env:PYTHONUTF8 = "1"        # lets emoji print correctly on Windows
python tests\test_e2e.py
python tests\test_deps.py
start sample-out\roottrace-report.html
```
Expected output: `ALL CHECKS PASSED` and `DEPS CHECKS PASSED`, then a sample report opens in your browser.
The test uses a built-in fake Loki with synthetic MOSIP-style logs.

---

## 4. Connect to your cluster

### Step 5: Download the kubeconfig from Rancher
1. In the Rancher UI, open your cluster.
2. In the top-right corner, click **Download KubeConfig** (the file icon with a down arrow).
3. Move the file into the project folder and tell kubectl to use it:
   ```powershell
   Move-Item "$HOME\Downloads\<downloaded-file>.yaml" .\mycluster.yaml
   $env:KUBECONFIG = "$PWD\mycluster.yaml"
   kubectl get nodes
   ```
   You should see your nodes with status `Ready`.
   ⚠️ Like the venv activation, `$env:KUBECONFIG` applies **per terminal**.
   You can ignore the *"Use tokens from the TokenRequest API…"* warning.

> Kubeconfig files contain credentials. **Never commit them to git.** `*.yaml` files with tokens should be in `.gitignore`.

### Step 6: Find Loki
```powershell
kubectl get svc -A | findstr /i "loki grafana"
```
Note the **namespace**, **service name** and **port**. On MOSIP clusters set up with Alloy, this is typically
`loki-monitoring   loki   3100/TCP`.

### Step 7: Check you're allowed to open a tunnel
```powershell
kubectl auth can-i create pods/portforward -n loki-monitoring
```
- **yes**: go to Step 8.
- **no**: see [Troubleshooting → port-forward forbidden](#port-forward-forbidden). Some clusters restrict this.

### Step 8: Open the tunnel to Loki (in a second terminal tab)
In IntelliJ's terminal panel, click **+** to open a new tab, then:
```powershell
$env:KUBECONFIG = "<full path to>\mycluster.yaml"
kubectl port-forward -n loki-monitoring svc/loki 3100:3100
```
Wait for `Forwarding from 127.0.0.1:3100`, then **leave this tab open**. Closing it stops the tunnel.

### Step 9: Check which labels your logs carry (back in the first tab)
```powershell
(Invoke-RestMethod http://localhost:3100/loki/api/v1/labels).data
```
You should see names like `app`, `container`, `namespace`, `pod`, `level`. If there is **no `app`** label, use
`service_label: container` in the next step.

### Step 10: Create `config.yaml`
Copy the example and edit it:
```powershell
Copy-Item config.example.yaml config.yaml
```
At minimum, set these:
```yaml
environment_name: dev-131ga                 # any name, shown in reports
loki:
  url: http://localhost:3100                # the tunnel from Step 8
k8s:
  enabled: true
  kubeconfig: D:\Work\roottrace\mycluster.yaml
service_label: app
```
> The file must be named exactly as you pass it with `-c`. `config.yaml` and `config.yml` are different files.

---

## 5. Run your first scan

```powershell
$env:PYTHONUTF8 = "1"
roottrace scan --since 30m -c config.yaml -v
start roottrace-out\roottrace-report.html
```
- A scan of the first 30 minutes usually takes 1–3 minutes on a mid-size cluster.
- `-v` prints progress (`INFO …`) so you can see it working.
- The terminal prints the verdict and the top 10 patterns. The full detail is in the HTML report.

**Exit code:** `0` means healthy or degraded, and `2` means **critical**. That's a critical known issue, failing pods, a silent service, or a high score, so you can use it to gate CI or stop a test run.

### Your daily routine (after the first setup)
```powershell
# Tab 1
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
# Tab 2
$env:KUBECONFIG = "<path>\mycluster.yaml"; kubectl port-forward -n loki-monitoring svc/loki 3100:3100
# Tab 1
roottrace scan --since 1h -c config.yaml
start roottrace-out\roottrace-report.html
```

---

## 6. Reading the report

The report runs top to bottom, most important first:

| Section | What to look for |
|---|---|
| **Verdict bar** | 🔴 unhealthy / 🟠 degraded / 🟢 healthy, plus the names of the root causes |
| **Failure chains** | One card per root cause, showing: <br>• **Status badge**: `down` (crashing, not ready or silent), `degraded` (has errors), or `suspected` (looks healthy, but others fail talking to it) <br>• **Evidence**: pod states and top error patterns <br>• **Impact tree**: the components failing because of this root, each with the log line that links them, and ⚠ marks where the timing doesn't fit <br>• **Troubleshoot**: the matching known-issue fix plus ready-to-run `kubectl` commands aimed at the right pod <br>• **At risk**: services that depend on this root but are still healthy |
| **Tiles** | Counts of patterns, new patterns, events, services with errors, pod problems, and silent services |
| **Probable root causes** | Every known-issue match and where it occurred |
| **Same error across several services** | One message seen in several services usually points to one shared upstream cause |
| **Top issues** | All patterns ranked by score. Click **samples** to see the real (masked) log lines and trace IDs, or use the filter box to search |
| **Kubernetes** | Pod states, restarts, warning events, and failed test-rig jobs |
| **Rollouts / Services gone silent / Gone since baseline** | Deploys in the window, services that stopped logging, and patterns that disappeared |

**Score** weighs log level × volume, new vs baseline, spread across pods, and known-issue severity. A high score means "look here first".

---

## 7. All commands

```powershell
# Scan a look-back window
roottrace scan --since 1h -c config.yaml
# Scan an exact window (for example a test run), limited to some namespaces
roottrace scan --from 2026-11-19T09:00:00Z --to 2026-11-19T10:30:00Z -n ida -n keymanager -c config.yaml
# Compare against last week instead of yesterday
roottrace scan --since 2h --baseline 7d -c config.yaml
# Post a summary to Slack (needs ROOTTRACE_SLACK_WEBHOOK)
roottrace scan --since 1h --slack -c config.yaml
# Print a JSON summary (for scripts or CI)
roottrace scan --since 1h --json -c config.yaml

# Follow one transaction across all services
roottrace trace TXN12345 --since 6h -c config.yaml

# Run as an HTTP API (this is what the Rancher Deployment runs)
roottrace serve -c config.yaml --port 8080
#   GET /scan?since=30m[&namespace=ida,resident][&from=..&to=..][&slack=1&only_if=unhealthy]
#   GET /trace/<id>?since=6h
#   GET /report      -> latest HTML report
#   GET /health
```
Time formats: `--since 30m | 2h | 1d | 1w`. `--from` and `--to` take ISO-8601 (`2026-11-19T09:00:00Z`) or epoch time.

---

## 8. Configuration reference

`config.yaml`. Every key is optional; defaults are shown.

| Key | Default | Meaning |
|---|---|---|
| `environment_name` | `env` | Name shown in reports and Slack |
| `public_url` | `null` | URL where the API is exposed; used for the "Full report" link in Slack |
| `loki.url` | `http://localhost:3100` | Loki base URL |
| `loki.username` / `loki.password` | `null` | Basic auth (prefer env vars) |
| `loki.bearer_token` | `null` | Bearer token auth |
| `loki.org_id` | `null` | `X-Scope-OrgID` for multi-tenant Loki |
| `loki.verify_tls` | `true` | Verify TLS certificates |
| `loki.max_lines` | `200000` | Safety cap per query window |
| `k8s.enabled` | `true` | Collect pod and event health |
| `k8s.kubeconfig` | `null` | Path to the Rancher kubeconfig (`null` = default or in-cluster) |
| `k8s.context` | `null` | Kubeconfig context |
| `namespaces` | `[]` | Namespaces to scan (empty = all except `exclude_namespaces`) |
| `exclude_namespaces` | system namespaces | Namespaces to skip |
| `service_label` | `app` | Loki label that names the service |
| `error_filter` | errors, exceptions, timeouts… | Regex that pre-filters lines in Loki |
| `include_warn` | `false` | Also count WARN lines |
| `baseline_offset` | `24h` | How far back the comparison window is |
| `spike_factor` | `3.0` | Current / baseline ratio counted as a spike |
| `critical_score` | `50` | An issue scoring at or above this makes the scan exit with code 2 |
| `catalog_paths` | `[]` | Extra known-issue YAML files |
| `dependency_map_paths` | `[]` | Extra dependency-map YAML files that add to or override the built-in one |
| `slack_webhook` | `null` | Slack incoming webhook (prefer the env var) |

**Environment variables** override the file and keep secrets out of it:
`ROOTTRACE_LOKI_URL`, `ROOTTRACE_LOKI_USER`, `ROOTTRACE_LOKI_PASSWORD`, `ROOTTRACE_LOKI_TOKEN`, `ROOTTRACE_LOKI_ORG`,
`ROOTTRACE_SLACK_WEBHOOK`, `ROOTTRACE_ENV_NAME`, `ROOTTRACE_PUBLIC_URL`.

---

## 9. Extending RootTrace

RootTrace gets smarter as the team adds its knowledge. **Every time you resolve an environment incident, add an entry.**

### Known-issue catalog
Create `team-known-issues.yaml` and list it under `catalog_paths`:
```yaml
issues:
  - id: MOSIP-PRINT-001
    title: Print stage stuck — credential not issued
    severity: high                    # critical | high | medium | low
    match:
      regex: "PRT-PDC-|credential.*not (found|issued)"
      service: "print|credential"     # optional: only match these services
    cause: What is usually behind it.
    fix: Exactly what to check or do.
    owner: regproc-team
  - id: K8S-CUSTOM-001
    scope: k8s                        # matches Kubernetes pod/event findings instead of logs
    title: ...
    match: {regex: "FailedScheduling"}
    cause: ...
    fix: ...
```
Built-in entries live in `roottrace/known_issues.yaml`. **Review them.** They're starting suggestions, not verified fixes.

### Dependency map
Create `team-deps.yaml` and list it under `dependency_map_paths`. A node with the same `name` overrides the built-in one, and new nodes are added:
```yaml
nodes:
  - name: esignet
    match: {namespace: "esignet"}           # regex on namespace (and optionally app / pod name)
    mentions: "esignet|ESI-"                # log text that refers to this component
    depends_on: [keycloak, keymanager, postgres]
    checks: ["Check the esignet client/keys in the admin console"]
```
The built-in map is `roottrace/dependencies.yaml`. **Have an architect review it** against your deployment.
Dependencies found in the logs are added automatically, so the map doesn't need to be perfect.

---

## 10. Deploy in Rancher (continuous mode)

Running inside the cluster removes the need for a laptop tunnel and enables scheduled scans with Slack alerts.

1. **Build and push the image**
   ```bash
   docker build -t <your-registry>/roottrace:0.1.0 .
   docker push <your-registry>/roottrace:0.1.0
   ```
2. **Edit `deploy/roottrace.yaml`**:
   - the image
   - the Loki URL in the ConfigMap (in-cluster, for example `http://loki.loki-monitoring.svc:3100`)
   - `environment_name`
   - the Slack webhook in the Secret
3. **Apply** with `kubectl apply -f deploy/roottrace.yaml`, or use Rancher → **Import YAML**. This creates:
   - namespace `roottrace`
   - a **read-only** ClusterRole (pods, events, namespaces, replicasets)
   - the API **Deployment** and **Service**
   - a **CronJob** that scans the last 30 minutes, every 30 minutes, and posts to Slack **only when unhealthy**
4. Optional: add an Ingress and set `public_url`, so Slack messages link to `/report`.

> Tip: point Slack at yourself for the first week and tune out noise before announcing it to the team.

---

## 11. Test-rig integration (Java / TestNG)

`java-client/` contains a zero-dependency client and a TestNG listener (Java 11+).

1. Build `java-client` (`mvn install`) and add it as a dependency of your test rig, or copy the two classes in.
2. Register the listener in `testng.xml`:
   ```xml
   <listeners><listener class-name="io.mosip.testrig.roottrace.RootTraceListener"/></listeners>
   ```
3. Run with `-DrootTraceUrl=http://roottrace.roottrace.svc`, or set the env var `ROOTTRACE_URL`.

**What happens:**
- **On each failed test:**
  - If the test set `result.setAttribute("transactionId", id)`, the listener follows that transaction across services.
  - Otherwise it scans the test's own time window (±60s).
  - The headline goes to the TestNG Reporter log and into the `rootTraceHeadline` result attribute, so your report can show *why* the test failed on the backend.
- **At suite end:** it scans the whole run window and prints the verdict plus a link to the report.
- If RootTrace is unreachable, the listener logs it and **never fails your tests**.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `running scripts is disabled on this system` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Prompt has no `(.venv)` / `roottrace` not recognized | `.\.venv\Scripts\Activate.ps1` (needed in every new terminal) |
| `python` opens the Microsoft Store | Reinstall Python from python.org with **Add to PATH** ticked; reopen the terminal |
| `UnicodeEncodeError` / garbled emoji | `$env:PYTHONUTF8 = "1"` |
| `FileNotFoundError: config.yml` | The file name passed to `-c` doesn't match the real file (`.yaml` vs `.yml`) |
| `kubectl get nodes` works in one tab but `the server could not find the requested resource` in another | The tabs point at different kubeconfigs. Print `$env:KUBECONFIG` in the working tab and set the same value in the other |
| <a name="port-forward-forbidden"></a>`pods/portforward is forbidden` | Your Rancher user can't open tunnels in that namespace. Options: **(a)** ask the admin for `pods/portforward` in the Loki namespace; **(b)** deploy RootTrace in the cluster (§10); **(c)** go through Grafana's data source proxy: set `loki.url: http://<grafana>/api/datasources/proxy/uid/<loki-uid>` and put your Grafana login in `ROOTTRACE_LOKI_USER` / `ROOTTRACE_LOKI_PASSWORD`. Find the UID in Grafana → Connections → Data sources → Loki, at the end of the URL. Option (c) is not yet tested |
| `Connection refused` to `localhost:3100` | The port-forward tab was closed or failed; restart Step 8 |
| `HTTP 400 … parse error` | Usually a hand-edited `error_filter` or namespace with special characters; restore the default |
| `no org id` | Multi-tenant Loki: set `loki.org_id` (ask your Loki admin for the tenant) |
| `Hit max_lines cap` warning | Narrow `--since`, add `-n <namespace>`, or raise `loki.max_lines` |
| Report says `Kubernetes data not collected: …` | Check `k8s.kubeconfig` in the config; the rest of the report still works |
| Services show as `unknown` or with odd names | Change `service_label` to a label from Step 9 (`container` or `service_name`) |
| `pip install -e .` fails | Make sure you are in the folder that contains `pyproject.toml`; upgrade pip with `python -m pip install --upgrade pip` |

---

## 13. Project structure

```
roottrace/
├── roottrace/                 Python package
│   ├── cli.py                 Commands: scan / trace / serve (+ HTTP API)
│   ├── engine.py              Orchestration: query → parse → mine → analyse → report
│   ├── loki.py                Loki HTTP client (pagination, auth)
│   ├── parser.py              Log parsing (Spring Boot, MOSIP logback, JSON), masking, stack-trace stitching
│   ├── miner.py               Drain3 clustering, baseline diff, scoring
│   ├── deps.py                Dependency analysis: root causes, impact trees, at-risk
│   ├── dependencies.yaml      Starter MOSIP dependency map  ← review & extend
│   ├── catalog.py             Known-issue matching
│   ├── known_issues.yaml      Starter known-issue catalog   ← review & extend
│   ├── k8s.py                 Kubernetes health collection (read-only)
│   ├── report.py              HTML + JSON report
│   ├── notify.py              Slack digest
│   └── config.py              Config loading + env-var overrides
├── tests/                     Fake Loki + end-to-end and dependency tests
├── java-client/               TestNG listener + client for test rigs
├── deploy/roottrace.yaml      Rancher/Kubernetes manifests (Deployment, CronJob, RBAC)
├── Dockerfile
├── config.example.yaml
└── pyproject.toml
```

## 14. Linux / macOS notes
Same steps, with these command differences:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export KUBECONFIG=$PWD/mycluster.yaml
kubectl get svc -A | grep -i -E "loki|grafana"
curl -s http://localhost:3100/loki/api/v1/labels
python tests/test_e2e.py && python tests/test_deps.py
roottrace scan --since 30m -c config.yaml && open roottrace-out/roottrace-report.html   # xdg-open on Linux
```

## 15. Roadmap
- Show **verified / unverified badges** on catalog fixes, plus a reviewer and owner for each entry
- **History and trends**: timestamped reports, and new patterns per day and per release
- **Rollout-aware baseline**: compare with the state before the last deployment
- A **Grafana link** for each issue (a pre-filled LogQL Explore URL)
- Optional **AI summary** (off by default, masked patterns only, clearly labelled)
