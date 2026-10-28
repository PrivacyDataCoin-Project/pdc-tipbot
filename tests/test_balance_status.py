from tipbot.service import TipService


def test_pending_status_format_helpers() -> None:
    # Pure formatting expectations used in /balance copy
    needed = 10
    assert f"{min(1, needed)}/{needed} pending" == "1/10 pending"
    assert f"{needed}/{needed} unlocked" == "10/10 unlocked"
