"""Telegram adapter (python-telegram-bot)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType, MessageEntityType, ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tipbot.service import TipService

if TYPE_CHECKING:
    from tipbot.config import Settings

logger = logging.getLogger("pdc-tipbot.telegram")

COMMANDS = (
    "start",
    "help",
    "helpme",
    "balance",
    "bal",
    "deposit",
    "tip",
    "giveaway",
    "claim",
    "cancel",
    "rain",
    "withdraw",
    "send",
    "totalbalance",  # hidden admin; not listed in /help
)


class TelegramBot:
    def __init__(self, settings: Settings, service: TipService) -> None:
        self.settings = settings
        self.service = service
        self.app: Application | None = None

    def build(self) -> Application:
        app = Application.builder().token(self.settings.telegram_token).build()
        for name in COMMANDS:
            app.add_handler(CommandHandler(name, self._on_command))
        app.add_handler(
            MessageHandler(
                filters.Regex(r"(?i)^/(tip|balance|deposit|giveaway|rain)\b"),
                self._on_command,
            )
        )
        app.add_handler(CallbackQueryHandler(self._on_callback, pattern=r"^ga_"))
        self.app = app
        return app

    async def start(self) -> Application:
        app = self.build()
        await app.initialize()
        await app.start()
        assert app.updater is not None
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling started")
        return app

    async def stop(self) -> None:
        if not self.app:
            return
        if self.app.updater:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Telegram stopped")

    async def notify_user(self, platform_user_id: str, text: str) -> bool:
        if not self.app:
            return False
        try:
            chat_id = (
                int(platform_user_id) if platform_user_id.isdigit() else platform_user_id
            )
            await self.app.bot.send_message(chat_id=chat_id, text=text)
            return True
        except Forbidden:
            # User never pressed Start — Telegram forbids bot-initiated DMs.
            logger.warning(
                "telegram notify blocked for %s (user must /start the bot first)",
                platform_user_id,
            )
            return False
        except Exception:
            logger.exception("telegram notify failed for %s", platform_user_id)
            return False

    async def _on_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.effective_user or not update.effective_message:
            return
        user = update.effective_user
        message = update.effective_message
        text = (message.text or "").strip()
        if not text:
            return

        parts = text.split()
        raw_cmd = parts[0]
        if raw_cmd.startswith("/"):
            raw_cmd = raw_cmd[1:]
        cmd = raw_cmd.split("@", 1)[0].lower()
        args = parts[1:]

        reply_id = None
        reply_name = ""
        if message.reply_to_message and message.reply_to_message.from_user:
            ru = message.reply_to_message.from_user
            reply_id = str(ru.id)
            reply_name = f"@{ru.username}" if ru.username else (ru.full_name or reply_id)

        # Prefer Telegram mention entities so /tip @user / text_mention resolve to
        # the real numeric user id (and plain "Mister" still works via ledger lookup).
        if cmd == "tip":
            mentioned = self._tip_mention_from_entities(message)
            if mentioned is not None:
                mid, mname = mentioned
                self.service.ensure_user("telegram", mid, mname)
                reply_id = mid
                reply_name = mname
                # Convert "/tip @user 1 msg" → reply-style args [amount, ...msg]
                if args and not self._looks_like_amount(args[0]):
                    args = args[1:]

        display = f"@{user.username}" if user.username else (user.full_name or str(user.id))
        chat = update.effective_chat
        is_private = chat is not None and chat.type == ChatType.PRIVATE
        chat_id = str(chat.id) if chat else ""

        result = self.service.handle(
            platform="telegram",
            platform_user_id=str(user.id),
            display_name=display,
            command=cmd,
            args=args,
            reply_to_platform_user_id=reply_id,
            reply_to_display_name=reply_name,
            is_private=is_private,
            chat_id=chat_id,
            is_bot=bool(user.is_bot),
        )

        if user.username:
            self.service.ensure_user("telegram", str(user.id), display)

        markup = None
        if result.buttons:
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(label, callback_data=data)
                        for label, data in row
                    ]
                    for row in result.buttons
                ]
            )

        send_text = result.text
        parse_mode = None
        # Tip in groups: deep-link mention so recipient gets a Telegram notification
        # even before they /start the bot (DM may still be blocked).
        if (
            cmd == "tip"
            and result.tip_telegram_id
            and result.tip_telegram_id.isdigit()
            and result.tip_display_name
        ):
            who = escape(result.tip_display_name)
            # Rebuild a mention-aware confirmation from the ledger result text.
            # Keep any " Message: …" suffix from service.
            suffix = ""
            if " Message: " in result.text:
                suffix = " Message: " + escape(result.text.split(" Message: ", 1)[1])
            # Extract amount fragment "with X PDC"
            amount_part = "PDC"
            if " with " in result.text:
                amount_part = escape(result.text.split(" with ", 1)[1].split(" Message:")[0])
            send_text = (
                f'Tipped <a href="tg://user?id={result.tip_telegram_id}">{who}</a> '
                f"with {amount_part}{suffix}"
            )
            parse_mode = ParseMode.HTML

        public_chat = chat is not None and chat.type != ChatType.PRIVATE
        # Never leak private payloads (balance/deposit/start) into groups/channels.
        if result.private and public_chat:
            silent_cmds = {
                "balance",
                "bal",
                "deposit",
                "start",
                "register",
                "rain",
            }
            try:
                await context.bot.send_message(chat_id=user.id, text=result.text)
                if cmd not in silent_cmds:
                    await message.reply_text("Sent details in private chat.")
                else:
                    # Best-effort: remove the public /balance (etc.) command spam.
                    try:
                        await message.delete()
                    except Exception:
                        pass
            except Exception:
                # Still do not print balance/deposit body publicly.
                await message.reply_text(
                    "Open a private chat with me, press Start, then run this command there."
                )
        else:
            sent = await message.reply_text(
                send_text, reply_markup=markup, parse_mode=parse_mode
            )
            if result.giveaway_id and markup:
                self.service.db.set_giveaway_message_id(
                    result.giveaway_id, str(sent.message_id)
                )

        tip_dm_failed = False
        for platform, user_id, ntext in result.all_notifies():
            if platform != "telegram":
                continue
            ok = await self.notify_user(user_id, ntext)
            if not ok and cmd == "tip":
                tip_dm_failed = True
        if tip_dm_failed:
            await message.reply_text(
                "Tip is on the ledger. I also mentioned them above so they get a "
                "Telegram notification. For tip DMs they must open a private chat "
                "and press /start."
            )

    @staticmethod
    def _looks_like_amount(value: str) -> bool:
        raw = (value or "").strip().replace(",", ".")
        if not raw:
            return False
        try:
            float(raw)
            return True
        except ValueError:
            return False

    def _tip_mention_from_entities(
        self, message: Message
    ) -> tuple[str, str] | None:
        """Return (telegram_user_id, display) from the first tip @mention / text_mention."""
        text = message.text or ""
        for ent in message.entities or []:
            if ent.type == MessageEntityType.BOT_COMMAND:
                continue
            if ent.type == MessageEntityType.TEXT_MENTION and ent.user:
                u = ent.user
                dname = f"@{u.username}" if u.username else (u.full_name or str(u.id))
                return str(u.id), dname
            if ent.type == MessageEntityType.MENTION:
                handle = text[ent.offset : ent.offset + ent.length]
                # Resolve @username via ledger (must already be known).
                found = self.service.db.find_user_by_display("telegram", handle)
                if found and found.platform_user_id.isdigit():
                    return found.platform_user_id, found.display_name or handle
        return None

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query or not query.data or not query.from_user:
            return

        data = query.data
        user = query.from_user
        display = f"@{user.username}" if user.username else (user.full_name or str(user.id))

        if data.startswith("ga_claim:"):
            giveaway_id = data.split(":", 1)[1]
            result = self.service.claim_giveaway_callback(
                "telegram", str(user.id), display, giveaway_id
            )
        elif data.startswith("ga_cancel:"):
            giveaway_id = data.split(":", 1)[1]
            result = self.service.cancel_giveaway_callback(
                "telegram", str(user.id), display, giveaway_id
            )
        else:
            return

        ga = self.service.db.get_giveaway(giveaway_id)
        closed = ga is not None and ga.status != "open"
        if not closed:
            await query.answer(result.text[:200], show_alert=True)
            return

        await query.answer()
        try:
            await query.edit_message_text(result.text, reply_markup=None)
        except Exception:
            logger.exception("failed to edit giveaway message")
            if query.message:
                await query.message.reply_text(result.text)

        for platform, user_id, text in result.all_notifies():
            if platform == "telegram":
                await self.notify_user(user_id, text)
