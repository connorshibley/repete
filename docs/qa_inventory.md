# QA acceptance inventory

Every user-facing feature, state and control on the two published surfaces,
with the criterion that decides whether it works and the edge cases worth
spending a check on. Companion to `docs/qa_findings.md`, which records what
failed these.

`tests/test_qa_inventory_is_current.py` pins this file **in both directions**:
every registered criterion appears here, and every criterion here maps to
something that actually runs. A criterion that is documented and never run
reads as coverage, which is worse than an admitted gap.

## How to reproduce the whole thing

```bash
REPO=/path/to/repete            # work in a worktree; the root is live
PY=$REPO/.venv/bin/python

for P in full thin empty hostile; do
  $PY scripts/qa_fixture.py --out /tmp/qa/$P --profile $P --anchor 2026-08-11
  $PY scripts/qa_render.py  --fixture /tmp/qa/$P --out /tmp/site/$P
  $PY scripts/qa_site_sweep.py --site /tmp/site/$P --fixture /tmp/qa/$P --profile $P
done
$PY scripts/qa_sweep.py --fixture /tmp/qa/full --verbose      # the publisher
```

Exit code of either sweep is the number of failed criteria.

## Fixture profiles

One fixture cannot cover the state space. At 455 closed trades every "not yet
meaningful" guard is satisfied and every empty state is unreachable, so the
copy a brand-new visitor sees is exactly the copy nothing exercises.

| Profile | Shape | What only it can reach |
|---|---|---|
| `full` | 18,877 records, 455 closed, 549 days | production scale; revenue gate OPEN; every populated region |
| `thin` | 58 records, 2 closed, 11 days | pending ratio cards (`MIN_CLOSED_FOR_RATIOS=10`), the n<5 trade list, gate CLOSED |
| `empty` | 0 records | every "No X yet" string, the <2-point chart copy |
| `hostile` | 118 records, adversarial fields | markup-shaped strings, quotes, RTL overrides, a 4,000-char field, a `javascript:` link, a fragment-hostile trade id |

Seeded and anchored, so the same command reproduces the same bytes (excluding
`publisher_data/pub.db`, which stamps grant expiries from the wall clock).

## Verification method

- **python** — `scripts/qa_site_sweep.py`, parsed DOM via stdlib `html.parser`.
  Never executes JavaScript.
- **browser** — driven against a copy served over loopback HTTP. Required for
  anything whose truth depends on JS running; polling is disabled entirely on
  `file:`, so these cannot be checked by opening the file.
- **structural** — a check on the emitted JS *text*. Recorded as structural,
  never as behavioural: it proves the page is built correctly, not that it
  behaves correctly.

## Static site — checked in Python

| ID | Surface | Role | Profiles | Acceptance criterion | Method |
|---|---|---|---|---|---|
| `SITE-DASH-RGN-01` | dashboard | visitor | all | every region in the JSON sidecar has a mount point, and every mount point has a region | python |
| `SITE-DASH-RGN-02` | dashboard | visitor | all | the sidecar hash is the sha256 of its own regions | python |
| `SITE-DASH-RGN-03` | dashboard | visitor | all | the badge's baked-in hash equals the sidecar's, so the first poll is a no-op | python |
| `SITE-DASH-ID-01` | dashboard | visitor | all | no id appears twice in the document | python |
| `SITE-DASH-BIND-01` | dashboard | visitor | all | interaction handlers are delegated to `document`, so a region swap cannot orphan them | structural |
| `SITE-DASH-BIND-02` | dashboard | visitor | full/thin/hostile | a replaced hero figure gets re-animated after a swap | structural |
| `SITE-DASH-CHIP-01` | dashboard | visitor | full/thin/hostile | exactly the six documented filter chips render, one marked on | python |
| `SITE-DASH-CHIP-02` | dashboard | visitor | full/thin/hostile | a filter with no matches has something to say for itself | python |
| `SITE-DASH-DETAILS-01` | dashboard | visitor | all | both collapsible sections render and start open | python |
| `SITE-DASH-TBL-01` | dashboard | visitor | full/thin/hostile | every table's body rows carry as many cells as its header, counting colspans | python |
| `SITE-PAPER-01` | all pages | visitor | all | every page discloses `[PAPER]` | python |
| `SITE-DISC-01` | all pages | visitor | all | every page carries the disclaimer from `src/disclaimer.py` | python |
| `SITE-XSURF-01` | dashboard vs journal | visitor | full/thin | the closed-trade count agrees across ledger, dashboard card and journal | python |
| `SITE-LINK-01` | all pages | visitor | all | every relative link resolves to a file present in the site | python |
| `SITE-LIVE-01` | dashboard | visitor | all | the self-refresh script is present with its configured thresholds | python |
| `SITE-ESC-01` | all pages | attacker | hostile | no fixture-supplied string becomes markup | python |
| `SITE-ESC-02` | blog | attacker | hostile/full | no anchor carries a dangerous URL scheme | python |
| `SITE-JOUR-01` | journal | visitor | full/hostile | the permalink the bot publishes resolves to the entry it names | python |
| `SITE-SAN-01` | all pages | visitor | all | no rendered artifact carries a personal identifier | python |
| `SITE-EMPTY-01` | all pages | visitor | empty | day one renders every empty-state message instead of a blank or a zero | python |
| `SITE-THIN-01` | dashboard | visitor | thin | ratios below the minimum sample render as pending, not as a number | python |
| `SITE-THIN-02` | dashboard | visitor | thin | the trade chart explains itself below five closed trades | python |

