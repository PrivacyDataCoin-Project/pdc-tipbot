from tipbot.scanner import DepositScanner


def test_deposit_ready_requires_inclusion_and_confs() -> None:
    assert not DepositScanner._deposit_ready(
        block_height=0,
        confirmations=0,
        required=10,
        unlock_time=0,
        tip_height=100,
    )
    assert not DepositScanner._deposit_ready(
        block_height=90,
        confirmations=9,
        required=10,
        unlock_time=0,
        tip_height=99,
    )
    assert DepositScanner._deposit_ready(
        block_height=90,
        confirmations=10,
        required=10,
        unlock_time=0,
        tip_height=100,
    )


def test_deposit_ready_waits_for_unlock_time() -> None:
    assert not DepositScanner._deposit_ready(
        block_height=90,
        confirmations=20,
        required=10,
        unlock_time=105,
        tip_height=100,
    )
    assert DepositScanner._deposit_ready(
        block_height=90,
        confirmations=20,
        required=10,
        unlock_time=100,
        tip_height=100,
    )
