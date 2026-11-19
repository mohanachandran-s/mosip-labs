"""Self-contained HTML + JSON report, failure-first."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment


def fmt_ts(ns: int) -> str:
    if not ns:
        return "-"
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RootTrace · {{ r.environment }}</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mut:#6b6b66;--line:#e4e3de;--crit:#c0392b;--high:#d9822b;--med:#b8a10d;--ok:#2e8b57;--new:#6c4bd8;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--mut:#9a9992;--line:#33332f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0}h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:34px 0 10px}
.sub{color:var(--mut);margin-top:4px}
.verdict{margin:18px 0;padding:14px 16px;border-radius:10px;background:var(--card);border-left:5px solid var(--ok);font-weight:600}
.verdict.bad{border-color:var(--crit)}.verdict.warn{border-color:var(--high)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.tile b{display:block;font-size:26px}.tile span{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--mut);font-weight:600;background:var(--bg)}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code,.mono{font-family:var(--mono);font-size:12.5px}
.tpl{font-family:var(--mono);font-size:12.5px;word-break:break-word}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;font-weight:600;color:#fff;margin-right:4px}
.FATAL,.critical{background:var(--crit)}.ERROR,.high{background:var(--high)}.WARN,.medium{background:var(--med)}.low{background:var(--mut)}.new{background:var(--new)}
details summary{cursor:pointer;color:var(--mut);font-size:12px;margin-top:6px}
pre{white-space:pre-wrap;word-break:break-all;background:var(--bg);padding:10px;border-radius:6px;max-height:320px;overflow:auto;font-size:12px}
.fix{margin-top:6px;padding:8px 10px;border-radius:6px;background:var(--bg);font-size:13px}
.muted{color:var(--mut)}input#q{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);margin-bottom:10px}
.chain{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.chain h3{margin:0 0 6px;font-size:16px}.down{background:var(--crit)}.degraded{background:var(--high)}.suspected{background:var(--new)}
.chain ul.ev{margin:6px 0 0 18px;padding:0}.chain ul.ev li{margin:2px 0}
.tree,.tree ul{list-style:none;margin:0;padding-left:18px;border-left:2px solid var(--line)}
.tree li{margin:6px 0;padding-left:8px}.tree .sym{color:var(--mut);font-size:12.5px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}@media(max-width:800px){.cols{grid-template-columns:1fr}}
.box{background:var(--bg);border-radius:8px;padding:10px 12px}.box b{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
.box code{display:block;margin:4px 0;white-space:pre-wrap}.warnnote{color:var(--high);font-size:12px}
.empty{padding:14px;color:var(--mut);background:var(--card);border:1px dashed var(--line);border-radius:10px}
</style></head><body><div class="wrap">
<h1>RootTrace — {{ r.environment }}</h1>
<div class="sub">Window {{ r.window_from }} → {{ r.window_to }} · baseline offset {{ r.baseline_offset }} · namespaces: {{ r.namespaces or "all" }} · generated {{ r.generated }}</div>

<div class="verdict {{ r.verdict_class }}">{{ r.verdict }}</div>

{% macro branch(t) %}<li><span class="pill {{ t.status }}">{{ t.status }}</span><b>{{ t.component }}</b>
 <span class="muted">← depends on it ({{ "seen in logs" if t.via == "logs" else "dependency map" }})</span>
 {% if t.before_root %}<div class="warnnote">⚠ started failing before the root — may be a separate problem</div>{% endif %}
 {% if t.via_evidence %}<div class="sym mono">{{ t.via_evidence }}</div>{% endif %}
 {% for s in t.symptoms %}<div class="sym">• {{ s }}</div>{% endfor %}
 {% if t.children %}<ul>{% for c in t.children %}{{ branch(c) }}{% endfor %}</ul>{% endif %}</li>{% endmacro %}

{% if r.chains %}<h2>Failure chains — root cause → impact</h2>
{% for ch in r.chains %}<div class="chain">
<h3><span class="pill {{ ch.status }}">{{ ch.status }}</span>{{ ch.root }} <span class="muted" style="font-weight:400;font-size:13px">({{ ch.kind }} · {{ ch.namespaces|join(", ") }}{% if ch.first != "-" %} · first seen {{ ch.first }}{% endif %})</span>
{% if ch.impacted_count %}<span class="pill high" style="float:right">{{ ch.impacted_count }} component(s) impacted</span>{% endif %}</h3>
<ul class="ev">{% for e in ch.evidence %}<li>{{ e }}</li>{% endfor %}</ul>
{% if ch.impacted %}<div style="margin-top:10px"><b class="muted" style="font-size:12px">IMPACT TREE</b><ul class="tree">{% for t in ch.impacted %}{{ branch(t) }}{% endfor %}</ul></div>{% endif %}
<div class="cols">
 <div class="box"><b>Troubleshoot</b>{% for k in ch.known %}<div class="fix"><span class="pill {{ k.severity or 'high' }}">{{ k.id }}</span>{{ k.cause }}<br>→ {{ k.fix }}</div>{% endfor %}
 {% for c in ch.checks %}<code>{{ c }}</code>{% endfor %}</div>
 <div class="box"><b>At risk (depends on {{ ch.root }}, healthy for now)</b><div class="mono" style="margin-top:6px">{{ ch.at_risk|join(", ") or "—" }}</div></div>
</div></div>{% endfor %}{% endif %}

<div class="tiles">
 <div class="tile"><b>{{ r.stats.issues }}</b><span>distinct error patterns</span></div>
 <div class="tile"><b>{{ r.stats.new }}</b><span>new vs baseline</span></div>
 <div class="tile"><b>{{ r.stats.events }}</b><span>error events</span></div>
 <div class="tile"><b>{{ r.stats.services }}</b><span>services with errors</span></div>
 <div class="tile"><b>{{ r.stats.k8s }}</b><span>pod / cluster problems</span></div>
 <div class="tile"><b>{{ r.silent|length }}</b><span>services gone silent</span></div>
</div>

{% if r.known_summary %}<h2>Probable root causes (known-issue catalog)</h2>
<table><tr><th>Issue</th><th>Where</th><th>Probable cause → fix</th></tr>
{% for k in r.known_summary %}<tr><td><span class="pill {{ k.severity }}">{{ k.severity }}</span><b>{{ k.title }}</b><div class="muted mono">{{ k.id }}{% if k.owner %} · owner: {{ k.owner }}{% endif %}</div></td>
<td class="mono">{{ k.where|join(", ") }}</td><td>{{ k.cause }}<div class="fix">→ {{ k.fix }}</div></td></tr>{% endfor %}</table>{% endif %}

{% if r.cross %}<h2>Same error across several services</h2>
<div class="muted" style="margin-bottom:8px">One root cause usually sits upstream of all of these — fix the shared dependency, not each service.</div>
<table><tr><th>Pattern</th><th>Services</th><th class="num">Total</th></tr>
{% for g in r.cross %}<tr><td>{% if g.new %}<span class="pill new">NEW</span>{% endif %}{% for k in g.known %}<span class="pill high">{{ k }}</span>{% endfor %}<div class="tpl">{{ g.template }}</div></td>
<td class="mono">{{ g.services|join(", ") }}</td><td class="num">{{ g.count }}</td></tr>{% endfor %}</table>{% endif %}

<h2>Top issues</h2>
<input id="q" placeholder="Filter by service, exception, error code, text…" oninput="f(this.value)">
{% if r.issues %}<table id="issues"><tr><th class="num">Score</th><th>Service</th><th>Pattern</th><th class="num">Count<br><span class="muted">vs base</span></th></tr>
{% for i in r.issues %}<tr><td class="num">{{ i.score }}</td>
<td><b>{{ i.service }}</b><div class="muted mono">{{ i.namespace }}</div><div class="muted">{{ i.pods|length }} pod(s)</div></td>
<td><span class="pill {{ i.level }}">{{ i.level }}</span>{% if i.is_new %}<span class="pill new">NEW</span>{% endif %}{% for k in i.known %}<span class="pill {{ k.severity }}">{{ k.id }}</span>{% endfor %}
<div class="tpl">{{ i.template }}</div>
{% if i.root_causes %}<div class="muted">root cause: <code>{{ i.root_causes|join(", ") }}</code></div>{% elif i.exceptions %}<div class="muted">exception: <code>{{ i.exceptions|join(", ") }}</code></div>{% endif %}
{% if i.error_codes %}<div class="muted">codes: <code>{{ i.error_codes|join(", ") }}</code></div>{% endif %}
<details><summary>samples · first {{ i.first }} · last {{ i.last }}{% if i.trace_ids %} · trace ids{% endif %}</summary>
{% for k in i.known %}<div class="fix"><b>{{ k.title }}:</b> {{ k.cause }} → {{ k.fix }}</div>{% endfor %}
{% if i.trace_ids %}<div class="mono muted">trace/txn: {{ i.trace_ids|join("  ") }}</div>{% endif %}
{% for s in i.samples %}<pre>{{ s.pod }}  {{ s.ts }}
{{ s.text }}</pre>{% endfor %}</details></td>
<td class="num">{{ i.count }}<br><span class="muted">{{ i.baseline_count }}</span></td></tr>{% endfor %}</table>
{% else %}<div class="empty">No error patterns in this window. 🎉</div>{% endif %}

<h2>Kubernetes</h2>
{% if r.k8s_error %}<div class="empty">Kubernetes data not collected: {{ r.k8s_error }}</div>
{% elif r.k8s %}<table><tr><th>Kind</th><th>Pod / object</th><th>Reason</th><th>Detail</th><th class="num">Restarts / count</th></tr>
{% for f in r.k8s %}<tr><td>{{ f.kind }}</td><td class="mono">{{ f.namespace }}/{{ f.pod }}<div class="muted">{{ f.container }}</div></td>
<td>{% for k in f.known or [] %}<span class="pill critical">{{ k.id }}</span>{% endfor %}<b>{{ f.reason }}</b></td>
<td>{{ f.message }}{% for k in f.known or [] %}<div class="fix">→ {{ k.fix }}</div>{% endfor %}</td><td class="num">{{ f.restarts }}</td></tr>{% endfor %}</table>
{% else %}<div class="empty">No pod restarts, bad states or warning events in the window.</div>{% endif %}

{% if r.rollouts %}<h2>Rollouts in window</h2><table><tr><th>When</th><th>Workload</th><th>Images</th></tr>
{% for d in r.rollouts %}<tr><td class="mono">{{ d.ts }}</td><td class="mono">{{ d.namespace }}/{{ d.name }}</td><td class="mono">{{ d.images }}</td></tr>{% endfor %}</table>{% endif %}

{% if r.silent %}<h2>Services gone silent</h2><div class="muted" style="margin-bottom:8px">Logged in the baseline window but no log lines at all now — possibly down or scaled to zero.</div>
<table><tr><th>Service</th><th class="num">Baseline lines</th></tr>{% for s in r.silent %}<tr><td class="mono">{{ s.namespace }}/{{ s.service }}</td><td class="num">{{ s.baseline }}</td></tr>{% endfor %}</table>{% endif %}

{% if r.resolved %}<h2>Gone since baseline</h2><table><tr><th>Service</th><th>Pattern</th><th class="num">Baseline</th></tr>
{% for i in r.resolved %}<tr><td class="mono">{{ i.service }}</td><td class="tpl">{{ i.template }}</td><td class="num">{{ i.baseline_count }}</td></tr>{% endfor %}</table>{% endif %}

<p class="muted" style="margin-top:40px">Payloads, tokens, e-mails and long numeric IDs are masked. Scores weigh level × volume, new-vs-baseline, spread across pods and known-issue severity.</p>
</div>
<script>function f(q){q=q.toLowerCase();document.querySelectorAll('#issues tr').forEach((r,i)=>{if(i)r.style.display=r.innerText.toLowerCase().includes(q)?'':'none'})}</script>
</body></html>"""


