# Deployment

This document describes a typical production layout. **Do not commit** real tokens, passwords, seeds, or `.env` files.

## Prerequisites

- Linux host with Python 3.11+ (3.12 recommended)
- PDC daemon + `simplewallet` with RPC on **localhost only**
- Telegram bot token (BotFather)
- Matrix account for the tipbot (password or access token)
- systemd (optional but recommended)

## Directory layout (example)

```text
/opt/pdc-tipbot/                  # application (git / rsync)
  tipbot/ … tests/ deploy/ docs/
  .env                            # secrets — mode 600, not in git
  data/                           # sqlite + matrix store (gitignored)
  wallet/                         # hot wallet files (gitignored)
  .venv/

/opt/pdc-tipbot-secrets/          # tokens, wallet password, seed copies (private)
/opt/pdc-tipbot-backup/           # daily snapshots (mode 700)
```

## Configuration

1. Copy `.env.example` → `.env` on the server.
2. Fill secrets from your secrets store (never from git history).
3. Set rain allowlists and `MATRIX_ALLOWED_ROOMS` as needed.
4. Point `WALLET_RPC_URL` / `DAEMON_RPC_URL` at localhost RPC endpoints.

`deploy/install_remote.sh.example` shows how to regenerate a baseline `.env` from a secrets directory. Copy it privately, review output before restart so Matrix tokens / allowlists are not wiped unexpectedly.

## Wallet RPC

Bind wallet RPC to loopback only. Example shape (operator-specific paths omitted):

```bash
simplewallet \
  --wallet-file /path/to/tipbot \
  --password-file /path/to/wallet.password \
  --rpc-bind-ip 127.0.0.1 \
  --rpc-bind-port <port> \
  --daemon-address 127.0.0.1:<daemon-port>
```

Units: copy `deploy/pdc-tipbot-wallet.service.example` and `deploy/pdc-tipbot.service.example`, edit paths, install under `/etc/systemd/system/`.

## Install / update

```bash
# On the app host, after syncing code (git pull or rsync of tracked files only):
cd /path/to/pdc-tipbot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
PYTHONPATH=. ./.venv/bin/pytest -q    # must be green before restart
sudo systemctl restart pdc-tipbot-wallet.service   # if wallet unit changed
sudo systemctl restart pdc-tipbot.service
systemctl is-active pdc-tipbot.service
journalctl -u pdc-tipbot -n 50 --no-pager
```

**Do not** rsync or publish: `.env`, `data/`, `wallet/`, `secrets/`, `.venv/`.

Startup refuses to run if ledger liabilities exceed hot wallet total.

## Matrix notes

- Prefer access token + device id after first password login.
- Encrypted rooms need `matrix-nio[e2e]` / `python-olm`.
- Invite `@pdctipbot:…` to allowed rooms; users should DM the bot once for private replies.

## Backups

`deploy/backup_daily.sh.example` (set `TIPBOT_BACKUP_ROOT`; never commit backup output):

- SQLite snapshot
- Ledger address map / snapshot JSON
- Hot wallet file
- Secret material references (treat as highly sensitive)

Retention and cron are operator-defined (example: 14 days, ~03:15 UTC). Backup directories should be mode `700`.

## Health checks

| Check | How |
|---|---|
| Process | `systemctl is-active pdc-tipbot` |
| Logs | `journalctl -u pdc-tipbot` |
| Solvency | Admin `/totalbalance` in DM |
| Rain | `/rain info` |
| Deposits | Pending DM / logs; credit after unlock |

## Rollback

Keep the previous code tree or git tag. Restore `.env` / `data/` / `wallet/` only from known-good backups. Never restore secrets into the git working tree that you push.
