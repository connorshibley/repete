#!/usr/bin/env python3
"""Render the public site from a QA fixture, into a scratch directory.

Why this is a separate script and not a --fixture flag on dashboard.py
----------------------------------------------------------------------
The seam already exists: `dashboard.render(cfg, out_path)`, `blog.render(...)`
and `journal.render(...)` all take a config dict and honour
`sitepaths.resolve()`. What was missing was a CALLER, not a flag — nothing
outside pytest ever built a config pointing at anything but `config.yaml`, so
`python src/dashboard.py` renders from the LIVE memory/ into the repo root
(`publish.out_dir: "."`) and overwrites the published artifacts.

A flag would also have to live inside the exact module behind the 2026-07-28
artifact incident, and it could not do the four things this actually needs:
set `publish.out_dir` so the JSON sidecar follows its HTML, reproduce the
PUBLISHED layout, pass spy_bars, and override the journal URL base so the
owner's GitHub handle stays out of QA output.

The precedent is `scripts/qa_sweep.py:build_client()`, which deep-copies the
publisher config and rewires paths at runtime. This is the same move against
config.yaml.

The published layout is not the rendered layout
-----------------------------------------------
`scripts/publish_dashboard.sh` renames dashboard.html -> index.html on the way
to GitHub Pages, and both `blog.py` and `journal.py` link back to
`index.html`. So a render into a directory containing `dashboard.html` is a
layout that never ships, and testing it hides the fact that those two links
are dead everywhere except the published site. `--layout` makes the difference
explicit and testable instead of accidental.

    python scripts/qa_render.py --fixture /tmp/qa/full --out /tmp/site/full
    python scripts/qa_render.py --fixture ... --out ... --age-hours 25
    python scripts/qa_render.py --fixture ... --out ... --break-sidecar 404
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from qa_fixture import _guard_not_live, _repo_roots  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
# Neutral stand-in for config.yaml's journal_url_base, which carries the
# owner's GitHub handle into every rendered permalink.
QA_URL_BASE = "https://qa.example.invalid/journal.html"


def _guard_out_dir(out: str):
    """A QA render must be unable to resolve into a live tree.

    `_guard_not_live` already refuses memory/, .site/, publisher_data/ and every
    repo root it can see. This adds the reason specific to rendering: the
    artifacts are GITIGNORED, so a bad write leaves `git status` clean and
    src/deploycheck.py structurally cannot notice — the same blindness that let
    the 2026-07-28 incident run for days.
    """
    _guard_not_live(out)
    target = os.path.realpath(out)
    for root in _repo_roots():
        if target == os.path.realpath(root):
            raise SystemExit(f"REFUSING to render into the repo root: {target}")


def build_cfg(fixture: str, out: str) -> dict:
    """config.yaml with every store and the output directory redirected.

    Everything else — symbols, starting_equity, the learning thresholds — is
    left at its production value on purpose. The point is production-LIKE
    settings against sanitized data, not a second configuration nobody runs.
    """
    import yaml
    with open(CONFIG) as f:
        cfg = copy.deepcopy(yaml.safe_load(f))

    def _f(name):
        return os.path.join(fixture, name)

    cfg.setdefault("memory", {})["ledger_path"] = _f("ledger.jsonl")
    cfg["memory"]["learnings_path"] = _f("learnings.md")
    cfg.setdefault("learning", {})["lessons_path"] = _f("lessons.jsonl")
    cfg["learning"]["judgments_path"] = _f("judgments.jsonl")
    cfg.setdefault("x_posting", {})["posts_log_path"] = _f("posts.jsonl")
    cfg["x_posting"]["journal_path"] = _f("journal.jsonl")
    cfg["x_posting"]["journal_url_base"] = QA_URL_BASE
    cfg.setdefault("publish", {})["out_dir"] = out
    # The fixture is JSONL. dashboard.render() never calls store.configure(),
    # so the process-wide default already applies — pinned here so a future
    # config change to storage.backend cannot silently point the renderers at
    # an empty SQLite database instead of the fixture.
    cfg.setdefault("storage", {})["backend"] = "jsonl"
    return cfg


def _spy_bars(fixture: str) -> list[dict]:
    """SPY bars from the fixture, or [] — never None.

    [] and None mean different things to dashboard.render() as of 2026-08-21:
    None asks it to FETCH the benchmark itself (the fix for the S&P column the
    16:20 review kept wiping), while [] is the explicit opt-out. QA renders
    must stay offline and byte-reproducible, so a fixture with no
    spy_bars.json opts out rather than reaching for the network.
    """
    path = os.path.join(fixture, "spy_bars.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def age(out: str, hours: float, dash_name: str):
    """Backdate the generation stamp in the SCRATCH COPIES only.

    The freshness badge computes its colour from `data-gen` on every 30s tick
    (dashboard.py:405), so backdating the stamp puts the page straight into the
    amber (>=8h) or red (>=24h) state at load. That is the whole reason not to
    mock a clock or patch the page's JS: the page under test stays byte-for-byte
    the page that ships, and the state comes from its own data.
    """
    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    html_path = os.path.join(out, dash_name)
    body = open(html_path).read()
    body, n = re.subn(r'data-gen="[^"]*"', f'data-gen="{when}"', body)
    if n != 1:
        raise SystemExit(f"expected exactly one data-gen, found {n}")
    with open(html_path, "w") as f:
        f.write(body)

    side = os.path.join(out, "dashboard_data.json")
    data = json.load(open(side))
    data["generated_at"] = when
    with open(side, "w") as f:
        json.dump(data, f)


def break_sidecar(out: str, how: str):
    """Force the two failure branches of poll(): a non-ok response and a body
    that is not JSON. Both land in the same .catch and must show
    'update check failed' rather than a silently frozen page."""
    side = os.path.join(out, "dashboard_data.json")
    if how == "404":
        os.remove(side)
    else:
        body = open(side).read()
        with open(side, "w") as f:
            f.write(body[:len(body) // 2])       # truncated mid-object


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fixture", required=True, help="a scripts/qa_fixture.py output dir")
    p.add_argument("--out", required=True, help="scratch dir (NOT the repo)")
    p.add_argument("--layout", choices=("published", "repo"), default="published",
                   help="published = index.html (what GitHub Pages serves); "
                        "repo = dashboard.html (what a local render produces)")
    p.add_argument("--age-hours", type=float, default=None,
                   help="backdate the generation stamp to force a stale badge")
    p.add_argument("--break-sidecar", choices=("404", "malformed"), default=None)
    args = p.parse_args()

    _guard_out_dir(args.out)
    if not os.path.isdir(args.fixture):
        raise SystemExit(f"no such fixture directory: {args.fixture}")
    os.makedirs(args.out, exist_ok=True)

    import artifactcheck
    import blog
    import dashboard
    import journal

    cfg = build_cfg(args.fixture, args.out)
    dash_name = "index.html" if args.layout == "published" else "dashboard.html"

    # Production order: dashboard (with its sidecar), then blog, then journal —
    # the order src/main.py:1223-1231 uses.
    dashboard.render(cfg, out_path=os.path.join(args.out, dash_name),
                     spy_bars=_spy_bars(args.fixture))
    blog.render(cfg, out_path=os.path.join(args.out, "blog.html"))
    journal.render(cfg, out_path=os.path.join(args.out, "journal.html"))

    if args.age_hours is not None:
        age(args.out, args.age_hours, dash_name)
    if args.break_sidecar:
        break_sidecar(args.out, args.break_sidecar)

    # Held to the production publish bar, not a QA-only one: if the artifacts
    # and the stores disagree, this render is as unpublishable as a real one.
    found = artifactcheck.problems(cfg, out_dir=args.out)
    for f in found:
        print(f"REFUSE: {f}", file=sys.stderr)

    sizes = {n: os.path.getsize(os.path.join(args.out, n))
             for n in sorted(os.listdir(args.out))}
    print(f"rendered [{args.layout}] -> {args.out}")
    for n, s in sizes.items():
        print(f"  {s:>10,}  {n}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
