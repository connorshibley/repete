"""What the news brain remembers, and whether remembering helped (W7).

The gap this closes
-------------------
The bot has read news hourly since 2026-07-17 and forgotten all of it. Each
refresh OVERWRITES `memory/market_context.json`, `src/learn.py` contains zero
references to news, and `memory.market_context_block()` shows the judge only
TODAY's flag. So the judge could never tell a symbol in the headlines for the
fourth day running from one mentioned once, and nothing anywhere recorded
whether attending to news made a single trade better or worse.

Two halves, and the second is the one that matters
--------------------------------------------------
**HISTORY** — every symbol the distiller flagged or nominated is appended here
with its date, so recurrence becomes visible.

**OUTCOMES** — when a trade closes on a symbol that had news at entry, the
realised P&L is written back. That is what turns a notepad into evidence, and
it is the only reason a future §46 can score this at all: it produces the two
populations a gate would compare (entered-with-news vs entered-without).

Why this cannot make the bot trade more
---------------------------------------
The only consumer is `memory.context_for_llm()`, which feeds the JUDGE. Under
invariant #2 the judge may veto or shrink and may never enlarge. So everything
here can stop a trade and cannot create one, which is why it ships without a
gate in front of it. Nominations are NOT influenced — that path would create
trades, and it was deliberately left alone.

Fail-soft, like the rest of news
--------------------------------
A corrupt store, an unreadable file, a bad record: all degrade to "no news
memory" and none of them may raise into a trading path. News has never been
allowed to block a cycle and this does not change that.

What it does NOT claim
----------------------
Nothing here has been gated. EDGE is 0 for 12 and this is not attempt 13 — it
is instrumentation that makes attempt 13 possible. It also creates sim/live
divergence #14 on purpose: the live judge sees this block and the backtest's
`judge_model` cannot, because there is no historical news archive to replay.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("news_memory")

DEFAULT_PATH = "memory/news_memory.jsonl"

_STATS: dict = {}


def reset_stats() -> None:
    """Run-scoped counters. Same shape as judge_model._STATS (W2-1) and
    market_context._STATS (W6-A1) so every subsystem reports the same way."""
    _STATS.clear()
    _STATS.update({"recorded": 0, "symbols": 0, "nominated": 0,
                   "outcomes": 0, "read_error": None})


def stats() -> dict:
    return json.loads(json.dumps(_STATS))


reset_stats()


def _cfg(cfg: dict) -> dict:
    return ((cfg or {}).get("news", {}) or {}).get("memory", {}) or {}


def enabled(cfg: dict) -> bool:
    return bool(_cfg(cfg).get("enabled", False))


def path(cfg: dict) -> str:
    return _cfg(cfg).get("path", DEFAULT_PATH)


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse_ts(raw) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def record_context(ctx: dict, cfg: dict, ledger=None,
                   now: datetime | None = None) -> int:
    """Append one record per symbol the distiller had something to say about.

    Returns how many were written. Never raises: called from the news refresh,
    which is fail-soft by contract.
    """
    if not enabled(cfg) or not ctx:
        _log_event(ledger, 0, 0)
        return 0
    stamp = _now(now)
    flags = ctx.get("symbol_flags") or {}
    noms = {n.get("symbol"): n.get("reason", "")
            for n in (ctx.get("nominations") or []) if isinstance(n, dict)}

    rows = []
    for sym in sorted(set(flags) | set(noms)):
        if not sym:
            continue
        rows.append({
            "ts": stamp.isoformat(),
            "date": stamp.date().isoformat(),
            "symbol": sym,
            "text": flags.get(sym) or noms.get(sym, ""),
            "nominated": sym in noms,
        })

    written = 0
    try:
        p = path(cfg)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
                written += 1
    except OSError as e:
        log.warning("news memory write failed: %s", type(e).__name__)

    _STATS["recorded"] += written
    _STATS["symbols"] = len(rows)
    _STATS["nominated"] = sum(1 for r in rows if r["nominated"])
    # The event is written even when nothing was remembered. A quiet news day
    # and a broken recorder must not look identical in the record (W6-A1).
    _log_event(ledger, written, _STATS["nominated"])
    return written


def _log_event(ledger, written: int, nominated: int) -> None:
    if ledger is None:
        return
    try:
        ledger.log_event("news_memory",
                         f"remembered {written} symbol mention(s), "
                         f"{nominated} nominated")
    except Exception as e:  # noqa: BLE001 — bookkeeping never breaks the cycle
        log.debug("news_memory ledger write skipped: %s", type(e).__name__)


def _read_all(cfg: dict) -> list[dict]:
    """Every record, or [] when the store is missing or unreadable.

    A single corrupt line is skipped rather than discarding the file: this is
    an append-only log written by a live process, so a torn final write is a
    normal event, not a reason to forget everything.
    """
    p = path(cfg)
    out = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("symbol"):
                    out.append(r)
    except FileNotFoundError:
        return []
    except OSError as e:
        _STATS["read_error"] = type(e).__name__
        log.warning("news memory unreadable: %s", type(e).__name__)
        return []
    return out


def history(symbol: str, cfg: dict, now: datetime | None = None) -> list[dict]:
    """This symbol's records inside the retention window, newest first.

    `now` is injectable so tests never depend on the wall clock — the trap that
    made the first watchdog fixtures pass or fail by time zone.
    """
    if not symbol or not enabled(cfg):
        return []
    days = int(_cfg(cfg).get("retention_days", 30))
    cutoff = _now(now) - timedelta(days=days)
    rows = [r for r in _read_all(cfg)
            if r.get("symbol") == symbol
            and (_parse_ts(r.get("ts")) or cutoff) >= cutoff]
    rows.sort(key=lambda r: str(r.get("ts")), reverse=True)
    return rows


def record_outcome(symbol: str, trade_id, pnl_pct: float, cfg: dict,
                   now: datetime | None = None) -> bool:
    """Write a closed trade's result back against the news that preceded it."""
    if not enabled(cfg) or not symbol:
        return False
    row = {
        "ts": _now(now).isoformat(),
        "date": _now(now).date().isoformat(),
        "symbol": symbol,
        "kind": "outcome",
        "trade_id": trade_id,
        "pnl_pct": round(float(pnl_pct), 4),
    }
    try:
        p = path(cfg)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(row) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.warning("news outcome write failed: %s", type(e).__name__)
        return False
    _STATS["outcomes"] += 1
    return True


