# Free hosted Indian paper trial

## Current status

The runnable code, scheduled workflow, durable account storage and SMTP email notifications are implemented. Local tests and an official NSE data check passed. **It has not been deployed. No email has been delivered.** A GitHub account and sender email credentials have not yet been connected.

This is a real software project running a simulated account. It cannot submit real broker orders. The initial hosted experiment lasts 28 calendar days from the first successful evening data cycle, then freezes pending orders and retains last marks. It does not automatically renew, liquidate positions, or begin live trading.

## Hosting choice

Use a **private GitHub repository** and GitHub Actions' included runner allowance. No continuously running web server is required. The dashboard/report is private in the repository and each workflow's Summary. There is no public website in this deployment.

GitHub scheduled runs can be delayed or dropped, so this is suitable for a daily paper experiment, not precise opening-bell execution. Free service is subject to your account quota and other usage; keep paid Actions spending disabled. A stopped or dropped scheduler cannot notify you itself. Enable GitHub's workflow failure emails as a second channel and inspect the run history if expected emails are missing. Independent external heartbeat monitoring is a later addition.

Render's free service was not selected because it sleeps and loses local files on restarts; a SQLite trading ledger must not live on ephemeral storage.

## What runs and when

- 09:17 IST weekdays: startup/readiness email. Reports actual start time, not a claim of exact opening-bell execution.
- 09:32 IST: second opportunity to run and retry undelivered mail. The startup email is deduplicated per date.
- 17:17 IST: download the latest completed NSE report, validate prices, process eligible pending paper orders and create new strategy proposals.
- 17:47 IST: retry if the report was unavailable, and retry mail delivery.
- Weekends and configured exchange holidays: no trading cycle. Existing notification retries and expired-trial completion still run when invoked.

The calendar is based on NSE's 2026 capital-market holiday circular plus the January 15 update. It stops on an unreviewed year. Special weekend sessions, including Muhurat trading, are not supported by this first strategy; these require a reviewed calendar/strategy update, not an automatic assumption. Unexpected closures cause a missing-report error and no fabricated session.

The current data is **end-of-day**, not live quotes. A simulated trade uses the next session's recorded opening price plus adverse slippage, and is only observed after the daily report is published. Its email gives both the modelled session and observation time. No intraday stop execution or real-time fill alert is claimed.

## One-time activation

1. Create your account at [GitHub signup](https://github.com/signup) and verify your email. Tell Codex when it is ready; sign in yourself. Do not send your password or recovery codes.
2. Create a **private** repository (suggested name: `indian-paper-trader`). Upload the contents of this project to its root, including `.github/workflows/`. The `cloud.py` file must be at the root. Do not upload `state/`, tokens or `.env` files. Codex can complete publishing after account access is available.
3. Under repository Settings → Actions → General, enable the workflow's read/write repository permission. The workflow writes only its dedicated `paper-state` branch. Keep the repository private; the worker refuses to run in a public repository.
4. Under Settings → Secrets and variables → Actions, create the five secrets below. Enter them directly in GitHub, never in chat, source code, workflow YAML or public screenshots.
   - `SMTP_HOST`: your sender's SMTP server, e.g. `smtp.gmail.com` for Gmail.
   - `SMTP_USER`: sender account login.
   - `SMTP_PASSWORD`: a provider-approved app password or SMTP credential, **not your normal mailbox password**.
   - `EMAIL_FROM`: one plain sender address allowed by that account.
   - `ALERT_EMAIL`: your one destination address.
   The implementation uses authenticated SMTP on port 587 with required TLS. Provider policy may require 2-step verification and an app password, or may disallow this method. No mail service subscription is purchased by this project.
5. Open Actions → Indian paper trial → Run workflow → select `bootstrap`, once. This creates the persistent empty account and queues a setup email. It does not invent a performance history. Check that the run is green and that the setup email arrives (including spam).
6. Subsequent runs use `tick` automatically. The first successful evening data cycle starts the 28-day paper window. A private `paper-state` branch contains `REPORT.md` and `trial-report.json`; do not edit its ledger manually.

Deployment acceptance: the account branch exists, all checks are green, a setup email was actually received, and the first scheduled run updates its report. Until those are verified, the service is not operational.

## Alerts

All messages are labelled **PAPER ONLY**:

- Worker started, with actual time and prior-session readiness.
- Simulated buy/sell with symbol, quantity, price, fees and observation time.
- Rejected, cancelled or expired proposal and reason.
- Missing/invalid data, expired calendar, processing failure.
- Loss-limit entry halt; open positions remain exposed.
- Daily account summary and pending proposals, including no-trade days.
- Pause/resume confirmation and trial completion.

Messages are persisted before sending. Successful receipts are saved after delivery. Retries do not recreate fills. Email is **at least once**: a crash after SMTP acceptance but before saving the receipt can produce a duplicate email with the same message ID. SMTP acceptance is not proof of inbox delivery. Errors before the worker can open its database, or a host outage, rely on GitHub's own failure notifications rather than the in-app queue.

## Controls and recovery

Use Run workflow → `pause` to cancel pending new buys. `resume` enables entries only within risk rules; it cannot clear a loss halt. Holding positions can still lose value while paused. To stop the scheduler entirely, disable the workflow in GitHub; that also stops all accounting and emails.

The account is restored from `paper-state` with SHA-256 checks and SQLite integrity checks. Missing or corrupted state stops the job. Do not use `bootstrap` to recover a lost account: restore the existing branch from its history/backup. The workflow serializes account jobs and never force-pushes state. It persists the ledger before email, so a failed state save blocks alerts claiming newly recorded trades.

Code and state are separated. State commits include only the ledger, reports, NSE archives and checksums. SMTP and broker secrets are never included. The raw archives are cached immutably; the adapter does not continuously check all prior reports for upstream corrections. Corporate-action gaps are blocked, but smaller adjustments/dividends need further handling.

Download a periodic private backup of `paper-state`. Git history is a useful recovery record, not an independent offsite backup.

## Local use and verification

```sh
python3 -m unittest discover -s tests -v
python3 app.py serve --provider nse --config hosted-config.json --port 8767
```

The second command starts a separate **local** trial and requires the Mac to remain running. It is not the hosted account. The existing demo and Upstox accounts remain separate.

Verified on 13 September 2026:

- 420 valid bars: six stocks and NIFTYBEES, 60 completed sessions from June 19 to September 11.
- Symbol/ISIN, date, segment, final-session identifier, OHLCV and archive checks passed.
- A disposable validation account initialized with ₹100,000, zero fills and zero pending proposals. This is valid: the strategy does not force trades.
- The disposable account was discarded. No forward profit or hosted start is claimed.

Current limitations: fixed small universe and rule-based strategy; no research-agent integration, dividends, tax reporting, intraday execution, authenticated live broker, or guarantee of profitability. Fees and slippage are documented modelling assumptions. The ₹100,000 account is entirely virtual.

## Primary sources

- [GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub included usage and billing](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [Render free-service persistence limitations](https://render.com/docs/free)
- [NSE 2026 capital-market holidays](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf)
- [NSE January 15 holiday update](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf)
- [NSE daily reports](https://www.nseindia.com/all-reports)
- [Google app-password guidance](https://support.google.com/accounts/answer/185833)
