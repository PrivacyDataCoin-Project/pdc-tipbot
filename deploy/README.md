# Deploy helpers

Templates and examples for running PDC TipBot in production.

## What belongs here

| File | Purpose |
|---|---|
| `pdc-tipbot.service.example` | systemd unit for the bot (edit paths) |
| `pdc-tipbot-wallet.service.example` | systemd unit for localhost wallet RPC |
| `install_remote.sh.example` | Example bootstrap of `.env` from a **local** secrets dir |
| `backup_daily.sh.example` | Example daily backup of DB + wallet + secret refs |
| `prepare_matrix.sh` | Optional Matrix `.env` helpers (no secrets in git) |
| `install_backup_cron.sh.example` | Example cron installer |

## What must **not** be in git

- Wallet seed / view key / password files
- Telegram bot token, Matrix password / access token
- Live `.env`, SQLite DB, Matrix E2EE store, wallet files
- Scripts that print or write mnemonics into the working tree

Operator-only seed export / restore tooling should live **outside** this repository (private ops folder or encrypted backup procedures).

## Hardening checklist before public mirrors

1. Copy `*.example` → real unit/script names on the server only.
2. Bind wallet RPC to `127.0.0.1`.
3. Secrets directory mode `700`; files mode `600`.
4. Run tests before restarting systemd.
5. Never commit generated `.env` or backup tarballs back into this repo.
