#!/usr/bin/env python3
"""Acceptance criteria for the published static site, checked against a parsed
DOM rather than a substring search.

Why a parser
------------
Every existing dashboard assertion in tests/ is `assert "some string" in html`
against a 100 KB document. That cannot see a duplicate id, a table whose body
rows have fewer cells than its header, an interactive control that has been
moved inside a region the poll replaces, or a link to a file that is not
there. Those are the defects this file is for.

stdlib html.parser only — no bs4, no lxml, no playwright. CLAUDE.md:265 is
"dependency-light" and README.md:222 makes a pin bump oblige re-running a
frozen gate, so a QA convenience is not worth a lockfile change.

What this deliberately does NOT do
----------------------------------
It never executes JavaScript, so it makes no claim about the boot splash, the
poll-and-swap, badge repainting, tooltip positioning, chip filtering or the
count-up. Those are browser criteria, marked `browser` in docs/qa_inventory.md
and checked by driving a real browser against a served copy. A string assertion
about page JS is a structural guard, not a behavioural one, and this file says
so rather than letting the two blur.

    python scripts/qa_site_sweep.py --site /tmp/site/full --fixture /tmp/qa/full \\
        --profile full [--verbose]

Exit code = number of failed criteria, same contract as scripts/qa_sweep.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qa_criteria import criterion, for_profile  # noqa: E402

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
# The ten regions the poll replaces wholesale (dashboard.py:1295-1307).
VOLATILE = ("tape", "hero", "cards", "positions", "decisions", "plchart",
            "eqchart", "bars", "strat", "months")


# ------------------------------------------------------------------ DOM

class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children: list[Node] = []
        self.parent = parent
        self.text = ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, tag=None, cls=None, attr=None):
        out = []
        for n in self.walk():
            if tag and n.tag != tag:
                continue
            if cls and cls not in (n.attrs.get("class") or "").split():
                continue
            if attr and attr not in n.attrs:
                continue
            out.append(n)
        return out

    def all_text(self) -> str:
        return "".join(n.text for n in self.walk())

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def region(self) -> str | None:
        """Which volatile region this node lives inside, if any."""
        for a in [self, *self.ancestors()]:
            rid = a.attrs.get("id") or ""
            if rid.startswith("rgn-"):
                return rid[4:]
        return None


class _Build(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


class Page:
    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.raw = open(path, encoding="utf-8").read()
        b = _Build()
        b.feed(self.raw)
        self.root = b.root

    def find(self, **kw):
        return self.root.find(**kw)

    def walk(self):
        return self.root.walk()

    @property
    def text(self):
        return self.root.all_text()

    def ids(self):
        return [n.attrs["id"] for n in self.root.walk() if "id" in n.attrs]


class Site:
    """A rendered site plus the fixture it came from — cross-surface criteria
    need both."""

    def __init__(self, site_dir: str, fixture: str, profile: str):
        self.dir, self.fixture, self.profile = site_dir, fixture, profile
        self.pages = {}
        for n in ("index.html", "dashboard.html", "blog.html", "journal.html"):
            p = os.path.join(site_dir, n)
            if os.path.exists(p):
                self.pages[n] = Page(p)
        self.dash = self.pages.get("index.html") or self.pages.get("dashboard.html")
        self.sidecar = None
        side = os.path.join(site_dir, "dashboard_data.json")
        if os.path.exists(side):
            try:
                self.sidecar = json.load(open(side))
            except json.JSONDecodeError:
                self.sidecar = "malformed"

    def store_lines(self, name):
        p = os.path.join(self.fixture, name)
        if not os.path.exists(p):
            return 0
        with open(p) as f:
            return sum(1 for line in f if line.strip())

    def ledger(self):
        with open(os.path.join(self.fixture, "ledger.jsonl")) as f:
            return [json.loads(x) for x in f if x.strip()]


# ------------------------------------------------------- structural core

@criterion("SITE-DASH-RGN-01", "dashboard", "visitor",
           "every region in the JSON sidecar has a mount point in the HTML, "
           "and every mount point has a region",
           profiles=("full", "thin", "empty", "hostile"),
           edge="a renamed region silently stops updating; nothing else notices")
def _regions_match(s: Site):
    if not isinstance(s.sidecar, dict):
        return False, f"sidecar is {s.sidecar!r}"
    mounts = {i[4:] for i in s.dash.ids() if i.startswith("rgn-")}
    keys = set(s.sidecar["regions"])
    return mounts == keys, (f"mounts={sorted(mounts)} keys={sorted(keys)} "
                            f"only_in_html={sorted(mounts - keys)} "
                            f"only_in_json={sorted(keys - mounts)}")


@criterion("SITE-DASH-RGN-02", "dashboard", "visitor",
           "the sidecar hash is the sha256 of its own regions",
           profiles=("full", "thin", "empty", "hostile"),
           edge="the swap path compares this hash; a wrong one means the page "
                "either never updates or updates on every poll")
def _hash_is_real(s: Site):
    if not isinstance(s.sidecar, dict):
        return False, f"sidecar is {s.sidecar!r}"
    want = hashlib.sha256(
        json.dumps(s.sidecar["regions"], sort_keys=True).encode()).hexdigest()[:16]
    return s.sidecar["hash"] == want, f"stored={s.sidecar['hash']} recomputed={want}"


@criterion("SITE-DASH-RGN-03", "dashboard", "visitor",
           "the badge's baked-in hash equals the sidecar's, so the first poll "
           "is a no-op rather than a spurious swap",
           profiles=("full", "thin", "empty", "hostile"))
def _badge_hash(s: Site):
    b = [n for n in s.dash.walk() if n.attrs.get("id") == "fresh"]
    if not b:
        return False, "no #fresh badge"
    if not isinstance(s.sidecar, dict):
        return False, f"sidecar is {s.sidecar!r}"
    return b[0].attrs.get("data-hash") == s.sidecar["hash"], (
        f"badge={b[0].attrs.get('data-hash')} sidecar={s.sidecar['hash']}")


@criterion("SITE-DASH-ID-01", "dashboard", "visitor",
           "no id appears twice in the document",
           profiles=("full", "thin", "empty", "hostile"),
           edge="getElementById returns the first match, so a duplicate id "
                "silently sends every update to the wrong element")
def _unique_ids(s: Site):
    seen, dupes = set(), []
    for i in s.dash.ids():
        (dupes.append(i) if i in seen else seen.add(i))
    return not dupes, f"duplicates={sorted(set(dupes))[:10]}"


# ------------------------------------- interaction survives a region swap

@criterion("SITE-DASH-BIND-01", "dashboard", "visitor",
           "interaction handlers are delegated to document, so a region swap "
           "cannot orphan them",
           profiles=("full", "thin", "empty", "hostile"),
           edge="chips live in the `decisions` region, [data-tip] in the three "
                "chart regions and [data-count] in `hero`; swap() replaces all "
                "of those wholesale, so per-element listeners are destroyed by "
                "the first poll that sees a new hash")
def _handlers_delegated(s: Site):
    """STRUCTURAL, not behavioural — and the difference is the point.

    This asserts the page is built so a swap cannot orphan a handler. It does
    NOT prove the handlers work; only a browser can do that, which is why
    SITE-BROWSER-01..03 exist and are marked `browser` in the inventory. An
    `assert "addEventListener" in JS` dressed up as proof would be exactly the
    anti-pattern scripts/qa_sweep.py:391 warns about.

    The check: every interactive element still lives inside a volatile region
    (that is fine and expected), so the listeners must be on `document`.
    """
    js = s.dash.raw
    delegated = [f"document.addEventListener('{e}'"
                 for e in ("click", "mouseover", "mouseout")]
    missing = [d for d in delegated if d not in js]
    # Every listener must be attached to `document`. Counting receivers is
    # what matters — an earlier version of this check grepped for
    # querySelectorAll('.chip').forEach and flagged the loop that merely
    # CLEARS the .on class, which binds nothing.
    receivers = re.findall(r"(\w+)\.addEventListener\(", js)
    # `boot` is exempt, and the exemption is the rule restated rather than a
    # hole in it: the splash is a full-page overlay that lives OUTSIDE every
    # rgn-* mount point, so swap() cannot reach it, and it removes itself on
    # click or after 3.4s. Binding straight to it is correct. Anything else
    # named here is a real orphan waiting to happen.
    allowed = {"document", "boot"}
    non_document = sorted({r for r in receivers if r not in allowed})
    if missing or non_document:
        return False, (f"missing delegated listeners={missing}; "
                       f"listeners bound to non-document receivers="
                       f"{non_document}")
    return True, (f"all {len(receivers)} listeners delegated to document "
                  f"(click/mouseover/mousemove/mouseout)")


@criterion("SITE-DASH-BIND-02", "dashboard", "visitor",
           "a replaced hero figure gets re-animated after a swap",
           profiles=("full", "thin", "hostile"),
           edge="the value itself is server-rendered and correct either way, "
                "so this is polish — recorded so the swap hook is not deleted "
                "as unused")
def _count_rehook(s: Site):
    js = s.dash.raw
    return ("window.__repete_after_swap" in js
            and "if(window.__repete_after_swap)" in js), (
        "swap() does not call the post-swap hook")


# -------------------------------------------------------------- controls

@criterion("SITE-DASH-CHIP-01", "dashboard", "visitor",
           "exactly the six documented filter chips are rendered, one marked on",
           profiles=("full", "thin", "hostile"))
def _chips_present(s: Site):
    chips = [n for n in s.dash.walk() if "data-f" in n.attrs]
    vals = [n.attrs["data-f"] for n in chips]
    on = [n for n in chips if "on" in (n.attrs.get("class") or "").split()]
    want = ["all", "r-exec", "r-approve", "r-downsize", "r-veto", "r-skip"]
    return (vals == want and len(on) == 1), f"chips={vals} on={len(on)}"


@criterion("SITE-DASH-CHIP-02", "dashboard", "visitor",
           "a filter with no matches has something to say for itself",
           profiles=("full", "thin", "hostile"),
           edge="the table renders only the last N_DECISIONS=30 decisions and "
                "executed trades are ~3% of them, so 'Executed' and 'Vetoed' "
                "routinely match nothing in the visible window — that is an "
                "ordinary day, not an edge case. Selecting one used to leave a "
                "header above an empty void, which reads as a page that failed "
                "to load rather than a filter with no hits.")
def _empty_filter_state(s: Site):
    nm = [n for n in s.dash.walk() if n.attrs.get("id") == "nomatch"]
    if not nm:
        return False, "no #nomatch row rendered; an empty filter says nothing"
    row = nm[0]
    hidden = "display:none" in (row.attrs.get("style") or "").replace(" ", "")
    classes = set((row.attrs.get("class") or "").split())
    filterable = {c for c in classes if c.startswith("r-")}
    # If the row carried an r-* class a filter could match it, and the "no
    # matches" message would appear as if it were a result.
    return (hidden and not filterable), (
        f"hidden_by_default={hidden} filterable_classes={sorted(filterable)}")


@criterion("SITE-DASH-DETAILS-01", "dashboard", "visitor",
           "both collapsible sections render and start open",
           profiles=("full", "thin", "empty", "hostile"))
def _details(s: Site):
    d = s.dash.find(tag="details")
    return (len(d) == 2 and all("open" in n.attrs for n in d)), (
        f"n={len(d)} open={[('open' in n.attrs) for n in d]}")


# ------------------------------------------------------------ the tables

@criterion("SITE-DASH-TBL-01", "dashboard", "visitor",
           "every table's body rows carry as many cells as its header, "
           "counting colspans",
           profiles=("full", "thin", "hostile"),
           edge="the positions table has TWO column sets (marked vs unmarked) "
                "and a colspan total row; a mismatch shifts every value one "
                "column left and still renders")
def _table_widths(s: Site):
    bad = []
    for t in s.dash.find(tag="table"):
        rows = [r for r in t.find(tag="tr")]
        if not rows:
            continue
        def width(r):
            return sum(int(c.attrs.get("colspan", 1))
                       for c in r.children if c.tag in ("td", "th"))
        head = width(rows[0])
        for i, r in enumerate(rows[1:], 1):
            w = width(r)
            if w and w != head:
                bad.append(f"{t.attrs.get('id') or 'table'} row{i}: {w} != {head}")
    return not bad, "; ".join(bad[:6])


# ------------------------------------------------------- required notices

@criterion("SITE-PAPER-01", "all pages", "visitor",
           "every page discloses [PAPER]",
           profiles=("full", "thin", "empty", "hostile"),
           edge="CLAUDE.md invariant 7 — never remove the disclosure")
def _paper(s: Site):
    missing = [n for n, p in s.pages.items() if "[PAPER]" not in p.raw]
    return not missing, f"missing on {missing}"


@criterion("SITE-DISC-01", "all pages", "visitor",
           "every page carries the disclaimer from src/disclaimer.py",
           profiles=("full", "thin", "empty", "hostile"))
def _disclaimer(s: Site):
    import disclaimer
    key = disclaimer.DISCLAIMER[:40]
    missing = [n for n, p in s.pages.items() if key not in p.text]
    return not missing, f"missing on {missing}"


# ------------------------------------------------------------- integrity

@criterion("SITE-XSURF-01", "dashboard vs journal", "visitor",
           "the closed-trade count agrees across the ledger, the dashboard "
           "card and the journal",
           profiles=("full", "thin"),
           edge="three surfaces derive this independently; a disagreement "
                "means at least one published number is wrong")
def _closed_agree(s: Site):
    led = sum(1 for r in s.ledger() if r.get("type") == "outcome")
    card = None
    for n in s.dash.walk():
        if "card" in (n.attrs.get("class") or "").split():
            k = [c for c in n.children if "k" in (c.attrs.get("class") or "").split()]
            v = [c for c in n.children if "v" in (c.attrs.get("class") or "").split()]
            if k and v and k[0].all_text().strip() == "closed trades":
                card = v[0].all_text().strip()
    j = s.pages.get("journal.html")
    arts = 0
    if j:
        arts = sum(1 for a in j.find(tag="article")
                   if "close" in a.all_text()[:200].lower())
    return (str(led) == str(card)), f"ledger={led} card={card} journal_close={arts}"


@criterion("SITE-LINK-01", "all pages", "visitor",
           "every relative link resolves to a file present in the site",
           profiles=("full", "thin", "empty", "hostile"),
           edge="publish_dashboard.sh renames dashboard.html -> index.html, so "
                "a link that works locally can 404 in production and vice versa")
def _links(s: Site):
    bad = []
    for name, p in s.pages.items():
        for a in p.find(tag="a"):
            href = a.attrs.get("href", "")
            # Anything with a scheme is somebody else's to resolve; a BAD
            # scheme is SITE-ESC-02's finding, not a broken-link finding.
            if not href or href.startswith("#") or urlparse(href).scheme:
                continue
            target = href.split("#", 1)[0]
            if target and not os.path.exists(os.path.join(s.dir, target)):
                bad.append(f"{name} -> {target}")
    return not bad, "; ".join(sorted(set(bad))[:8])


@criterion("SITE-LIVE-01", "dashboard", "visitor",
           "the self-refresh script is present with its configured thresholds",
           profiles=("full", "thin", "empty", "hostile"))
def _live_js(s: Site):
    need = ["AMBER=8", "RED=24", "fetch('dashboard_data.json'",
            "location.protocol!=='file:'"]
    missing = [x for x in need if x not in s.dash.raw]
    return not missing, f"missing={missing}"


# --------------------------------------------------------- escaping / XSS

@criterion("SITE-ESC-01", "all pages", "attacker",
           "no fixture-supplied string becomes markup",
           profiles=("hostile",),
           edge="symbols, reasons, journal text and post bodies all reach the "
                "DOM defended only by html.escape()")
def _no_injection(s: Site):
    """Checked against the PARSED DOM, not raw substrings.

    A raw search is wrong in both directions here. `html.escape` turns
    `<img src=x onerror=alert(1)>` into `&lt;img src=x onerror=alert(1)&gt;`,
    which still contains the literal text "onerror=alert(" while being inert —
    so a substring check reports an XSS that does not exist. What matters is
    whether a TAG or an ATTRIBUTE was created that the renderer never emitted.
    """
    bad = []
    for name, p in s.pages.items():
        for n in p.walk():
            for attr in n.attrs:
                if attr.startswith("on"):
                    bad.append(f"{name}: <{n.tag} {attr}=...>")
            if n.tag == "img" and (n.attrs.get("src") or "").strip() == "x":
                bad.append(f"{name}: injected <img src=x>")
        # Page JS is inline, so a script element is expected; one that carries
        # fixture text is not.
        for sc in p.find(tag="script"):
            if "alert(" in sc.all_text():
                bad.append(f"{name}: <script> containing alert(")
    return not bad, "; ".join(sorted(set(bad))[:6])


@criterion("SITE-ESC-02", "blog", "attacker",
           "no anchor carries a dangerous URL scheme",
           profiles=("hostile", "full"),
           edge="blog.py escapes a post's TEXT but builds <a href> from the "
                "same record with no scheme check")
def _href_schemes(s: Site):
    bad = []
    for name, p in s.pages.items():
        for a in p.find(tag="a"):
            scheme = urlparse(a.attrs.get("href", "")).scheme.lower()
            if scheme and scheme not in ("http", "https", "mailto"):
                bad.append(f"{name}: {a.attrs.get('href')[:60]}")
    return not bad, "; ".join(sorted(set(bad))[:6])


@criterion("SITE-JOUR-01", "journal", "visitor",
           "the permalink the bot publishes resolves to the entry it names",
           profiles=("full", "hostile"),
           edge="journal.html#<trade_id> is the only deep-link surface in the "
                "project. A '#' inside a trade_id truncates the fragment at "
                "the first '#', so the browser looks for an element named 't', "
                "finds none, and silently leaves the reader at the top of a "
                "page of hundreds of entries — no error, no 404.")
def _permalinks_resolve(s: Site):
    """Checks the LINK, not the id. An id containing '#' or a space is legal
    HTML5 and matches fine once the fragment is percent-encoded, so requiring
    ids to be 'clean' would be fixing the wrong end."""
    import journal as journal_mod
    j = s.pages.get("journal.html")
    if not j:
        return True, "no journal page"
    ids = {a.attrs.get("id") for a in j.find(tag="article")}
    bad = []
    for tid in ids:
        link = journal_mod.permalink("https://qa.example.invalid/journal.html",
                                     tid or "")
        frag = link.split("#", 1)[1] if "#" in link else ""
        # A browser sends everything after the FIRST '#', then percent-decodes
        # it before matching against the id.
        sent = link.split("#", 1)[1].split("#")[0] if "#" in link else ""
        if unquote(sent) != tid or sent != frag:
            bad.append(f"{tid!r} -> {link!r}")
    return not bad, f"permalinks that miss their entry: {bad[:4]}"


@criterion("SITE-SAN-01", "all pages", "visitor",
           "no rendered artifact carries a personal identifier",
           profiles=("full", "thin", "empty", "hostile"),
           edge="QA output gets attached to bug reports and shared")
def _sanitized(s: Site):
    bad = [n for n, p in s.pages.items()
           if "connorshibley" in p.raw or "github.io" in p.raw]
    return not bad, f"leaking={bad}"


# ------------------------------------------------------- degraded states

_EMPTY_COPY = ["No open positions", "No decisions yet",
               "No trades journaled yet", "No posts yet",
               "No closed trades yet", "Monthly scorecard appears after"]


@criterion("SITE-EMPTY-01", "all pages", "visitor",
           "day one renders every empty-state message instead of a blank or a zero",
           profiles=("empty",),
           edge="this is the only copy a brand-new visitor ever sees")
def _empty_states(s: Site):
    blob = "".join(p.raw for p in s.pages.values())
    missing = [c for c in _EMPTY_COPY if c not in blob]
    return not missing, f"missing={missing}"


@criterion("SITE-THIN-01", "dashboard", "visitor",
           "ratios below the minimum sample render as pending, not as a number",
           profiles=("thin",),
           edge="MIN_CLOSED_FOR_RATIOS=10; a 100% win rate off two trades is "
                "the same misreading in a smaller font")
def _pending(s: Site):
    pend = [n for n in s.dash.walk()
            if "pending" in (n.attrs.get("class") or "").split()]
    return bool(pend), f"pending cells={len(pend)}"


@criterion("SITE-THIN-02", "dashboard", "visitor",
           "the trade chart explains itself below five closed trades",
           profiles=("thin",))
def _tiny_chart(s: Site):
    return ("The chart appears at 5" in s.dash.raw
            or "No closed trades yet" in s.dash.raw), "neither n<5 note present"


# ------------------------------------------------------------------ main

def run(site: Site, verbose: bool) -> int:
    fails = 0
    for c in for_profile(site.profile):
        try:
            ok, ev = c["fn"](site)
        except Exception as e:                       # noqa: BLE001
            ok, ev = False, f"{type(e).__name__}: {e}"
        if ok:
            if verbose:
                print(f"  PASS {c['id']}  {c['statement']}")
        else:
            fails += 1
            print(f"  FAIL {c['id']}  {c['statement']}")
            print(f"       {ev}")
    n = len(for_profile(site.profile))
    print(f"[{site.profile}] {n - fails}/{n} criteria passed")
    return fails


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", required=True)
    p.add_argument("--fixture", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    return run(Site(a.site, a.fixture, a.profile), a.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
