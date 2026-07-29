"""Every configured news source must be answerable from the bot's own record.

The defect (W6-A0/A1, found 2026-07-29)
---------------------------------------
The owner asked a simple question — "does it read all the sources I gave it?" —
and the answer could not be obtained from any log, ledger event or page. The
module logged ONLY failures, so a source returning zero every day looked
identical to a source working fine.

That absence was hiding a live bug. `refresh` did:

    headlines = fetch_headlines(cfg) + fetch_rss_sources(cfg)
    headlines = headlines[:_ncfg(cfg).get("max_headlines", 50)]

and `max_headlines` was ALSO the Alpaca request limit. Alpaca returns its full
allowance every trading day, so the slice kept 50 wire items and discarded
every RSS item. Measured that day: Alpaca 50, RSS 45 fetched successfully
(WSJ 20, CNBC 15, MarketWatch 10), RSS surviving the slice **zero** — the
distiller saw 50 headlines, all `benzinga`. Eleven feeds were fetched, parsed
and freshness-checked over the network every hour and thrown away one line
later.

Nothing failed. Nothing could have.

What these tests pin
--------------------
1. The budget arithmetic, in both directions — the RSS reserve is honoured, and
   GIVEN BACK when unused, so a dead-feed morning cannot shrink the prompt.
2. Round-robin, at both levels: across sources, and across a source's own feeds.
   A flat concatenation is what caused the bug one level up, and it recurred one
   level down when only source-level round-robin was in place.
3. The accounting: a zero-item source is NAMED with a zero, an HTML-not-RSS body
   is recorded as a parse error rather than a silence, and stale drops are
   counted.
4. The failure path leaves evidence. A morning with no headlines at all — the
   exact 10:34 ET case — must still write a `news_sources` ledger event.
"""
import json
from datetime import date, datetime, timedelta, timezone

import market_context as mc
import watchdog
from ledger import Ledger

# AP's real behaviour on 2026-07-29: HTTP 200, 1.7 MB, `<!DOCTYPE html>`, and
# `ET.fromstring` rejecting it at "line 3, column 239".
#
# The first draft of this fixture was accidentally well-formed XML, so it PARSED
# and recorded no error — the test failed and was right to. Real HTML breaks XML
# on void elements (`<meta charset="utf-8">` never closes) and bare ampersands,
# so both are here deliberately. A fixture that is easier to parse than the
# thing it stands for tests nothing.
AP_HTML = ('<!DOCTYPE html>\n<html class="SectionPage" lang="en">\n'
           '<head><meta charset="utf-8">\n<title>Business & Finance</title>\n'
           '</head><body>news</body></html>')


def _rss(items_xml, title="Feed"):
    return ('<?xml version="1.0"?><rss version="2.0"><channel>'
            f'<title>{title}</title>' + items_xml + '</channel></rss>')


def _item(title, when=None):
    pub = ""
    if when is not None:
        pub = f"<pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>"
    return f"<item><title>{title}</title><description>d</description>{pub}</item>"


def _stub_feeds(monkeypatch, bodies: dict):
    """Map url -> body string, or url -> Exception instance to fail the fetch."""
    def fake_open(req, timeout=8):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        body = bodies.get(url)
        if isinstance(body, Exception):
            raise body

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return (body or "").encode()
        return _R()
    monkeypatch.setattr(mc.urllib.request, "urlopen", fake_open)


def _items(source, n):
    return [{"headline": f"{source}-{i}", "summary": "s", "symbols": [],
             "source": source, "ts": ""} for i in range(n)]


# ---- the budget: the bug itself ----

def test_rss_reaches_the_distiller_at_all():
    """The regression. With the old code this assertion was 0."""
    cfg = {"news": {"max_headlines": 50, "alpaca_max_headlines": 30,
                    "rss_reserved_slots": 20}}
    out = mc.merge_budget(_items("benzinga", 50),
                          {"WSJ": _items("WSJ:Markets", 20),
                           "CNBC": _items("CNBC:Finance", 15)}, cfg)
    rss = [x for x in out if x["source"] != "benzinga"]
    assert len(out) == 50
    assert len(rss) == 20, (
        "RSS got no share of the budget — this is the W6-A0 bug, in which "
        "every RSS item was fetched over the network and then discarded")


