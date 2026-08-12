"""
Backtest suite: proven strategies vs S&P 500 (SPY) buy-and-hold benchmark.
Data source: Perplexity Finance connector OHLCV histories (2005-2024 daily closes).
All strategies use only past data at each decision point (no look-ahead).
"""
import pandas as pd
import numpy as np
import json
import os

DATA_DIR = "/home/user/workspace/trading_kb/data"
OUT_DIR = "/home/user/workspace/trading_kb/backtests"

def load_close(ticker, filename):
    df = pd.read_csv(os.path.join(DATA_DIR, filename))
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').drop_duplicates('date').set_index('date')
    return df['close'].rename(ticker)

# ---- Load all series ----
files = {
    'SPY': 'SPY_price_history_1999-01-01_2024-12-31_1day_cd0858.csv',
    'XLK': 'XLK_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLF': 'XLF_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLE': 'XLE_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLV': 'XLV_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLY': 'XLY_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLP': 'XLP_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLI': 'XLI_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLU': 'XLU_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'XLB': 'XLB_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'TLT': 'TLT_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'BIL': 'BIL_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'MTUM': 'MTUM_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'VLUE': 'VLUE_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'QUAL': 'QUAL_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'USMV': 'USMV_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'SIZE': 'SIZE_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
    'QYLD': 'QYLD_price_history_2005-01-01_2024-12-31_1day_310ff2.csv',
}
prices = {}
for t, f in files.items():
    prices[t] = load_close(t, f)

# ---- Metrics helper ----
def perf_metrics(equity_curve, freq=252):
    equity_curve = equity_curve.dropna()
    rets = equity_curve.pct_change().dropna()
    n_years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
    cagr = (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1/n_years) - 1
    vol = rets.std() * np.sqrt(freq)
    sharpe = (rets.mean() * freq) / (rets.std() * np.sqrt(freq)) if rets.std() > 0 else np.nan
    cum = equity_curve / equity_curve.cummax() - 1
    max_dd = cum.min()
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    return {
        'start': str(equity_curve.index[0].date()),
        'end': str(equity_curve.index[-1].date()),
        'years': round(n_years, 2),
        'total_return_pct': round(total_return * 100, 1),
        'cagr_pct': round(cagr * 100, 2),
        'ann_vol_pct': round(vol * 100, 2),
        'sharpe': round(sharpe, 2),
        'max_drawdown_pct': round(max_dd * 100, 1),
    }

results = {}

# =========================================================
# STRATEGY 1: Buy & Hold SPY (benchmark)
# =========================================================
spy = prices['SPY'].dropna()
results['1_Buy_and_Hold_SPY_benchmark'] = perf_metrics(spy)

# =========================================================
# STRATEGY 2: 200-day SMA trend-following timing (Meb Faber style)
# Long SPY when close > 200-day SMA (using PRIOR day's SMA to avoid lookahead),
# otherwise hold cash (BIL total return proxy, else 0% flat).
# Rebalance decision made at end of day t using data through t, executed next day (t+1 open approx via next close).
# =========================================================
df = pd.DataFrame({'SPY': prices['SPY']}).dropna()
df['SMA200'] = df['SPY'].rolling(200).mean()
df['signal'] = (df['SPY'] > df['SMA200']).astype(int)
df['signal'] = df['signal'].shift(1)  # trade on next day using yesterday's signal (no lookahead)
df = df.dropna()
df['spy_ret'] = df['SPY'].pct_change().fillna(0)
# cash return proxy from BIL (T-bill ETF); align dates
bil_ret = prices['BIL'].pct_change()
df = df.join(bil_ret.rename('bil_ret'))
df['bil_ret'] = df['bil_ret'].fillna(0)
df['strat_ret'] = np.where(df['signal'] == 1, df['spy_ret'], df['bil_ret'])
df['equity'] = (1 + df['strat_ret']).cumprod()
results['2_SMA200_Trend_Timing_SPY'] = perf_metrics(df['equity'])
pct_time_invested = df['signal'].mean()
results['2_SMA200_Trend_Timing_SPY']['pct_time_in_market'] = round(pct_time_invested * 100, 1)

# =========================================================
# STRATEGY 3: Sector momentum rotation
# Monthly: rank 9 SPDR sectors by trailing 12-1 month momentum (i.e. return from t-252 to t-21),
# hold top-3 equal-weighted for the next month. No lookahead: decision made using data up to and
# including month-end t, applied to returns of month t+1.
# =========================================================
sector_tickers = ['XLK','XLF','XLE','XLV','XLY','XLP','XLI','XLU','XLB']
sec_df = pd.DataFrame({t: prices[t] for t in sector_tickers}).dropna()
monthly = sec_df.resample('ME').last()
# 12-1 momentum: (price at t-1mo / price at t-12mo) - 1, i.e. skip most recent month
mom = monthly.shift(1) / monthly.shift(12) - 1
mom = mom.dropna(how='all')
monthly_ret = monthly.pct_change()

