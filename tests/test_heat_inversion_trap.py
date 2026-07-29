"""The heat inversion is latent — and preflight keeps it that way.

`portfolio_heat` charges a position with no recorded stop
`equity x risk_per_trade_pct` (src/risk.py:626,633-634), and the heat check
charges an entry with no candidate stop the same (src/risk.py:845-847). The
shipped values make that fallback 8.0% of equity against a cap of 4.0% — double
the entire budget. Any stopless entry is blocked unconditionally, and once one
stopless position is open, every further entry is blocked behind it.

MEASURED 2026-07-28 on the 2022-2026 snapshot, 500 symbols:
    entries with no stop available          0 of 244,920
    heat evaluations seeing a stopless pos  0 of 465
    heat from real stop-risk                $1,777,533
    heat from the fallback                  $0

So all 436 heat blocks in §40's census were legitimate accumulated stop-risk,
NOT the inversion. The inversion is unreachable while `brackets.enabled` is
true, because `unprotectable_entry` (src/risk.py:355-392) refuses entries whose
stop cannot be computed.

No risk number was changed on the strength of that diagnostic — a measurement
does not license a tuning. What IS closed here is the trap: turning brackets off
would arm the inversion silently, and the bot would stop entering with no error,
which is precisely the §40 failure shape.
"""
import preflight
import risk


def _cfg(base, **over):
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    c["risk"] = dict(base["risk"])
    c["risk"].update(over)
    return c


def test_the_arithmetic_of_the_inversion_is_real(cfg):
    """Not hypothetical: with the shipped ratio, one stopless position exceeds
    the whole cap by itself."""
    equity = 100_000.0
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=4.0)
    stopless = {"AAA": {"qty": 10, "entry_price": 100.0, "order": {}}}
    heat = risk.portfolio_heat(stopless, c, equity)
    cap = equity * c["risk"]["max_portfolio_heat_pct"] / 100
    assert heat == 8_000.0
    assert cap == 4_000.0
    assert heat > cap, "the inversion arithmetic no longer holds"


def test_a_position_with_a_real_stop_charges_its_actual_risk(cfg):
    """The fallback is the only path that inverts. A recorded stop charges
    qty x (entry - stop), which is what the cap was sized for."""
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=4.0)
    stopped = {"AAA": {"qty": 10, "entry_price": 100.0,
                       "order": {"stop_price": 95.0}}}
    assert risk.portfolio_heat(stopped, c, 100_000.0) == 50.0


def test_preflight_passes_while_brackets_keep_it_unreachable(cfg):
    """Shipped config: inverted ratio, brackets ON. This is the live state and
    it must stay clear to trade — the guard must not fire on the real config.
    """
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=4.0)
    c["risk"]["brackets"] = {**c["risk"]["brackets"], "enabled": True}
    assert not [f for f in preflight.run(c) if "heat" in f]


def test_preflight_refuses_the_combination_that_arms_it(cfg):
    """Brackets off + inverted ratio = a bot that silently stops entering."""
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=4.0)
    c["risk"]["brackets"] = {**c["risk"]["brackets"], "enabled": False}
    fails = [f for f in preflight.run(c) if "max_portfolio_heat_pct" in f]
    assert len(fails) == 1
    assert "stop trading without saying why" in fails[0]


def test_brackets_off_is_fine_when_the_ratio_is_sane(cfg):
    """The guard targets the INTERACTION, not brackets being off. Disabling
    brackets with a per-trade risk inside the cap is a legitimate setup."""
    c = _cfg(cfg, risk_per_trade_pct=2.0, max_portfolio_heat_pct=4.0)
    c["risk"]["brackets"] = {**c["risk"]["brackets"], "enabled": False}
    assert not [f for f in preflight.run(c) if "max_portfolio_heat_pct" in f]


def test_a_disabled_heat_cap_does_not_trip_the_guard(cfg):
    """0 disables the cap (§29 convention). No cap, no inversion."""
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=0)
    c["risk"]["brackets"] = {**c["risk"]["brackets"], "enabled": False}
    assert not [f for f in preflight.run(c) if "max_portfolio_heat_pct" in f]


def test_a_gutted_guard_would_fail_this_file(cfg):
    """Meta-assertion. If the preflight clause were deleted, the refusal test
    above would pass its setup and assert nothing. Prove the clause fires."""
    c = _cfg(cfg, risk_per_trade_pct=8.0, max_portfolio_heat_pct=4.0)
    c["risk"]["brackets"] = {**c["risk"]["brackets"], "enabled": False}
    assert any("max_portfolio_heat_pct" in f for f in preflight.run(c)), (
        "preflight raised nothing on the armed combination — the guard is "
        "inert and every refusal test in this file is vacuous")
