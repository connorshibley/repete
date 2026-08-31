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
| F-14 | `/healthz` measures the host's CWD, not the fixture | publisher | S2 | CLOSED | `test_state_paths_are_one_answer.py::test_the_writer_and_both_readers_use_the_same_file` |
| F-15 | `SITE-DASH-DETAILS-01` asserted a page two PRs old; the sweep sat red on every profile with nobody running it | tooling | S2 | CLOSED | `qa_site_sweep.py` per-section criterion; 18/18 ×3 + 12/12 rerun 2026-08-30 |
| F-16 | `/healthz` under the sweep inherited two live-host checks (mirror receipt, judge probe); PUB-04 red on every fixture | tooling | S2 | CLOSED | `qa_sweep.build_client` hermetic switches; 58/58 rerun 2026-08-30 |
| F-17 | One unbroken token widens the whole blog page (40,316px measured); journal shared the gap latently | blog, journal | S2 | CLOSED | `test_page_contracts_browser_pinned.py::test_blog_and_journal_break_long_tokens` |
| F-18 | No browser-native sign-in path: `/auth/request-link` is JSON-only and no page renders an email form | publisher | S3 | OPEN | — |
| F-19 | `/account`, the post-login landing every magic link 303s to, renders raw JSON | publisher | S3 | OPEN | — |

Open: **4** (F-12, F-13, F-18, F-19). All S3, all recorded rather than fixed —
see "Deliberately not fixed". F-18/F-19 are product decisions from the
2026-08-30 browser audit: both flows WORK (verified end-to-end in a real
browser), they are just not shaped for a human arriving without a link.

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

### Cluster F — a monitor answering about the wrong machine (F-14)

`src/health.py` held `HEARTBEAT_FILE = "memory/heartbeat"` and
`HALT_FILE = "HALT"` as module constants no config could redirect, five lines
from a `cfg["memory"]["ledger_path"]` that *was* config-driven. So `/healthz`
reported on whatever sat next to the process:

```
$ python scripts/qa_sweep.py --fixture /tmp/qa/full     # with ./memory/heartbeat
58 passed, 0 FAILED
$ rm -rf memory && python scripts/qa_sweep.py --fixture /tmp/qa/full
57 passed, 1 FAILED          # PUB-04, HTTP 503, heartbeat_age_hours: null
```

`PUB-04` had never measured the fixture. It measured the host, and passed
historically because the sweep ran from a live checkout. Same class as this
project's "CI measures the laptop" note, and the same CWD-relative mistake
`src/sitepaths.py` was created to fix for the published artifacts — state was
never migrated.

The deeper problem was duplication: **three** modules held their own copy of
`"memory/heartbeat"` — the writer in `main.py`, readers in `health.py` and
`watchdog.py` — with nothing making them agree.

Fixed with `src/statepaths.py`: one definition, resolvers that are total (they
run inside `run_cycle`'s `finally:` and inside `/healthz`, so anything they
raised would replace a real crash with a path error) and that never touch the
filesystem (health is read-only under invariant 9, which is why this is not two
more functions in `sitepaths`, whose `resolve()` deliberately mkdirs).

**HALT is deliberately half-migrated, and that is the interesting part.** The
monitors resolve it from config; `risk.check_halt()` — the trading rail — does
not, because a config key able to move the kill switch is a way to disable it,
and `scripts/halt.py:80` had already settled the question: *"a HALT engaged into
some other directory is a stop button wired to nothing."* The asymmetry is made
safe by `preflight._state_path_fails`, which refuses to start a cycle when
either key is non-default — so a process that trades cannot have its monitor and
its kill switch reading different files. The keys are QA-only by enforcement,
not by convention.

Closure, with positive controls on both fields — the sweep is now sensitive to
the fixture and insensitive to the host, which is a stronger claim than "it
passes":

| scenario | result |
|---|---|
| no `./memory`, no `./HALT` | 58/58 |
| **host** HALT engaged | 58/58 — the host no longer leaks in |
| fixture heartbeat removed | 57/58, `heartbeat_age_hours: null` |
| HALT inside the **fixture** | 57/58, `"halted": true` |

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

**F-13 — `journal.html` renders every entry ever written, with no paging.**
At the fixture's production scale (918 entries over 18 months) the page is
469 KB; the live journal is 42 entries. Growth is linear and unbounded — at
three years it would be around 1.5 MB of HTML on every page load. Not a defect
today, and any fix is a product decision about what the journal is for.

## F-15 — the sweep asserted a page two PRs old (S2, tooling, CLOSED)

**Found 2026-08-30, in the first minute of the audit's baseline gate.**
`SITE-DASH-DETAILS-01` failed on **all four profiles**: it asserted "2
details, both open", written for the page as of PR #111. PR #123 added the
first-time-visitor explainer — a third `<details>`, deliberately collapsed —
and the criterion was never updated.

