"""Shared tipbot business logic for all platforms."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from tipbot.amounts import compute_rain_claim_amount, format_amount, parse_amount
from tipbot.config import Settings
from tipbot.db import Database, UserRow
from tipbot.wallet import WalletClient, WalletError

logger = logging.getLogger("pdc-tipbot.service")

def _cmd_prefix(platform: str) -> str:
    """User-facing command prefix: Matrix prefers !, Telegram uses /."""
    return "!" if platform == "matrix" else "/"


def help_text(platform: str = "telegram") -> str:
    p = _cmd_prefix(platform)
    lines = [
        "PDC TipBot — custodial ledger (operator holds keys).",
        "",
        "Commands:",
        f"  {p}start — open account + deposit address",
        f"  {p}balance — private: spendable balance + deposit unlock status",
        f"  {p}deposit — show your integrated deposit address",
        f"  {p}tip <@user|reply> <amount> [message]",
        f"  {p}giveaway <amount> — group/room giveaway (Claim / Cancel)",
        f"  {p}claim <id> — claim a Matrix giveaway",
        f"  {p}cancel <id> — cancel your giveaway",
        f"  {p}rain — claim from the communal rain pool (whitelisted chats)",
        f"  {p}rain info|fund|deposit — pool status / top-up / deposit address",
        f"  {p}withdraw <address|@alias> — all unlocked balance minus network fee",
        f"  {p}send <address|@alias> <amount> — send amount (max unlocked − fee)",
        f"  {p}help — this text",
        "",
        "Tips and giveaways between users are instant (off-chain ledger).",
        "Deposits and withdrawals are on-chain via the hot wallet.",
    ]
    if platform == "matrix":
        lines.append(
            "Prefix on Matrix: ! (also accepts /), "
            "e.g. !tip @alice:privacydatacoin.com 1."
        )
    else:
        lines.append(
            "On Matrix the same bot uses ! "
            "(e.g. !tip @alice:privacydatacoin.com 1)."
        )
    return "\n".join(lines)


# Back-compat for imports/tests
HELP_TEXT = help_text("telegram")


@dataclass
class CommandResult:
    text: str
    private: bool = False
    notify_user_id: Optional[str] = None
    notify_platform: Optional[str] = None
    notify_text: Optional[str] = None
    # Extra DMs: list of (platform, platform_user_id, text)
    extra_notifies: list[tuple[str, str, str]] = field(default_factory=list)
    # Telegram inline buttons: list of rows of (label, callback_data)
    buttons: Optional[list[list[tuple[str, str]]]] = None
    giveaway_id: Optional[str] = None
    # For group tips: numeric Telegram id to deep-link mention in the public reply
    tip_telegram_id: Optional[str] = None
    tip_display_name: Optional[str] = None

    def all_notifies(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        if self.notify_platform and self.notify_user_id and self.notify_text:
            out.append((self.notify_platform, self.notify_user_id, self.notify_text))
        out.extend(self.extra_notifies)
        return out


class TipService:
    def __init__(self, settings: Settings, db: Database, wallet: WalletClient) -> None:
        self.settings = settings
        self.db = db
        self.wallet = wallet

    def ensure_user(
        self, platform: str, platform_user_id: str, display_name: str = ""
    ) -> UserRow:
        existing = self.db.get_user(platform, platform_user_id)
        if existing:
            if display_name and display_name != existing.display_name:
                self.db.update_display_name(existing.id, display_name)
                existing = self.db.get_user_by_id(existing.id) or existing
            if not existing.integrated_address:
                addr = self._integrated_for(existing.payment_id)
                self.db.update_integrated_address(existing.id, addr)
                existing = self.db.get_user_by_id(existing.id) or existing
            return existing

        payment_id = secrets.token_hex(8)
        try:
            integrated, payment_id = self.wallet.make_integrated_address(payment_id)
        except WalletError as exc:
            logger.warning("wallet make_integrated_address failed: %s", exc)
            integrated = ""
        user = self.db.create_user(
            platform=platform,
            platform_user_id=platform_user_id,
            display_name=display_name or platform_user_id,
            payment_id=payment_id,
            integrated_address=integrated,
        )
        return user

    def _integrated_for(self, payment_id: str) -> str:
        try:
            addr, _ = self.wallet.make_integrated_address(payment_id)
            return addr
        except WalletError as exc:
            logger.warning("wallet make_integrated_address failed: %s", exc)
            return ""

    def handle(
        self,
        platform: str,
        platform_user_id: str,
        display_name: str,
        command: str,
        args: list[str],
        *,
        reply_to_platform_user_id: Optional[str] = None,
        reply_to_display_name: str = "",
        is_private: bool = True,
        chat_id: str = "",
        is_bot: bool = False,
    ) -> CommandResult:
        cmd = command.lower().lstrip("!/")
        user = self.ensure_user(platform, platform_user_id, display_name)

        p = _cmd_prefix(platform)
        if cmd in ("start", "register"):
            return self._cmd_start(user, platform=platform)
        if cmd in ("help", "helpme"):
            return CommandResult(help_text(platform))
        if cmd in ("balance", "bal"):
            return self._cmd_balance(user, platform=platform)
        if cmd == "deposit":
            return self._cmd_deposit(user, private=True, platform=platform)
        if cmd == "tip":
            return self._cmd_tip(
                user,
                platform,
                args,
                reply_to_platform_user_id=reply_to_platform_user_id,
                reply_to_display_name=reply_to_display_name,
                is_private=is_private,
            )
        if cmd == "giveaway":
            return self._cmd_giveaway(
                user, platform, args, is_private=is_private, chat_id=chat_id
            )
        if cmd == "claim":
            return self._cmd_claim(user, platform, args)
        if cmd == "cancel":
            return self._cmd_cancel(user, args, platform=platform)
        if cmd == "rain":
            return self._cmd_rain(
                user,
                platform,
                args,
                is_private=is_private,
                chat_id=chat_id,
                is_bot=is_bot,
                display_name=display_name,
            )
        if cmd == "withdraw":
            return self._cmd_withdraw(user, args, platform=platform)
        if cmd == "send":
            return self._cmd_send(user, args, platform=platform)
        if cmd == "totalbalance":
            return self._cmd_totalbalance(
                user,
                platform=platform,
                display_name=display_name,
                is_private=is_private,
            )
        return CommandResult(f"Unknown command: {cmd}. Try {p}help")

    def claim_giveaway_callback(
        self,
        platform: str,
        claimer_platform_user_id: str,
        claimer_display_name: str,
        giveaway_id: str,
    ) -> CommandResult:
        claimer = self.ensure_user(
            platform, claimer_platform_user_id, claimer_display_name
        )
        return self._finish_claim(claimer, giveaway_id)

    def cancel_giveaway_callback(
        self,
        platform: str,
        requester_platform_user_id: str,
        requester_display_name: str,
        giveaway_id: str,
    ) -> CommandResult:
        user = self.ensure_user(
            platform, requester_platform_user_id, requester_display_name
        )
        return self._finish_cancel(user, giveaway_id)
    def _cmd_start(self, user: UserRow, *, platform: str = "telegram") -> CommandResult:
        p = _cmd_prefix(platform)
        deposit = self._cmd_deposit(user, private=True, platform=platform)
        mode = "live wallet RPC" if self.wallet.enabled else "dry-run (no chain)"
        text = (
            f"Welcome to {self.settings.bot_display_name}.\n"
            f"Mode: {mode}\n"
            f"Your ledger account is ready.\n\n"
            f"{deposit.text}\n\n"
            f"Use {p}tip to tip other users. Funds tip off-chain; withdraw on-chain."
        )
        return CommandResult(text, private=True)

    def _cmd_totalbalance(
        self,
        user: UserRow,
        *,
        platform: str,
        display_name: str,
        is_private: bool,
    ) -> CommandResult:
        # Hidden admin command — do not advertise in /help.
        if not is_private or not self.settings.is_admin(
            platform, user.platform_user_id, display_name
        ):
            return CommandResult(
                f"Unknown command: totalbalance. Try {_cmd_prefix(platform)}help"
            )

        lines: list[str] = ["=== TipBot totalbalance (admin) ==="]
        if self.wallet.enabled:
            try:
                total = self.wallet.get_balance_native()
                unlocked = self.wallet.get_unlocked_native()
                lines.append(
                    f"Hot wallet total: {format_amount(total)} PDC"
                )
                lines.append(
                    f"Hot wallet unlocked: {format_amount(unlocked)} PDC"
                )
            except WalletError as exc:
                lines.append(f"Hot wallet: error ({exc})")
        else:
            lines.append("Hot wallet: dry-run / offline")

        users = self.db.list_users()
        liabilities = self.db.total_liabilities()
        open_held = liabilities - sum(u.balance_atomic for u in users)
        lines.append(f"Ledger liabilities: {format_amount(liabilities)} PDC")
        if open_held > 0:
            lines.append(
                f"  (incl. open giveaway escrow ≈ {format_amount(open_held)} PDC)"
            )
        if self.wallet.enabled:
            try:
                hot = self.wallet.get_balance_native()
                if liabilities > hot:
                    lines.append(
                        f"SOLVENCY FAIL: liabilities exceed hot wallet by "
                        f"{format_amount(liabilities - hot)} PDC"
                    )
            except WalletError:
                pass
        orphans = self.db.list_open_payouts()
        if orphans:
            lines.append("")
            lines.append(f"Open/suspect payouts ({len(orphans)}):")
            for p in orphans:
                total = p.amount_atomic + p.fee_atomic
                lines.append(
                    f"  #{p.id} user={p.user_id} {p.status} "
                    f"{format_amount(total)} PDC {p.kind}"
                )
        lines.append("")
        lines.append("Unlocked per account (ledger):")
        if not users:
            lines.append("  (no users)")
        else:
            for u in users:
                who = u.display_name or u.platform_user_id
                lines.append(
                    f"• [{u.platform}] {who} "
                    f"({u.platform_user_id}): {format_amount(u.balance_atomic)} PDC"
                )
        return CommandResult("\n".join(lines), private=True)

    def _cmd_balance(
        self, user: UserRow, *, platform: str = "telegram"
    ) -> CommandResult:
        p = _cmd_prefix(platform)
        needed = max(1, int(self.settings.deposit_min_confirmations))
        lines = [
            f"Spendable balance: {format_amount(user.balance_atomic)} PDC",
            f"(available for {p}tip and {p}giveaway)",
            f"Unlock requirement: {needed}/{needed} confirmations",
        ]

        pending_lines = self._pending_deposit_lines(user, needed)
        if pending_lines:
            lines.append("")
            lines.append("Deposits:")
            lines.extend(pending_lines)
        else:
            lines.append("")
            lines.append("Deposits: none pending")

        return CommandResult("\n".join(lines), private=True)

    def _pending_deposit_lines(self, user: UserRow, needed: int) -> list[str]:
        if not self.wallet.enabled:
            return ["(wallet offline — pending status unavailable)"]
        try:
            tip_height = self.wallet.get_height()
            payments = self.wallet.get_bulk_payments(
                [user.payment_id],
                min_block_height=0,
                allow_locked=True,
            )
        except WalletError as exc:
            logger.warning("balance pending lookup failed: %s", exc)
            return [f"(could not check chain: {exc})"]

        lines: list[str] = []
        for pay in sorted(payments, key=lambda p: p.block_height or 0):
            # Already credited → counted in spendable balance; skip detail noise.
            if self.db.has_deposit(pay.tx_hash, pay.payment_id):
                continue

            if tip_height > 0 and pay.block_height > 0:
                confs = max(0, tip_height - pay.block_height)
            else:
                confs = 0
            shown = min(confs, needed)
            if pay.block_height <= 0:
                status = f"?/{needed} pending (awaiting block height)"
            elif pay.unlock_time and confs < needed:
                status = f"{shown}/{needed} pending"
            elif pay.unlock_time:
                status = f"{needed}/{needed} pending (timelock)"
            elif confs >= needed:
                status = f"{needed}/{needed} unlocked"
            else:
                status = f"{shown}/{needed} pending"
            lines.append(
                f"• {format_amount(pay.amount)} PDC — {status}"
                f"\n  {self.settings.explorer_tx_url.rstrip('/')}/{pay.tx_hash}"
            )

        if len(lines) > 8:
            lines = lines[-8:]
            lines.insert(0, "(showing latest 8 pending deposits)")
        return lines

    def _cmd_deposit(
        self, user: UserRow, private: bool, *, platform: str = "telegram"
    ) -> CommandResult:
        p = _cmd_prefix(platform)
        addr = user.integrated_address or self._integrated_for(user.payment_id)
        if addr and addr != user.integrated_address:
            self.db.update_integrated_address(user.id, addr)
        if not addr:
            addr = (
                f"(wallet offline — payment_id={user.payment_id}; "
                f"retry {p}deposit when RPC is up)"
            )
        text = (
            f"Deposit address (integrated, only for you):\n`{addr}`\n"
            f"payment_id: `{user.payment_id}`\n"
            f"Send native PDC only. Credits after "
            f"~{self.settings.deposit_min_confirmations} confirmations."
        )
        return CommandResult(text, private=private)

    def _cmd_tip(
        self,
        sender: UserRow,
        platform: str,
        args: list[str],
        *,
        reply_to_platform_user_id: Optional[str],
        reply_to_display_name: str,
        is_private: bool,
    ) -> CommandResult:
        recipient_ref: Optional[str] = None
        amount_raw: Optional[str] = None
        message_parts: list[str] = []

        if (
            reply_to_platform_user_id
            and args
            and _looks_like_amount(args[0])
        ):
            amount_raw = args[0]
            message_parts = args[1:]
            recipient_ref = reply_to_platform_user_id
        elif len(args) >= 2:
            recipient_ref = args[0]
            amount_raw = args[1]
            message_parts = args[2:]
        else:
            p = _cmd_prefix(platform)
            return CommandResult(
                f"Usage: {p}tip <@user> <amount> [message]\n"
                f"Or reply to a message: {p}tip <amount> [message]\n"
                f"Minimum tip: {format_amount(self.settings.min_tip_atomic)} PDC"
            )

        try:
            amount = parse_amount(amount_raw or "")
        except ValueError as exc:
            return CommandResult(f"Bad amount: {exc}")

        if amount < self.settings.min_tip_atomic:
            return CommandResult(
                f"Minimum tip is {format_amount(self.settings.min_tip_atomic)} PDC"
            )

        recipient = self._resolve_recipient(
            platform,
            recipient_ref or "",
            reply_to_platform_user_id=reply_to_platform_user_id,
            reply_to_display_name=reply_to_display_name,
        )
        if recipient is None:
            p = _cmd_prefix(platform)
            if is_private:
                return CommandResult(
                    "Recipient not found. Tip new users in a group/room "
                    f"(they get an account automatically), or ask them to {p}start first."
                )
            # Create placeholder account for unknown handle in public chat.
            handle = (recipient_ref or "").lstrip("@").strip()
            if not handle:
                return CommandResult("Could not resolve recipient.")
            if platform == "telegram" and not handle.isdigit():
                # Prefer an existing ledger user by display/username before creating
                # a phantom username-keyed account.
                existing = self.db.find_user_by_display(platform, handle)
                if existing:
                    recipient = existing
                else:
                    recipient = self.ensure_user(
                        platform, handle.lower(), f"@{handle.lstrip('@')}"
                    )
            elif platform == "matrix":
                mxid = handle if handle.startswith("@") else recipient_ref
                if not mxid or ":" not in str(mxid):
                    return CommandResult(
                        "On Matrix tip with a full MXID, e.g. "
                        "!tip @alice:privacydatacoin.com 1"
                    )
                recipient = self.ensure_user(platform, str(mxid), str(mxid))
            else:
                return CommandResult("Could not resolve recipient.")

        note = " ".join(message_parts).strip()
        try:
            self.db.tip(sender.id, recipient.id, amount, note=note)
        except ValueError as exc:
            return CommandResult(str(exc).capitalize())

        who = recipient.display_name or recipient.platform_user_id
        text = f"Tipped {who} with {format_amount(amount)} PDC."
        if note:
            text += f" Message: {note}"
        notify = (
            f"You received a tip of {format_amount(amount)} PDC "
            f"from {sender.display_name or sender.platform_user_id}."
        )
        if note:
            notify += f"\nMessage: {note}"
        tip_tg_id = None
        if platform == "telegram" and recipient.platform_user_id.isdigit():
            tip_tg_id = recipient.platform_user_id
        return CommandResult(
            text,
            notify_platform=recipient.platform,
            notify_user_id=recipient.platform_user_id,
            notify_text=notify,
            tip_telegram_id=tip_tg_id,
            tip_display_name=who,
        )

    def _resolve_recipient(
        self,
        platform: str,
        recipient_ref: str,
        *,
        reply_to_platform_user_id: Optional[str],
        reply_to_display_name: str,
    ) -> Optional[UserRow]:
        if reply_to_platform_user_id and (
            not recipient_ref
            or recipient_ref.lstrip("@") == reply_to_platform_user_id.lstrip("@")
            or recipient_ref.replace(",", "").replace(".", "").isdigit()
        ):
            # amount-only reply form already handled; if reply id present and
            # recipient_ref looks like amount we already branched. Here recipient_ref
            # is an id/handle.
            user = self.db.get_user(platform, reply_to_platform_user_id)
            if user:
                return user
            return self.ensure_user(
                platform, reply_to_platform_user_id, reply_to_display_name
            )

        ref = recipient_ref.strip()
        if not ref:
            return None
        if platform == "matrix":
            mxid = ref if ref.startswith("@") else f"@{ref}"
            # Normalize bare localpart tip: require domain for new users
            user = self.db.get_user(platform, mxid)
            if user:
                return user
            user = self.db.find_user_by_display(platform, mxid)
            return user

        handle = ref.lstrip("@")
        # Prefer numeric telegram id
        user = self.db.get_user(platform, handle)
        if user:
            return user
        user = self.db.find_user_by_display(platform, handle)
        return user

    def _cmd_giveaway(
        self,
        sender: UserRow,
        platform: str,
        args: list[str],
        *,
        is_private: bool,
        chat_id: str,
    ) -> CommandResult:
        if is_private:
            return CommandResult(
                "Giveaways can only be made in groups/rooms. Aborting."
            )
        if not args:
            p = _cmd_prefix(platform)
            return CommandResult(
                f"Usage: {p}giveaway <amount>\n"
                f"Minimum: {format_amount(self.settings.min_tip_atomic)} PDC"
            )
        try:
            amount = parse_amount(args[0])
        except ValueError as exc:
            return CommandResult(f"Bad amount: {exc}")
        if amount < self.settings.min_tip_atomic:
            return CommandResult(
                f"Minimum giveaway is {format_amount(self.settings.min_tip_atomic)} PDC"
            )
        if not chat_id:
            return CommandResult("Missing chat/room id for giveaway.")

        giveaway_id = secrets.token_hex(4)
        try:
            self.db.create_giveaway(
                giveaway_id=giveaway_id,
                platform=platform,
                chat_id=chat_id,
                sender_user_id=sender.id,
                amount=amount,
            )
        except ValueError as exc:
            return CommandResult(str(exc).capitalize())

        who = sender.display_name or sender.platform_user_id
        amount_s = format_amount(amount)
        if platform == "telegram":
            text = (
                f"{who} is giving {amount_s} PDC away.\n"
                f"Click Claim to take it (or Cancel to refund)."
            )
            buttons = [
                [
                    ("Claim", f"ga_claim:{giveaway_id}"),
                    ("Cancel", f"ga_cancel:{giveaway_id}"),
                ]
            ]
            return CommandResult(text, buttons=buttons, giveaway_id=giveaway_id)

        text = (
            f"{who} is giving {amount_s} PDC away (id `{giveaway_id}`).\n"
            f"Claim: !claim {giveaway_id}\n"
            f"Cancel (creator only): !cancel {giveaway_id}"
        )
        return CommandResult(text, giveaway_id=giveaway_id)

    def _cmd_claim(
        self, claimer: UserRow, platform: str, args: list[str]
    ) -> CommandResult:
        if not args:
            p = _cmd_prefix(platform)
            return CommandResult(f"Usage: {p}claim <giveaway_id>")
        return self._finish_claim(claimer, args[0].strip().lower())

    def _cmd_cancel(
        self, user: UserRow, args: list[str], *, platform: str = "telegram"
    ) -> CommandResult:
        if not args:
            p = _cmd_prefix(platform)
            return CommandResult(f"Usage: {p}cancel <giveaway_id>")
        return self._finish_cancel(user, args[0].strip().lower())

    def _finish_claim(self, claimer: UserRow, giveaway_id: str) -> CommandResult:
        ga = self.db.get_giveaway(giveaway_id)
        if not ga or ga.status != "open":
            return CommandResult("Giveaway not found or already claimed.")
        try:
            self.db.claim_giveaway(giveaway_id, claimer.id)
        except ValueError as exc:
            return CommandResult(str(exc).capitalize())

        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        sender = self.db.get_user_by_id(ga.sender_user_id)
        from_name = (
            (sender.display_name or sender.platform_user_id) if sender else "someone"
        )
        amount_s = format_amount(ga.amount_atomic)
        claimer_name = claimer.display_name or claimer.platform_user_id
        text = (
            f"Giveaway claimed by {claimer_name}!\n"
            f"Amount: {amount_s} PDC\n"
            f"When: {when}"
        )
        claimer_dm = (
            f"You claimed {amount_s} PDC from {from_name}.\n"
            f"Giveaway id: `{giveaway_id}`\n"
            f"When: {when}"
        )
        extra: list[tuple[str, str, str]] = []
        if sender and sender.id != claimer.id:
            creator_dm = (
                f"Your giveaway was claimed.\n"
                f"Claimed by: {claimer_name}\n"
                f"Amount: {amount_s} PDC\n"
                f"Giveaway id: `{giveaway_id}`\n"
                f"When: {when}"
            )
            extra.append((sender.platform, sender.platform_user_id, creator_dm))
        return CommandResult(
            text,
            notify_platform=claimer.platform,
            notify_user_id=claimer.platform_user_id,
            notify_text=claimer_dm,
            extra_notifies=extra,
            giveaway_id=giveaway_id,
        )

    def _finish_cancel(self, user: UserRow, giveaway_id: str) -> CommandResult:
        ga = self.db.get_giveaway(giveaway_id)
        if not ga or ga.status != "open":
            return CommandResult("Giveaway not found or already closed.")
        try:
            self.db.cancel_giveaway(giveaway_id, user.id)
        except ValueError as exc:
            return CommandResult(str(exc).capitalize())
        amount_s = format_amount(ga.amount_atomic)
        return CommandResult(
            f"Giveaway cancelled. {amount_s} PDC returned to your balance.",
            giveaway_id=giveaway_id,
        )

    def ensure_rain_pool(self) -> UserRow:
        existing = self.db.get_rain_user()
        if existing:
            if not existing.integrated_address:
                addr = self._integrated_for(existing.payment_id)
                if addr:
                    self.db.update_integrated_address(existing.id, addr)
                    existing = self.db.get_user_by_id(existing.id) or existing
            return existing
        payment_id = secrets.token_hex(8)
        try:
            integrated, payment_id = self.wallet.make_integrated_address(payment_id)
        except WalletError as exc:
            logger.warning("rain make_integrated_address failed: %s", exc)
            integrated = ""
        return self.db.ensure_rain_user(payment_id, integrated)

    def _rain_claim_preview(self, pool_atomic: int) -> tuple[Optional[int], str]:
        try:
            amount = compute_rain_claim_amount(
                pool_atomic,
                target_claims_per_day=self.settings.rain_target_claims_per_day,
                min_atomic=self.settings.rain_claim_min_atomic,
                max_atomic=self.settings.rain_claim_max_atomic,
            )
            return amount, ""
        except ValueError:
            return None, "Rain pool is empty or too small for a claim."

    def _cmd_rain(
        self,
        user: UserRow,
        platform: str,
        args: list[str],
        *,
        is_private: bool,
        chat_id: str,
        is_bot: bool,
        display_name: str,
    ) -> CommandResult:
        p = _cmd_prefix(platform)
        if not self.settings.rain_enabled:
            return CommandResult("Rain is currently disabled.")
        sub = (args[0].lower() if args else "claim").strip()
        rest = args[1:] if args else []

        if sub in ("info", "status", "pool"):
            return self._cmd_rain_info(user, platform)
        if sub in ("deposit", "address"):
            return self._cmd_rain_deposit(platform=platform)
        if sub == "fund":
            return self._cmd_rain_fund(user, rest, platform=platform)
        if sub in ("pause", "resume"):
            return self._cmd_rain_admin(
                user, platform, display_name, pause=(sub == "pause")
            )
        if sub in ("claim", "take"):
            return self._cmd_rain_claim(
                user,
                platform,
                is_private=is_private,
                chat_id=chat_id,
                is_bot=is_bot,
            )
        # Bare /rain or unknown → claim (unless looks like amount for fund typo)
        if not args:
            return self._cmd_rain_claim(
                user,
                platform,
                is_private=is_private,
                chat_id=chat_id,
                is_bot=is_bot,
            )
        return CommandResult(
            f"Usage:\n"
            f"  {p}rain — claim (whitelisted chat)\n"
            f"  {p}rain info\n"
            f"  {p}rain fund <amount>\n"
            f"  {p}rain deposit"
        )

    def _cmd_rain_info(self, user: UserRow, platform: str) -> CommandResult:
        p = _cmd_prefix(platform)
        rain = self.ensure_rain_pool()
        pool = rain.balance_atomic
        amount, err = self._rain_claim_preview(pool)
        paused = self.db.rain_paused()
        last = self.db.last_rain_claim_at(user.id)
        lines = [
            "Rain pool",
            f"Available: {format_amount(pool)} PDC",
            f"Status: {'paused' if paused else 'open'}",
            f"Cooldown: {self.settings.rain_cooldown_sec // 3600}h rolling",
            f"Target claims/day sizing: {self.settings.rain_target_claims_per_day}",
        ]
        if amount is not None:
            lines.append(f"Next claim size now: {format_amount(amount)} PDC")
        else:
            lines.append(err or "Next claim size: n/a")
        if last:
            lines.append(f"Your last claim: {last} UTC")
        lines.append(f"Top up: {p}rain fund <amount> or {p}rain deposit")
        return CommandResult("\n".join(lines), private=True)

    def _cmd_rain_deposit(self, *, platform: str) -> CommandResult:
        rain = self.ensure_rain_pool()
        addr = rain.integrated_address or self._integrated_for(rain.payment_id)
        if addr and addr != rain.integrated_address:
            self.db.update_integrated_address(rain.id, addr)
        if not addr:
            return CommandResult(
                "Rain deposit address unavailable (wallet dry-run or RPC error).",
                private=True,
            )
        return CommandResult(
            "Rain pool deposit address (on-chain top-up):\n"
            f"{addr}\n"
            "Funds credit the communal rain pool after confirmations — "
            "not your personal tipbot balance.",
            private=True,
        )

    def _cmd_rain_fund(
        self, user: UserRow, args: list[str], *, platform: str
    ) -> CommandResult:
        p = _cmd_prefix(platform)
        if not args:
            return CommandResult(f"Usage: {p}rain fund <amount>")
        try:
            amount = parse_amount(args[0])
        except ValueError:
            return CommandResult(f"Invalid amount. Usage: {p}rain fund <amount>")
        if amount < self.settings.min_tip_atomic:
            return CommandResult(
                f"Minimum fund is {format_amount(self.settings.min_tip_atomic)} PDC"
            )
        rain = self.ensure_rain_pool()
        try:
            self.db.fund_rain(user.id, rain.id, amount)
        except ValueError as exc:
            msg = str(exc)
            if "insufficient" in msg:
                return CommandResult("Insufficient tipbot balance to fund rain.")
            return CommandResult(msg.capitalize())
        rain2 = self.db.get_user_by_id(rain.id)
        pool_s = format_amount(rain2.balance_atomic) if rain2 else "?"
        return CommandResult(
            f"Added {format_amount(amount)} PDC to the rain pool.\n"
            f"Pool now: {pool_s} PDC"
        )

    def _cmd_rain_admin(
        self,
        user: UserRow,
        platform: str,
        display_name: str,
        *,
        pause: bool,
    ) -> CommandResult:
        if not self.settings.is_admin(platform, user.platform_user_id, display_name):
            return CommandResult("Admin only.")
        self.db.set_rain_paused(pause)
        return CommandResult(
            "Rain claims paused." if pause else "Rain claims resumed."
        )

    def _cmd_rain_claim(
        self,
        user: UserRow,
        platform: str,
        *,
        is_private: bool,
        chat_id: str,
        is_bot: bool,
    ) -> CommandResult:
        p = _cmd_prefix(platform)
        if is_bot:
            return CommandResult("Bots cannot claim rain.")
        if self.db.rain_paused():
            return CommandResult("Rain is paused by an admin.")
        if is_private or not self.settings.rain_chat_allowed(platform, chat_id):
            where = (
                "Telegram @privacydatacoin or Matrix PDC General"
                if platform == "telegram"
                else "Matrix PDC General or Telegram @privacydatacoin"
            )
            return CommandResult(
                f"Claim rain only in {where}.\n"
                f"Use {p}rain info for pool status (DM ok)."
            )
        rain = self.ensure_rain_pool()
        amount, err = self._rain_claim_preview(rain.balance_atomic)
        if amount is None:
            return CommandResult(err or "Rain pool is empty.")
        try:
            self.db.claim_rain(
                claimer_user_id=user.id,
                rain_user_id=rain.id,
                amount=amount,
                platform=platform,
                platform_user_id=user.platform_user_id,
                cooldown_sec=self.settings.rain_cooldown_sec,
            )
        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("cooldown:"):
                remaining = int(msg.split(":", 1)[1])
                hours = remaining // 3600
                mins = (remaining % 3600) // 60
                return CommandResult(
                    f"Already claimed. Next rain in ~{hours}h {mins}m "
                    f"(24h rolling cooldown)."
                )
            if "empty" in msg or "too small" in msg:
                return CommandResult("Rain pool is empty or too small.")
            return CommandResult(msg.capitalize())
        fresh = self.db.get_user_by_id(user.id)
        bal = format_amount(fresh.balance_atomic) if fresh else "?"
        rain2 = self.db.get_user_by_id(rain.id)
        pool_s = format_amount(rain2.balance_atomic) if rain2 else "?"
        return CommandResult(
            f"Rain claimed: {format_amount(amount)} PDC.\n"
            f"Your tipbot balance: {bal} PDC\n"
            f"Pool left: {pool_s} PDC\n"
            f"Next claim after 24h."
        )

    def _unlocked_balance(self, user: UserRow) -> int:
        """Spendable tipbot ledger balance (only credited/unlocked deposits)."""
        return int(user.balance_atomic)

    def _cmd_withdraw(
        self, user: UserRow, args: list[str], *, platform: str = "telegram"
    ) -> CommandResult:
        """Sweep all unlocked balance minus network fee."""
        p = _cmd_prefix(platform)
        if len(args) < 1:
            return CommandResult(f"Usage: {p}withdraw <address>")
        address = args[0].strip()
        if not _looks_like_address(address):
            return CommandResult("That does not look like a PDC address or alias.")

        # Refresh balance from DB in case of concurrent updates
        fresh = self.db.get_user_by_id(user.id) or user
        unlocked = self._unlocked_balance(fresh)
        fee = int(self.settings.transfer_fee_atomic)
        if unlocked <= fee:
            return CommandResult(
                f"Nothing to withdraw. Unlocked {format_amount(unlocked)} PDC, "
                f"network fee is {format_amount(fee)} PDC."
            )
        amount = unlocked - fee
        if amount < self.settings.min_withdraw_atomic:
            return CommandResult(
                f"Unlocked after fee is {format_amount(amount)} PDC; "
                f"minimum withdraw is "
                f"{format_amount(self.settings.min_withdraw_atomic)} PDC."
            )
        return self._onchain_payout(
            fresh, address=address, amount=amount, kind="withdraw"
        )

    def _cmd_send(
        self, user: UserRow, args: list[str], *, platform: str = "telegram"
    ) -> CommandResult:
        """Send a chosen amount; must fit in unlocked balance − fee."""
        p = _cmd_prefix(platform)
        if len(args) < 2:
            return CommandResult(f"Usage: {p}send <address|@alias> <amount>")
        address = args[0].strip()
        try:
            amount = parse_amount(args[1])
        except ValueError as exc:
            return CommandResult(f"Bad amount: {exc}")

        if not _looks_like_address(address):
            return CommandResult("That does not look like a PDC address or alias.")

        if amount < self.settings.min_withdraw_atomic:
            return CommandResult(
                f"Minimum send is "
                f"{format_amount(self.settings.min_withdraw_atomic)} PDC"
            )

        fresh = self.db.get_user_by_id(user.id) or user
        unlocked = self._unlocked_balance(fresh)
        fee = int(self.settings.transfer_fee_atomic)
        max_send = unlocked - fee
        if max_send <= 0:
            return CommandResult(
                f"Insufficient unlocked balance. Have {format_amount(unlocked)} PDC; "
                f"need amount + fee {format_amount(fee)} PDC."
            )
        if amount > max_send:
            return CommandResult(
                f"Amount too high. Max send is {format_amount(max_send)} PDC "
                f"(unlocked {format_amount(unlocked)} − fee {format_amount(fee)})."
            )
        return self._onchain_payout(
            fresh, address=address, amount=amount, kind="send"
        )

    def recover_payouts(self) -> dict[str, int]:
        """Startup recovery for crash mid-payout.

        - queued (RPC never started) → safe auto-refund
        - transferring (RPC may have broadcast) → mark suspect, no auto-refund
        """
        stats = {"refunded": 0, "suspect": 0}
        for p in self.db.list_open_payouts():
            if p.status == "queued":
                self.db.refund_payout(p.id, note=f"{p.kind}_crash_refund")
                stats["refunded"] += 1
                logger.warning(
                    "Recovered queued payout #%s — refunded user=%s",
                    p.id,
                    p.user_id,
                )
            elif p.status == "transferring":
                self.db.mark_payout_suspect(p.id)
                stats["suspect"] += 1
                logger.error(
                    "SUSPECT payout #%s user=%s amount=%s — check explorer; "
                    "manual refund or attach tx_hash required",
                    p.id,
                    p.user_id,
                    p.amount_atomic + p.fee_atomic,
                )
            elif p.status == "suspect":
                stats["suspect"] += 1
                logger.error(
                    "Unresolved SUSPECT payout #%s user=%s still open",
                    p.id,
                    p.user_id,
                )
        return stats

    def _onchain_payout(
        self,
        user: UserRow,
        *,
        address: str,
        amount: int,
        kind: str,
    ) -> CommandResult:
        fee = int(self.settings.transfer_fee_atomic)
        total_debit = amount + fee
        if amount <= 0 or total_debit <= 0:
            return CommandResult("Invalid amount.")

        # Re-check ledger just before debit (tips/giveaways may have raced).
        fresh = self.db.get_user_by_id(user.id) or user
        if fresh.balance_atomic < total_debit:
            return CommandResult(
                f"Insufficient balance. Have {format_amount(fresh.balance_atomic)} PDC; "
                f"need {format_amount(amount)} + fee {format_amount(fee)} PDC."
            )

        already = self.db.withdrawn_today(fresh.id)
        if already + total_debit > self.settings.daily_withdraw_limit_atomic:
            return CommandResult(
                "Daily withdraw limit exceeded "
                f"({format_amount(self.settings.daily_withdraw_limit_atomic)} PDC)."
            )

        # Block payout while a prior crash-suspect payout is unresolved.
        for p in self.db.list_open_payouts():
            if p.user_id == fresh.id and p.status == "suspect":
                return CommandResult(
                    f"{kind.capitalize()} blocked: unresolved payout #{p.id}. "
                    "Contact admin."
                )

        # Hot-wallet preflight + pool solvency (liabilities must fit in hot total).
        if self.wallet.enabled:
            try:
                hot_total = self.wallet.get_balance_native()
                hot_unlocked = self.wallet.get_unlocked_native()
            except WalletError as exc:
                return CommandResult(f"{kind.capitalize()} unavailable: {exc}")
            liabilities = self.db.total_liabilities()
            if liabilities > hot_total:
                logger.error(
                    "solvency block: liabilities=%s hot=%s",
                    liabilities,
                    hot_total,
                )
                return CommandResult(
                    f"{kind.capitalize()} temporarily unavailable: tipbot pool "
                    "is underfunded vs ledger. Try later or contact admin."
                )
            if hot_unlocked < total_debit:
                return CommandResult(
                    f"{kind.capitalize()} temporarily unavailable: hot wallet "
                    f"unlocked {format_amount(hot_unlocked)} PDC "
                    f"< required {format_amount(total_debit)} PDC. Try later."
                )

        try:
            payout = self.db.create_queued_payout(
                fresh.id,
                address=address,
                amount=amount,
                fee=fee,
                kind=kind,
            )
        except ValueError as exc:
            return CommandResult(
                f"{str(exc).capitalize()} "
                f"(need {format_amount(amount)} + fee {format_amount(fee)} PDC)"
            )

        # Mark transferring before RPC so a crash mid-call becomes 'suspect'
        # (no blind refund — TX may already be on chain).
        if not self.db.mark_payout_transferring(payout.id):
            return CommandResult(f"{kind.capitalize()} failed: payout state error.")

        try:
            tx_hash = self.wallet.transfer(
                address,
                amount,
                comment=f"tipbot {kind} payout={payout.id} user={fresh.id}",
            )
        except (WalletError, ValueError) as exc:
            self.db.refund_payout(payout.id, note=f"{kind}_failed")
            return CommandResult(f"{kind.capitalize()} failed: {exc}")

        self.db.complete_payout(payout.id, tx_hash)

        link = f"{self.settings.explorer_tx_url.rstrip('/')}/{tx_hash}"
        return CommandResult(
            f"{kind.capitalize()} {format_amount(amount)} PDC to `{address}`\n"
            f"Network fee: {format_amount(fee)} PDC "
            f"(debited from your tipbot balance)\n"
            f"Total debited: {format_amount(total_debit)} PDC\n"
            f"Tx: {link}",
            private=True,
        )


# Standard (Px… / pdc…), testnet (tpdc…), integrated (i…)
_ADDR_RE = re.compile(
    r"^(?:pdc|tpdc|Px|iP|i)[1-9A-HJ-NP-Za-km-z]{20,200}$"
)
_ALIAS_RE = re.compile(r"^@[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$", re.IGNORECASE)


def _looks_like_address(value: str) -> bool:
    v = (value or "").strip()
    if _ALIAS_RE.match(v) and 3 <= len(v) <= 65:
        return True
    return bool(_ADDR_RE.match(v))


def _looks_like_amount(token: str) -> bool:
    try:
        parse_amount(token)
        return True
    except ValueError:
        return False
