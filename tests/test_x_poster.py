import x_poster


def _buy_trade():
    return {"symbol": "SPY", "action": "buy", "qty": 10, "entry_price": 450.0,
            "strategy_reason": "SMA3 crossed above SMA5"}


def test_template_includes_paper_and_details(cfg):
    text = x_poster._template_post(_buy_trade(), cfg)
    assert text.startswith("[PAPER] ")
    assert "BUY 10 SPY" in text and "$450.00" in text
    assert "SMA3 crossed above SMA5" in text


def test_template_includes_result_on_close(cfg):
    trade = {**_buy_trade(), "action": "sell", "exit_price": 460.0, "pnl_pct": 2.22}
    text = x_poster._template_post(trade, cfg)
    assert "+2.22%" in text


def test_template_respects_275_cap_and_hashtag_fit(cfg):
    trade = {**_buy_trade(), "strategy_reason": "x" * 400}
    text = x_poster._template_post(trade, cfg)
    assert len(text) <= 275
    assert "#papertrading" not in text  # no room left for hashtags


def test_template_appends_hashtags_when_they_fit(cfg):
    text = x_poster._template_post(_buy_trade(), cfg)
    assert text.endswith(cfg["x_posting"]["hashtags"])


def test_dry_run_prints_instead_of_posting(cfg, capsys):
    x_poster.post_recap(_buy_trade(), cfg)  # no tweepy env keys -> would crash if not dry
    out = capsys.readouterr().out
    assert "X post (dry run)" in out and "[PAPER]" in out


def test_paper_disclosure_injected_into_llm_draft(cfg, capsys):
    x_poster.post_recap(_buy_trade(), cfg, llm_draft="Bot bought 10 SPY on a crossover.")
    out = capsys.readouterr().out
    assert "[PAPER] Bot bought 10 SPY" in out


def test_disabled_posting_is_silent(cfg, capsys):
    cfg["x_posting"]["enabled"] = False
    x_poster.post_recap(_buy_trade(), cfg)
    assert capsys.readouterr().out == ""
