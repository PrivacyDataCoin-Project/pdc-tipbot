from pathlib import Path

from tipbot.config import Settings
from tipbot.db import Database
from tipbot.service import TipService
from tipbot.wallet import WalletClient


def _service(tmp_path: Path) -> TipService:
    db = Database(tmp_path / "g.db")
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        database_path=tmp_path / "g.db",
    )
    return TipService(settings, db, WalletClient(settings))


def test_giveaway_claim(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "1", "@alice")
    b = svc.ensure_user("telegram", "2", "@bob")
    assert svc.db.credit_deposit(a.id, a.payment_id, "txg1", 3_000_000_000_000, 10)

    created = svc.handle(
        "telegram",
        "1",
        "@alice",
        "giveaway",
        ["1"],
        is_private=False,
        chat_id="-1001",
    )
    assert created.giveaway_id
    assert created.buttons
    a_mid = svc.db.get_user_by_id(a.id)
    assert a_mid and a_mid.balance_atomic == 2_000_000_000_000

    claimed = svc.claim_giveaway_callback("telegram", "2", "@bob", created.giveaway_id)
    assert "claimed" in claimed.text.lower()
    assert "when:" in claimed.text.lower()
    assert claimed.notify_user_id == "2"
    assert claimed.notify_text and "when:" in claimed.notify_text.lower()
    # Creator gets a DM with who + when
    assert claimed.extra_notifies
    plat, uid, msg = claimed.extra_notifies[0]
    assert plat == "telegram" and uid == "1"
    assert "bob" in msg.lower()
    assert "when:" in msg.lower()
    b2 = svc.db.get_user_by_id(b.id)
    assert b2 and b2.balance_atomic == 1_000_000_000_000
    ga = svc.db.get_giveaway(created.giveaway_id)
    assert ga and ga.status == "claimed"


def test_giveaway_cancel_refund(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "10", "@alice")
    assert svc.db.credit_deposit(a.id, a.payment_id, "txg2", 2_000_000_000_000, 10)
    created = svc.handle(
        "telegram",
        "10",
        "@alice",
        "giveaway",
        ["0.5"],
        is_private=False,
        chat_id="-1002",
    )
    assert created.giveaway_id
    cancelled = svc.cancel_giveaway_callback(
        "telegram", "10", "@alice", created.giveaway_id
    )
    assert "cancelled" in cancelled.text.lower()
    a2 = svc.db.get_user_by_id(a.id)
    assert a2 and a2.balance_atomic == 2_000_000_000_000


def test_cannot_claim_own(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = svc.ensure_user("telegram", "20", "@alice")
    assert svc.db.credit_deposit(a.id, a.payment_id, "txg3", 2_000_000_000_000, 10)
    created = svc.handle(
        "telegram",
        "20",
        "@alice",
        "giveaway",
        ["1"],
        is_private=False,
        chat_id="-1003",
    )
    assert created.giveaway_id
    bad = svc.claim_giveaway_callback("telegram", "20", "@alice", created.giveaway_id)
    assert "own" in bad.text.lower()
    ga = svc.db.get_giveaway(created.giveaway_id)
    assert ga and ga.status == "open"


def test_giveaway_private_rejected(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.ensure_user("telegram", "30", "@alice")
    result = svc.handle(
        "telegram",
        "30",
        "@alice",
        "giveaway",
        ["1"],
        is_private=True,
        chat_id="30",
    )
    assert "group" in result.text.lower()
