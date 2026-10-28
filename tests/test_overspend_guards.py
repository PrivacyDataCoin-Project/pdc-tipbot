from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient, WalletError


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "os.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "os.db",
        transfer_fee_atomic=10_000_000_000,
        min_tip_atomic=1_000_000_000,
        min_withdraw_atomic=1_000_000_000,
    )
    return TipService(settings, db, WalletClient(settings))


def test_tip_cannot_overspend(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@a")
    svc.ensure_user("telegram", "2", "@b")
    assert svc.db.credit_deposit(a.id, a.payment_id, "x1", 1_000_000_000_000, 1)
    bad = svc.handle("telegram", "1", "@a", "tip", ["@b", "2"], is_private=False)
    assert "insufficient" in bad.text.lower()
    a2 = svc.db.get_user_by_id(a.id)
    assert a2 and a2.balance_atomic == 1_000_000_000_000


def test_giveaway_cannot_overspend(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@a")
    assert svc.db.credit_deposit(a.id, a.payment_id, "x2", 500_000_000_000, 1)
    bad = svc.handle(
        "telegram", "1", "@a", "giveaway", ["1"], is_private=False, chat_id="-1"
    )
    assert "insufficient" in bad.text.lower()


def test_send_cannot_exceed_unlocked_minus_fee(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@a")
    assert svc.db.credit_deposit(a.id, a.payment_id, "x3", 1_000_000_000_000, 1)
    bad = svc.handle(
        "telegram", "1", "@a", "send", ["@dest", "1"], is_private=True
    )
    assert "too high" in bad.text.lower() or "max send" in bad.text.lower()