**22 criteria.** Notable edge cases they carry: the positions table has two
different column sets (marked vs unmarked) plus a colspan total row; the
collapsed hold run is a `colspan=7` row that survives the "Skipped" filter;
the `#nomatch` row must carry no `r-*` class or a filter could match it.

## Static site — requires a real browser

These are not checkable in Python and are not claimed to be. Each was verified
by driving a served copy; the reproduction is in `docs/qa_findings.md`.

| ID | Criterion | Evidence |
|---|---|---|
| `SITE-BROWSER-01` | filter chips still filter after a poll replaces the decisions region | 8 rows → 1, chip takes `.on` |
| `SITE-BROWSER-02` | chart tooltips still populate after a poll replaces the chart regions | `#tip` reads `2025-02-07 · +$0.00` |
| `SITE-BROWSER-03` | the hero figure re-animates after the hero region is replaced | `data-counted` set on the new element |
| `SITE-BROWSER-04` | the poll swaps changed regions in place without a reload | injected row appears, scroll position kept |
| `SITE-BROWSER-05` | badge is green fresh, amber ≥8h, red ≥24h | `--age-hours 9` → amber, `25` → red |
| `SITE-BROWSER-06` | a failed poll says so, and keeps saying so across repaints | 9/9 samples carry the note post-fix |
| `SITE-BROWSER-07` | boot splash plays once per session, then never again | `sessionStorage.repete_boot` |
| `SITE-BROWSER-08` | an empty filter result shows the no-match row | row visible, then hidden again on "All" |
| `SITE-BROWSER-09` | no console errors on any page at any viewport | `read_console_messages(onlyErrors)` empty |
| `SITE-BROWSER-10` | opened as `file:`, the page says auto-update is unavailable and fetches nothing | `location.protocol` branch |

## Known coverage gaps

Recorded rather than quietly skipped.

- **`prefers-reduced-motion`** — the branch runs in the inline script at parse
  time, before anything can set the emulated preference, so it is pinned by
  `tests/test_dashboard_swing.py:206` structurally and has never been observed
  in a browser.
- **Touch and keyboard tooltips** — `[data-tip]` is bound to pointer events
  only. There is no keyboard or touch path to a tooltip's content at all. Not
  a regression; an accessibility gap that predates this sweep and is not
  fixed by it.
- **The publisher's rendered HTML** — checked structurally by
  `scripts/qa_sweep.py` through an in-process `TestClient`. It has never been
  loaded in a browser, so its CSS, console and form submission are unverified.
  Deliberate: serving it over HTTP would expose a mail-sending endpoint.
- **`SITE-XSURF-01` on `hostile`** — excluded, because the hostile profile
  deliberately contains zero-qty and negative-price rows whose "correct"
  aggregate is undefined.

## Publisher — existing criteria

`scripts/qa_sweep.py` registers 35 criteria across five roles (`anon`, `free`,
`paid`, `expired`, `unsubscribed`) plus an `attacker` role. IDs are reused
verbatim from that file — renaming them would orphan every reference in it and
in `tests/test_publisher.py`.

Listed individually, not as ranges: a range hides which IDs actually exist,
and the pin test cannot check what it cannot see.

| ID | Route / subject |
|---|---|
| `PUB-01`, `PUB-02` | `/` marketing page |
| `PUB-03` | `/status` |
| `PUB-04` | `/healthz` |
| `LEG-404` | `/legal/{page}` |
| `AUTH-OK`, `AUTH-NOLEAK` | `/auth/request-link` |
| `TOK-01`, `TOK-02`, `TOK-03`, `TOK-04` | `/auth/verify` |
| `AUTHZ-unsub` | `/unsubscribe` requires a session |
| `AUTHZ-checkout` | `/billing/checkout` requires a session |
| `TIER-LEAK-anon`, `TIER-LEAK-free`, `TIER-LEAK-expired` | `/feed` withholds paid content |
| `TIER-PAID` | `/feed` serves paid content to paid |
| `TIME-01` | `/feed` free-tier delay |
| `GATE-CLOSED`, `GATE-REASONS`, `GATE-OPEN` | `/billing/checkout` revenue gate |
| `WEBHOOK-NOGRANT` | `/billing/webhook` grants nothing unsigned |
| `UNSUB-01` | `/unsubscribe` three-step flow |
| `RO-01` | `ReadOnlyLedger` refuses writes (invariant 9) |
| `NORM-01`, `NORM-02` | `/account` email normalisation |
| `RL-01` | rate limiting on `/auth/request-link` |
| `SCALE-01` | `/feed` at production scale |
| `XSURF-01` | review output vs `/feed` |
| `DIGEST-01`, `DIGEST-02`, `DIGEST-03`, `DIGEST-04`, `DIGEST-05`, `DIGEST-06` | broadcast, digest and unsubscribe-by-token |

The paid path is structurally unreachable on live data — `publisher/gates.py`
requires 30 closed trades and 90 days, and the live ledger has 11 over 28 days
— so the `full` fixture is the only way any of it has ever been exercised.
