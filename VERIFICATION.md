# Prototype verification — September 12, 2026

29 automated checks passed on Python 3.9.6. Coverage includes next-session timing, idempotency, restart recovery, rollback during a failed fill, sell handling while entries are paused, cash and sector limits, loss halts, trial expiration, stale/missing/revised data, and Upstox response parsing.

The local dashboard was opened in a browser. A synthetic session advanced successfully, displaying fills, fees, positions, and an updated balance. After the app was restarted, the same records remained. Pause and Resume controls were verified. The browser report export excludes the session action token, and a cross-site action request returned HTTP 403.

The official Upstox public instrument list was fetched successfully and resolved all six configured stocks plus NIFTYBEES. The missing-token path was exercised and reported a clear setup error without starting the trial.

Not verified: authenticated Upstox historical access for your account, a full week of operation, actual execution quality, profitability, or readiness for real trading. No real order was placed. Demo prices and results are synthetic.
# Hosted deployment verification — 13 September 2026

- 42 local tests pass, covering core accounting plus hosted scheduling, NSE parsing, email retries/TLS, durable state restoration, tamper detection and secret exclusion.
- Both GitHub workflows pass actionlint 1.7.12 validation. GitHub action dependencies are pinned to verified v4/v5 commit IDs.
- Official NSE reports validated: 420 bars, 60 sessions, June 19–September 11, 2026. A disposable account initialized without historical fills and was discarded.
- Hosted workflow execution and real SMTP delivery remain **unverified** because user accounts are not connected. No deployment URL, inbox delivery or running cloud trial is claimed.
