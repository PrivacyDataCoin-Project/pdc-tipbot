from tipbot.wallet import Payment, WalletClient
from tipbot.config import Settings


def test_bulk_payments_dedupe_prefers_nonzero_height(monkeypatch) -> None:
    settings = Settings(
        telegram_token="",
        matrix_access_token="",
        matrix_password="",
        wallet_rpc_url="http://127.0.0.1:9/json_rpc",
    )
    client = WalletClient(settings)

    def fake_call(method, params=None):
        assert method == "get_bulk_payments"
        return {
            "payments": [
                {
                    "payment_id": "aabbccddeeff0011",
                    "tx_hash": "deadbeef",
                    "amount": 1000,
                    "block_height": 0,
                    "unlock_time": 0,
                },
                {
                    "payment_id": "aabbccddeeff0011",
                    "tx_hash": "deadbeef",
                    "amount": 1000,
                    "block_height": 42444,
                    "unlock_time": 0,
                },
            ]
        }

    monkeypatch.setattr(client, "_call", fake_call)
    pays = client.get_bulk_payments(["aabbccddeeff0011"], allow_locked=True)
    assert len(pays) == 1
    assert pays[0].block_height == 42444
