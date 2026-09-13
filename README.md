# Paper Trading Lab

A runnable local prototype for a seven-calendar-day operational trial using Upstox daily market data and ₹1,00,000 of virtual capital. It cannot submit real orders. The synthetic demo is already available separately from the real-market paper account.

## Start here

Python 3.9 or later is required. This Mac already has Python 3.9.6. No packages need installing.

**Try the demo:** double-click `Start Demo.command`, then open [the demo dashboard](http://127.0.0.1:8765). If the preview is already running, simply open that link. Press **Advance one demo session** to exercise the trading loop. All demo prices are generated, including prices displayed alongside familiar stock symbols. They are not historical prices or evidence of a profitable strategy. The demo has its own accelerated clock and expires after seven simulated calendar days.

**Start the real-market paper trial:**

1. Open [Upstox Developer Apps](https://account.upstox.com/developer/apps). Create an app if necessary, following Upstox's current developer workflow, then generate an access token. Follow any required terms or account authorization yourself. This prototype uses the manual-token route; it does not require you to provide an API secret to it.
2. Double-click `Set Upstox Token.command`. Paste the token into the hidden local prompt and press Enter. Do not paste it into chat. Nothing appears while you paste: that is intentional. The file is saved with owner-only permissions under `state/paper/upstox.token`. Upstox tokens can carry broader account permissions even though this application calls only historical-data endpoints. Do not share the token or your state directory.
3. Before the first start, review `config.json`: virtual balance, risk limits, estimated account fees, operating expense, holidays, and known corporate actions. The default universe is six stocks, not investment recommendations. The September 14, 2026 holiday is included; this is not a full annual exchange calendar.
4. Double-click `Start Paper Trial.command`, keep its terminal open, and open [the paper dashboard](http://127.0.0.1:8766).
5. A successful complete data check starts the seven-day clock and creates the first signals. No token or failed data check means the trial has **not** started. Pending orders get their first simulated fill attempt on the next completed trading session. The dashboard updates after market close, not tick by tick.

The Mac launcher keeps the machine awake while it runs; keep the lid open, plug it in, and retain internet access. Closing the terminal or shutting down the Mac stops the scheduler. It does not install a background system service. To stop it, press Ctrl+C in its terminal. If a port is in use, open the already-running dashboard or choose a different port in the command-line interface.

Upstox may require token replacement or renewed login. This version does not bypass or automate authentication. Replace the token with the same local setup whenever required; the running process reloads the file on each check. Exact entitlement, token validity, and any API subscription costs depend on your Upstox account. [Authentication documentation](https://upstox.com/developer/api-documentation/authentication/)

## What the program does

- Fetches approximately 180 calendar days of daily bars as warm-up history, from the official Upstox historical-data API. Resolves instrument keys using its public NSE instrument list and caches the mapping per trial.
- At startup and every 30 minutes after 17:00 IST on weekdays, checks for a newly completed market session. Repeated checks of the same session do not create extra trades. Holidays have no new decisions.
- Ranks eligible stocks by 20-session momentum; requires positive momentum, a close above the 50-session mean, and sufficient average traded value. Rebalances every five processed sessions, starting immediately at initialization.
- Allocates up to three positions with 25% maximum position size, 75% gross exposure, and 30% sector exposure at entry. Uses a 5% sizing buffer for price changes before execution. No short selling or borrowing.
- Simulates a single all-or-none attempt at the following session's open, with 10 bps adverse slippage per side and configurable estimated fees. It applies cash and exposure checks before each simulated fill. Rejected orders stay in the audit log.
- Keeps signals, features, orders, fills, fees, cashflows, and closing valuations in SQLite. Atomic processing and deterministic order IDs prevent duplicates after restarts. The internal ledger reconciles fills against cashflows and order states.
- Halts new entries after a 2% observed daily loss or 5% observed drawdown. These limits are checked at simulated open and close, not intraday. A risk halt stays latched for the trial; user Resume does not bypass it. Existing holdings remain exposed and previously planned exits can still execute. There is no automatic liquidation or guaranteed loss ceiling.
- Exports `state/paper/trial-report.json` after each run. At the seven-day deadline, the next scheduler check expires pending orders and freezes the account at its last recorded closing marks. It does not liquidate or value holdings beyond that record. The deadline may fall between market sessions.

Upstox data calls are GET requests only. There is no live broker order adapter or live mode. Network destinations for the data client are allowlisted, redirects are rejected, and tokens are excluded from reports. The dashboard binds only to localhost, validates its Host header, and protects actions against cross-site requests. Do not expose this development server to the internet.

## What we assess after a week

Operational acceptance requires:

- Every expected session has a complete data check or an explained, visible failure.
- No duplicate fills, unexplained ledger mismatch, or negative virtual cash.
- Every decision has a timestamp, strategy version, and feature snapshot.
- Missing, stale, inconsistent, or revised data blocks account advancement.
- Restarting preserves orders and balances; missed sessions never create backdated signals.
- Net marked account value includes modelled transaction costs, and the report separates fees from embedded slippage.

We also inspect turnover, concentration, skipped trades, modelled costs, drawdown, and comparison with the displayed benchmark. Passing these gates means the software behaved as designed. It does **not** mean the strategy is profitable or ready for real capital. A quiet week with few trades can still be a valid operational trial.

If activated on September 12, 2026, the trial ends seven elapsed days later. Monday September 14 is an NSE holiday, leaving four regular sessions from September 15–18. Initial signals can be prepared from Friday September 11's completed data. [NSE 2026 holiday circular](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf)

## Limits of this first version

Daily historical bars do not show the actual price you could have obtained. All fills are simulated after the daily data arrives, based on signals that existed beforehand. This is not real-time paper execution. There is no order-book queue, partial-fill, spread measurement, intraday stop, auction, settlement-restriction, or price-band model. No live broker ledger is reconciled because no broker orders are placed.

Corporate actions are not automatically booked. Declared action dates and large opening-price discontinuities block processing for review. Small dividends and actions that do not trigger those checks can still distort results. Verify the trial universe for actions; stop and investigate any such event. A detected historical data revision also blocks processing rather than silently rewriting the record.

The displayed NIFTYBEES reference is a fully invested price-return series with no fees or dividends. Its risk exposure differs from the strategy and it is not a risk-adjusted alpha estimate. Strategy account value is marked, not fully liquidated; open positions have no estimated exit fees deducted. No Sharpe, win rate, or annualized return is presented from a one-week sample. Historical warm-up is not a backtest. The fixed current universe is unsuitable for unbiased long historical performance claims.

Default costs approximate an ordinary Upstox delivery plan: up to ₹20 brokerage per order subject to 2.5% cap, 0.1% STT per side, 0.00307% exchange charge, SEBI fee, buy stamp duty, GST, and ₹23.60 DP including GST on sells. The simplified fee rounding and account-specific plans may differ from contract notes. The model allows at most one order per symbol per session, so sell-side DP is not charged repeatedly for multiple same-day fills. Verify your plan and combined exchange/IPFT treatment. Personal income tax is excluded. [Upstox charge schedule](https://upstox.com/brokerage-charges/)

Operating expense defaults to zero for the local prototype; this does not claim that electricity, subscriptions, or time are free. Enter a realistic monthly amount **before** a new trial when evaluating self-funding economics. The configured expense is accrued across processed calendar intervals, not actual provider billing.

Strategy and configuration are fingerprinted and frozen for each account. An intentional change needs a new `--state` directory. Keep the old trial for comparison; do not overwrite it or cherry-pick results. For a longer historical validation and robust fill simulation, a later iteration is necessary before considering live trading.

## Command-line use

Run commands from this folder:

```bash
python3 app.py demo --port 8765
python3 app.py token
python3 app.py serve --provider upstox --port 8766
python3 app.py once --provider upstox
python3 app.py report --provider upstox
python3 -m unittest discover -s tests -v
```

For another frozen experiment, pass `--state state/paper-02` consistently to the token, serve, and report commands. For a fresh synthetic demo, use `python3 app.py demo --state state/demo-02`.

An alternative provider accepts a CSV with `symbol,date,open,high,low,close,volume`, containing the full universe and benchmark with matching sessions and adequate warm-up. For example: `python3 app.py serve --provider csv --csv /absolute/path/bars.csv --state state/csv-trial`. CSV updates must come from you or an external feed; this mode cannot retrieve data autonomously. Dates are Indian market-session dates and bars must be complete. No partial-day data is accepted.

Implementation references: [Upstox historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/), [instrument list](https://upstox.com/developer/api-documentation/instruments/). Authenticated data access has not been verified against your account; the parser has been tested with recorded-format fixtures, and the public instrument mapping was checked successfully.
# Hosted Indian paper trial — September 2026 update

For the active project, read [DEPLOY.md](DEPLOY.md). It adds validated NSE daily reports, a private GitHub Actions schedule, durable account storage and email notifications. Hosting and email are not yet activated; user account setup is pending. Use `hosted-config.json` for the separate 28-day NSE experiment. The original local demo/Upstox instructions below remain available.
