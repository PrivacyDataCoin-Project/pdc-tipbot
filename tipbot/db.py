"""SQLite persistence for users, ledger and deposit watermark."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    payment_id TEXT NOT NULL UNIQUE,
    integrated_address TEXT NOT NULL DEFAULT '',
    balance_atomic INTEGER NOT NULL DEFAULT 0 CHECK (balance_atomic >= 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL,
    counterparty_user_id INTEGER,
    tx_hash TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL,
    block_height INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL REFERENCES users(id),
    credited_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tx_hash, payment_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaways (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    sender_user_id INTEGER NOT NULL REFERENCES users(id),
    amount_atomic INTEGER NOT NULL CHECK (amount_atomic > 0),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'claimed', 'cancelled')),
    claimer_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);

-- Outbox for on-chain send/withdraw (crash-safe debit → transfer → finalize).
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    address TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL CHECK (amount_atomic > 0),
    fee_atomic INTEGER NOT NULL CHECK (fee_atomic >= 0),
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'transferring', 'sent', 'refunded', 'suspect')),
    tx_hash TEXT,
    ledger_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rain_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL CHECK (amount_atomic > 0),
    claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id);
CREATE INDEX IF NOT EXISTS idx_users_payment ON users(payment_id);
CREATE INDEX IF NOT EXISTS idx_giveaways_open ON giveaways(status, platform, chat_id);
CREATE INDEX IF NOT EXISTS idx_payouts_open ON payouts(status);
CREATE INDEX IF NOT EXISTS idx_rain_claims_user_time ON rain_claims(user_id, claimed_at DESC);
"""

RAIN_PLATFORM = "system"
RAIN_PLATFORM_USER_ID = "rain"
RAIN_META_PAUSED = "rain_paused"


@dataclass
class UserRow:
    id: int
    platform: str
    platform_user_id: str
    display_name: str
    payment_id: str
    integrated_address: str
    balance_atomic: int


@dataclass
class GiveawayRow:
    id: str
    platform: str
    chat_id: str
    message_id: str
    sender_user_id: int
    amount_atomic: int
    status: str
    claimer_user_id: Optional[int]


