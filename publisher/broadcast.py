"""The daily digest broadcast — the thing that joins recipients to content.

Why this is its own module and not part of digest.py: `digest.py` is pure
builders plus a dry-run-first single-recipient sender. It touches no database,
no ledger, and knows nothing about tiers. A broadcast needs `subscribers` +
`content` + `auth` + `digest` at once; it is an orchestration layer sitting
ABOVE both, and burying it inside digest.py would make the two responsibilities
impossible to test apart.

Read-only against agent state (invariant #9): this reads the ledger through
`ReadOnlyLedger` and writes only inside `publisher_data/`.

Three independent switches must all be set before a real email leaves the
building — `publisher.digest.enabled`, `publisher.email.dry_run: false`, and
`RESEND_API_KEY`. See publisher/config.py for why the first one exists
separately from the other two.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from publisher import auth, content, digest

# The scheduler thinks in ET (scripts/scheduler.py), and so does the market.
# The same-day guard and the subject date must agree with it: stamping a
# 17:30 ET digest in UTC dates it tomorrow, which would let the guard fire a
# day early and suppress a real send.
ET = ZoneInfo("America/New_York")


def _runs_path(cfg: dict) -> str:
    return os.path.join(cfg["publisher"]["data_dir"], "digest_runs.jsonl")


def _already_ran_today(cfg: dict, date_et: str) -> bool:
    """True if a run today actually mailed something.

    The scheduler dedupes per scheduled minute; it cannot see a human running
    the CLI twice, and a second run mails the entire list a second time. A run
    that was skipped or reached nobody does not count as having run.
    """
    path = _runs_path(cfg)
    if not os.path.exists(path):
        return False
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:      # a truncated tail must not crash
                continue                       # the send; treat it as absent
            if r.get("date_et") == date_et and (r.get("queued", 0)
                                                + r.get("sent", 0)) > 0:
                return True
    return False


def _record(cfg: dict, rec: dict) -> dict:
    os.makedirs(cfg["publisher"]["data_dir"], exist_ok=True)
    with open(_runs_path(cfg), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def send_daily_digest(cfg: dict, db, ledger, *, now: datetime | None = None,
                      only: str | None = None, force: bool = False) -> dict:
    """Mail one digest per ACTIVE subscriber; return an auditable run record.

    `only` restricts the run to a single address for a smoke test. It is
    checked AGAINST the active list rather than trusted, so a test-send can
    never reach someone who unsubscribed — the hole is closed by construction
    instead of by remembering.

    Never raises for a delivery problem. `digest.send()` swallows transport
    errors by design, so the returned record — not an exception — is the only
    signal that something went wrong.
    """
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    pub = cfg["publisher"]
    dcfg = pub["digest"]
    date_et = now.astimezone(ET).strftime("%Y-%m-%d")

    def out(**kw) -> dict:
        return _record(cfg, {
            "ts": now.isoformat(), "date_et": date_et,
            "dry_run": bool(pub["email"].get("dry_run", True)),
            "recipients": 0, "by_tier": {}, "queued": 0, "sent": 0,
            "failed": 0, "failures": [], "skipped_reason": None,
            "skipped_tiers": [],
            "duration_ms": round((time.monotonic() - t0) * 1000, 1), **kw})

    # 1. The enable gate runs FIRST — before a token is minted or an outbox
    #    line is written. A flag checked halfway down is a flag that has
    #    already had side effects by the time it says no.
    if not dcfg.get("enabled") and not force:
        return out(skipped_reason="publisher.digest.enabled is false")

    # 2. Never mail the same list twice in a day.
    if _already_ran_today(cfg, date_et) and not force:
        return out(skipped_reason=f"already ran today ({date_et})")

    # 3. active_emails() is the ONLY admissible source of recipients. Reading
    #    the subscribers table directly would include people who opted out.
    recipients = db.active_emails()
    if only:
        only = only.strip().lower()
        if only not in recipients:
            return out(skipped_reason=(
                f"--to {only!r} is not an active subscriber; refusing to "
                f"send (it may have unsubscribed or never signed up)"))
        recipients = [only]

    # 4. Reaching nobody is a NAMED outcome, never a silent success. The way
    #    this feature breaks quietly is an empty/wrong subscriber DB: zero
    #    recipients, zero errors, exit 0, and it looks like it worked.
    if not recipients:
        return out(skipped_reason="no active subscribers")

    cap = dcfg.get("max_recipients_per_run") or 0
    if cap and len(recipients) > cap:
        return out(recipients=len(recipients), skipped_reason=(
            f"{len(recipients)} recipients exceeds "
            f"max_recipients_per_run={cap}; refusing rather than truncating"))

    # 5. One feed per TIER, not per recipient. content.feed() walks the whole
    #    record stream several times; at the QA fixture's 18,597 records a
    #    per-recipient build would be tens of millions of traversals for
    #    output that is identical within a tier.
    by_tier: dict[str, list[str]] = {}
    for email in recipients:
        by_tier.setdefault(db.tier(email), []).append(email)
    feeds = {t: content.feed(cfg, ledger, t, now=now) for t in sorted(by_tier)}

    # 6. An email that is a header and a disclaimer is worse than no email.
    skipped_tiers = [t for t, fd in feeds.items()
                     if not fd["decisions"] and fd["equity"].get("equity") is None]

    ttl_min = int(dcfg.get("unsubscribe_token_ttl_days", 90)) * 24 * 60
    subject = digest.daily_subject(
        now.astimezone(ET), dcfg.get("subject_prefix", "Repete daily"),
        paper=str(cfg.get("mode", "paper")).lower() != "live")

    queued = sent = 0
    failures: list[dict] = []
    for tier, emails in sorted(by_tier.items()):
        if tier in skipped_tiers:
            continue
        feed_obj = feeds[tier]
        for email in emails:
            # Each recipient is isolated: a sqlite error on one address must
            # not abort the run and mail only the first half of the alphabet.
            try:
                tok = auth.new_token()
                db.issue_token(email, "unsubscribe", tok, ttl_min)
                html = digest.daily_digest_html(
                    feed_obj, pub["base_url"],
                    digest.unsubscribe_url(pub["base_url"], tok))
                rec = digest.send(cfg, email, subject, html)
            except Exception as e:      # noqa: BLE001 — record and continue
                failures.append({"to": email, "status": f"error:{e}"})
                continue
            if not rec.get("ok"):
                failures.append({"to": email, "status": rec.get("status")})
            elif rec.get("dry_run"):
                queued += 1
            else:
                sent += 1

    return out(recipients=sum(len(v) for t, v in by_tier.items()
                              if t not in skipped_tiers),
               by_tier={t: len(v) for t, v in by_tier.items()},
               queued=queued, sent=sent, failed=len(failures),
               failures=failures, skipped_tiers=skipped_tiers)
