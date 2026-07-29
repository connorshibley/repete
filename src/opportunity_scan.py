"""Midday look at what is setting up — ALERTS ONLY, never trades.

The problem this solves, and the one it deliberately does not
--------------------------------------------------------------
"Don't make me wait until 15:45" is a fair ask, but the obvious fix does not
work. Signals are computed on **completed daily bars**, so the newest completed
bar is identical at 10:00 and at 14:00 — extra trading cycles would re-derive
the same answer and change nothing. The bot already runs a 09:35 open cycle
(§19a) that acts on the prior close, and protective stops are broker-side
bracket legs that are live continuously, so downside protection never waits for
a cycle either.

The genuine gap is narrower: a setup that becomes true *during* today is not
acted on until 15:45. Closing it by trading would mean evaluating a **partial**
bar — the input `config.yaml` warns against and that no gate has ever measured.
§19a declined exactly that, and nothing since has changed the evidence.

So this module splits the difference honestly: it looks at the forming bar and
**tells you what it sees**, while every trading decision stays on the gated
completed-bar inputs.

Why it cannot trade
-------------------
Not by convention — by construction. It never imports or receives a broker, it
never touches the ledger, and it holds no order-submitting call path at all.
`scan()` returns text. A test asserts the module contains no order verbs.
"""
from __future__ import annotations

import logging

import strategies

log = logging.getLogger("opportunity_scan")


def find_setups(all_bars: dict, cfg: dict, held: set | None = None) -> list:
    """Which enabled strategies would fire on the CURRENT (forming) bar.

    Pure: bars in, findings out. No side effects, no orders, no ledger writes.
    Findings are explicitly *provisional* — the bar is still moving, and a
    setup true at noon may be gone by the close.
    """
    held = held or set()
    out = []
    scan = [s for s in cfg.get("symbols", []) if s in all_bars]
    for name, params in strategies.enabled(cfg):
        mod = strategies.REGISTRY.get(name)
        if mod is None:
            continue
        xs = None
        if getattr(mod, "NEEDS_CROSS_SECTION", False):
            try:
                xs = mod.prepare(all_bars, params)
            except Exception as e:  # noqa: BLE001 — a scan must never raise
                log.warning("cross-section prep failed for %s: %s", name, e)
                continue
        for sym in scan:
            if sym in held:
                continue          # already positioned; not a new opportunity
            bars = all_bars.get(sym) or []
            if not bars:
                continue
            try:
                sig = strategies.generate(name, sym, bars, cfg, False,
                                          cross_section=xs)
            except Exception as e:  # noqa: BLE001
                log.warning("scan failed for %s/%s: %s", name, sym, e)
                continue
            if sig.action == "buy":
                out.append({"symbol": sym, "strategy": name,
                            "reason": sig.reason,
                            "indicators": dict(sig.indicators or {})})
    return out


def format_alert(setups: list, cutoff: str = "15:45 ET") -> str:
    """The message a human reads. States plainly that nothing has been traded
    and that these are provisional — no implied certainty."""
    if not setups:
        return ""
    lines = [f"Repete — {len(setups)} setup(s) forming (nothing traded yet):", ""]
    for s in setups[:10]:
        lines.append(f"  • {s['symbol']} [{s['strategy']}] — {s['reason']}")
    if len(setups) > 10:
        lines.append(f"  … and {len(setups) - 10} more")
    lines += [
        "",
        f"These are computed on TODAY'S STILL-FORMING bar and may not survive "
        f"to the close. The bot decides on completed bars at {cutoff}; only "
        f"setups that still qualify then will be traded, and every risk rail "
        f"still applies. This message places no orders.",
    ]
    return "\n".join(lines)


def scan(all_bars: dict, cfg: dict, held: set | None = None,
         notify: bool = True) -> str:
    """Run the scan and (optionally) push the alert. Returns the message.

    Never raises: an alerting failure must not affect trading in any way.
    """
    try:
        setups = find_setups(all_bars, cfg, held)
    except Exception as e:  # noqa: BLE001
        log.warning("opportunity scan failed: %s", e)
        return ""
    msg = format_alert(setups)
    if msg and notify:
        try:
            import alerting
            alerting.send("Repete: setups forming", msg)
        except Exception as e:  # noqa: BLE001 — alerting never breaks anything
            log.warning("opportunity alert failed to send: %s", e)
    return msg


def main() -> int:
    """CLI entry point for the scheduled midday scan.

    Reads market data and the current position list, then alerts. It obtains a
    broker only to READ bars and positions — there is no order call anywhere in
    this module, and a test enforces that.
    """
    from dotenv import load_dotenv
    load_dotenv()
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    from broker import Broker
    b = Broker(cfg)
    held = set(b.positions())
    lookback = strategies.max_lookback_bars(cfg)
    tf = cfg["strategy"]["timeframe"]

    all_bars = {}
    for sym in cfg.get("symbols", []):
        try:
            rows = b.bars(sym, tf, lookback)
            if rows:
                all_bars[sym] = rows
        except Exception as e:  # noqa: BLE001 — one bad symbol is not fatal
            log.warning("bars unavailable for %s: %s", sym, e)

    msg = scan(all_bars, cfg, held)
    print(msg or "no setups forming right now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
