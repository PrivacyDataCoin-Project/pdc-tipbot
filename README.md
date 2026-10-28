# PDC TipBot (Telegram + Matrix)

Custodial tip bot for **Privacy Data Coin (PDC)**: a shared off-chain ledger with on-chain deposits and withdrawals through one hot wallet (PDC / Zano-compatible `simplewallet` RPC).

| Platform | Library | Notes |
|---|---|---|
| Telegram | `python-telegram-bot` | Slash commands (`/tip`, `/rain`, …) |
| Matrix | `matrix-nio` | Homeserver `matrix.privacydatacoin.com`; prefixes `!` and `/` |

> **Custodial:** the operator holds the hot-wallet keys. Keep that wallet thin; store surplus cold.

## Features

- Per-user **integrated deposit addresses** (`payment_id`)
- Instant **tips** and **giveaways** on the ledger (no chain fee)
- On-chain **send** / **withdraw** with fee + daily limits
- Crash-safe **payout outbox** and deposit scanner (credit after confirmations / unlock)
- Communal **`/rain`** faucet (whitelisted chats, rolling 24h cooldown, dynamic claim size)

## Why not Monero-style accounts?

PDC/Zano wallets do not expose Monero `create_account` / subaddresses. The supported pattern is:

1. One hot `simplewallet` RPC
2. Per-user random 8-byte `payment_id` → `make_integrated_address`
3. Tips / giveaways / rain = atomic SQLite transfers
4. Deposits credited via `get_bulk_payments` after confirmations (mempool tracked; ledger credit only when included + unlocked)
5. Withdraw / send = `transfer` + network fee debited from the user ledger

## Quick start (local / dry-run)

```bash
git clone https://github.com/PrivacyDataCoin-Project/pdc-tipbot.git
cd pdc-tipbot
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` (see [Configuration](#configuration)). Leave `WALLET_RPC_URL` empty for **dry-run** (ledger + bots only, no chain I/O).

```bash
python -m tipbot
PYTHONPATH=. pytest
```

Never commit `.env`, wallet files, seeds, or Matrix/Telegram tokens.

## Commands

Telegram uses `/…`. Matrix accepts `!` or `/` (e.g. `!tip @alice:privacydatacoin.com 1`).

| Command | Description |
|---|---|
| `/start` | Create ledger account + deposit address |
| `/balance` | DM: spendable balance + pending deposit status |
| `/deposit` | Your integrated deposit address (prefer DM) |
| `/tip <@user\|reply> <amount> [msg]` | Instant ledger tip |
| `/giveaway <amount>` | Group/room giveaway (Claim / Cancel) |
| `/claim <id>` | Claim a Matrix giveaway by id |
| `/cancel <id>` | Cancel your open giveaway |
| `/rain` | Claim from the communal rain pool (whitelisted chats only) |
| `/rain info` | Pool size, next claim size, your cooldown |
| `/rain fund <amount>` | Move tipbot balance into the rain pool |
| `/rain deposit` | On-chain address that credits the rain pool |
| `/withdraw <address\|@alias>` | Sweep all unlocked balance minus network fee |
| `/send <address\|@alias> <amount>` | On-chain send (max = unlocked − fee) |
| `/help` | Help text |
| `/totalbalance` | Admin-only DM: hot wallet vs ledger liabilities |

In groups, `/balance` (and similar private payloads) stay silent — use a DM. Telegram tip-by-reply: reply with `/tip 0.5 thanks`.

Telegram and Matrix identities are **separate** ledger accounts (no cross-link in v0.1).

## Rain (communal faucet)

See **[docs/RAIN.md](docs/RAIN.md)** for full rules.

Short version:

- Fund via `/rain fund` or on-chain `/rain deposit` (credited only after unlock)
- Claim only in configured Telegram / Matrix rooms
- One claim per identity per **24h** (rolling); bots blocked
- Claim size is dynamic (min/max + target claims/day sizing)

## Safety model

- Overspend blocked by atomic SQLite debits (`WHERE balance >= …`)
- On-chain payouts need unlocked ledger funds and sufficient hot-wallet unlocked balance
- Solvency: if ledger liabilities exceed hot wallet **total**, on-chain payouts are refused
- Payout outbox: crash before RPC → auto-refund; crash mid-RPC → `suspect` (no blind refund)

Details: **[docs/SECURITY.md](docs/SECURITY.md)**, **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Configuration

Copy `.env.example` → `.env`. All secrets stay empty in the example file.

| Area | Important variables |
|---|---|
| Platforms | `TELEGRAM_TOKEN`, `MATRIX_*` |
| Wallet | `WALLET_RPC_URL`, `DAEMON_RPC_URL`, `PDC_ASSET_ID`, fees / confirmations |
| Policy | `MIN_TIP_ATOMIC`, `DAILY_WITHDRAW_LIMIT_ATOMIC`, admin usernames |
| Rain | `RAIN_*` (enable, chat/room allowlists, claim bounds, cooldown) |

Atomic amounts use **1 PDC = 10¹²**.

## Production

High-level layout (paths are examples — keep secrets **outside** git):

- App directory + SQLite under `data/` (gitignored)
- Secrets directory (tokens, wallet password, seed) **not** in the repo
- `simplewallet` RPC on localhost + local daemon
- systemd / backup helpers: copy `deploy/*.example` and edit locally (do not commit filled copies)

Full runbook: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

Public hot-wallet alias (informational): `@pdctipbot`.  
User deposits still use **per-user integrated addresses** from `/deposit`, not the bare alias.

## Layout

```text
tipbot/
  app.py                 # asyncio orchestrator + payout recovery
  service.py             # shared command logic
  db.py                  # SQLite ledger, giveaways, rain, payouts
  wallet.py              # JSON-RPC client
  scanner.py             # deposit poller (mempool → unlock → credit)
  platforms/
    telegram_app.py
    matrix_app.py
deploy/                  # *.example systemd / backup / install templates
docs/                    # architecture, rain, deploy, security, public release
tests/
.env.example             # safe template (no secrets)
LICENSE
```

## Documentation

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Ledger, deposits, payouts, platforms |
| [docs/RAIN.md](docs/RAIN.md) | Rain pool design and ops |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production install, backup, restart |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat notes and secret handling |

## Public release

Before flipping the GitHub repo to **public**, follow **[docs/PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md)** (secret scan, rotate tokens, keep ops/seed scripts private).

## License / contributing

MIT — see [LICENSE](LICENSE).

Do not open PRs that include `.env`, wallet files, seeds, backups, or production tokens. See [docs/SECURITY.md](docs/SECURITY.md).
