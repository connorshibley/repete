"""Morning market awareness — news context + judged watchlist nominations.

Every trading morning (from the 9:35 plan job) this module:
  1. pulls the last ~24h of market news from the Alpaca News API
     (existing keys, read-only) MERGED with WSJ's free public RSS feeds
     (headline + summary only, no login, no scraping, no credentials —
     see fetch_wsj_rss),
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
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import risk
import strategies

log = logging.getLogger("news")

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_TAG_RE = re.compile(r"<[^>]+>")  # strip HTML from RSS descriptions
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


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


def parse_rss(xml_text: str, source_label: str,
              max_age_hours: int = 36, now=None) -> list[dict]:
    """Parse an RSS 2.0 feed body into the same item shape as fetch_headlines
    ({headline, summary, symbols, source, ts}). RSS carries no ticker tags, so
    symbols is always []. Items older than max_age_hours (by <pubDate>) are
    dropped; an unparseable/absent date is KEPT (fail-open). Any XML error =>
    []. Pure + offline — the network lives in fetch_wsj_rss."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("RSS parse failed (%s): %s", source_label, e)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        desc = _TAG_RE.sub("", desc).strip()  # strip embedded HTML
        pub = (item.findtext("pubDate") or "").strip()
        ts_dt = None
        if pub:
            try:
                ts_dt = parsedate_to_datetime(pub)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                ts_dt = None  # unparseable date => keep the item
        if ts_dt is not None and ts_dt < cutoff:
            continue  # stale
        if not title and not desc:
            continue
        out.append({"headline": title,
                    "summary": desc[:300],
                    "symbols": [],
                    "source": source_label,
                    "ts": pub})
    return out


def fetch_wsj_rss(cfg: dict) -> list[dict]:
    """WSJ's public RSS feeds (headline + summary only, no login). Merged into
    the news brain alongside Alpaca. Every feed is fail-soft: a bad/slow/absent
    feed is logged and skipped, never raised. [] when disabled or all fail."""
    wcfg = _ncfg(cfg).get("wsj_rss", {})
    if not wcfg.get("enabled", False):
        return []
    feeds = wcfg.get("feeds", []) or []
    max_per_feed = int(wcfg.get("max_per_feed", 8))
    max_items = int(wcfg.get("max_items", 20))
    max_age = int(wcfg.get("max_age_hours", 36))
    out: list[dict] = []
    for url in feeds:
        try:
            label = "WSJ:" + _feed_label(url)
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 — https feeds only
                body = resp.read().decode("utf-8", "replace")
            items = parse_rss(body, label, max_age)[:max_per_feed]
            out.extend(items)
        except Exception as e:  # noqa: BLE001 — a dead feed is a quiet morning, not a crash
            log.warning("WSJ RSS fetch failed (%s): %s", url, e)
            continue
    return out[:max_items]


def _feed_label(url: str) -> str:
    """Human-readable section from a WSJ feed filename, best-effort. Handles
    both the old .xml URLs and the new extensionless dowjones.io ones."""
    fname = url.rstrip("/").rsplit("/", 1)[-1].replace(".xml", "")
    known = {"RSSMarketsMain": "Markets", "WSJcomUSBusiness": "Business",
             "RSSWSJD": "Tech", "RSSWorldNews": "World", "RSSOpinion": "Opinion"}
    return known.get(fname, fname or "RSS")


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
    # Alpaca first (ticker-tagged, drives symbol_flags), then WSJ public RSS.
    headlines = fetch_headlines(cfg) + fetch_wsj_rss(cfg)
    headlines = headlines[:_ncfg(cfg).get("max_headlines", 50)]
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


if __name__ == "__main__":
    # Hourly refresh entry point (com.trading-agent.newsbrain, 9:25-15:25 ET).
    import yaml
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    with open("config.yaml") as f:
        _cfg = yaml.safe_load(f)
    from broker import Broker
    from ledger import Ledger
    ctx = refresh(_cfg, Broker(_cfg),
                  ledger=Ledger(_cfg["memory"]["ledger_path"]))
    print("refreshed" if ctx else "no context (quiet news or LLM unavailable)")
