"""Digest building + delivery over a REAL channel (SMTP e-mail and/or Slack incoming webhook).
No channel configured -> the digest is still rendered and saved as HTML and the status says 'dry run'."""
import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from html import escape
from pathlib import Path

HEX = {"Red": "#c62828", "Yellow": "#f2b01e", "Green": "#2e7d32", "Blue": "#1565c0", "Grey": "#78909c"}
EMOJI = {"Red": ":red_circle:", "Yellow": ":large_yellow_circle:", "Green": ":large_green_circle:", "Blue": ":large_blue_circle:", "Grey": ":white_circle:"}
HEADLINE = {"Red": "CRITICAL - margin below threshold", "Yellow": "ATTENTION - margin outside target band", "Green": "HEALTHY - within target",
            "Blue": "SUSPICIOUS - margin unusually high, check for under-reported costs", "Grey": "DATA ISSUE - margin not computed"}


def build_digest(day: dict, anomalies: list[dict], insight: str, manager: dict | None) -> dict:
    col = day["colour"]
    greet = (manager or {}).get("manager", "FC Manager")
    subject = f"[{col.upper()}] {day['fc']} daily P&L - {day['day']}" + (f" - CM2 {day['cm2_pct']:.1f}%" if day["cm2_pct"] is not None else "")
    flagged = [a for a in anomalies if a["severity"] != "watch"]
    ranked = sorted(flagged, key=lambda a: -a["impact_inr"])[:6]
    headline = HEADLINE[col]
    if col == "Green" and flagged:  # CM2 is fine but individual lines are out of range - say so
        headline = f"HEALTHY - CM2 within target, but {len(flagged)} line item(s) outside range"
    text = [f"Hi {greet},", "", f"{day['fc']} | {day['day']} | {col.upper()} - {headline}"]
    if day["cm2_pct"] is not None:
        text += [f"CM1 {day['cm1_pct']:.1f}%   CM2 {day['cm2_pct']:.1f}%   Revenue Rs {day['revenue']:,.0f}"]
    text += ["", insight, ""]
    if ranked:
        text.append("Flagged items (largest impact first):")
        text += [f"- {a['line_item']}: {a['pct'] if a['pct'] is not None else '-'}% vs {a['target_range'] or '-'} [{a['status']}]" for a in ranked]
    body_text = "\n".join(text)
    rows = "".join(f"<tr><td>{escape(a['line_item'])}</td><td style='text-align:right'>{'' if a['pct'] is None else f'{a['pct']:.1f}%'}</td>"
                   f"<td>{escape(a['target_range'] or '')}</td><td>{escape(a['status'])}</td></tr>" for a in ranked)
    kpis = ""
    if day["cm2_pct"] is not None:
        kpis = (f"<p style='margin:10px 0'><b>CM1</b> {day['cm1_pct']:.1f}% &nbsp; <b>CM2</b> {day['cm2_pct']:.1f}% &nbsp; "
                f"<b>Revenue</b> Rs {day['revenue']:,.0f}</p>")
    html = f"""<div style="font-family:Arial,sans-serif;max-width:620px;border:1px solid #dde3e6;border-radius:6px;overflow:hidden">
<div style="background:{HEX[col]};color:#fff;padding:14px 18px"><div style="font-size:12px;opacity:.9">{escape(day['fc'])} &middot; {escape(str(day['day']))}</div>
<div style="font-size:18px;font-weight:bold">{col.upper()} &mdash; {escape(headline)}</div></div>
<div style="padding:14px 18px;font-size:14px;color:#1c2b30">Hi {escape(greet)},{kpis}<p style="line-height:1.5">{escape(insight)}</p>
{('<table cellpadding=6 style="border-collapse:collapse;width:100%;font-size:13px"><tr style="background:#eef2f3"><th align=left>Line item</th><th align=right>% of revenue</th><th align=left>Target</th><th align=left>Status</th></tr>' + rows + '</table>') if rows else ''}
<p style="color:#6b7b80;font-size:12px;margin-top:14px">Generated automatically by Prozo Operations Intelligence. Figures are computed deterministically from the uploaded daily data.</p></div></div>"""
    slack = f"{EMOJI[col]} *{day['fc']} - {day['day']}* - {headline}\n{insight}"
    return {"subject": subject, "text": body_text, "html": html, "slack": slack}


def channels_configured(manager: dict | None) -> list[str]:
    ch = []
    if os.getenv("SMTP_HOST") and (manager or {}).get("email"):
        ch.append("email")
    if (manager or {}).get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL"):
        ch.append("slack")
    return ch


def _send_email(to: str, subject: str, text: str, html: str):
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "alerts@localhost"))
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
        if os.getenv("SMTP_TLS", "1") != "0":
            s.starttls()
    with s:
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)


def _send_slack(url: str, text: str):
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"Slack returned HTTP {r.status}")


def deliver(digest: dict, manager: dict | None, send: bool) -> list[dict]:
    """Returns one result per attempted channel: {channel, recipient, status, detail}."""
    chans = channels_configured(manager) if send else []
    if not chans:
        why = "sending disabled for this run" if not send else "no SMTP_HOST / Slack webhook configured (see README)"
        return [{"channel": "none", "recipient": (manager or {}).get("email", ""), "status": "dry_run", "detail": f"Digest saved, not sent: {why}"}]
    out = []
    for c in chans:
        try:
            if c == "email":
                _send_email(manager["email"], digest["subject"], digest["text"], digest["html"])
                out.append({"channel": "email", "recipient": manager["email"], "status": "sent", "detail": "Delivered via SMTP"})
            else:
                _send_slack(manager.get("slack_webhook") or os.environ["SLACK_WEBHOOK_URL"], digest["slack"])
                out.append({"channel": "slack", "recipient": "webhook", "status": "sent", "detail": "Posted to Slack webhook"})
        except Exception as e:
            out.append({"channel": c, "recipient": (manager or {}).get("email", ""), "status": "failed", "detail": f"{type(e).__name__}: {e}"[:300]})
    return out


def save_html(path: Path, digest: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><meta charset='utf-8'><title>{escape(digest['subject'])}</title>{digest['html']}", encoding="utf-8")