@dataclass
class PayoutRow:
    id: int
    user_id: int
    address: str
    amount_atomic: int
    fee_atomic: int
    kind: str
    status: str
    tx_hash: Optional[str]
    ledger_id: Optional[int]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                yield conn
            finally:
                conn.close()

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_user(self, platform: str, platform_user_id: str) -> Optional[UserRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE platform = ? AND platform_user_id = ?",
                (platform, platform_user_id),
            ).fetchone()
            return _user_from_row(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[UserRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return _user_from_row(row) if row else None

    def get_user_by_payment_id(self, payment_id: str) -> Optional[UserRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE payment_id = ?", (payment_id.lower(),)
            ).fetchone()
            return _user_from_row(row) if row else None

    def find_user_by_display(
        self, platform: str, display_or_handle: str
    ) -> Optional[UserRow]:
        needle = display_or_handle.lstrip("@").strip().lower()
        if not needle:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE platform = ? AND ("
                "lower(platform_user_id) = ? OR lower(display_name) = ? OR "
                "lower(display_name) = ?)",
                (platform, needle, needle, f"@{needle}"),
            ).fetchone()
            return _user_from_row(row) if row else None

    def create_user(
        self,
        platform: str,
        platform_user_id: str,
        display_name: str,
        payment_id: str,
        integrated_address: str = "",
    ) -> UserRow:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "INSERT INTO users(platform, platform_user_id, display_name, "
                    "payment_id, integrated_address) VALUES (?, ?, ?, ?, ?)",
                    (
                        platform,
                        platform_user_id,
                        display_name,
                        payment_id.lower(),
                        integrated_address,
                    ),
                )
                user_id = int(cur.lastrowid)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        user = self.get_user_by_id(user_id)
        assert user is not None
        return user

    def update_integrated_address(self, user_id: int, address: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET integrated_address = ? WHERE id = ?",
                (address, user_id),
            )

    def update_display_name(self, user_id: int, display_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, user_id),
            )

    def tip(self, from_id: int, to_id: int, amount: int, note: str = "") -> None:
        if amount <= 0:
            raise ValueError("tip amount must be positive")
        if from_id == to_id:
            raise ValueError("cannot tip yourself")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (amount, from_id, amount),
                )
                if cur.rowcount != 1:
                    raise ValueError("insufficient balance")
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, to_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'tip_out', ?, ?, ?)",
                    (from_id, -amount, to_id, note),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'tip_in', ?, ?, ?)",
                    (to_id, amount, from_id, note),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def has_deposit(self, tx_hash: str, payment_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM deposits WHERE tx_hash = ? AND payment_id = ?",
                (tx_hash, payment_id.lower()),
            ).fetchone()
            return row is not None

    def credit_deposit(
        self,
        user_id: int,
        payment_id: str,
        tx_hash: str,
        amount: int,
        block_height: int,
    ) -> bool:
        """Credit deposit if new. Returns True when credited."""
        if amount <= 0:
            return False
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                try:
                    conn.execute(
                        "INSERT INTO deposits(payment_id, tx_hash, amount_atomic, "
                        "block_height, user_id) VALUES (?, ?, ?, ?, ?)",
                        (payment_id.lower(), tx_hash, amount, block_height, user_id),
                    )
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, user_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, tx_hash, note) "
                    "VALUES (?, 'deposit', ?, ?, ?)",
                    (user_id, amount, tx_hash, f"height={block_height}"),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def debit_withdraw(
        self, user_id: int, amount: int, tx_hash: str, note: str = "withdraw"
    ) -> None:
        if amount <= 0:
            raise ValueError("withdraw amount must be positive")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (amount, user_id, amount),
                )
                if cur.rowcount != 1:
                    raise ValueError("insufficient balance")
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, tx_hash, note) "
                    "VALUES (?, 'withdraw', ?, ?, ?)",
                    (user_id, -amount, tx_hash, note),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def create_queued_payout(
        self,
        user_id: int,
        *,
        address: str,
        amount: int,
        fee: int,
        kind: str,
    ) -> PayoutRow:
        """Atomically debit ledger and enqueue an on-chain payout outbox row."""
        total = amount + fee
        if amount <= 0 or total <= 0:
            raise ValueError("withdraw amount must be positive")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (total, user_id, total),
                )
                if cur.rowcount != 1:
                    raise ValueError("insufficient balance")
                led = conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, tx_hash, note) "
                    "VALUES (?, 'withdraw', ?, 'pending', ?)",
                    (user_id, -total, f"{kind}+fee"),
                )
                ledger_id = int(led.lastrowid)
                pay = conn.execute(
                    "INSERT INTO payouts(user_id, address, amount_atomic, fee_atomic, "
                    "kind, status, ledger_id) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                    (user_id, address, amount, fee, kind, ledger_id),
                )
                payout_id = int(pay.lastrowid)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        row = self.get_payout(payout_id)
        assert row is not None
        return row

    def get_payout(self, payout_id: int) -> Optional[PayoutRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM payouts WHERE id = ?", (payout_id,)
            ).fetchone()
            return _payout_from_row(row) if row else None

    def mark_payout_transferring(self, payout_id: int) -> bool:
        """Flip queued → transferring just before wallet RPC (crash = suspect)."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE payouts SET status = 'transferring', "
                    "updated_at = datetime('now') "
                    "WHERE id = ? AND status = 'queued'",
                    (payout_id,),
                )
                ok = cur.rowcount == 1
                conn.execute("COMMIT")
                return ok
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def complete_payout(self, payout_id: int, tx_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM payouts WHERE id = ?", (payout_id,)
                ).fetchone()
                if not row:
                    raise ValueError("payout not found")
                ledger_id = row["ledger_id"]
                kind = str(row["kind"])
                amount = int(row["amount_atomic"])
                fee = int(row["fee_atomic"])
                conn.execute(
                    "UPDATE payouts SET status = 'sent', tx_hash = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (tx_hash, payout_id),
                )
                if ledger_id is not None:
                    conn.execute(
                        "UPDATE ledger SET tx_hash = ?, note = ? WHERE id = ?",
                        (
                            tx_hash,
                            f"{kind} amount={amount} fee={fee}",
                            int(ledger_id),
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def refund_payout(self, payout_id: int, note: str = "withdraw_refund") -> None:
        """Credit user back and mark payout refunded (only from queued/transferring/suspect)."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM payouts WHERE id = ?", (payout_id,)
                ).fetchone()
                if not row:
                    raise ValueError("payout not found")
                status = str(row["status"])
                if status in ("sent", "refunded"):
                    raise ValueError(f"payout already {status}")
                user_id = int(row["user_id"])
                total = int(row["amount_atomic"]) + int(row["fee_atomic"])
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (total, user_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, note) "
                    "VALUES (?, 'withdraw_refund', ?, ?)",
                    (user_id, total, note),
                )
                conn.execute(
                    "UPDATE payouts SET status = 'refunded', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (payout_id,),
                )
                if row["ledger_id"] is not None:
                    conn.execute(
                        "UPDATE ledger SET tx_hash = ?, note = ? WHERE id = ?",
                        ("refunded", note, int(row["ledger_id"])),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mark_payout_suspect(self, payout_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE payouts SET status = 'suspect', "
                "updated_at = datetime('now') "
                "WHERE id = ? AND status = 'transferring'",
                (payout_id,),
            )

    def list_open_payouts(self) -> list[PayoutRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payouts WHERE status IN "
                "('queued', 'transferring', 'suspect') ORDER BY id ASC"
            ).fetchall()
            return [_payout_from_row(r) for r in rows]

    def refund_withdraw(self, user_id: int, amount: int, note: str = "withdraw_refund") -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, user_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, note) "
                    "VALUES (?, 'withdraw_refund', ?, ?)",
                    (user_id, amount, note),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def withdrawn_today(self, user_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(-amount_atomic), 0) AS total FROM ledger "
                "WHERE user_id = ? AND kind = 'withdraw' "
                "AND date(created_at) = date('now')",
                (user_id,),
            ).fetchone()
            return int(row["total"])

    def all_payment_ids(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payment_id FROM users").fetchall()
            return [str(r["payment_id"]) for r in rows]

    def list_users(self) -> list[UserRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY balance_atomic DESC, id ASC"
            ).fetchall()
            return [_user_from_row(r) for r in rows]

    def total_liabilities(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(balance_atomic), 0) AS total FROM users"
            ).fetchone()
            held = conn.execute(
                "SELECT COALESCE(SUM(amount_atomic), 0) AS total FROM giveaways "
                "WHERE status = 'open'"
            ).fetchone()
            return int(row["total"]) + int(held["total"])

    def create_giveaway(
        self,
        giveaway_id: str,
        platform: str,
        chat_id: str,
        sender_user_id: int,
        amount: int,
    ) -> GiveawayRow:
        """Escrow `amount` from sender into an open giveaway."""
        if amount <= 0:
            raise ValueError("giveaway amount must be positive")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (amount, sender_user_id, amount),
                )
                if cur.rowcount != 1:
                    raise ValueError("insufficient balance")
                conn.execute(
                    "INSERT INTO giveaways(id, platform, chat_id, sender_user_id, "
                    "amount_atomic, status) VALUES (?, ?, ?, ?, ?, 'open')",
                    (giveaway_id, platform, chat_id, sender_user_id, amount),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, note) "
                    "VALUES (?, 'giveaway_hold', ?, ?)",
                    (sender_user_id, -amount, giveaway_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        row = self.get_giveaway(giveaway_id)
        assert row is not None
        return row

    def get_giveaway(self, giveaway_id: str) -> Optional[GiveawayRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
            ).fetchone()
            return _giveaway_from_row(row) if row else None

    def set_giveaway_message_id(self, giveaway_id: str, message_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE giveaways SET message_id = ? WHERE id = ?",
                (message_id, giveaway_id),
            )

    def claim_giveaway(self, giveaway_id: str, claimer_user_id: int) -> GiveawayRow:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
                ).fetchone()
                if not row:
                    raise ValueError("giveaway not found")
                if str(row["status"]) != "open":
                    raise ValueError("giveaway not found or already claimed")
                sender_id = int(row["sender_user_id"])
                amount = int(row["amount_atomic"])
                if claimer_user_id == sender_id:
                    raise ValueError("cannot claim your own giveaway")
                cur = conn.execute(
                    "UPDATE giveaways SET status = 'claimed', claimer_user_id = ?, "
                    "closed_at = datetime('now') WHERE id = ? AND status = 'open'",
                    (claimer_user_id, giveaway_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("giveaway not found or already claimed")
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, claimer_user_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'giveaway_claim', ?, ?, ?)",
                    (claimer_user_id, amount, sender_id, giveaway_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        out = self.get_giveaway(giveaway_id)
        assert out is not None
        return out

    def cancel_giveaway(self, giveaway_id: str, requester_user_id: int) -> GiveawayRow:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
                ).fetchone()
                if not row:
                    raise ValueError("giveaway not found")
                if str(row["status"]) != "open":
                    raise ValueError("giveaway not found or already closed")
                sender_id = int(row["sender_user_id"])
                if requester_user_id != sender_id:
                    raise ValueError("only the creator can cancel this giveaway")
                amount = int(row["amount_atomic"])
                cur = conn.execute(
                    "UPDATE giveaways SET status = 'cancelled', "
                    "closed_at = datetime('now') WHERE id = ? AND status = 'open'",
                    (giveaway_id,),
                )
                if cur.rowcount != 1:
                    raise ValueError("giveaway not found or already closed")
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, sender_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, note) "
                    "VALUES (?, 'giveaway_cancel', ?, ?)",
                    (sender_id, amount, giveaway_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        out = self.get_giveaway(giveaway_id)
        assert out is not None
        return out

    def get_rain_user(self) -> Optional[UserRow]:
        return self.get_user(RAIN_PLATFORM, RAIN_PLATFORM_USER_ID)

    def ensure_rain_user(
        self, payment_id: str, integrated_address: str = ""
    ) -> UserRow:
        existing = self.get_rain_user()
        if existing:
            if integrated_address and not existing.integrated_address:
                self.update_integrated_address(existing.id, integrated_address)
                existing = self.get_user_by_id(existing.id) or existing
            return existing
        return self.create_user(
            platform=RAIN_PLATFORM,
            platform_user_id=RAIN_PLATFORM_USER_ID,
            display_name="Rain pool",
            payment_id=payment_id,
            integrated_address=integrated_address,
        )

    def rain_paused(self) -> bool:
        return self.get_meta(RAIN_META_PAUSED, "0") in ("1", "true", "yes")

    def set_rain_paused(self, paused: bool) -> None:
        self.set_meta(RAIN_META_PAUSED, "1" if paused else "0")

    def last_rain_claim_at(self, user_id: int) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT claimed_at FROM rain_claims WHERE user_id = ? "
                "ORDER BY claimed_at DESC, id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return str(row["claimed_at"]) if row else None

    def fund_rain(self, from_user_id: int, rain_user_id: int, amount: int) -> None:
        if amount <= 0:
            raise ValueError("fund amount must be positive")
        if from_user_id == rain_user_id:
            raise ValueError("cannot fund rain from rain")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (amount, from_user_id, amount),
                )
                if cur.rowcount != 1:
                    raise ValueError("insufficient balance")
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, rain_user_id),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'rain_fund', ?, ?, ?)",
                    (from_user_id, -amount, rain_user_id, "rain"),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'rain_fund_in', ?, ?, ?)",
                    (rain_user_id, amount, from_user_id, "rain"),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def claim_rain(
        self,
        claimer_user_id: int,
        rain_user_id: int,
        amount: int,
        platform: str,
        platform_user_id: str,
        cooldown_sec: int,
    ) -> None:
        """Atomically debit rain pool and credit claimer if cooldown allows."""
        if amount <= 0:
            raise ValueError("claim amount must be positive")
        if claimer_user_id == rain_user_id:
            raise ValueError("rain pool cannot claim")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                last = conn.execute(
                    "SELECT claimed_at FROM rain_claims WHERE user_id = ? "
                    "ORDER BY claimed_at DESC, id DESC LIMIT 1",
                    (claimer_user_id,),
                ).fetchone()
                if last is not None:
                    row = conn.execute(
                        "SELECT CAST("
                        "(julianday('now') - julianday(?)) * 86400 AS INTEGER"
                        ") AS elapsed",
                        (str(last["claimed_at"]),),
                    ).fetchone()
                    elapsed = int(row["elapsed"]) if row else 0
                    if elapsed < cooldown_sec:
                        remaining = cooldown_sec - elapsed
                        raise ValueError(f"cooldown:{remaining}")
                cur = conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic - ? "
                    "WHERE id = ? AND balance_atomic >= ?",
                    (amount, rain_user_id, amount),
                )
                if cur.rowcount != 1:
                    raise ValueError("rain pool empty or too small")
                conn.execute(
                    "UPDATE users SET balance_atomic = balance_atomic + ? WHERE id = ?",
                    (amount, claimer_user_id),
                )
                conn.execute(
                    "INSERT INTO rain_claims(user_id, platform, platform_user_id, "
                    "amount_atomic) VALUES (?, ?, ?, ?)",
                    (claimer_user_id, platform, platform_user_id, amount),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'rain_claim_out', ?, ?, ?)",
                    (rain_user_id, -amount, claimer_user_id, "rain"),
                )
                conn.execute(
                    "INSERT INTO ledger(user_id, kind, amount_atomic, "
                    "counterparty_user_id, note) VALUES (?, 'rain_claim', ?, ?, ?)",
                    (claimer_user_id, amount, rain_user_id, "rain"),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise


def _user_from_row(row: sqlite3.Row) -> UserRow:
    return UserRow(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        display_name=str(row["display_name"]),
        payment_id=str(row["payment_id"]),
        integrated_address=str(row["integrated_address"]),
        balance_atomic=int(row["balance_atomic"]),
    )


def _giveaway_from_row(row: sqlite3.Row) -> GiveawayRow:
    claimer = row["claimer_user_id"]
    return GiveawayRow(
        id=str(row["id"]),
        platform=str(row["platform"]),
        chat_id=str(row["chat_id"]),
        message_id=str(row["message_id"] or ""),
        sender_user_id=int(row["sender_user_id"]),
        amount_atomic=int(row["amount_atomic"]),
        status=str(row["status"]),
        claimer_user_id=int(claimer) if claimer is not None else None,
    )


def _payout_from_row(row: sqlite3.Row) -> PayoutRow:
    tx = row["tx_hash"]
    led = row["ledger_id"]
    return PayoutRow(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        address=str(row["address"]),
        amount_atomic=int(row["amount_atomic"]),
        fee_atomic=int(row["fee_atomic"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        tx_hash=str(tx) if tx is not None else None,
        ledger_id=int(led) if led is not None else None,
    )
