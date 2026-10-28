from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "t.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "t.db",
    )
    return TipService(settings, db, WalletClient(settings))


def test_tip_moves_balance(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@alice")
    b = svc.ensure_user("telegram", "2", "@bob")
    # credit alice via deposit helper
    assert svc.db.credit_deposit(a.id, a.payment_id, "tx1", 5_000_000_000_000, 100)
    a = svc.db.get_user_by_id(a.id)
    assert a and a.balance_atomic == 5_000_000_000_000

    result = svc.handle(
        "telegram",
        "1",
        "@alice",
        "tip",
        ["@bob", "1.5"],
        is_private=False,
    )
    assert "Tipped" in result.text
    a2 = svc.db.get_user_by_id(a.id)
    b2 = svc.db.get_user_by_id(b.id)
    assert a2 and b2
    assert a2.balance_atomic == 3_500_000_000_000
    assert b2.balance_atomic == 1_500_000_000_000


def test_insufficient_balance(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.ensure_user("matrix", "@alice:privacydatacoin.com", "alice")
    svc.ensure_user("matrix", "@bob:privacydatacoin.com", "bob")
    result = svc.handle(
        "matrix",
        "@alice:privacydatacoin.com",
        "alice",
        "tip",
        ["@bob:privacydatacoin.com", "1"],
        is_private=False,
    )
    assert "insufficient" in result.text.lower()


def test_payment_id_unique(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    u1 = svc.ensure_user("telegram", "10", "u1")
    u2 = svc.ensure_user("telegram", "11", "u2")
    assert u1.payment_id != u2.payment_id
    assert len(u1.payment_id) == 16
    assert all(c in "0123456789abcdef" for c in u1.payment_id)
