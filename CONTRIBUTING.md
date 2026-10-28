# Contributing

## Rules

- Do not commit secrets (`.env`, tokens, seeds, wallet files, backups).
- Prefer PRs against `master` with focused commits.
- Run `PYTHONPATH=. pytest` before opening a PR.
- Keep documentation in English.

## Code style

- Match existing module patterns (`TipService` for business logic; thin platform adapters).
- Atomic SQLite transactions for any balance mutation.
- No AI / Cursor trailers in commit messages.

## Security issues

Do not file public issues with exploit details that could drain a custodial hot wallet. Contact operators privately first (see `docs/SECURITY.md`).
