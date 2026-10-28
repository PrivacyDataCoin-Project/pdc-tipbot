"""Matrix adapter for matrix.privacydatacoin.com (matrix-nio)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteEvent,
    LoginResponse,
    MatrixRoom,
    MegolmEvent,
    RoomCreateError,
    RoomMessageText,
    RoomSendError,
)

from tipbot.service import TipService

if TYPE_CHECKING:
    from tipbot.config import Settings

logger = logging.getLogger("pdc-tipbot.matrix")

_CMD_SPLIT = re.compile(r"\s+")
_SILENT_PUBLIC_CMDS = frozenset(
    {"balance", "bal", "deposit", "start", "register", "rain"}
)


class MatrixBot:
    def __init__(self, settings: Settings, service: TipService) -> None:
        self.settings = settings
        self.service = service
        self.client: Optional[AsyncClient] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._dm_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        store = Path(self.settings.matrix_store_path)
        store.mkdir(parents=True, exist_ok=True)

        # E2EE required for encrypted rooms (PDC General).
        encryption_ok = False
        try:
            import olm  # noqa: F401

            encryption_ok = True
        except Exception:
            logger.error(
                "python-olm missing — encrypted Matrix rooms will not decrypt. "
                "Install: apt install libolm-dev && pip install 'matrix-nio[e2e]'"
            )

        config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=encryption_ok,
        )
        client = AsyncClient(
            self.settings.matrix_homeserver,
            self.settings.matrix_user,
            store_path=str(store),
            config=config,
        )
        self.client = client

        if self.settings.matrix_access_token:
            client.restore_login(
                user_id=self.settings.matrix_user,
                device_id=self.settings.matrix_device_id or "PDC_TIPBOT",
                access_token=self.settings.matrix_access_token,
            )
            logger.info("Matrix restored login as %s", self.settings.matrix_user)
        else:
            resp = await client.login(
                self.settings.matrix_password, device_name="pdc-tipbot"
            )
            if not isinstance(resp, LoginResponse):
                raise RuntimeError(f"Matrix login failed: {resp}")
            logger.info(
                "Matrix logged in as %s device=%s",
                resp.user_id,
                resp.device_id,
            )
            self._persist_login(resp.access_token, resp.device_id)

        client.add_event_callback(self._on_invite, InviteEvent)
        client.add_event_callback(self._on_message, RoomMessageText)
        if encryption_ok:
            client.add_event_callback(self._on_megolm, MegolmEvent)

        # Catch up once so we ignore historical commands.
        await client.sync(timeout=30_000, full_state=True)
        if encryption_ok:
            try:
                if client.should_upload_keys:
                    await client.keys_upload()
                    logger.info("Matrix device keys uploaded")
                if client.should_query_keys:
                    await client.keys_query()
                if client.should_claim_keys:
                    await client.keys_claim(client.get_users_for_key_claiming())
            except Exception:
                logger.exception("matrix key bootstrap failed")

        for rid, room in client.rooms.items():
            logger.info(
                "Matrix joined room %s name=%r encrypted=%s members=%s dm=%s",
                rid,
                room.display_name or room.name,
                room.encrypted,
                room.member_count,
                self._is_dm(room),
            )

        self._ready.set()
        self._sync_task = asyncio.create_task(
            self._sync_forever(), name="matrix-sync"
        )
        logger.info(
            "Matrix sync started (%s) encryption=%s",
            self.settings.matrix_homeserver,
            encryption_ok,
        )

    def _persist_login(self, access_token: str, device_id: str) -> None:
        """Write token/device into .env so password login is not required next time."""
        env_path = Path("/home/ubuntu/pdc-tipbot/.env")
        if not env_path.exists():
            env_path = Path(self.settings.database_path).resolve().parent.parent / ".env"
        if not env_path.exists():
            logger.warning("cannot persist Matrix token — .env not found")
            return
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            seen_token = seen_device = False
            for line in lines:
                if line.startswith("MATRIX_ACCESS_TOKEN="):
                    out.append(f"MATRIX_ACCESS_TOKEN={access_token}")
                    seen_token = True
                elif line.startswith("MATRIX_DEVICE_ID="):
                    out.append(f"MATRIX_DEVICE_ID={device_id}")
                    seen_device = True
                else:
                    out.append(line)
            if not seen_token:
                out.append(f"MATRIX_ACCESS_TOKEN={access_token}")
            if not seen_device:
                out.append(f"MATRIX_DEVICE_ID={device_id}")
            env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
            env_path.chmod(0o600)
            logger.info("Saved MATRIX_ACCESS_TOKEN / MATRIX_DEVICE_ID to %s", env_path)
        except Exception:
            logger.exception("failed to persist Matrix login to .env")

    async def _sync_forever(self) -> None:
        assert self.client is not None
        client = self.client
        while True:
            try:
                await client.sync_forever(timeout=30_000, full_state=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("matrix sync_forever crashed — retry in 5s")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        if self.client:
            await self.client.close()
            self.client = None
        logger.info("Matrix stopped")

    @staticmethod
    def _is_dm(room: MatrixRoom) -> bool:
        # 1:1 (or bot-only while invite pending) — treat as private chat.
        return int(getattr(room, "member_count", 99) or 99) <= 2

    def _room_allowed(self, room: MatrixRoom) -> bool:
        """Public commands only in allow-list; DMs always allowed (like Telegram private)."""
        if self._is_dm(room):
            return True
        allowed = self.settings.matrix_allowed_rooms
        if not allowed:
            return True
        return room.room_id in allowed

    def _dm_meta_key(self, mxid: str) -> str:
        return f"matrix_dm:{mxid.lower()}"

    async def _ensure_dm(self, mxid: str) -> Optional[str]:
        """Find or create a DM room with mxid; cache room id in DB meta."""
        if not self.client:
            return None
        mxid = mxid.strip()
        if not mxid.startswith("@"):
            return None

        lock = self._dm_locks.setdefault(mxid.lower(), asyncio.Lock())
        async with lock:
            cached = self.service.db.get_meta(self._dm_meta_key(mxid))
            if cached and cached in self.client.rooms:
                return cached

            # Prefer an already-joined 1:1 with this user.
            for rid, room in self.client.rooms.items():
                users = getattr(room, "users", {}) or {}
                if mxid in users and self._is_dm(room):
                    self.service.db.set_meta(self._dm_meta_key(mxid), rid)
                    return rid

            # Create a new direct room and invite the user (Telegram-style push DM).
            try:
                created = await self.client.room_create(
                    is_direct=True,
                    invite=[mxid],
                )
            except Exception:
                logger.exception("matrix room_create failed for %s", mxid)
                return None
            if isinstance(created, RoomCreateError) or not getattr(
                created, "room_id", None
            ):
                logger.warning("matrix DM create failed for %s: %s", mxid, created)
                return None

            room_id = str(created.room_id)
            self.service.db.set_meta(self._dm_meta_key(mxid), room_id)

            # Mark as direct in account data (best-effort).
            try:
                direct = dict(getattr(self.client, "direct", None) or {})
                rooms = list(direct.get(mxid, []) or [])
                if room_id not in rooms:
                    rooms.append(room_id)
                direct[mxid] = rooms
                if hasattr(self.client, "set_account_data"):
                    await self.client.set_account_data("m.direct", direct)
            except Exception:
                logger.debug("m.direct account_data update failed", exc_info=True)

            # One short sync so encryption / membership settle.
            try:
                await self.client.sync(timeout=5_000)
            except Exception:
                pass
            return room_id

    async def notify_user(self, platform_user_id: str, text: str) -> bool:
        """Send a private Matrix DM (deposits, tips, balance from public rooms)."""
        if not self.client:
            return False
        try:
            room_id = await self._ensure_dm(platform_user_id)
            if not room_id:
                logger.warning("no DM room for %s", platform_user_id)
                return False
            ok = await self._send(room_id, text)
            if ok:
                logger.info("Matrix DM sent to %s room=%s", platform_user_id, room_id)
            return ok
        except Exception:
            logger.exception("matrix notify failed for %s", platform_user_id)
            return False

    async def _on_invite(self, room: MatrixRoom, event: InviteEvent) -> None:
        if not self.client:
            return
        try:
            await self.client.join(room.room_id)
            logger.info("Joined invited room %s", room.room_id)
            # Cache DM if this invite is a 1:1.
            sender = getattr(event, "sender", None)
            if sender and isinstance(sender, str) and sender.startswith("@"):
                self.service.db.set_meta(self._dm_meta_key(sender), room.room_id)
            if self.client.should_upload_keys:
                await self.client.keys_upload()
        except Exception:
            logger.exception("failed to join %s", room.room_id)

    async def _on_megolm(self, room: MatrixRoom, event: MegolmEvent) -> None:
        """Request room keys when we cannot decrypt (new device / missing session)."""
        if not self.client:
            return
        try:
            await self.client.request_room_key(event)
        except Exception:
            logger.debug(
                "room key request failed room=%s session=%s",
                room.room_id,
                getattr(event, "session_id", "?"),
                exc_info=True,
            )

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if not self.client or not self._ready.is_set():
            return
        if event.sender == self.client.user_id:
            return
        if not self._room_allowed(room):
            return

        body = (event.body or "").strip()
        parsed = self._parse_command(body)
        if not parsed:
            return
        cmd, args = parsed

        is_private = self._is_dm(room)
        display = room.user_name(event.sender) or event.sender
        member = room.users.get(event.sender)
        is_bot = bool(getattr(member, "bot", False) or getattr(member, "is_bot", False))

        # Remember DM mapping when user messages us privately.
        if is_private:
            self.service.db.set_meta(self._dm_meta_key(event.sender), room.room_id)

        result = self.service.handle(
            platform="matrix",
            platform_user_id=event.sender,
            display_name=display,
            command=cmd,
            args=args,
            reply_to_platform_user_id=None,
            reply_to_display_name="",
            is_private=is_private,
            chat_id=room.room_id,
            is_bot=is_bot,
        )

        if result.private and not is_private:
            # Same as Telegram: private payload → DM only (balance silent in channel).
            dm_ok = await self.notify_user(event.sender, result.text)
            if cmd not in _SILENT_PUBLIC_CMDS:
                if dm_ok:
                    await self._send(
                        room.room_id, f"{display}: details sent in DM."
                    )
                else:
                    await self._send(
                        room.room_id,
                        f"{display}: open a DM with me first, then retry "
                        f"(invite `{self.settings.matrix_user}`).",
                    )
        else:
            await self._send(room.room_id, result.text)

        for platform, user_id, text in result.all_notifies():
            if platform == "matrix":
                await self.notify_user(user_id, text)

    def _parse_command(self, body: str) -> Optional[tuple[str, list[str]]]:
        if not body:
            return None
        # Strip reply quote blocks Matrix clients prepend.
        lines = [ln for ln in body.splitlines() if not ln.startswith(">")]
        body = "\n".join(lines).strip()
        prefixes = self.settings.matrix_command_prefixes
        matched = None
        for p in prefixes:
            if body.startswith(p):
                matched = p
                break
        if matched is None:
            return None
        rest = body[len(matched) :].strip()
        if not rest:
            return None
        parts = _CMD_SPLIT.split(rest)
        cmd = parts[0].lower()
        # Ignore @bot:server mention form: !tipbot:server help → skip
        if ":" in cmd and cmd.startswith("@"):
            return None
        return cmd, parts[1:]

    async def _send(self, room_id: str, text: str) -> bool:
        if not self.client:
            return False
        result = await self.client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.notice", "body": text},
            ignore_unverified_devices=True,
        )
        if isinstance(result, RoomSendError):
            logger.error("room_send failed room=%s: %s", room_id, result.message)
            return False
        return True