The finding is not the stale criterion; it is what the all-profiles failure
proves: **nobody had run the static sweep since #123 merged.** The sweep is a
script chain, not a pytest, so its own redness was invisible — the same shape
as the 171 `noop` capture rows: an instrument nobody reads is not evidence.

Closed by rewriting the criterion per-section (explainer closed, decisions
and lessons open — a new details element is now a conscious edit, not a
blanket pass/fail) and syncing the inventory row. Reruns: 18/18 ×3 + 12/12.

## F-16 — the fixture healthz inherited the host (S2, tooling, CLOSED)

**Found immediately after F-15**: PUB-04 red — `/healthz` 503 on every
fixture. Two health checks added after the sweep last ran both read the HOST:
`ops.require_offhost_mirror` (a receipt only production has) and the judge
reachability probe of 2026-08-29 (a network round-trip to a Docker-network
hostname only production resolves). F-14's exact lesson, learned twice more,
which is why the fix sits beside F-14's in `build_client` with both switched
off and a comment naming this entry.

One more instrument lesson from the fix itself: the first patch set
`ops.offhost_mirror_required` — a key `health.py` never reads — and **failed
silently**. The criterion staying red is what caught the typo. A config key
with no reader is indistinguishable from a fix.

## F-17 — one unbroken token widens the whole blog (S2, blog+journal, CLOSED)

**Found in the browser, hostile profile, 2026-08-30.** A 4,000-char unbroken
string in a post's market-context line rendered `blog.html` at
**40,316px wide** (viewport 1,280px — 39,036px of overflow, measured via
`document.documentElement.scrollWidth`). Every post on the page becomes
horizontally scrolling text. The realistic trigger is one long URL in a
headline or post body — this does not need an attacker.

The dashboard survives the same content class via per-table
`overflow-x:auto` wrappers. The journal *looked* immune but only because the
hostile fixture routes its long payload to a blog-only field — the CSS gap
was identical. Closed with `overflow-wrap:anywhere` on `body` in both
stylesheets, pinned by
`test_page_contracts_browser_pinned.py::test_blog_and_journal_break_long_tokens`,
which records the measured number so the next reader knows what it costs.

## F-18 / F-19 — the publisher works, but only for a robot (S3, OPEN)

From the first-ever browser session against the publisher (2026-08-30; every
prior verification was an in-process TestClient). Both flows FUNCTION — these
are shape observations, held open as product decisions in the F-12/F-13
manner:

- **F-18**: there is no way to sign in from a browser. `/auth/request-link`
  accepts only a JSON POST and no rendered page contains an email form. A
  human who lands on the marketing page cannot start the magic-link flow
  without devtools.
- **F-19**: `/account` — where every magic link 303s to — returns raw JSON.
  The first thing a newly signed-in subscriber sees is
  `{"email":...,"tier":"free","status":"active"}`.

What WAS verified working end-to-end in the browser, for the record: verify →
303 → HttpOnly cookie → tier-scoped dashboard (free: delay notice + reasoning
withheld; paid: same-day + bull/bear + confidence); token single-use (reuse →
400 undifferentiated); unsubscribe GET-peek idempotent, POST → 303 →
tokenless done, spent token → undifferentiated page naming no address;
checkout 403-with-reasons gate-closed / stub gate-open, with the 403's
reasons matching the dashboard's gate-unmet list verbatim; rate limiter 200
×5 then 429; zero JS console errors on every page at desktop and mobile
widths; every page contained at 375px.

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

**Testing an injectable seam says nothing about the entrypoint.** The F-14 guard
first called `watchdog.check()` with explicit paths — so the mutation reverting
`watchdog.main()` to a bare `check()`, which is exactly half the bug, SURVIVED.
Proving the seam works is not proving production uses it. Two tests against
`main()` itself (one positive, one with a decoy in the cwd) now catch it.

**A design report's claims are data until checked in the files.** The
2026-08-30 audit's design pass reported the fixture emails as
`@qa.example.invalid`; they are `@example.invalid`. The first paid-tier
browser walk logged in as accounts that did not exist, the system quietly
auto-provisioned them as new free subscribers (the unified passwordless
signup path — working as designed), and the paid dashboard "failed" for two
servers' worth of investigation before the DB query showed the audit was
testing users it had invented. The generator had even printed the right
domain during Phase A.

**One pass of a sampled judge is a draw, not a rate** — recorded at §79 for
the trading side, and it held here too: the count-up "frozen mid-animation"
observation was browser rAF-throttling in a hidden pane, not a page bug, and
would have been filed as one if the second look hadn't been taken.

**`git status` does not tell you whether you touched anything.** `memory/`,
`.site/` and the rendered artifacts are all gitignored. Two mutation runs wrote
into a worktree's `memory/` and the tree stayed clean. Checkpoints in this
sweep verify live artifact mtimes explicitly, not `git status`.
