from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "pr.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "pr.db",
        transfer_fee_atomic=10_000_000_000,
        min_withdraw_atomic=1_000_000_000,
    )
    return TipService(settings, db, WalletClient(settings))


def test_queued_payout_refunded_on_recover(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    u = svc.ensure_user("telegram", "1", "@a")
    assert svc.db.credit_deposit(u.id, u.payment_id, "r1", 5_000_000_000_000, 1)
    payout = svc.db.create_queued_payout(
        u.id,
        address="pdcTEST",
        amount=1_000_000_000_000,
        fee=10_000_000_000,
        kind="send",
    )
    assert payout.status == "queued"
    mid = svc.db.get_user_by_id(u.id)
    assert mid and mid.balance_atomic == 5_000_000_000_000 - 1_010_000_000_000

    stats = svc.recover_payouts()
    assert stats["refunded"] == 1
    assert stats["suspect"] == 0
    end = svc.db.get_user_by_id(u.id)
    assert end and end.balance_atomic == 5_000_000_000_000
    assert svc.db.get_payout(payout.id).status == "refunded"


def test_transferring_becomes_suspect_no_refund(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    u = svc.ensure_user("telegram", "1", "@a")
    assert svc.db.credit_deposit(u.id, u.payment_id, "r2", 5_000_000_000_000, 1)
    payout = svc.db.create_queued_payout(
        u.id,
        address="pdcTEST",
        amount=1_000_000_000_000,
        fee=10_000_000_000,
        kind="withdraw",
    )
    assert svc.db.mark_payout_transferring(payout.id)
    bal_after_debit = svc.db.get_user_by_id(u.id).balance_atomic

    stats = svc.recover_payouts()
    assert stats["suspect"] == 1
    assert stats["refunded"] == 0
    assert svc.db.get_payout(payout.id).status == "suspect"
    assert svc.db.get_user_by_id(u.id).balance_atomic == bal_after_debit

    # Further on-chain payout blocked for this user
    blocked = svc.handle(
        "telegram",
        "1",
        "@a",
        "send",
        ["@dest", "0.5"],
        is_private=True,
    )
    assert "blocked" in blocked.text.lower() or "unresolved" in blocked.text.lower()
