# QA findings register

Defects found by driving the user-facing surfaces as a user, against sanitized
production-scale fixtures. Companion to `docs/qa_inventory.md`, which lists the
acceptance criteria; this file lists what failed them.

**A finding is CLOSED only when a test would fail if it reopened.** Not "fixed
in code" — the same rule `docs/divergences.md` uses, and for the same reason:
this project has been wrong before about what "fixed" meant.

Severity:

- **S1** — the user is shown a wrong number, or a control silently stops working
- **S2** — a degraded or error state renders wrong, or a safety property is
  weaker than it looks
- **S3** — cosmetic, copy, accessibility, or developer-facing

The `F-nn` namespace continues the one already in use at
`scripts/qa_sweep.py:349,391` and `tests/test_publisher.py:549,587`; F-01 and
F-02 are retro-documented below because they set the closure bar.

## Summary

| # | Finding | Surface | Sev | Status | Closed by |
|---|---|---|---|---|---|
| F-01 | Rate limiter keyed on a spoofable header | publisher | S2 | CLOSED | `test_publisher.py::test_rate_limit_key_ignores_forwarded_header_by_default` |
| F-02 | Read-only claim asserted by source inspection, not behaviour | publisher | S2 | CLOSED | `test_publisher.py::test_readonly_ledger_refuses_writes` |
| F-03 | Fixture guard was worktree-relative; live `.site/` unprotected | tooling | S1 | CLOSED | `test_qa_fixture_shapes.py::test_the_main_checkout_is_discovered_from_a_worktree` |
| F-04 | Filter chips die after the first poll | dashboard | S1 | CLOSED | `test_dashboard_survives_a_region_swap.py::test_no_listener_is_bound_to_an_element_inside_a_volatile_region` |
| F-05 | Chart tooltips die after the first poll | dashboard | S1 | CLOSED | same as F-04 |
| F-06 | Hero count-up does not re-animate after a swap | dashboard | S3 | CLOSED | `::test_swap_reanimates_the_replaced_figure` |
| F-07 | "update check failed" erased by the 30s repaint | dashboard | S2 | CLOSED | `::test_the_failure_note_survives_a_repaint` |
| F-08 | A filter matching nothing renders a blank void | dashboard | S2 | CLOSED | `::test_an_empty_filter_explains_itself` |
| F-09 | `javascript:` post link renders as a clickable anchor | blog | S2 | CLOSED | `test_blog.py::test_a_dangerous_url_scheme_is_not_linked` |
| F-10 | Journal permalinks are not fragment-encoded | journal | S2 | CLOSED | `test_journal.py::test_permalink_encodes_the_fragment` |
| F-11 | `dashboard.py` docstring described a theme removed 2026-07-26 | docs | S3 | CLOSED | n/a — prose, see note |
| F-12 | `blog.html`/`journal.html` link to `index.html`, absent locally | dev | S3 | OPEN | — |
| F-13 | `journal.html` renders every entry, unboundedly | journal | S3 | OPEN | — |
| F-14 | `/healthz` measures the host's CWD, not the fixture | publisher | S2 | OPEN | — |

Open: **3** (F-12, F-13, F-14). All recorded rather than fixed — see
"Deliberately not fixed".

## Root-cause clusters

Findings were grouped by mechanism before anything was changed, and fixed one
cluster per commit. Fixing in discovery order would have produced four patches
to the same underlying mistake.

### Cluster A — bound once, swapped later (F-04, F-05, F-06)

One cause. `JS` attached listeners with
`querySelectorAll(...).forEach(el => el.addEventListener(...))` at load, and
`LIVE_JS`'s `swap()` replaces the `innerHTML` of ten volatile regions on the
first poll that sees a new hash. Every element carrying a listener lived inside
one of those regions: the chips in `decisions`, all 1,239 `[data-tip]` hover
targets in the three chart regions, the `[data-count]` figure in `hero`.
Replacing `innerHTML` destroys the element the listener was attached to.

Sixty seconds after load, on a page whose entire premise is staying current
without a reload, filtering and every chart tooltip were dead. Nothing surfaced
it: no console error, and the chips still changed colour on hover because that
is CSS.

Fixed by **delegating to `document`** rather than by remembering to rebind —
`document` is the one node `swap()` never touches, so the fix survives future
region changes too. The count-up is the one thing that genuinely must re-run
for a replaced figure, so `swap()` calls a single idempotent hook.

Measured, same page, same 60s poll:

| | before the poll | after the poll (pre-fix) | after the poll (fixed) |
|---|---|---|---|
| chip click → visible rows | 7 → 1 | 8 → 8 (no effect) | 8 → 1 |
| chip takes `.on` | yes | **no** | yes |
| tooltip text on hover | populated | **empty** | `2025-02-07 · +$0.00` |

### Cluster B — the failure note was sampled, not displayed (F-07)

`paint(note)` was called from two places: the poll (with a real note) and a 30s
repaint interval (with `''`). The repaint therefore erased "update check
failed" thirty seconds after every failed poll and the next poll restored it a
minute later. A page whose update path had been broken for hours looked
healthy for roughly half of every minute.

Measured against a 404 sidecar:

```
 4s..19s  live · 16m old · update check failed
57s       live · 17m old              <- repaint wiped it
```

This is a near-miss on the module's own stated intent. `LIVE_JS`'s comment
says the badge is repainted on every tick precisely so that "if polling breaks,
the badge still goes amber and then red on schedule" — the *colour* was always
computed correctly. The *explanation* was not. Fixed by making the note sticky:
`paint()` with no argument repaints age and colour without clearing it.

