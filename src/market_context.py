"""Morning market awareness — news context + judged watchlist nominations.

Every trading morning (from the 9:35 plan job) this module:
  1. pulls the last ~24h of market news from the Alpaca News API
     (existing keys, read-only),
  2. has the LLM distill it into a structured context: what's driving
     markets, today's scheduled events, per-symbol news flags, and up to
     `news.max_nominations` watchlist NOMINATIONS,
  3. validates nominations DETERMINISTICALLY (real ticker, not already in
     the universe, fresh bars with enough history) and drops the rest,
  4. writes memory/market_context.json + a `market_context` ledger event.

HARD BOUNDARY (CLAUDE.md invariant): the LLM summarizes news and points the
scanner at symbols — it can NEVER generate a trade. A nominated symbol is
bought only if a deterministic strategy fires a real signal on it, the judge
(explicitly told the symbol is outside the backtested universe) doesn't veto,
and every risk rail passes; max `news.max_news_entries_per_cycle` per day.

Every step degrades gracefully: no news / no LLM / bad JSON => no context
today, and the bot trades exactly as it did before this module existed.
"""
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import risk
import strategies

log = logging.getLogger("news")

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _ncfg(cfg: dict) -> dict:
    return cfg.get("news", {})


def fetch_headlines(cfg: dict) -> list[dict]:
    """Last ~24h of market news via Alpaca's News API. [] on any failure."""
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        client = NewsClient(os.environ["ALPACA_API_KEY"],
                            os.environ["ALPACA_SECRET_KEY"])
        req = NewsRequest(start=datetime.now(timezone.utc) - timedelta(hours=24),
                          limit=_ncfg(cfg).get("max_headlines", 50))
        resp = client.get_news(req)
        items = resp.data.get("news", []) if hasattr(resp, "data") else []
        out = []
        for n in items:
            out.append({"headline": getattr(n, "headline", ""),
                        "summary": (getattr(n, "summary", "") or "")[:300],
                        "symbols": list(getattr(n, "symbols", []) or []),
                        "source": getattr(n, "source", ""),
                        "ts": str(getattr(n, "created_at", ""))})
        return out
    except Exception as e:  # noqa: BLE001 — no news is a quiet morning, not a crash
        log.warning("news fetch failed: %s", e)
        return []


def validate_nominations(raw, cfg: dict, broker) -> list[dict]:
    """Deterministic gate between LLM output and the scanner: real-looking
    ticker, not already in the universe, and fresh bars with enough history
    for the enabled strategies. Everything else is dropped and logged."""
    if not isinstance(raw, list):
        return []
    universe = set(cfg["symbols"])
    max_n = _ncfg(cfg).get("max_nominations", 3)
    lookback = strategies.max_lookback_bars(cfg)
    max_age = cfg["risk"].get("max_bar_age_days", 4)
    out, seen = [], set()
    for item in raw:
        if len(out) >= max_n:
            break
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol", "")).upper().strip()
        reason = str(item.get("reason", ""))[:200]
        if not TICKER_RE.match(sym) or sym in universe or sym in seen:
            continue
        seen.add(sym)
        try:
            bars = broker.bars(sym, cfg["strategy"]["timeframe"], lookback)
        except Exception as e:  # noqa: BLE001
            log.info("nomination %s dropped: bars fetch failed (%s)", sym, e)
            continue
        if not bars or not risk.bars_fresh(bars, max_age):
            log.info("nomination %s dropped: no fresh bars", sym)
            continue
        if len(bars) < min(lookback, 60):  # too young/illiquid to evaluate
            log.info("nomination %s dropped: only %d bars", sym, len(bars))
            continue
        out.append({"symbol": sym, "reason": reason})
    return out


def refresh(cfg: dict, broker, ledger=None) -> dict | None:
    """Build and persist today's market context. Returns it, or None."""
    if not _ncfg(cfg).get("enabled", False):
        return None
    headlines = fetch_headlines(cfg)
    if not headlines:
        log.info("no headlines this morning — no market context today")
        return None
    import llm
    distilled = llm.summarize_market_context(headlines, cfg["symbols"], cfg)
    if not distilled:
        return None
    ctx = {
        "date": date.today().isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "summary": str(distilled.get("summary", ""))[:600],
        "events_today": [str(e)[:100]
                         for e in (distilled.get("events_today") or [])][:6],
        "symbol_flags": {str(k).upper(): str(v)[:150]
                         for k, v in (distilled.get("symbol_flags")
                                      or {}).items()},
        "nominations": validate_nominations(
            distilled.get("nominations"), cfg, broker),
    }
    path = _ncfg(cfg).get("context_path", "memory/market_context.json")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(ctx, f)
    except OSError as e:
        log.warning("market context write failed: %s", e)
        return None
    if ledger is not None:
        noms = ", ".join(n["symbol"] for n in ctx["nominations"]) or "none"
        ledger.log_event("market_context",
                         f"{ctx['summary']} | nominations: {noms}")
    log.info("market context: %s | nominations: %s", ctx["summary"],
             [n["symbol"] for n in ctx["nominations"]])
    return ctx


def load(cfg: dict) -> dict | None:
    """Today's context, or None — yesterday's news is ignored on purpose."""
    path = _ncfg(cfg).get("context_path", "memory/market_context.json")
    try:
        with open(path) as f:
            ctx = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if ctx.get("date") != date.today().isoformat():
        return None
    return ctx
