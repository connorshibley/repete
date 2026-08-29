# Archify maps

Self-contained, interactive HTML system maps. No hosted runtime, no network calls —
open the `.html` directly in a browser.

The `.json` file is the source of truth; the `.html` is generated from it.

## Regenerate

```bash
node ~/.claude/skills/archify/bin/archify.mjs deliver sequence \
  docs/maps/repete-cycle.sequence.json docs/maps/repete-cycle.html \
  --quality showcase --json
```

`deliver` is the acceptance gate. A non-zero exit is a failure — do not treat it as
success. A showcase pass reports all 9 artifact checks with 0 errors and 0 warnings.

**A failed `deliver` leaves the previous HTML in place.** Always check `deliver ok`
before measuring anything from the output, or you will diagnose a stale artifact.

## Current maps

| Map | Type | Source of truth |
|---|---|---|
| `repete-cycle.html` | sequence | `src/main.py::_run_cycle` call order, L1303–L2036 |

Every message label carries the line number it was read from. If `_run_cycle` is
reordered, the map is stale — regenerate it and re-verify the line numbers.

## House style

These maps are engineering reference, not decoration. The rules exist because the
first draft broke all of them and looked generated.

1. **Omit `visual_preset`.** The renderer opens in `classic`. Author `signal-flow`,
   `blueprint`, or `editorial` only when explicitly asked — archify's own authoring
   contract says the same thing.
2. **Omit `subtitle`.** Provenance belongs in one `Source` card, not the header.
3. **Type by role, not for variety.** Repeated roles share a colour: two third-party
   systems are both `external`, two internal computations are both `backend`. If
   every participant has a different `type`, the colour is decoration and carries
   no information.
4. **One accent, maximum.** Reserve `security` for the thing that can actually abort
   or block. Everything else is `default` / `return`. Colour has to earn its place.
5. **At most one card, and only for checkable facts** — source path, line range, how
   the order was established. No three-card sets, no three-bullet symmetry, no
   alliterative headers, no coloured dots used as decoration. Use `slate`.
6. **Cite line numbers in labels** so staleness is checkable by a reader who does
   not trust the diagram.

## Diagram types

`architecture` · `workflow` · `sequence` · `dataflow` · `lifecycle`

Use `sequence` for an ordered call chain — it takes explicit y coordinates and has no
column cap. `workflow` is a lane/column grid **capped at 6 columns**; it is the wrong
shape for a long linear chain, and narrowing nodes does not help because the canvas
auto-sizes and the column pitch shrinks with it.

Ask: `node ~/.claude/skills/archify/bin/archify.mjs guide "<scenario>" --json`

## What this is not

Archify draws **system structure**, not data. Equity curves, drawdown plots, and
return distributions are not diagrams — use the `dataviz` skill with matplotlib or
plotly locally. Do not send bot performance data to a hosted chart API.

## On the dashboard

The map is inlined into the dashboard as a static SVG at `src/assets/system_map.svg`.
Inlined rather than linked for the reason the dashboard's own asset loader
gives: the publish script copies hardcoded filenames, so a linked SVG would
render as a broken image on the live site one commit out of date.

**Two steps, and the second is easy to forget:**

```bash
node ~/.claude/skills/archify/bin/archify.mjs deliver sequence \
  docs/maps/repete-cycle.sequence.json docs/maps/repete-cycle.html \
  --quality showcase --json
python3 scripts/build_map_svg.py docs/maps/repete-cycle.html src/assets/system_map.svg
```

Regenerating the artifact alone does **not** update the dashboard. Nothing
enforces this — the dashboard will happily keep serving the previous map.

`scripts/build_map_svg.py` does more than lift the `<svg>` out:

- pulls **both** palettes, dark behind `prefers-color-scheme`, so the map
  follows the page theme instead of staying light on a dark ground;
- namespaces every rule under `.archify-map` so it cannot collide with the
  dashboard stylesheet;
- strips `tabindex` / `role="button"` / `aria-*` state. Those are viewer
  affordances that do nothing in a static page but imply a control that acts.

