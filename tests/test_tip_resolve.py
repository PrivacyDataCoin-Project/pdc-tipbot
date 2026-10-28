from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "tip.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "tip.db",
        min_tip_atomic=1_000_000_000,
    )
    return TipService(settings, db, WalletClient(settings))


def test_tip_by_display_name_without_at(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "100", "@ArqTras")
    b = svc.ensure_user("telegram", "200", "Mister")
    assert svc.db.credit_deposit(a.id, a.payment_id, "t1", 5_000_000_000_000, 1)

    result = svc.handle(
        "telegram",
        "100",
        "@ArqTras",
        "tip",
        ["Mister", "1", "hello"],
        is_private=False,
        chat_id="-1",
    )
    assert "Tipped" in result.text
    assert "hello" in result.text
    assert result.tip_telegram_id == "200"
    assert result.notify_user_id == "200"
    b2 = svc.db.get_user_by_id(b.id)
    assert b2 and b2.balance_atomic == 1_000_000_000_000


def test_tip_by_at_username_case_insensitive(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "100", "@ArqTras")
    b = svc.ensure_user("telegram", "200", "Mister")
    assert svc.db.credit_deposit(a.id, a.payment_id, "t2", 5_000_000_000_000, 1)

    result = svc.handle(
        "telegram",
        "100",
        "@ArqTras",
        "tip",
        ["@mister", "1"],
        is_private=False,
        chat_id="-1",
    )
    assert result.tip_telegram_id == "200"
    b2 = svc.db.get_user_by_id(b.id)
    assert b2 and b2.balance_atomic == 1_000_000_000_000
