# Making this repository public

Checklist for operators before switching GitHub visibility to **Public**.

## Already verified in git history (as of public-prep)

- [x] No Telegram bot tokens, Matrix passwords, or wallet mnemonics in tracked files
- [x] No production `.env` / DB / wallet binaries in git
- [x] Seed export scripts removed from the public tree (keep privately under ops/secrets)
- [x] Deploy units/scripts shipped as `*.example` with generic paths
- [x] Rain/admin allowlists empty by default (set only in private `.env`)
- [x] English docs + `SECURITY.md` present
- [x] MIT `LICENSE` added

## You must do before / right after going public

1. **Confirm GitHub secret scanning**  
   Repository → Settings → Code security → enable secret scanning + push protection.

2. **Rotate credentials** (recommended even if history looks clean)  
   - Telegram bot token (BotFather → revoke/regenerate)  
   - Matrix account password / access token  
   - Hot-wallet RPC password file permissions (not the seed unless you also migrate funds)

3. **Keep production secrets off the clone you push from**  
   Never `git add` `.env`, `data/`, `wallet/`, backup tarballs, or `RESTORE.txt`.

4. **Server deploy path**  
   Production scripts were archived under the host secrets `ops/` directory.  
   Do not rsync `--delete` in a way that wipes live systemd units or backup cron without restoring from that archive.

5. **Set production `.env` allowlists explicitly**  
   `ADMIN_*`, `RAIN_*`, `MATRIX_ALLOWED_ROOMS` — empty defaults mean rain claims stay disabled until configured.

6. **Switch visibility**  
   ```bash
   gh repo edit PrivacyDataCoin-Project/pdc-tipbot --visibility public
   ```
   (Requires org permission; confirm no private issues/wiki with secrets.)

7. **Announce custodial risk**  
   README already states the bot holds keys. Do not market it as non-custodial.

## Optional hardening

- Add GitHub Actions: `pytest` on PR + `gitleaks` / `trufflehog`
- CODEOWNERS for `deploy/` and `docs/SECURITY.md`
- Separate private ops repo for seed export / restore runbooks
- Freeze hot-wallet balance policy in an internal ops doc (not public)

## Do not publish

| Item | Why |
|---|---|
| Seed / view key / spend key | Full fund theft |
| Telegram / Matrix tokens | Bot takeover |
| Live SQLite ledger | User balances + payment_ids |
| Backup folders | Usually contain seed copies |
| Filled systemd units with host paths | Optional; prefer examples only |
