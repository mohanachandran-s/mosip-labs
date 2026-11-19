"""Slack digest via incoming webhook."""
from __future__ import annotations

import requests


def slack(webhook: str, r: dict, report_url: str | None = None):
    lines = [f"*RootTrace — {r['environment']}*  ({r['window_from']} → {r['window_to']})", r["verdict"]]
    for k in r["known_summary"][:5]:
        lines.append(f"• `{k['id']}` *{k['title']}* — {', '.join(k['where'][:4])}\n    ↳ {k['fix']}")
    top = [i for i in r["issues"] if i["is_new"]][:5] or r["issues"][:5]
    if top:
        lines.append("*Top patterns:*")
        for i in top:
            tag = " 🆕" if i["is_new"] else ""
            lines.append(f"• `{i['service']}` ×{i['count']}{tag}: {i['template'][:140]}")
    if report_url:
        lines.append(f"<{report_url}|Full report>")
    requests.post(webhook, json={"text": "\n".join(lines)}, timeout=15).raise_for_status()