def test_the_reserve_is_round_robined_across_sources():
    """Not first-come. A flat concatenation lets whichever source YAML names
    first eat the entire reserve — the ordering trap §24 found in the scan."""
    cfg = {"news": {"max_headlines": 20, "rss_reserved_slots": 8}}
    out = mc.merge_budget(_items("benzinga", 20),
                          {"WSJ": _items("WSJ:Markets", 50),
                           "CNBC": _items("CNBC:Finance", 50),
                           "FT": _items("FT:Markets", 50),
                           "BB": _items("BB:Markets", 50)}, cfg)
    got = {}
    for x in out:
        if x["source"] != "benzinga":
            got[x["source"]] = got.get(x["source"], 0) + 1
    assert got == {"WSJ:Markets": 2, "CNBC:Finance": 2,
                   "FT:Markets": 2, "BB:Markets": 2}, got


def test_the_reserve_is_given_back_when_feeds_are_thin():
    """A bad news morning must not shrink the prompt. If RSS cannot fill its
    reserve, the wire backfills — otherwise the distiller gets least to work
    with on exactly the day the feeds are broken."""
    cfg = {"news": {"max_headlines": 50, "rss_reserved_slots": 20}}
    out = mc.merge_budget(_items("benzinga", 50),
                          {"WSJ": _items("WSJ:Markets", 3)}, cfg)
    assert len(out) == 50
    assert sum(1 for x in out if x["source"] == "WSJ:Markets") == 3
    assert sum(1 for x in out if x["source"] == "benzinga") == 47


def test_the_split_is_symmetric_when_the_wire_is_down():
    """2026-07-29 10:34 ET: DNS failure took the wire out. RSS should expand
    into the space rather than the budget going unspent."""
    cfg = {"news": {"max_headlines": 50, "rss_reserved_slots": 20}}
    out = mc.merge_budget([], {"WSJ": _items("WSJ:Markets", 40),
                               "CNBC": _items("CNBC:Finance", 40)}, cfg)
    assert len(out) == 50, "RSS did not expand into the wire's unused share"


def test_no_item_is_delivered_twice():
    """The backfill loop must not re-add what the reserve already took."""
    cfg = {"news": {"max_headlines": 30, "rss_reserved_slots": 25}}
    out = mc.merge_budget(_items("benzinga", 4),
                          {"WSJ": _items("WSJ:Markets", 5)}, cfg)
    assert len(out) == len({id(x) for x in out}) == 9


def test_alpaca_request_limit_is_its_own_key():
    """One number doing two jobs is the entire root cause of W6-A0."""
    cfg = {"news": {"max_headlines": 50, "alpaca_max_headlines": 30,
                    "rss_reserved_slots": 20}}
    assert mc.alpaca_budget(cfg) == 30
    # An old config without the new key must still leave room for RSS rather
    # than reproducing the bug.
    assert mc.alpaca_budget({"news": {"max_headlines": 50,
                                      "rss_reserved_slots": 20}}) == 30


# ---- the accounting ----

def test_a_source_that_returns_nothing_is_named_with_a_zero(monkeypatch):
    """`empty is not the same as absent`. The whole question the owner asked
    hinges on a dead source being visible rather than missing."""
    mc.reset_stats()
    _stub_feeds(monkeypatch, {"http://live": _rss(_item("a")),
                              "http://dead": _rss("")})
    cfg = {"news": {"rss_sources": {
        "Live": {"enabled": True, "feeds": {"S": "http://live"}},
        "Dead": {"enabled": True, "feeds": {"S": "http://dead"}}}}}
    mc.fetch_rss_by_source(cfg)
    st = mc.stats()
    assert st["rss"]["Dead"]["kept"] == 0
    assert "Dead" in st["rss"], "a source with no items vanished from the record"
    assert "Dead=0" in mc.source_line(st)


def test_html_instead_of_rss_is_recorded_not_silent(monkeypatch):
    """AP answers 200 with a webpage. A fetcher trusting the status code would
    call that a success forever; the parse error has to reach the record."""
    mc.reset_stats()
    _stub_feeds(monkeypatch, {"http://ap": AP_HTML})
    cfg = {"news": {"rss_sources": {
        "AP": {"enabled": True, "feeds": {"Business": "http://ap"}}}}}
    assert mc.fetch_rss_by_source(cfg) == {"AP": []}
    feed = mc.stats()["rss"]["AP"]["feeds"]["AP:Business"]
    assert feed["parse_error"], "an HTML body was discarded without a trace"
    assert feed["kept"] == 0
    assert "failed:Business" in mc.source_line()


