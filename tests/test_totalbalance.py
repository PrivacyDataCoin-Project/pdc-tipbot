from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "tb.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "tb.db",
        admin_telegram_usernames=("arqtras",),
    )
    return TipService(settings, db, WalletClient(settings))


def test_totalbalance_admin_dm(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@ArqTras")
    b = svc.ensure_user("telegram", "2", "@bob")
    assert svc.db.credit_deposit(a.id, a.payment_id, "t1", 2_000_000_000_000, 1)
    assert svc.db.credit_deposit(b.id, b.payment_id, "t2", 1_000_000_000_000, 1)
    result = svc.handle(
        "telegram",
        "1",
        "@ArqTras",
        "totalbalance",
        [],
        is_private=True,
    )
    assert "totalbalance" in result.text.lower() or "Hot wallet" in result.text
    assert "@ArqTras" in result.text or "ArqTras" in result.text
    assert "@bob" in result.text or "bob" in result.text
    assert result.private


def test_totalbalance_denied_public_or_other_user(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.ensure_user("telegram", "1", "@ArqTras")
    svc.ensure_user("telegram", "2", "@bob")
    denied_public = svc.handle(
        "telegram", "1", "@ArqTras", "totalbalance", [], is_private=False
    )
    assert "Unknown command" in denied_public.text
    denied_other = svc.handle(
        "telegram", "2", "@bob", "totalbalance", [], is_private=True
    )
    assert "Unknown command" in denied_other.text
