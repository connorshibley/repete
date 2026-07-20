"""Model-version fingerprint — which rulebook produced each decision.

A paper track record is only attributable if the decision surface that
produced it can be named: every gate adoption, config tweak, or strategy
edit otherwise silently blends rulebooks inside one P/L series (vol-target
sizing landed 2026-07-18 — the record is already more than one model).

This stamps a short hash of the decision-surface files onto every ledger
record; review.py segments the record by version. STAMP-ONLY on purpose —
no buy-blocking freeze (common-trade's variant): this bot changes through
pre-registered gates, and the honest fit for an iterating system is
segmentation, not a lock. Offline, dependency-free, fail-soft.
"""
import glob
import hashlib
import os

_FINGERPRINT_FILES = [
    "config.yaml",
    "src/risk.py",
    "src/strategy.py",
    "src/broker.py",
    "src/llm.py",
    "src/regime.py",
    "src/earnings.py",
    "src/main.py",
]


def fingerprint(root: str = ".") -> dict:
    """{"version": <12-hex>, "files": {relpath: <8-hex>}} over the decision
    surface. Missing files hash as absent (so the version still changes when
    a file appears or disappears)."""
    paths = list(_FINGERPRINT_FILES)
    paths += sorted(glob.glob(os.path.join(root, "src/strategies/*.py")))
    agg = hashlib.sha256()
    files = {}
    for p in paths:
        rel = os.path.relpath(p, root) if os.path.isabs(p) else p
        full = p if os.path.isabs(p) else os.path.join(root, p)
        try:
            with open(full, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            h = "absent"
        files[rel] = h[:8]
        agg.update(f"{rel}:{h}\n".encode())
    return {"version": agg.hexdigest()[:12], "files": files}


def current_version(root: str = ".") -> str | None:
    """Just the short version string; None if anything goes wrong —
    stamping is instrumentation and must never block a cycle."""
    try:
        return fingerprint(root)["version"]
    except Exception:  # noqa: BLE001
        return None
