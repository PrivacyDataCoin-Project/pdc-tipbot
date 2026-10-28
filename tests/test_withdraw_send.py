from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "w.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "w.db",
        transfer_fee_atomic=10_000_000_000,
        min_withdraw_atomic=1_000_000_000,
    )
    return TipService(settings, db, WalletClient(settings))


def test_withdraw_sweeps_unlocked_minus_fee(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    u = svc.ensure_user("telegram", "1", "@a")
    assert svc.db.credit_deposit(u.id, u.payment_id, "txw1", 5_000_000_000_000, 1)
    # dry-run transfer succeeds
    addr = "pdc1111111111111111111111111111111111111111111111111111111111111111111111111111111111"
    # force address check: use alias style
    result = svc.handle("telegram", "1", "@a", "withdraw", ["@mywallet"], is_private=True)
    assert "Withdraw" in result.text or "withdraw" in result.text.lower() or "Network fee" in result.text
    u2 = svc.db.get_user_by_id(u.id)
    assert u2 and u2.balance_atomic == 0


def test_send_rejects_over_unlocked_minus_fee(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    u = svc.ensure_user("telegram", "2", "@b")
    assert svc.db.credit_deposit(u.id, u.payment_id, "txs1", 1_000_000_000_000, 1)
    # unlocked 1.0, fee 0.01 → max send 0.99; try 1.0
    result = svc.handle(
        "telegram",
        "2",
        "@b",
        "send",
        ["@dest", "1"],
        is_private=True,
    )
    assert "too high" in result.text.lower() or "max send" in result.text.lower()
