# Rain (communal faucet)

`/rain` is a shared tipbot pool that users can top up and claim from on a cooldown. It is **not** the same as Wrkz-style timed `/freetip` splits; closer to a fixed/dynamic faucet with a funded pool.

## User commands

| Command | Where | Effect |
|---|---|---|
| `/rain` or `/rain claim` | Whitelisted public chats only | Claim current dynamic amount |
| `/rain info` | Chat or DM | Pool size, next claim size, cooldown |
| `/rain fund <amount>` | Chat or DM | Move your tipbot balance into the pool |
| `/rain deposit` | Prefer DM | Integrated address; on-chain top-ups credit the pool |
| `/rain pause` / `/rain resume` | Admin | Disable / enable claims |

Matrix: use `!rain` or `/rain`.

## Allowlist

Configured via env (`RAIN_TELEGRAM_CHAT_IDS`, `RAIN_MATRIX_ROOM_IDS`).  
**Defaults are empty** (claims disabled until configured). Chat/room ids are public identifiers, not secrets — examples are commented in `.env.example`.

Claims outside the allowlist are rejected. Funding and info may be used in DMs.

## Claim rules

- **Cooldown:** rolling **24 hours** from the last successful claim (`RAIN_COOLDOWN_SEC`), per `(platform, platform_user_id)`.
- **Bots:** rejected (`is_bot`).
- **No account-age gate** in v1.
- **Telegram vs Matrix:** separate ledger identities → separate cooldowns.
- **Paused:** admin pause blocks claims only (funding still allowed).

## Dynamic claim size

```text
ideal = floor(pool_balance / RAIN_TARGET_CLAIMS_PER_DAY)
if ideal >= RAIN_CLAIM_MIN:
    claim = min(ideal, RAIN_CLAIM_MAX)
elif pool_balance >= RAIN_CLAIM_MIN:
    claim = RAIN_CLAIM_MIN          # slow drain
else:
    refuse (“pool empty / too small”)
```

Defaults:

| Variable | Default | Meaning |
|---|---|---|
| `RAIN_TARGET_CLAIMS_PER_DAY` | `10` | Sizing target |
| `RAIN_CLAIM_MIN_ATOMIC` | `0.1 PDC` | Floor |
| `RAIN_CLAIM_MAX_ATOMIC` | `1 PDC` | Ceiling |
| `RAIN_COOLDOWN_SEC` | `86400` | 24h |

Example: pool **10 PDC** → claim **1 PDC** (~10 claims at that size).

## Funding

1. **`/rain fund`** — immediate ledger transfer into `system/rain`.
2. **On-chain** to `/rain deposit` — same deposit scanner as users:
   - Visible while unconfirmed (`awaiting_in` / `block_height = 0`)
   - **Ledger credit only after** inclusion + confirmations + unlock
   - No DM for the system rain account (logged only)

There is **no automatic channel announcement** when the pool is topped up; operators should post manually if desired.

## Data model

- System user: `platform=system`, `platform_user_id=rain`
- Table `rain_claims` — audit + cooldown
- Ledger kinds: `rain_fund`, `rain_fund_in`, `rain_claim`, `rain_claim_out`
- Meta flag `rain_paused`

All rain balance counts toward total liabilities for solvency checks.

## Ops checklist

1. Ensure `RAIN_ENABLED=1` and allowlists match live chats.
2. Seed the pool (`fund` or on-chain deposit; wait for unlock on-chain).
3. Announce in Telegram / Matrix General.
4. Monitor with `/rain info` and admin `/totalbalance`.
5. `pause` if abuse or empty-pool spam becomes a problem.
