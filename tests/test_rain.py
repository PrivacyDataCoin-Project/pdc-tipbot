from pathlib import Path

from tipbot.amounts import ATOMIC, compute_rain_claim_amount
from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient

TG_CHAT = "-1004358061219"
MX_ROOM = "!9qWYkIpYCxEZl50pSJyLqr7xJJcfGe1L5y1_n8rf5Io"


def _service(tmp_path: Path, monkeypatch) -> TipService:
    monkeypatch.setenv("RAIN_TELEGRAM_CHAT_IDS", TG_CHAT)
    monkeypatch.setenv("RAIN_MATRIX_ROOM_IDS", MX_ROOM)
    db = Database(tmp_path / "rain.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "rain.db",
    )
    return TipService(settings, db, WalletClient(settings))


def test_compute_rain_claim_amount() -> None:
    mn = 100_000_000_000  # 0.1
    mx = 1_000_000_000_000  # 1
    # large pool → max
    assert (
        compute_rain_claim_amount(
            50 * ATOMIC, target_claims_per_day=10, min_atomic=mn, max_atomic=mx
        )
        == mx
    )
    # medium → ideal
    assert (
        compute_rain_claim_amount(
            5 * ATOMIC, target_claims_per_day=10, min_atomic=mn, max_atomic=mx
        )
        == 500_000_000_000
    )
    # small but >= min → min drain
    assert (
        compute_rain_claim_amount(
            500_000_000_000,
            target_claims_per_day=10,
            min_atomic=mn,
            max_atomic=mx,
        )
        == mn
    )
    import pytest

    with pytest.raises(ValueError):
        compute_rain_claim_amount(
            50_000_000_000, target_claims_per_day=10, min_atomic=mn, max_atomic=mx
        )


def test_rain_fund_and_claim(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path, monkeypatch)
    a = svc.ensure_user("telegram", "1", "@alice")
    assert svc.db.credit_deposit(a.id, a.payment_id, "txr1", 10 * ATOMIC, 10)

    funded = svc.handle(
        "telegram",
        "1",
        "@alice",
        "rain",
        ["fund", "5"],
        is_private=True,
        chat_id="",
    )
    assert "Added 5" in funded.text
    rain = svc.db.get_rain_user()
    assert rain and rain.balance_atomic == 5 * ATOMIC

    claimed = svc.handle(
        "telegram",
        "2",
        "@bob",
        "rain",
        [],
        is_private=False,
        chat_id=TG_CHAT,
    )
    assert "Rain claimed" in claimed.text
    bob = svc.db.get_user("telegram", "2")
    assert bob and bob.balance_atomic > 0

    again = svc.handle(
        "telegram",
        "2",
        "@bob",
        "rain",
        [],
        is_private=False,
        chat_id=TG_CHAT,
    )
    assert "Already claimed" in again.text or "24h" in again.text


def test_rain_rejects_wrong_chat_and_bots(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path, monkeypatch)
    a = svc.ensure_user("telegram", "10", "@a")
    assert svc.db.credit_deposit(a.id, a.payment_id, "txr2", 5 * ATOMIC, 10)
    svc.handle("telegram", "10", "@a", "rain", ["fund", "3"], is_private=True)

    wrong = svc.handle(
        "telegram",
        "11",
        "@b",
        "rain",
        [],
        is_private=False,
        chat_id="-100999",
    )
    assert "only in" in wrong.text.lower() or "Claim rain" in wrong.text

    bot = svc.handle(
        "telegram",
        "12",
        "@bot",
        "rain",
        [],
        is_private=False,
        chat_id=TG_CHAT,
        is_bot=True,
    )
    assert "Bot" in bot.text

    mx_ok = svc.handle(
        "matrix",
        "@bob:privacydatacoin.com",
        "bob",
        "rain",
        [],
        is_private=False,
        chat_id=MX_ROOM,
    )
    assert "Rain claimed" in mx_ok.text


def test_rain_whitelist_from_env(monkeypatch) -> None:
    monkeypatch.setenv("RAIN_TELEGRAM_CHAT_IDS", TG_CHAT)
    monkeypatch.setenv("RAIN_MATRIX_ROOM_IDS", MX_ROOM)
    s = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
    )
    assert TG_CHAT in s.rain_telegram_chat_ids
    assert MX_ROOM in s.rain_matrix_room_ids
    assert s.rain_chat_allowed("telegram", TG_CHAT)
    assert s.rain_chat_allowed("matrix", MX_ROOM)


def test_rain_whitelist_empty_by_default(monkeypatch) -> None:
    monkeypatch.setenv("RAIN_TELEGRAM_CHAT_IDS", "")
    monkeypatch.setenv("RAIN_MATRIX_ROOM_IDS", "")
    s = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
    )
    assert s.rain_telegram_chat_ids == ()
    assert s.rain_matrix_room_ids == ()
    assert not s.rain_chat_allowed("telegram", TG_CHAT)
