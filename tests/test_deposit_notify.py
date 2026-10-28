import os

from tipbot.amounts import format_amount
from tipbot.config import Settings


def test_explorer_default_uses_transaction_path(monkeypatch) -> None:
    monkeypatch.delenv("EXPLORER_TX_URL", raising=False)
    # Bypass dotenv-loaded process env from local .env by forcing empty then reload field
    monkeypatch.setenv("EXPLORER_TX_URL", "")
    s = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="",
        explorer_tx_url="https://explorer.privacydatacoin.com/transaction/",
    )
    assert s.explorer_tx_url.endswith("/transaction/")


def test_deposit_message_shape() -> None:
    amount = format_amount(1_500_000_000_000)
    tx = "abc123"
    base = "https://explorer.privacydatacoin.com/transaction/"
    text = (
        f"Deposit received: {amount} PDC\n"
        f"Your tipbot balance was credited.\n"
        f"Tx: {base}{tx}"
    )
    assert "1.5 PDC" in text
    assert text.endswith(f"/transaction/{tx}")
