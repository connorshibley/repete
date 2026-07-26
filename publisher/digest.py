"""Email digests: pure HTML builders + a dry-run-first sender.

send() appends to publisher_data/outbox.jsonl unless RESEND_API_KEY is set
AND publisher.email.dry_run is false — the x_poster convention: flipping a
key in the environment changes behavior, never a code edit. Every email
carries the disclaimer and an unsubscribe link.
"""
import json
import os
import urllib.parse
from datetime import datetime, timezone

from publisher.gates import DISCLAIMER


def unsubscribe_url(base_url: str, raw_token: str) -> str:
    """The opt-out link that goes in an email body.

    One place builds this so the route and the mail can never disagree about
    the path or the parameter name. It points at GET /unsubscribe, which
    renders a confirmation page and does NOT unsubscribe anyone — see the
    route for why a mutating GET would be a defect.
    """
    return (f"{base_url.rstrip('/')}/unsubscribe"
            f"?token={urllib.parse.quote(raw_token, safe='')}")


def daily_subject(now: datetime, prefix: str = "Repete daily",
                  paper: bool = True) -> str:
    """`Repete daily — 2026-07-25 [PAPER]`.

    The paper-trading disclosure rides in the SUBJECT, not only in the footer:
    a recipient scanning an inbox sees what this is before opening it, and
    subject lines survive forwarding and quoting when footers get trimmed.
    """
    return (f"{prefix} — {now.strftime('%Y-%m-%d')}"
            + (" [PAPER]" if paper else ""))


def daily_digest_html(feed_obj: dict, base_url: str,
                      unsubscribe_url: str) -> str:
    eq = feed_obj["equity"].get("equity")
    # `if eq` would render an equity of exactly 0.00 as "n/a" — a blown-up
    # account reported as a missing datapoint. Only None means "unknown".
    eq_txt = f"${eq:,.2f}" if eq is not None else "n/a"
    lines = [f"<h2>Repete — daily recap [{feed_obj['tier'].upper()}]</h2>",
             f"<p>Equity: {eq_txt} · "
             f"open positions: {feed_obj['open_positions']} · "
             f"closed trades: {feed_obj['closed_trades']}</p>"]
    for d in feed_obj["decisions"][-8:]:
        j = d.get("judge") or {}
        lines.append(
            f"<p><b>{d['action'].upper()} {d['symbol']}</b>"
            f" ({d.get('strategy') or ''}) — "
            f"{'executed' if d['executed'] else d.get('detail') or 'skipped'}"
            + (f"<br><i>judge: {j.get('reasoning')}</i>"
               if j.get("reasoning") else "") + "</p>")
    if feed_obj.get("delayed_by_days"):
        lines.append(f"<p><i>Free tier — decisions delayed "
                     f"{feed_obj['delayed_by_days']} day(s). Paid removes "
                     f"the delay and adds the judge's reasoning.</i></p>")
    lines.append(f'<hr><p style="font-size:11px;color:#777">{DISCLAIMER}</p>')
    lines.append(f'<p style="font-size:11px"><a href="{unsubscribe_url}">'
                 f'unsubscribe</a> · <a href="{base_url}">{base_url}</a></p>')
    return "\n".join(lines)


def send(cfg: dict, to_email: str, subject: str, html: str) -> dict:
    """Queue (dry-run, default) or actually send. Returns a delivery record;
    ALSO logged to the outbox either way — delivery must be auditable."""
    pub = cfg["publisher"]
    dry = pub["email"].get("dry_run", True) or \
        not os.environ.get("RESEND_API_KEY")
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "to": to_email,
           "subject": subject, "dry_run": dry, "status": "queued", "ok": True}
    if not dry:
        try:
            import httpx
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization":
                         f"Bearer {os.environ['RESEND_API_KEY']}"},
                json={"from": pub["email"]["from"], "to": [to_email],
                      "subject": subject, "html": html}, timeout=15)
            rec["status"] = ("sent" if r.status_code < 300
                             else f"error:{r.status_code}")
        except Exception as e:  # noqa: BLE001 — a failed email never raises
            rec["status"] = f"error:{e}"
        # `ok` exists so callers branch on a bool instead of string-matching
        # "error:". A broadcast loop over hundreds of recipients is the wrong
        # place for stringly-typed control flow.
        rec["ok"] = rec["status"] in ("queued", "sent")
    outbox = os.path.join(pub["data_dir"], "outbox.jsonl")
    os.makedirs(pub["data_dir"], exist_ok=True)
    with open(outbox, "a") as f:
        f.write(json.dumps({**rec, "html_bytes": len(html)}) + "\n")
    return rec