def test_a_body_that_parses_but_holds_no_items_is_still_visible(monkeypatch):
    """The third case, found while fixing this file's own fixture: HTML that
    happens to be well-formed XML parses cleanly and yields nothing. There is no
    error to record — so the only thing standing between that and invisibility
    is the zero being NAMED."""
    mc.reset_stats()
    _stub_feeds(monkeypatch, {"http://x": "<html><body>news</body></html>"})
    cfg = {"news": {"rss_sources": {
        "Odd": {"enabled": True, "feeds": {"S": "http://x"}}}}}
    assert mc.fetch_rss_by_source(cfg) == {"Odd": []}
    feed = mc.stats()["rss"]["Odd"]["feeds"]["Odd:S"]
    assert feed["parse_error"] is None and feed["http_error"] is None
    assert feed["kept"] == 0
    assert "Odd=0" in mc.source_line()


def test_http_failure_is_recorded_per_feed(monkeypatch):
    mc.reset_stats()
    _stub_feeds(monkeypatch, {"http://x": OSError("nodename nor servname")})
    cfg = {"news": {"rss_sources": {
        "WSJ": {"enabled": True, "feeds": {"Markets": "http://x"}}}}}
    mc.fetch_rss_by_source(cfg)
    assert mc.stats()["rss"]["WSJ"]["feeds"]["WSJ:Markets"]["http_error"]


def test_stale_items_are_counted_not_merely_dropped(monkeypatch):
    mc.reset_stats()
    now = datetime.now(timezone.utc)
    body = _rss(_item("fresh", now) + _item("old", now - timedelta(hours=90)))
    _stub_feeds(monkeypatch, {"http://x": body})
    cfg = {"news": {"rss_sources": {
        "WSJ": {"enabled": True, "max_age_hours": 36,
                "feeds": {"Markets": "http://x"}}}}}
    mc.fetch_rss_by_source(cfg)
    feed = mc.stats()["rss"]["WSJ"]["feeds"]["WSJ:Markets"]
    assert feed["dropped_stale"] == 1
    assert feed["kept"] == 1


def test_items_lost_to_the_source_cap_are_counted(monkeypatch):
    """`max_items` truncates across feeds, so without this counter the tail
    feeds of a source look exactly like feeds that returned nothing."""
    mc.reset_stats()
    body = _rss("".join(_item(f"i{i}") for i in range(6)))
    _stub_feeds(monkeypatch, {"http://a": body, "http://b": body})
    cfg = {"news": {"rss_sources": {
        "WSJ": {"enabled": True, "max_per_feed": 6, "max_items": 4,
                "feeds": {"A": "http://a", "B": "http://b"}}}}}
    mc.fetch_rss_by_source(cfg)
    src = mc.stats()["rss"]["WSJ"]
    assert src["kept"] == 4
    assert src["dropped_cap"] == 8


def test_a_sources_own_feeds_are_interleaved(monkeypatch):
    """The bug, one level down. Concatenating a source's sections meant
    WSJ:Markets supplied everything and Business/Tech/World were fetched only
    to be discarded by the cap."""
    mc.reset_stats()
    body = _rss("".join(_item(f"i{i}") for i in range(8)))
    _stub_feeds(monkeypatch, {f"http://{k}": body for k in "abcd"})
    cfg = {"news": {"rss_sources": {"WSJ": {
        "enabled": True, "max_per_feed": 8, "max_items": 8,
        "feeds": {"Markets": "http://a", "Business": "http://b",
                  "Tech": "http://c", "World": "http://d"}}}}}
    got = {x["source"] for x in mc.fetch_rss_by_source(cfg)["WSJ"]}
    assert got == {"WSJ:Markets", "WSJ:Business", "WSJ:Tech", "WSJ:World"}, (
        f"only {got} reached the budget — the other sections were fetched "
        f"over the network and thrown away")


def test_stats_is_a_copy_not_the_live_dict():
    mc.reset_stats()
    snap = mc.stats()
    snap["alpaca"]["items"] = 999
    assert mc.stats()["alpaca"]["items"] == 0


# ---- the failure path leaves evidence ----

def test_a_newsless_morning_still_writes_a_ledger_event(tmp_path, monkeypatch):
    """The 10:34 ET case. Every feed and the wire died, and the only output was
    one INFO line — so there was no ledger entry, nothing on the dashboard, and
    nothing for the watchdog to see."""
    monkeypatch.setattr(mc, "fetch_headlines", lambda cfg: [])
    monkeypatch.setattr(mc, "fetch_rss_by_source", lambda cfg: {})
    led = Ledger(str(tmp_path / "l.jsonl"))
    assert mc.refresh({"news": {"enabled": True, "max_headlines": 50},
                       "symbols": []}, None, ledger=led) is None
    events = [r for r in led.all_records() if r.get("event") == "news_sources"]
    assert len(events) == 1, "a morning with no news left no evidence"
    assert json.loads(events[0]["detail"])["headlines"] == 0


