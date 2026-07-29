"""Morning market awareness — news context + judged watchlist nominations.

Every trading morning (from the 9:35 plan job) this module:
  1. pulls the last ~24h of market news from the Alpaca News API
     (existing keys, read-only) MERGED with free public RSS feeds from
     WSJ and CNBC (headline + summary only, no login, no scraping, no
     credentials — see fetch_rss_sources),
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


# ---------------------------------------------------------------------------
# PER-SOURCE ACCOUNTING (W6-A1, 2026-07-29)
#
# Until today this module logged ONLY failures. On a successful run nothing
# recorded how many headlines came from WSJ versus CNBC versus MarketWatch
# versus Alpaca — so "does it read the sources I gave it?" was not answerable
# from the bot's own record, and a source returning zero every single day would
# have looked exactly like a source working fine.
#
# That was not hypothetical. It was hiding a real bug: see `merge_budget()`.
#
# Same shape as `judge_model._STATS` (W2-1) — module-level dict, reset per run,
# read back by the caller — so the fetch signatures and every existing caller
# stay unchanged.
# ---------------------------------------------------------------------------
_STATS: dict = {"alpaca": {}, "rss": {}, "budget": {}}


def reset_stats() -> None:
    """Clear the run-scoped counters. Called at the top of `refresh`."""
    _STATS["alpaca"] = {"items": 0, "error": None}
    _STATS["rss"] = {}
    _STATS["budget"] = {}


def stats() -> dict:
    """A copy of this run's accounting — copy, not the live dict, so a caller
    that stashes it in a ledger event can't be mutated out from under."""
    return json.loads(json.dumps(_STATS))


def _feed_stat(source: str, label: str) -> dict:
    """Get-or-create the per-feed record.

    Created EAGERLY, before the fetch is attempted, and that is the whole point:
    a feed that dies has to appear in the record as a failure rather than be
    absent from it. `empty is not the same as absent`.
    """
    src = _STATS["rss"].setdefault(source, {"kept": 0, "dropped_cap": 0,
                                            "feeds": {}})
    return src["feeds"].setdefault(label, {
        "fetched": 0,        # parsed, fresh, non-empty
        "kept": 0,           # survived max_per_feed
        "dropped_stale": 0,  # older than max_age_hours
        "dropped_empty": 0,  # no title and no description
        "http_error": None,
        "parse_error": None,
    })


def fetch_headlines(cfg: dict) -> list[dict]:
    """Last ~24h of market news via Alpaca's News API. [] on any failure.

    The request limit is `news.alpaca_max_headlines`, NOT `news.max_headlines`.
    One key doing both jobs is precisely what caused the discard bug that
    `merge_budget` documents.
    """
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        client = NewsClient(os.environ["ALPACA_API_KEY"],
                            os.environ["ALPACA_SECRET_KEY"])
        req = NewsRequest(start=datetime.now(timezone.utc) - timedelta(hours=24),
                          limit=alpaca_budget(cfg))
        resp = client.get_news(req)
        items = resp.data.get("news", []) if hasattr(resp, "data") else []
        out = []
        for n in items:
            out.append({"headline": getattr(n, "headline", ""),
                        "summary": (getattr(n, "summary", "") or "")[:300],
                        "symbols": list(getattr(n, "symbols", []) or []),
                        "source": getattr(n, "source", ""),
                        "ts": str(getattr(n, "created_at", ""))})
        _STATS["alpaca"] = {"items": len(out), "error": None}
        return out
    except Exception as e:  # noqa: BLE001 — no news is a quiet morning, not a crash
        log.warning("news fetch failed: %s", e)
        _STATS["alpaca"] = {"items": 0, "error": type(e).__name__}
        return []


