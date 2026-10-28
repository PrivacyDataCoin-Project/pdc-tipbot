# Architecture

## Overview

PDC TipBot is a **custodial** multi-platform tip service:

```text
Telegram / Matrix adapters
        │
        ▼
   TipService (commands)
        │
        ├── SQLite ledger (users, balances, giveaways, rain, payouts)
        │
        ├── WalletClient ──► simplewallet JSON-RPC (hot wallet)
        │
        └── DepositScanner ──► get_bulk_payments + daemon height
```

Business logic lives in `tipbot/service.py` so Telegram and Matrix stay thin adapters.

## Ledger

- One SQLite DB (`DATABASE_PATH`, default `data/tipbot.db`).
- Users are keyed by `(platform, platform_user_id)`.
- Balances are integer **atomic** units (`1 PDC = 10^12`).
- Tips, giveaway escrow/claim, and rain fund/claim are **atomic SQL transactions** (`BEGIN IMMEDIATE`) so concurrent overspend fails closed.

### Liability invariant

At startup and before on-chain payouts:

```text
sum(user.balance_atomic) + open_giveaway_escrow  ≤  hot_wallet.total
```

(The rain pool is a normal `users` row under `platform=system`, so it is included in the sum.)

## Deposits

1. Each user (and the rain system account) has a unique `payment_id` and integrated address.
2. `DepositScanner` polls `get_bulk_payments` (including locked / mempool rows).
3. **Pending:** tx seen with `block_height = 0` or insufficient confirmations → optional DM (skipped for `system/*`).
4. **Credit:** only when
   - `block_height > 0`
   - confirmations ≥ `DEPOSIT_MIN_CONFIRMATIONS` (default 10)
   - `unlock_time == 0` or `unlock_time ≤ chain tip`
5. Credits are idempotent via `UNIQUE(tx_hash, payment_id)` on `deposits`.

Mempool payments use `block_height = 0`. The scanner merges a `min_block_height = 0` query with the height watermark so unconfirmed deposits are not dropped.

## Withdrawals / sends

1. Debit ledger + enqueue `payouts` row (`queued`).
2. Mark `transferring`, call wallet `transfer`.
3. On success → `sent` + ledger fee/withdraw kinds.
4. Crash before RPC → refund; crash after RPC started → `suspect` (manual admin resolution).

Daily withdraw volume is capped per user (`DAILY_WITHDRAW_LIMIT_ATOMIC`).

## Giveaways

User-funded escrow in `giveaways` (`open` → `claimed` | `cancelled`). First successful claimer wins; creator can cancel while open. Telegram uses inline buttons; Matrix uses `/claim <id>`.

## Rain

Separate system ledger account + `rain_claims` history. See [RAIN.md](RAIN.md).

## Platforms

| Concern | Telegram | Matrix |
|---|---|---|
| Transport | Long polling | Sync + E2EE (olm) when available |
| Private replies | DM / delete noisy group commands | DM room mapping in `meta` |
| Admin | Username allowlist | MXID allowlist |

## Related modules

| File | Role |
|---|---|
| `tipbot/app.py` | Process entry, scanner + bots lifecycle, solvency check |
| `tipbot/config.py` | Env-backed settings |
| `tipbot/amounts.py` | Parse/format atomic amounts; rain claim sizing |
| `tipbot/db.py` | Schema + transactional operations |
| `tipbot/wallet.py` | RPC + alias resolve via daemon |
| `tipbot/scanner.py` | Deposit pipeline |