port_rets = []
dates = []
for i in range(len(mom.index) - 1):
    dt = mom.index[i]
    next_dt = mom.index[i+1]
    if next_dt not in monthly_ret.index:
        continue
    scores = mom.loc[dt].dropna()
    if len(scores) < 3:
        continue
    top3 = scores.sort_values(ascending=False).head(3).index.tolist()
    r = monthly_ret.loc[next_dt, top3].mean()
    port_rets.append(r)
    dates.append(next_dt)

sector_mom = pd.Series(port_rets, index=pd.to_datetime(dates)).dropna()
sector_equity = (1 + sector_mom).cumprod()
results['3_Sector_Momentum_Rotation_Top3of9'] = perf_metrics(sector_equity, freq=12)

# For comparison, SPY over the exact same monthly-return window
spy_monthly = prices['SPY'].resample('ME').last().pct_change()
spy_aligned = spy_monthly.reindex(sector_mom.index).dropna()
spy_equity_aligned = (1 + spy_aligned).cumprod()
results['3b_SPY_same_period_monthly'] = perf_metrics(spy_equity_aligned, freq=12)

# =========================================================
# STRATEGY 4: Live factor ETFs vs SPY (since each fund's inception, same window comparison)
# =========================================================
factor_tickers = ['MTUM', 'VLUE', 'QUAL', 'USMV', 'SIZE']
for t in factor_tickers:
    fseries = prices[t].dropna()
    start, end = fseries.index[0], fseries.index[-1]
    spy_matched = prices['SPY'].loc[start:end].dropna()
    # align to common dates
    common = fseries.index.intersection(spy_matched.index)
    results[f'4_{t}_since_inception'] = perf_metrics(fseries.loc[common])
    results[f'4_{t}_SPY_same_window'] = perf_metrics(spy_matched.loc[common])

# =========================================================
# STRATEGY 5: Covered-call ETF (QYLD) vs SPY, matched window
# =========================================================
qyld = prices['QYLD'].dropna()
spy_matched = prices['SPY'].loc[qyld.index[0]:qyld.index[-1]].dropna()
common = qyld.index.intersection(spy_matched.index)
results['5_QYLD_covered_call_since_inception'] = perf_metrics(qyld.loc[common])
results['5_QYLD_SPY_same_window'] = perf_metrics(spy_matched.loc[common])

# =========================================================
# STRATEGY 6: Dual Momentum (Antonacci-style) — SPY vs TLT vs Cash (BIL), monthly
# Hold whichever of SPY/TLT has higher trailing 12-month absolute return, but only if that
# return is positive; else hold cash (BIL). Rebalance monthly, decision uses trailing data only.
# =========================================================
dm_df = pd.DataFrame({'SPY': prices['SPY'], 'TLT': prices['TLT'], 'BIL': prices['BIL']}).dropna()
dm_monthly = dm_df.resample('ME').last()
dm_mom12 = dm_monthly.pct_change(12)
dm_ret = dm_monthly.pct_change()

dm_port_rets = []
dm_dates = []
for i in range(len(dm_mom12.index) - 1):
    dt = dm_mom12.index[i]
    next_dt = dm_mom12.index[i+1]
    row = dm_mom12.loc[dt, ['SPY', 'TLT']].dropna()
    if row.empty:
        continue
    best = row.idxmax()
    if row[best] > 0:
        r = dm_ret.loc[next_dt, best]
    else:
        r = dm_ret.loc[next_dt, 'BIL']
    dm_port_rets.append(r)
    dm_dates.append(next_dt)

dual_mom = pd.Series(dm_port_rets, index=pd.to_datetime(dm_dates)).dropna()
dual_mom_equity = (1 + dual_mom).cumprod()
results['6_Dual_Momentum_SPY_TLT_Cash'] = perf_metrics(dual_mom_equity, freq=12)
spy_aligned2 = spy_monthly.reindex(dual_mom.index).dropna()
results['6b_SPY_same_period_monthly'] = perf_metrics((1+spy_aligned2).cumprod(), freq=12)

# Save results
with open(os.path.join(OUT_DIR, 'backtest_results.json'), 'w') as f:
    json.dump(results, f, indent=2)

for k, v in results.items():
    print(k, v)
