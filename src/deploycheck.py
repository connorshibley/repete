"""Is the code that is RUNNING the code that was reviewed? (§26 divergence #7)

Why this module exists
----------------------
On 2026-07-25 an audit found the bot placing live orders was **57 commits and
8,151 lines behind** the repository. It was enforcing `max_open_positions: 5` —
a ceiling §13 had gated up to 8 two days earlier — and had refused 68 entries
because of it. Nothing noticed. Nothing could have: every check in this project
watched the *market* or the *ledger*, and none watched the deployment.

The first six sim/live divergences were all "the simulator models something the
world does differently." #7 was a new species: **repository versus reality.**
Every gate in `knowledge/backtest_candidates.md` had been correctly run,
correctly recorded and correctly merged — and none of it was running.

    A gate verdict is not in production until something checks that it is.

This is that something.

Fail-OPEN, deliberately — and the opposite polarity from preflight
-----------------------------------------------------------------
`src/preflight.py` is fail-SAFE: anything it cannot verify stops trading, because
the things it checks (interlock, risk params, ledger integrity) make trading
unsafe when wrong. Staleness is different. Running last week's gated config is
*wrong*, but it is not *dangerous* — the rails are all still there, just older.
Halting the bot over it would turn a reporting problem into a missed trading day,
which is a worse outcome than the disease.

So every layer here degrades to `None`/"unknown" and the cycle proceeds. The
signal is a `degradation` event plus an alert, the same fail-open pattern
`main.py` already uses for quote outages and vendor disagreements — where
silence must stay distinguishable from "checked and fine".

Containers
----------
The image ships without `.git`, so layer 1 reads **`REPETE_GIT_SHA`** first —
bake it at build time (`--build-arg`/`ENV`). Without it a container reports an
unknown sha and the guard degrades to config-drift only, which still catches the
case that actually happened.
"""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("deploycheck")

SHA_ENV = "REPETE_GIT_SHA"
_TIMEOUT = 5           # a hung git must never hold up a trading cycle


def _git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command, or return None for ANY failure.

    No git binary, no repo, detached HEAD, a hung network — all identical from
    here: unknown. Never raises; that is the whole contract.
    """
    try:
        out = subprocess.run(("git",) + args, cwd=cwd or _repo_root(),
                             capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception as e:                       # noqa: BLE001 — see docstring
        log.debug("deploycheck: git %s failed: %s", args[0], type(e).__name__)
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def running_sha() -> tuple[str | None, str]:
    """(sha, source). Env var wins so containers without .git still report."""
    env = (os.environ.get(SHA_ENV) or "").strip()
    if env:
        return env[:40], "env"
    sha = _git("rev-parse", "HEAD")
    return (sha[:40], "git") if sha else (None, "unknown")


def config_dirty(path: str = "config.yaml") -> bool | None:
    """True when the running config is NOT the committed one.

    The layer that would have caught divergence #7, and it needs no network:
    the running bot's `max_open_positions: 5` against a repo that said 8 is
    exactly a dirty/behind config. None means "could not tell".
    """
    out = _git("status", "--porcelain", "--", path)
    if out is None:
        return None
    return bool(out.strip())


def _count(rev_range: str) -> int | None:
    out = _git("rev-list", "--count", rev_range)
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def behind_upstream(branch: str = "origin/main") -> int | None:
    """How many commits the checkout is BEHIND `branch`, or None.

    Reads the LAST FETCHED ref — no network of its own, so a machine that never
    fetches reports 0 rather than the truth. That is an accepted limitation and
    the reason `config_dirty()` carries the real weight: it is offline-exact.
    """
    return _count(f"HEAD..{branch}")


def ahead_of_upstream(branch: str = "origin/main") -> int | None:
    """How many commits the checkout has that `branch` has NEVER SEEN.

    Added 2026-07-25, within half an hour of this module shipping, because the
    module failed to notice its own repository. `behind_upstream()` answers
    "what has main got that I lack" — and the production checkout was sitting on
    an unmerged feature branch, **4 commits ahead**, running code no review had
    passed. Behind was 0. The guard written to catch "the running code is not
    the reviewed code" reported clean on exactly that.

    Divergence #7 was production behind main; this is production ahead of it.
    Same disease, mirror image, and it needs its own number: one count catches
    BOTH a feature branch and `main` carrying unpushed local commits. Checking
    the branch NAME instead would miss the second, which is the more dangerous
    one precisely because the name looks right.
    """
    return _count(f"{branch}..HEAD")


def status() -> dict:
    """One read-only snapshot. Never raises, never blocks, never writes."""
    sha, source = running_sha()
    return {"sha": sha, "sha_source": source,
            "config_dirty": config_dirty(),
            "behind": behind_upstream(),
            "ahead": ahead_of_upstream()}


def drift_message(st: dict, behind_threshold: int = 1) -> str:
    """Human-readable reason to alert, or "" when nothing is wrong.

    Empty string means "no drift worth reporting" — which explicitly includes
    "could not tell". An unknown is not an alert; alerting on unknowns in a
    container that legitimately has no `.git` would fire every single day and
    train the owner to ignore the channel.
    """
    parts = []
    if st.get("config_dirty"):
        parts.append("config.yaml differs from the committed version — the "
                     "running rails are not the gated ones")
    behind = st.get("behind")
    if behind is not None and behind >= behind_threshold:
        parts.append(f"checkout is {behind} commit(s) behind upstream")
    ahead = st.get("ahead")
    if ahead is not None and ahead >= behind_threshold:
        parts.append(f"running {ahead} commit(s) that origin/main has never "
                     f"seen — this code has not been reviewed")
    if not parts:
        return ""
    sha = st.get("sha") or "unknown"
    return (f"DEPLOYMENT DRIFT: {'; '.join(parts)}. Running {sha[:12]} "
            f"({st.get('sha_source')}). Gated changes may not be live — see "
            f"knowledge/backtest_candidates.md §26 divergence #7.")