def outcomes(symbol: str, cfg: dict, now: datetime | None = None) -> list[dict]:
    return [r for r in history(symbol, cfg, now) if r.get("kind") == "outcome"]


def format_block(symbol: str, cfg: dict, now: datetime | None = None,
                 max_chars: int | None = None) -> str:
    """The judge-facing block. Empty string when there is nothing to say.

    Deliberately returns NOTHING rather than an empty header: a heading with no
    rows under it reads to the judge as "we looked and found nothing", which is
    a different claim from "we have no history", and only one of them is true.
    """
    if not enabled(cfg):
        return ""
    rows = history(symbol, cfg, now)
    mentions = [r for r in rows if r.get("kind") != "outcome"]
    outs = [r for r in rows if r.get("kind") == "outcome"]
    # This single check covers the empty case too: min_mentions_to_show is >= 1,
    # so zero mentions always fails it. A second `if not mentions` guard used to
    # sit below here and was UNREACHABLE — found because a negative control
    # mutated it and the test stayed green, which is the only way an unreachable
    # branch ever announces itself.
    if len(mentions) < max(1, int(_cfg(cfg).get("min_mentions_to_show", 1))):
        return ""

    days = int(_cfg(cfg).get("retention_days", 30))
    head = (f"NEWS MEMORY — {symbol} ({len(mentions)} mention(s) in the last "
            f"{days} days, {len(outs)} prior news-linked closed trade(s)):")
    lines = [head]
    for r in mentions[:int(_cfg(cfg).get("max_rows", 6))]:
        tag = "  [nominated]" if r.get("nominated") else ""
        lines.append(f"  {r.get('date', '?')}  {str(r.get('text', ''))[:150]}{tag}")
    if outs:
        avg = sum(float(o.get("pnl_pct", 0)) for o in outs) / len(outs)
        lines.append(f"  prior news-linked trades: {len(outs)} closed, "
                     f"{avg:+.2f}% average")
    cap = max_chars if max_chars is not None else int(
        _cfg(cfg).get("max_context_chars", 600))
    return "\n".join(lines)[:cap]
