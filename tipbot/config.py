"""Environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if not raw:
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if not raw:
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_TOKEN"))
    matrix_homeserver: str = field(
        default_factory=lambda: _env(
            "MATRIX_HOMESERVER", "https://matrix.privacydatacoin.com"
        ).rstrip("/")
    )
    matrix_user: str = field(
        default_factory=lambda: _env(
            "MATRIX_USER", "@pdctipbot:privacydatacoin.com"
        )
    )
    matrix_access_token: str = field(default_factory=lambda: _env("MATRIX_ACCESS_TOKEN"))
    matrix_password: str = field(default_factory=lambda: _env("MATRIX_PASSWORD"))
    matrix_device_id: str = field(default_factory=lambda: _env("MATRIX_DEVICE_ID"))
    matrix_allowed_rooms: tuple[str, ...] = field(default_factory=tuple)
    matrix_command_prefixes: tuple[str, ...] = field(default_factory=tuple)
    matrix_store_path: Path = field(default_factory=lambda: Path(_env("MATRIX_STORE_PATH", "data/matrix_store")))
    database_path: Path = field(default_factory=lambda: Path(_env("DATABASE_PATH", "data/tipbot.db")))
    wallet_rpc_url: str = field(default_factory=lambda: _env("WALLET_RPC_URL"))
    wallet_rpc_user: str = field(default_factory=lambda: _env("WALLET_RPC_USER"))
    wallet_rpc_password: str = field(default_factory=lambda: _env("WALLET_RPC_PASSWORD"))
    daemon_rpc_url: str = field(
        default_factory=lambda: _env("DAEMON_RPC_URL", "http://127.0.0.1:19211").rstrip(
            "/"
        )
    )
    pdc_asset_id: str = field(
        default_factory=lambda: _env(
            "PDC_ASSET_ID",
            "d6329b5b1f7c0805b5c345f4957554002a2f557845f64d7645dae0e051a6498a",
        )
    )
    transfer_fee_atomic: int = field(
        default_factory=lambda: _env_int("TRANSFER_FEE_ATOMIC", 10_000_000_000)
    )
    deposit_min_confirmations: int = field(
        default_factory=lambda: _env_int("DEPOSIT_MIN_CONFIRMATIONS", 10)
    )
    deposit_scan_interval_sec: float = field(
        default_factory=lambda: _env_float("DEPOSIT_SCAN_INTERVAL_SEC", 30.0)
    )
    min_tip_atomic: int = field(
        default_factory=lambda: _env_int("MIN_TIP_ATOMIC", 1_000_000_000)
    )
    min_withdraw_atomic: int = field(
        default_factory=lambda: _env_int("MIN_WITHDRAW_ATOMIC", 10_000_000_000)
    )
    daily_withdraw_limit_atomic: int = field(
        default_factory=lambda: _env_int("DAILY_WITHDRAW_LIMIT_ATOMIC", 50_000_000_000_000)
    )
    explorer_tx_url: str = field(
        default_factory=lambda: _env(
            "EXPLORER_TX_URL", "https://explorer.privacydatacoin.com/transaction/"
        )
    )
    bot_display_name: str = field(
        default_factory=lambda: _env("BOT_DISPLAY_NAME", "PDC TipBot")
    )
    admin_telegram_usernames: tuple[str, ...] = field(default_factory=tuple)
    admin_matrix_user_ids: tuple[str, ...] = field(default_factory=tuple)
    # /rain — communal faucet pool (claim in whitelisted chats only)
    rain_enabled: bool = field(
        default_factory=lambda: _env("RAIN_ENABLED", "1").lower()
        not in ("0", "false", "no", "off")
    )
    rain_telegram_chat_ids: tuple[str, ...] = field(default_factory=tuple)
    rain_matrix_room_ids: tuple[str, ...] = field(default_factory=tuple)
    rain_target_claims_per_day: int = field(
        default_factory=lambda: _env_int("RAIN_TARGET_CLAIMS_PER_DAY", 10)
    )
    rain_claim_min_atomic: int = field(
        default_factory=lambda: _env_int("RAIN_CLAIM_MIN_ATOMIC", 100_000_000_000)
    )
    rain_claim_max_atomic: int = field(
        default_factory=lambda: _env_int("RAIN_CLAIM_MAX_ATOMIC", 1_000_000_000_000)
    )
    rain_cooldown_sec: int = field(
        default_factory=lambda: _env_int("RAIN_COOLDOWN_SEC", 86_400)
    )

    def __post_init__(self) -> None:
        rooms = tuple(
            r.strip()
            for r in _env("MATRIX_ALLOWED_ROOMS").split(",")
            if r.strip()
        )
        prefixes = tuple(
            p.strip()
            for p in _env("MATRIX_COMMAND_PREFIXES", "!,/").split(",")
            if p.strip()
        ) or ("!", "/")
        admins_tg = tuple(
            a.lstrip("@").strip().lower()
            for a in _env("ADMIN_TELEGRAM_USERNAMES", "").split(",")
            if a.strip()
        )
        admins_mx = tuple(
            a.strip().lower()
            for a in _env("ADMIN_MATRIX_USER_IDS", "").split(",")
            if a.strip()
        )
        # Empty by default (safe for public forks). Set in .env for production.
        rain_tg = tuple(
            c.strip()
            for c in _env("RAIN_TELEGRAM_CHAT_IDS", "").split(",")
            if c.strip()
        )
        rain_mx = tuple(
            r.strip()
            for r in _env("RAIN_MATRIX_ROOM_IDS", "").split(",")
            if r.strip()
        )
        object.__setattr__(self, "matrix_allowed_rooms", rooms)
        object.__setattr__(self, "matrix_command_prefixes", prefixes)
        # Keep explicit constructor values (tests); otherwise take env.
        if not self.admin_telegram_usernames:
            object.__setattr__(self, "admin_telegram_usernames", admins_tg)
        if not self.admin_matrix_user_ids:
            object.__setattr__(self, "admin_matrix_user_ids", admins_mx)
        if not self.rain_telegram_chat_ids:
            object.__setattr__(self, "rain_telegram_chat_ids", rain_tg)
        if not self.rain_matrix_room_ids:
            object.__setattr__(self, "rain_matrix_room_ids", rain_mx)

    def rain_chat_allowed(self, platform: str, chat_id: str) -> bool:
        cid = (chat_id or "").strip()
        if not cid:
            return False
        if platform == "telegram":
            return cid in self.rain_telegram_chat_ids
        if platform == "matrix":
            return cid in self.rain_matrix_room_ids
        return False

    def is_admin(
        self, platform: str, platform_user_id: str, display_name: str = ""
    ) -> bool:
        if platform == "telegram":
            uname = display_name.lstrip("@").split()[0].lower() if display_name else ""
            if uname in self.admin_telegram_usernames:
                return True
            # fallback: platform_user_id sometimes stores username
            return platform_user_id.lstrip("@").lower() in self.admin_telegram_usernames
        if platform == "matrix":
            return platform_user_id.lower() in self.admin_matrix_user_ids
        return False


    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token)

    @property
    def matrix_enabled(self) -> bool:
        return bool(
            self.matrix_homeserver
            and self.matrix_user
            and (self.matrix_access_token or self.matrix_password)
        )

    @property
    def wallet_enabled(self) -> bool:
        return bool(self.wallet_rpc_url)


def load_settings() -> Settings:
    return Settings()
