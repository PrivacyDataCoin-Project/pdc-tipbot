"""Process entry: run Telegram and/or Matrix tipbot + deposit scanner."""

from __future__ import annotations

import asyncio
import logging
import signal

from tipbot.config import load_settings
from tipbot.db import Database
from tipbot.platforms.matrix_app import MatrixBot
from tipbot.platforms.telegram_app import TelegramBot
from tipbot.scanner import DepositScanner
from tipbot.service import TipService
from tipbot.wallet import WalletClient

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("pdc-tipbot")


async def run() -> None:
    settings = load_settings()
    if not settings.telegram_enabled and not settings.matrix_enabled:
        raise SystemExit(
            "Configure TELEGRAM_TOKEN and/or MATRIX_ACCESS_TOKEN|MATRIX_PASSWORD "
            "(see .env.example)."
        )

    db = Database(settings.database_path)
    wallet = WalletClient(settings)
    service = TipService(settings, db, wallet)

    telegram: TelegramBot | None = None
    matrix: MatrixBot | None = None

    if settings.telegram_enabled:
        telegram = TelegramBot(settings, service)
    if settings.matrix_enabled:
        matrix = MatrixBot(settings, service)

    async def notify_user(platform: str, user_id: str, text: str) -> None:
        if platform == "telegram" and telegram:
            await telegram.notify_user(user_id, text)
        elif platform == "matrix" and matrix:
            await matrix.notify_user(user_id, text)

    scanner = DepositScanner(settings, db, wallet, notify=notify_user)
    # Tip/giveaway same-platform notify stays in adapters.
    # Telegram ↔ Matrix identity link is not in v0.1 (separate ledger accounts).

    stop_event = asyncio.Event()

    def _ask_stop(*_args: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _ask_stop)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda *_: _ask_stop())

    mode = "live" if wallet.enabled else "dry-run"
    logger.info(
        "Starting PDC TipBot (%s) telegram=%s matrix=%s db=%s",
        mode,
        settings.telegram_enabled,
        settings.matrix_enabled,
        settings.database_path,
    )
    recovered = service.recover_payouts()
    if recovered["refunded"] or recovered["suspect"]:
        logger.warning("Payout recovery: %s", recovered)
    if wallet.enabled:
        try:
            addr = await asyncio.to_thread(wallet.get_address)
            bal = await asyncio.to_thread(wallet.get_balance_native)
            logger.info("Hot wallet %s balance_atomic=%s", addr, bal)
            liabilities = db.total_liabilities()
            if liabilities > bal:
                logger.error(
                    "INVARIANT FAIL: ledger liabilities %s > wallet %s "
                    "(on-chain send/withdraw blocked until topped up or clawed back)",
                    liabilities,
                    bal,
                )
        except Exception:
            logger.exception("wallet probe failed — continuing")

    scanner.start()
    if telegram:
        await telegram.start()
    if matrix:
        await matrix.start()

    await stop_event.wait()
    logger.info("Shutting down…")
    await scanner.stop()
    if telegram:
        await telegram.stop()
    if matrix:
        await matrix.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
