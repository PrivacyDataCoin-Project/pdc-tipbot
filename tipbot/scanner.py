"""Background deposit scanner using get_bulk_payments."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tipbot.amounts import format_amount
from tipbot.config import Settings
from tipbot.db import Database, UserRow
from tipbot.wallet import WalletClient, WalletError

logger = logging.getLogger("pdc-tipbot.scanner")

META_HEIGHT = "deposit_scan_height"

NotifyFn = Callable[[str, str, str], Awaitable[None]]


@dataclass
class DepositEvent:
    kind: str  # "pending" | "credited"
    user: UserRow
    amount_atomic: int
    tx_hash: str
    block_height: int
    confirmations: int
    required: int


class DepositScanner:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        wallet: WalletClient,
        notify: Optional[NotifyFn] = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.wallet = wallet
        self.notify = notify
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not self.wallet.enabled:
            logger.info("deposit scanner idle (wallet dry-run)")
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="deposit-scanner")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        logger.info("deposit scanner started")
        while not self._stop.is_set():
            try:
                events = await asyncio.to_thread(self.scan_once)
                if events and self.notify:
                    for item in events:
                        await self._notify_deposit(item)
            except Exception:
                logger.exception("deposit scan failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.deposit_scan_interval_sec,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("deposit scanner stopped")

    def _tx_url(self, tx_hash: str) -> str:
        return f"{self.settings.explorer_tx_url.rstrip('/')}/{tx_hash}"

    async def _notify_deposit(self, item: DepositEvent) -> None:
        if not self.notify:
            return
        amount_s = format_amount(item.amount_atomic)
        shown = min(item.confirmations, item.required)
        tx_url = self._tx_url(item.tx_hash)
        if item.kind == "pending":
            if item.block_height <= 0:
                conf_line = "Status: in mempool / awaiting confirmation"
            else:
                conf_line = f"Confirmations: {shown}/{item.required} pending"
            text = (
                f"Incoming deposit (not yet spendable): {amount_s} PDC\n"
                f"{conf_line}\n"
                f"Tx: {tx_url}"
            )
        else:
            text = (
                f"Deposit unlocked: {amount_s} PDC\n"
                f"Confirmations: {item.required}/{item.required} unlocked\n"
                f"Your tipbot balance was credited.\n"
                f"Tx: {tx_url}"
            )
        if item.user.platform == "system":
            # Rain pool (and other system ledgers): credit only, no chat DM.
            logger.info(
                "system deposit %s %s tx=%s user=%s",
                item.kind,
                format_amount(item.amount_atomic),
                item.tx_hash,
                item.user.id,
            )
            return
        try:
            await self.notify(
                item.user.platform, item.user.platform_user_id, text
            )
        except Exception:
            logger.exception(
                "deposit notify failed user=%s tx=%s",
                item.user.id,
                item.tx_hash,
            )

    def _notify_key(self, tx_hash: str, payment_id: str) -> str:
        return f"deposit_notify:{tx_hash}:{payment_id.lower()}"

    def _fetch_payments(self, payment_ids: list[str], min_height: int):
        """Merge watermark scan with height=0 (mempool / awaiting_in) payments.

        Wallet RPC hides block_height=0 rows when min_block_height > 0, so a
        pure watermark scan would miss unconfirmed deposits until they confirm.
        """
        from tipbot.wallet import Payment

        by_key: dict[tuple[str, str], Payment] = {}
        for mh in {0, max(0, min_height)}:
            for pay in self.wallet.get_bulk_payments(
                payment_ids, mh, allow_locked=True
            ):
                key = (pay.tx_hash, pay.payment_id)
                prev = by_key.get(key)
                if prev is None or pay.block_height > prev.block_height:
                    by_key[key] = pay
        return list(by_key.values())

    @staticmethod
    def _deposit_ready(
        *,
        block_height: int,
        confirmations: int,
        required: int,
        unlock_time: int,
        tip_height: int,
    ) -> bool:
        """Credit only after inclusion + confirmations + unlock (not mempool)."""
        if block_height <= 0:
            return False
        if confirmations < required:
            return False
        # unlock_time 0 = spendable; otherwise unlock at/after that block height
        if unlock_time > 0 and unlock_time > tip_height:
            return False
        return True

    def scan_once(self) -> list[DepositEvent]:
        payment_ids = self.db.all_payment_ids()
        if not payment_ids:
            return []
        min_height = int(self.db.get_meta(META_HEIGHT, "0") or "0")
        try:
            payments = self._fetch_payments(payment_ids, min_height)
            tip_height = self.wallet.get_height()
        except WalletError as exc:
            logger.warning("wallet scan error: %s", exc)
            return []

        if tip_height <= 0:
            logger.warning("deposit scan skipped: unknown chain height")
            return []

        events: list[DepositEvent] = []
        required = max(1, int(self.settings.deposit_min_confirmations))

        for pay in payments:
            user = self.db.get_user_by_payment_id(pay.payment_id)
            if not user or pay.amount <= 0:
                continue

            confs = max(0, tip_height - pay.block_height) if pay.block_height else 0
            shown = min(confs, required)
            already = self.db.has_deposit(pay.tx_hash, pay.payment_id)
            nkey = self._notify_key(pay.tx_hash, pay.payment_id)
            last_raw = self.db.get_meta(nkey, "")
            last_confs = -1
            last_phase = ""
            if last_raw:
                parts = last_raw.split(":", 1)
                try:
                    last_confs = int(parts[0])
                except ValueError:
                    last_confs = -1
                if len(parts) > 1:
                    last_phase = parts[1]

            ready = self._deposit_ready(
                block_height=pay.block_height,
                confirmations=confs,
                required=required,
                unlock_time=pay.unlock_time,
                tip_height=tip_height,
            )

            if not already and not ready:
                # Pending: notify on first sight or when confirmations increase.
                if last_phase != "pending" or shown > last_confs:
                    events.append(
                        DepositEvent(
                            kind="pending",
                            user=user,
                            amount_atomic=pay.amount,
                            tx_hash=pay.tx_hash,
                            block_height=pay.block_height,
                            confirmations=confs,
                            required=required,
                        )
                    )
                    self.db.set_meta(nkey, f"{shown}:pending")
                continue

            if not already and ready:
                ok = self.db.credit_deposit(
                    user_id=user.id,
                    payment_id=pay.payment_id,
                    tx_hash=pay.tx_hash,
                    amount=pay.amount,
                    block_height=pay.block_height,
                )
                if ok:
                    events.append(
                        DepositEvent(
                            kind="credited",
                            user=user,
                            amount_atomic=pay.amount,
                            tx_hash=pay.tx_hash,
                            block_height=pay.block_height,
                            confirmations=confs,
                            required=required,
                        )
                    )
                    self.db.set_meta(nkey, f"{required}:credited")
                    logger.info(
                        "credited deposit %s PDC to user %s tx=%s confs=%s",
                        format_amount(pay.amount),
                        user.id,
                        pay.tx_hash,
                        confs,
                    )
                continue

            # Already credited — ensure notify marker is final.
            if last_phase != "credited":
                self.db.set_meta(nkey, f"{required}:credited")

        safe = max(0, tip_height - required - 5)
        if safe > min_height:
            self.db.set_meta(META_HEIGHT, str(safe))
        return events
