"""Where the public artifacts get written — one answer, read from config.

On 2026-07-28 `blog.html` and `journal.html` in the repo root were silently
replaced by test fixtures: 2,456 bytes where the published copy held 16,570,
and 2,533 where the published copy held 62,354. `scripts/publish_dashboard.sh`
compares each artifact against its `.site/` twin and pushes on any difference,
so the next scheduled cycle would have published four duplicate fake posts over
an eleven-day public archive.

The cause was not the test. Every one of the nine production render call sites
omitted `out_path`, so all of them fell back to a CWD-relative module constant
(`blog.OUT_PATH = "blog.html"`). Running pytest from the repo root put the CWD
at the repo root, and `tests/test_backfill_posts.py` reached `blog.render(cfg)`
through `backfill_posts.run()`. Any of the other eight call sites would have
done the same from the same directory.

The artifacts are gitignored, so `git status` stayed clean and
`src/deploycheck.py` — which reads `git status --porcelain` — was structurally
incapable of noticing. Nothing in the system could see it.

So the fix is a single resolution point rather than nine explicit arguments:
callers that pass `out_path` keep exact control, and callers that don't get the
configured directory instead of the process's working directory. Tests set
`publish.out_dir` to a tmp path in `tests/conftest.py`, which makes the
repo-root write unreachable from the fixture rather than merely unused by it.
"""
from __future__ import annotations

import os

DEFAULT_OUT_DIR = "."


def out_dir(cfg: dict | None) -> str:
    """Directory the public artifacts are written to. Defaults to the CWD,
    which is what production wants: run_cycle.sh cd's to the repo root."""
    if not cfg:
        return DEFAULT_OUT_DIR
    return cfg.get("publish", {}).get("out_dir") or DEFAULT_OUT_DIR


def resolve(cfg: dict | None, name: str) -> str:
    """Full path for artifact `name`, creating the directory if needed.

    mkdir here rather than at each call site: a configured directory that does
    not exist yet must not turn a cosmetic render into a cycle-breaking
    OSError.
    """
    d = out_dir(cfg)
    if d and d != DEFAULT_OUT_DIR:
        os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)