def build(result: dict) -> dict:
    """Shape the scan result into a template-friendly dict (also the JSON output)."""
    issues = result["issues"]
    known: dict[str, dict] = {}
    for i in issues:
        for k in i["known"]:
            e = known.setdefault(k["id"], {**k, "where": set()})
            e["where"].add(i["service"])
    for f in result["k8s"]:
        for k in f.get("known", []):
            e = known.setdefault(k["id"], {**k, "severity": "critical", "where": set()})
            e["where"].add(f["pod"])
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    known_summary = sorted(({**v, "where": sorted(v["where"])[:8]} for v in known.values()),
                           key=lambda k: sev.get(k.get("severity"), 9))
    crit = sum(1 for k in known_summary if k.get("severity") == "critical")
    new = sum(1 for i in issues if i["is_new"])
    bad_pods = len({f["pod"] for f in result["k8s"] if f["kind"] in ("pod-state", "restart")})
    chains = result.get("deps", {}).get("chains", [])
    roots_txt = (f" · {len(chains)} root cause(s): " + ", ".join(c["root"] for c in chains[:4])) if chains else ""
    if crit or bad_pods or result["silent"]:
        parts = []
        if crit: parts.append(f"{crit} critical known issue(s)")
        if bad_pods: parts.append(f"{bad_pods} pod(s) restarting/failing")
        if result["silent"]: parts.append(f"{len(result['silent'])} service(s) silent")
        if new: parts.append(f"{new} new error pattern(s)")
        verdict, vcls = "🔴 Environment unhealthy — " + ", ".join(parts) + roots_txt, "bad"
    elif new or issues:
        verdict, vcls = f"🟠 Degraded — {new} new error pattern(s), {len(issues)} total" + roots_txt, "warn"
    else:
        verdict, vcls = "🟢 Healthy — no errors found in the window", ""
    groups: dict[str, dict] = {}
    for i in issues:
        g = groups.setdefault(i["template"].strip().lower(), {"template": i["template"], "services": [],
                                                              "count": 0, "new": False, "known": []})
        g["services"].append(i["service"]); g["count"] += i["count"]; g["new"] |= i["is_new"]
        g["known"] += [k["id"] for k in i["known"] if k["id"] not in g["known"]]
    cross = sorted((g for g in groups.values() if len(set(g["services"])) >= 2), key=lambda g: -len(g["services"]))
    for i in issues:
        i["first"], i["last"] = fmt_ts(i["first_ns"]), fmt_ts(i["last_ns"])
        for s in i["samples"]:
            s["ts"] = fmt_ts(s["ts_ns"])
        i["exceptions"] = list(i["exceptions"])
        i["root_causes"] = list(i["root_causes"])
        i["error_codes"] = list(i["error_codes"])
    for ch in chains:
        ch["first"] = fmt_ts(ch["first_ns"])
    for d in result["rollouts"]:
        d["ts"] = fmt_ts(d["ts_ns"])
    return {
        **result,
        "verdict": verdict, "verdict_class": vcls, "known_summary": known_summary, "cross": cross,
        "chains": chains,
        "stats": {"issues": len(issues), "new": new, "events": sum(i["count"] for i in issues),
                  "services": len({(i["namespace"], i["service"]) for i in issues}),
                  "k8s": len(result["k8s"])},
        "unhealthy": vcls == "bad",
    }


def write(r: dict, out_dir: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html = Environment(autoescape=True).from_string(TEMPLATE).render(r=r)
    hp, jp = out / "roottrace-report.html", out / "roottrace-report.json"
    hp.write_text(html, encoding="utf-8")
    jp.write_text(json.dumps(r, indent=2, default=list), encoding="utf-8")
    return hp, jp