def parse_rss(xml_text: str, source_label: str,
              max_age_hours: int = 36, now=None, counts=None) -> list[dict]:
    """Parse an RSS 2.0 feed body into the same item shape as fetch_headlines
    ({headline, summary, symbols, source, ts}). RSS carries no ticker tags, so
    symbols is always []. Items older than max_age_hours (by <pubDate>) are
    dropped; an unparseable/absent date is KEPT (fail-open). Any XML error =>
    []. Pure + offline — the network lives in _fetch_rss_source.

    `counts`, when given, is the per-feed record from `_feed_stat` and gets
    `dropped_stale` / `dropped_empty` / `parse_error` written into it. It stays
    optional so this function is still callable as a pure parser (which is how
    every existing test calls it).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        # A feed that answers 200 with an HTML page lands here. AP does exactly
        # that (1.7 MB of `<!DOCTYPE html>`, measured 2026-07-29) — which is why
        # the error is RECORDED and not merely logged: a status-code-only check
        # would have called that a success and contributed nothing, silently,
        # forever.
        log.warning("RSS parse failed (%s): %s", source_label, e)
        if counts is not None:
            counts["parse_error"] = str(e)[:120]
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
            if counts is not None:
                counts["dropped_stale"] += 1
            continue  # stale
        if not title and not desc:
            if counts is not None:
                counts["dropped_empty"] += 1
            continue
        out.append({"headline": title,
                    "summary": desc[:300],
                    "symbols": [],
                    "source": source_label,
                    "ts": pub})
    if counts is not None:
        counts["fetched"] += len(out)
    return out


def fetch_rss_by_source(cfg: dict) -> dict:
    """{source name -> its items}, so the budget can round-robin across sources
    instead of letting whichever source YAML lists first eat the whole share."""
    sources = _ncfg(cfg).get("rss_sources", {}) or {}
    out: dict = {}
    for name, scfg in sources.items():
        if not isinstance(scfg, dict) or not scfg.get("enabled", False):
            continue
        out[str(name)] = _fetch_rss_source(str(name), scfg)
    return out


def fetch_rss_sources(cfg: dict) -> list[dict]:
    """All configured public RSS sources (WSJ, CNBC, ...) merged into one list,
    same item shape as fetch_headlines. Headline + summary only — no login, no
    scraping, no credentials. Each source is fail-soft: a bad/slow/absent feed
    is logged and skipped, never raised. [] when none are enabled.

    Flat view of `fetch_rss_by_source`, kept because callers and tests use it.
    """
    return [item for items in fetch_rss_by_source(cfg).values()
            for item in items]


def _fetch_rss_source(name: str, scfg: dict) -> list[dict]:
    """One named source: fetch each {Section: url} feed, cap per-feed and per-
    source. `feeds` may be a {section: url} mapping or a plain list of urls."""
    feeds = scfg.get("feeds", {}) or {}
    pairs = list(feeds.items()) if isinstance(feeds, dict) \
        else [(None, u) for u in feeds]
    max_per_feed = int(scfg.get("max_per_feed", 8))
    max_items = int(scfg.get("max_items", 20))
    max_age = int(scfg.get("max_age_hours", 36))
    per_feed: list[list[dict]] = []
    for section, url in pairs:
        label = name + (":" + str(section) if section else "")
        st = _feed_stat(name, label)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 — https feeds only
                body = resp.read().decode("utf-8", "replace")
            kept = parse_rss(body, label, max_age, counts=st)[:max_per_feed]
            st["kept"] += len(kept)
            per_feed.append(kept)
        except Exception as e:  # noqa: BLE001 — a dead feed is a quiet morning, not a crash
            log.warning("RSS fetch failed (%s / %s): %s", name, url, e)
            st["http_error"] = type(e).__name__
            continue
    # INTERLEAVE the sections rather than concatenating them. `max_items` cuts
    # from the tail, and `merge_budget` takes from the head, so a plain
    # concatenation meant a source's first section supplied everything and the
    # rest were fetched only to be discarded — WSJ:Markets in, WSJ:Business and
    # WSJ:Tech and WSJ:World out, every single run. Same ordering trap as W6-A0
    # one level down, and as §24 found in the symbol scan.
    out = [item for row in zip(*per_feed) for item in row] if per_feed else []
    seen = {id(x) for x in out}
    for feed in per_feed:                # zip() stops at the shortest; the
        for item in feed:                # remainder still belongs in the list
            if id(item) not in seen:
                out.append(item)
                seen.add(id(item))
    capped = out[:max_items]
    src = _STATS["rss"].setdefault(name, {"kept": 0, "dropped_cap": 0,
                                          "feeds": {}})
    src["kept"] = len(capped)
    # `max_items` truncates ACROSS feeds in iteration order, so without this
    # counter the last feeds of a source would silently contribute nothing and
    # look identical to feeds that returned nothing.
    src["dropped_cap"] = len(out) - len(capped)
    return capped


# ---------------------------------------------------------------------------
# THE HEADLINE BUDGET (W6-A0, 2026-07-29) — this was a live bug, not a nicety.
#
# `refresh` used to do:
#
#     headlines = fetch_headlines(cfg) + fetch_rss_sources(cfg)
#     headlines = headlines[:_ncfg(cfg).get("max_headlines", 50)]
#
# and `max_headlines` was ALSO the Alpaca request limit. Alpaca returns its full
# allowance every trading day, so the slice kept 50 Alpaca items and threw away
# every RSS item. Measured on 2026-07-29: Alpaca 50, RSS 45 fetched
# successfully (WSJ 20, CNBC 15, MarketWatch 10), RSS items surviving the
# slice: ZERO. What the distiller saw was 50 headlines, all from `benzinga`.
#
# WSJ, CNBC and MarketWatch had been fetched, parsed and freshness-checked over
# the network every hour and discarded one line later. The config comment said
# the caps existed to keep Alpaca "dominant"; the code made it exclusive, and
# nothing counted per source, so no surface could contradict it.
#
# One number doing two jobs is the whole root cause. They are separate keys now.
# ---------------------------------------------------------------------------

def alpaca_budget(cfg: dict) -> int:
    """How many wire headlines to request. Falls back to total-minus-reserve so
    an old config without the new key still leaves room for RSS."""
    n = _ncfg(cfg)
    if n.get("alpaca_max_headlines") is not None:
        return int(n["alpaca_max_headlines"])
    return max(1, int(n.get("max_headlines", 50)) - rss_budget(cfg))


def rss_budget(cfg: dict) -> int:
    """Slots RESERVED for RSS. Reserved, not fixed — see `merge_budget`."""
    return int(_ncfg(cfg).get("rss_reserved_slots", 0) or 0)


def merge_budget(alpaca: list[dict], by_source: dict, cfg: dict) -> list[dict]:
    """Fill the distiller's `max_headlines` budget from the wire and the RSS
    sources, guaranteeing RSS a share and round-robining within it.

    Two properties matter more than the ratio, and both are tested:

      * THE RESERVE IS GIVEN BACK. If the feeds are dead or thin, Alpaca
        backfills the unused slots — a bad news morning must not shrink the
        prompt, which would quietly degrade the read on exactly the day the
        distiller has least to work with.
      * IT IS SYMMETRIC. If the wire is short (or down, as on 2026-07-29 at
        10:34 ET when DNS failed), RSS expands into the space instead of the
        budget going unspent.

    Round-robin across SOURCES, not a flat concatenation: with a flat list the
    source YAML happens to name first eats the reserve and the rest starve —
    the same ordering trap §24 found in the symbol scan.
    """
    total = int(_ncfg(cfg).get("max_headlines", 50))
    reserve = min(rss_budget(cfg), total)

    queues = [list(items) for items in by_source.values() if items]
    rss_taken: list[dict] = []
    while len(rss_taken) < reserve and queues:
        for q in list(queues):
            if len(rss_taken) >= reserve:
                break
            rss_taken.append(q.pop(0))
            if not q:
                queues.remove(q)

    out = list(alpaca[:total - len(rss_taken)]) + rss_taken
    if len(out) < total:                      # the reserve, given back
        seen = {id(x) for x in out}
        for pool in (alpaca, *by_source.values()):
            for item in pool:
                if len(out) >= total:
                    break
                if id(item) not in seen:
                    out.append(item)
                    seen.add(id(item))
    _STATS["budget"] = {
        "total": total, "rss_reserved": reserve,
        "alpaca_offered": len(alpaca),
        "rss_offered": sum(len(v) for v in by_source.values()),
        "delivered": len(out),
        "rss_delivered": sum(1 for x in out
                             if x.get("source", "") in _rss_labels(by_source)),
    }
    return out[:total]


def _rss_labels(by_source: dict) -> set:
    """Every `source` label the RSS side produced, for counting what landed."""
    return {x.get("source", "") for items in by_source.values() for x in items}


def source_line(st: dict | None = None) -> str:
    """One-line human summary of who fed today's read. This is the surface the
    owner actually reads, so a source that contributed nothing has to be
    NAMED with a zero rather than left out of the sentence."""
    st = st or stats()
    parts = []
    a = st.get("alpaca") or {}
    parts.append(f"alpaca={a.get('items', 0)}"
                 + (f"({a['error']})" if a.get("error") else ""))
    for name, src in sorted((st.get("rss") or {}).items()):
        bad = [lbl.split(":")[-1] for lbl, f in (src.get("feeds") or {}).items()
               if f.get("http_error") or f.get("parse_error")]
        parts.append(f"{name}={src.get('kept', 0)}"
                     + (f"(failed:{','.join(sorted(bad))})" if bad else ""))
    b = st.get("budget") or {}
    if b:
        parts.append(f"-> distiller={b.get('delivered', 0)}"
                     f" (rss {b.get('rss_delivered', 0)}"
                     f"/{b.get('rss_reserved', 0)} reserved)")
    return " ".join(parts)


def _log_sources() -> None:
    log.info("news sources: %s", source_line())


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
    reset_stats()
    # Alpaca first (ticker-tagged, drives symbol_flags), then public RSS, and
    # `merge_budget` guarantees the RSS sources actually reach the distiller —
    # for eleven feeds and five days, they did not. See merge_budget's note.
    headlines = merge_budget(fetch_headlines(cfg), fetch_rss_by_source(cfg), cfg)
    _log_sources()
    if not headlines:
        # RECORD THE FAILURE, don't just log it. On 2026-07-29 at 10:34 ET this
        # branch was taken with all eleven feeds and the wire lost to DNS, and
        # because the only output was this one INFO line there was no ledger
        # entry, no dashboard change, and nothing for the watchdog to see. A
        # morning with no news has to leave evidence that it happened.
        log.info("no headlines this morning — no market context today")
        if ledger is not None:
            ledger.log_event("news_sources", json.dumps(
                {"headlines": 0, **stats()})[:1800])
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
        # W6-A1: which sources actually fed this read. Lives in the artifact the
        # cycle loads, so the dashboard can show it without a second fetch.
        "sources": stats(),
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
                         f"{ctx['summary']} | nominations: {noms}"
                         f" | sources: {source_line()}")
        ledger.log_event("news_sources", json.dumps(
            {"headlines": len(headlines), **stats()})[:1800])
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
