from tipbot.amounts import format_amount, parse_amount


def test_parse_and_format_roundtrip() -> None:
    assert parse_amount("1") == 1_000_000_000_000
    assert parse_amount("0.001") == 1_000_000_000
    assert format_amount(1_500_000_000_000) == "1.5"
    assert format_amount(1_000_000_000_000) == "1"


def test_parse_rejects_bad() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_amount("-1")
    with pytest.raises(ValueError):
        parse_amount("abc")