# ---- the watchdog asks the question ----

def _ctx(day, event="market_context"):
    """Midday UTC, not midnight. `_event_on` compares LOCAL dates, so a
    midnight-UTC stamp lands on the previous day west of Greenwich — which is
    how the first draft of these tests failed."""
    return {"type": "event", "event": event,
            "ts": datetime.combine(day, datetime.min.time())
            .replace(hour=12, tzinfo=timezone.utc).isoformat()}


def test_a_weekday_with_no_market_context_is_a_problem(tmp_path):
    hb = tmp_path / "hb"
    day = date(2026, 7, 29)                      # a Wednesday
    hb.write_text(datetime(2026, 7, 29, 12, tzinfo=timezone.utc).isoformat())
    problems = watchdog.check(today=day, heartbeat_path=str(hb),
                              halt_path=str(tmp_path / "none"),
                              records=[_ctx(day, "cycle_complete")])
    assert any("no market_context" in p for p in problems), problems


def test_market_context_present_means_no_problem(tmp_path):
    hb = tmp_path / "hb"
    day = date(2026, 7, 29)
    hb.write_text(datetime(2026, 7, 29, 12, tzinfo=timezone.utc).isoformat())
    assert watchdog.check(today=day, heartbeat_path=str(hb),
                          halt_path=str(tmp_path / "none"),
                          records=[_ctx(day, "cycle_complete"),
                                   _ctx(day)]) == []


def test_the_news_check_is_silent_at_the_weekend(tmp_path):
    hb = tmp_path / "hb"
    hb.write_text(datetime(2026, 7, 25, 12, tzinfo=timezone.utc).isoformat())
    assert watchdog.check(today=date(2026, 7, 26),   # Sunday
                          heartbeat_path=str(hb),
                          halt_path=str(tmp_path / "none"),
                          records=[]) == []


def test_a_broken_cycle_reports_one_cause_not_two(tmp_path):
    """If the cycle never completed, don't also complain about the news it
    never had a chance to fetch — one root cause beats two symptoms."""
    hb = tmp_path / "hb"
    day = date(2026, 7, 29)
    hb.write_text(datetime(2026, 7, 29, 12, tzinfo=timezone.utc).isoformat())
    problems = watchdog.check(today=day, heartbeat_path=str(hb),
                              halt_path=str(tmp_path / "none"), records=[])
    assert len(problems) == 1
    assert "cycle_complete" in problems[0]


def test_an_unreadable_ledger_is_not_read_as_missing_news(tmp_path):
    """`absence != false`. A monitor that cannot see its input must not report
    the specific failure it cannot actually observe."""
    hb = tmp_path / "hb"
    day = date(2026, 7, 29)
    hb.write_text(datetime(2026, 7, 29, 12, tzinfo=timezone.utc).isoformat())
    problems = watchdog.check(today=day, heartbeat_path=str(hb),
                              halt_path=str(tmp_path / "none"),
                              records=watchdog._UNREADABLE)
    assert not any("no market_context" in p for p in problems), problems
    assert any("cannot read the ledger" in p for p in problems), problems


# ---- the shipped config is the one that was verified ----

def test_shipped_config_gives_rss_a_real_share():
    import os

    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        n = yaml.safe_load(f)["news"]
    assert n["alpaca_max_headlines"] + n["rss_reserved_slots"] \
        == n["max_headlines"], (
        "the wire limit plus the RSS reserve must equal the budget, or one of "
        "them is silently squeezing the other")
    assert n["rss_reserved_slots"] > 0, (
        "rss_reserved_slots is 0 — every RSS source is back to contributing "
        "nothing, which is the W6-A0 bug restored")
    assert n["alpaca_max_headlines"] > n["rss_reserved_slots"], (
        "Alpaca must stay the majority: its items are the only ticker-TAGGED "
        "ones and symbol_flags is built from them")


def test_the_three_rejected_sources_stay_rejected():
    """Reuters (401), Barron's (404) and AP (200-with-HTML) were each tested on
    2026-07-29. Re-adding one without re-testing would put a source in the
    config that contributes nothing — which is what this whole file is about."""
    import os

    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        srcs = yaml.safe_load(f)["news"]["rss_sources"]
    for dead in ("Reuters", "AP", "Barrons", "Barron's"):
        assert dead not in srcs, (
            f"{dead} was re-added. It was measured dead on 2026-07-29 — "
            f"re-test it through market_context._fetch_rss_source and record "
            f"the item count in config.yaml before enabling it.")