### Cluster C — a filter with nothing to say (F-08)

The decisions table renders only the last `N_DECISIONS = 30` decisions, and
executed trades are ~3% of decisions, so "Executed" and "Vetoed" routinely
match nothing in the visible window — an ordinary day, not an edge case.
Selecting one hid every row and left a table header above an empty void, which
reads as a page that failed to load. Fixed with a `#nomatch` row, rendered
hidden, carrying no `r-*` class so no filter can ever match it.

### Cluster D — URLs built by string concatenation (F-09, F-10)

Two sites, one habit: a URL assembled with an f-string and no validation or
encoding.

`blog.py` escaped a post's *text* and then built `<a href="{url}">` from the
same record with no scheme check, so a post whose link was
`javascript:alert(1)` rendered as an ordinary clickable "full write-up →" on a
public page. Confirmed against the hostile fixture: 14 such anchors, with text
escaping clean everywhere else. Not reachable by an attacker today — every link
is written by this bot from its own config — which is exactly why the allowlist
is cheap now and expensive later.

`journal.html#<trade_id>` is the only deep-link surface in the project and both
callers built it with `f"{base}#{trade_id}"`. A `#` inside a trade id truncates
the fragment at the first `#`, so the browser looks for an element named `t`,
finds none, and silently leaves the reader at the top of a page of hundreds of
entries. Verified in a browser: the article exists under id `t#0001 spaced`,
and the naive link delivers the fragment `t`. Fixed with one
`journal.permalink()` helper used by both call sites; percent-encoding works
because browsers decode the fragment before matching it against the id.

### Cluster E — documentation that outlived the code (F-11)

`src/dashboard.py`'s docstring described a "dark trading-terminal theme" for
two weeks after the page was migrated to white on 2026-07-26, while the palette
constants twelve lines below said `SURFACE = "#ffffff"`.

## Deliberately not fixed

**F-12 — `blog.html` and `journal.html` link to `index.html`, which does not
exist in a local render.** `scripts/publish_dashboard.sh` renames
`dashboard.html` → `index.html` on the way to GitHub Pages, so those links
resolve *only* on the published site. Locally they 404. This is correct for
production and wrong for anyone opening a rendered site by hand. Fixing it
means either renaming the local artifact or making the link conditional, and
both change publish behaviour — out of scope for a QA sweep, and the publish
path is the one place in this repo where a mistake is public. Recorded, and
`scripts/qa_render.py --layout {published,repo}` now makes the difference
visible instead of accidental.

**F-14 — `/healthz` reports on the process's working directory, not on the
data it was pointed at.** `src/health.py:20` sets
`HEARTBEAT_FILE = "memory/heartbeat"` as a module constant, and nothing in the
config can redirect it. `scripts/qa_sweep.py` rewires every store to the
fixture, but the health endpoint keeps reading whatever `memory/heartbeat`
happens to sit next to the process.

The same fixture and the same command give different answers:

```
$ python scripts/qa_sweep.py --fixture /tmp/qa/full     # with ./memory/heartbeat
58 passed, 0 FAILED
$ rm -rf memory && python scripts/qa_sweep.py --fixture /tmp/qa/full
57 passed, 1 FAILED          # PUB-04, HTTP 503, heartbeat_age_hours: null
```

So `PUB-04` has never measured the fixture — it measures the host, and it
passed historically because the sweep was run from a live checkout that had a
real heartbeat. This is the failure class already recorded in this project as
"CI measures the laptop", and it is the same CWD-relative-path mistake that
`src/sitepaths.py` was created to fix for the published artifacts; health was
never migrated.

Not fixed here. `src/health.py` feeds the alerting path and `docs/runbooks.md`,
so changing how it resolves paths is an operational change that needs its own
divergence check — outside a QA sweep's remit. Until then, a clean publisher
run means **58/58 from a checkout with a current `memory/heartbeat`**; without
one the delta is exactly PUB-04 and nothing else.

**F-13 — `journal.html` renders every entry ever written, with no paging.**
At the fixture's production scale (918 entries over 18 months) the page is
469 KB; the live journal is 42 entries. Growth is linear and unbounded — at
three years it would be around 1.5 MB of HTML on every page load. Not a defect
today, and any fix is a product decision about what the journal is for.

## Method notes

Two failures of this sweep's own instrumentation are recorded because they
change how much the passes are worth.

**A guard test that shares the guard's arithmetic proves nothing.** F-03's
first test computed its target the same wrong way the guard did, so it asserted
a refusal for a directory nobody publishes from while the directory that
mattered was open. Every guard here was subsequently verified by running the
destructive command for real against the live path.

**A criterion can pass because the fixture never produced the input.**
`SITE-JOUR-01` passed on its first run: the fragment-hostile trade id was keyed
to `d == 1` and no trade executed on day 1, so the id was never generated.
`tests/test_qa_fixture_shapes.py::test_the_hostile_profile_emits_every_edge_case_it_claims`
now asserts each hostile input is present in the fixture, so the criteria
cannot pass by absence.

**`git status` does not tell you whether you touched anything.** `memory/`,
`.site/` and the rendered artifacts are all gitignored. Two mutation runs wrote
into a worktree's `memory/` and the tree stayed clean. Checkpoints in this
sweep verify live artifact mtimes explicitly, not `git status`.
