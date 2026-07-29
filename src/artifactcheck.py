"""Does the artifact about to be published match the record it claims to show?

    python -m artifactcheck            # exit 0 = safe to publish, 1 = refuse

`scripts/publish_dashboard.sh` compares each artifact against its `.site/` twin
and pushes on ANY difference. Difference is not the same as improvement, and on
2026-07-28 the difference was destruction: the repo-root `blog.html` held 0
posts against 33 in `memory/posts.jsonl`, and `journal.html` held 1 entry
against 19. Both were test fixtures written by a pytest run whose CWD was the
repo root. The next scheduled cycle would have pushed them over an eleven-day
public archive.

`src/sitepaths.py` fixed the cause. This is the second, independent line: even
if something else writes a bad artifact tomorrow, it does not get published.
Two mechanisms, because the incident proved the failure is silent — the files
are gitignored, so `git status --porcelain` stays clean and `src/deploycheck.py`
cannot see them.

THE INVARIANT IS EQUALITY, NOT A SIZE HEURISTIC. Each renderer emits exactly
one marker per stored record — `blog.py` writes `<div class=post>` per post,
`journal.py` writes `<article` per entry — so `rendered == stored` is checkable
rather than approximate, the same discipline as the §40 block census. A
threshold like "at least 80% of the previous size" would have passed a fixture
that happened to be verbose, and would fail honestly on the day an old post is
deleted.

FAIL-CLOSED, unlike src/deploycheck.py. That polarity is deliberate and the
opposite of the drift guard's: a drift alarm that fires on unknowns trains the
owner to ignore the channel, but an artifact check that cannot read its own
store must never conclude "safe to publish". If the store is unreadable we
refuse and say why.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sitepaths

# artifact filename -> (store config path, per-record marker in the HTML)
CHECKS = (
    ("blog.html", ("x_posting", "posts_log_path"), "memory/posts.jsonl",
     "<div class=post>"),
    ("journal.html", ("x_posting", "journal_path"), "memory/journal.jsonl",
     "<article"),
)


def _store_count(path: str) -> int | None:
    """Non-blank JSONL records, or None if the store cannot be read.

    None is distinct from 0. An empty store is a real state (nothing posted
    yet); an unreadable one is a failure to measure, and the two must not
    collapse into the same answer — that conflation is what let the frozen
    journal look healthy for four days.
    """
    try:
        with open(path) as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return None


def problems(cfg: dict, out_dir: str | None = None) -> list[str]:
    """Reasons the current artifacts must NOT be published. Empty = safe."""
    d = out_dir if out_dir is not None else sitepaths.out_dir(cfg)
    out: list[str] = []

    for name, (section, key), default, marker in CHECKS:
        artifact = os.path.join(d, name)
        if not os.path.exists(artifact):
            continue          # publish script skips missing files; not a fault

        store_path = cfg.get(section, {}).get(key, default)
        stored = _store_count(store_path)
        if stored is None:
            out.append(f"{name}: cannot read {store_path} — refusing to "
                       f"publish rather than guess")
            continue

        try:
            rendered = open(artifact).read().count(marker)
        except OSError as e:
            out.append(f"{name}: cannot read the artifact ({e})")
            continue

        if rendered != stored:
            out.append(
                f"{name}: renders {rendered} of {stored} record(s) in "
                f"{store_path} — the artifact and the record disagree")
    return out


def main() -> int:
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    found = problems(cfg)
    for p in found:
        print(f"REFUSE: {p}", file=sys.stderr)
    if not found:
        print("artifacts match the record")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
