"""A deterministic stand-in for the LLM judge, for BACKTESTS ONLY.

Why this module exists
----------------------
`backtest.py` never imports `llm` — by design, stated in its own docstring. A
simulator that called a model per bar would be slow, non-reproducible, and
contaminated by hindsight the model absorbed in training. So the judge has
simply been absent from every backtest this project has ever run.

That absence is not neutral. §26 recorded it as the live half of divergence #8:

    the backtest never applies the judge's verdict: there is no
    review["scale"] anywhere in backtest.py

and the live ledger shows the judge cutting **53.5% of buys**, mean scale
**0.752**. So the simulator has been sizing every entry ~33% larger than the
live bot would, on the majority of trades. Every OOS number in
`knowledge/backtest_candidates.md` inherits that.

This module cannot recover *which* trades the judge would have cut — nothing
can, retroactively. It reproduces the *distribution* instead: how often, and by
how much. That is strictly closer to live than modelling no judge at all, and
it is honest about being a distribution rather than a replay.

WHY IT LIVES HERE AND NOT IN risk.py
------------------------------------
`risk.py` is shared by the live bot. A haircut applied there would stack on top
of the REAL judge in `main.py:623` — every live order cut twice, and a ninth
sim/live divergence created by the code fixing the eighth. Keeping the model in
a module the live path never imports makes that mistake structurally impossible
rather than merely discouraged. `main.py` must never import this file.

DETERMINISM
-----------
Gate re-runs have to reproduce byte-identically (the same requirement §24 put on
scan rotation: "Deterministic, not random — gate re-runs still reproduce"). The
draw is therefore a hash of (symbol, timestamp, salt), not a PRNG sequence: it
does not depend on how many symbols were evaluated before it, so adding a
symbol to the universe cannot change the verdict on an unrelated one.
"""
from __future__ import annotations

import hashlib
import json
import os

_CAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "knowledge", "judge_calibration.json")

_cache: dict | None = None


class CalibrationError(RuntimeError):
    """Raised rather than silently falling back to 'no haircut'.

    A missing or too-small calibration must stop the run. Defaulting to scale
    1.0 would reintroduce divergence #8 while the config claimed the model was
    active — the exact shape of failure this project keeps finding.
    """


def load_calibration(path: str | None = None, force: bool = False) -> dict:
    global _cache
    if _cache is not None and not force and path is None:
        return _cache
    p = path or _CAL_PATH
    if not os.path.exists(p):
        raise CalibrationError(
            f"judge_model is enabled but {p} is missing. "
            f"Run: python scripts/calibrate_judge.py --write")
    with open(p) as f:
        cal = json.load(f)
    n = cal.get("n_judged_buys", 0)
    floor = cal.get("min_sample", 50)
    if n < floor:
        raise CalibrationError(
            f"judge calibration has n={n}, below its own min_sample={floor}")
    if not cal.get("scale_histogram"):
        raise CalibrationError("judge calibration has an empty scale histogram")
    if path is None:
        _cache = cal
    return cal


def _cdf(cal: dict) -> list[tuple[float, float]]:
    """[(cumulative_probability, scale)], ascending. Veto occupies the head.

    Veto is folded in as scale 0.0 rather than kept as a separate draw so that
    one uniform sample decides the whole outcome. Two draws would double the
    ways a future edit could desynchronise them.
    """
    hist = cal["scale_histogram"]
    total = sum(hist.values())
    out: list[tuple[float, float]] = []
    acc = float(cal.get("veto_rate", 0.0))
    if acc > 0:
        out.append((acc, 0.0))
    remaining = 1.0 - acc
    for k in sorted(hist, key=float):
        acc += remaining * hist[k] / total
        out.append((acc, float(k)))
    # Guard the last bucket against float drift so a draw of 0.999999 always
    # lands somewhere instead of falling off the end.
    if out:
        out[-1] = (1.0, out[-1][1])
    return out


def _uniform(symbol: str, ts: str, salt: str) -> float:
    h = hashlib.sha256(f"{symbol}|{ts}|{salt}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def scale_for(symbol: str, ts: str, cfg: dict) -> float:
    """The judge's simulated verdict for one entry: 0.0 (veto) or a haircut.

    Returns 1.0 when the model is disabled, so the caller can multiply
    unconditionally and every pre-existing gate reproduces exactly.
    """
    jcfg = ((cfg.get("backtest") or {}).get("judge_model")) or {}
    if not jcfg.get("enabled"):
        return 1.0
    cal = load_calibration()
    u = _uniform(symbol, ts, str(jcfg.get("salt", "judge-v1")))
    for cum, scale in _cdf(cal):
        if u < cum:
            return scale
    return 1.0


def apply(qty: int, symbol: str, ts: str, cfg: dict) -> int:
    """Mirror of the live path at `main.py:623` — `int(full_qty * scale)`.

    Truncation toward zero is the point, not an artifact: the judge may only
    SHRINK a position (invariant #2), so rounding a 0.5-share intent up to 1
    would trade more than it sanctioned. A downsize that truncates to 0 skips
    the trade live, and must skip it here too.
    """
    if qty <= 0:
        return qty
    return int(qty * scale_for(symbol, ts, cfg))
