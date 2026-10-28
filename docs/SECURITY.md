# Security notes

## Trust model

- The tipbot is **custodial**. Anyone who can read the hot wallet seed or RPC can move funds.
- Users trust the operator for ledger honesty and deposit/withdraw processing.
- Platforms (Telegram / Matrix) authenticate command senders; the bot does not add a second factor.

## Secrets — never publish

Do **not** commit or paste into issues/PRs:

| Item | Examples |
|---|---|
| Env secrets | `TELEGRAM_TOKEN`, `MATRIX_PASSWORD`, `MATRIX_ACCESS_TOKEN`, `WALLET_RPC_PASSWORD` |
| Wallet material | seed, spend key, view key, `.keys`, wallet password files |
| Runtime data | production `tipbot.db`, Matrix E2EE store, live `.env` |
| Backups | anything under backup dirs that includes seed/wallet |
| Seed tooling | `export_seed` / `show_seed` scripts (keep in private ops only) |
Safe to keep in git:

- `.env.example` with **empty** secret values
- Public asset id, explorer URLs, public room/chat ids
- Docs and deploy **templates** that reference secret *paths* without contents

`.gitignore` already excludes `.env`, `data/`, `wallet/`, `secrets/`, wallet key material.

## Operational controls

- Bind wallet and daemon RPC to **127.0.0.1** only; do not expose them publicly.
- Keep hot wallet balance low; cold-store the remainder.
- Enforce `DAILY_WITHDRAW_LIMIT_ATOMIC` and solvency checks before chain sends.
- Prefer Matrix access tokens over long-lived passwords in `.env` after bootstrap.
- Restrict `/totalbalance` and rain pause/resume to admin allowlists.

## Deposit / rain credit

Ledger credit happens only after block inclusion and confirmation/unlock thresholds. Unconfirmed (`awaiting_in`) amounts may appear in hot wallet **total** while remaining outside the ledger — this is expected.

## Incident hints

| Symptom | Likely action |
|---|---|
| `suspect` payout | Inspect wallet history; manual resolve; do not auto-refund |
| Liabilities > hot total | Stop payouts; reconcile deposits/credits; restore from backup if corrupted |
| Rain drain / multi-account abuse | `/rain pause`; tighten allowlists; consider future anti-sybil |
| Token leak | Rotate Telegram bot token / Matrix password / wallet as applicable |

## Responsible disclosure

Report security issues privately to the project operators. Do not open public issues that include production secrets or wallet material.

## Public repository

See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md). Summary:

- Enable GitHub secret scanning + push protection before visibility=public
- Rotate Telegram / Matrix credentials after publishing
- Never reintroduce seed-export tooling into this tree
