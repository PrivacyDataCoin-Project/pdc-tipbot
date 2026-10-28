"""Atomic amount helpers (1 PDC = 10^12)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN

ATOMIC = 10**12


def parse_amount(text: str) -> int:
    """Parse human PDC amount to atomic units. Raises ValueError on bad input."""
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        raise ValueError("empty amount")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {text!r}") from exc
    if value <= 0:
        raise ValueError("amount must be positive")
    atomic = (value * ATOMIC).to_integral_value(rounding=ROUND_DOWN)
    if atomic <= 0:
        raise ValueError("amount too small")
    return int(atomic)


def format_amount(atomic: int) -> str:
    """Format atomic units as a readable PDC string without trailing zeros noise."""
    if atomic < 0:
        raise ValueError("negative amount")
    whole, frac = divmod(atomic, ATOMIC)
    if frac == 0:
        return str(whole)
    frac_str = f"{frac:012d}".rstrip("0")
    return f"{whole}.{frac_str}"


def compute_rain_claim_amount(
    pool_atomic: int,
    *,
    target_claims_per_day: int,
    min_atomic: int,
    max_atomic: int,
) -> int:
    """Dynamic rain claim size so a pool covers ~target claims.

    - ideal = floor(pool / target)
    - if ideal >= min → clamp(ideal, min, max)
    - elif pool >= min → min (slow drain when pool is small)
    - else → ValueError (pool empty / too small)
    """
    if min_atomic <= 0 or max_atomic < min_atomic:
        raise ValueError("invalid rain claim bounds")
    if target_claims_per_day <= 0:
        raise ValueError("target_claims_per_day must be positive")
    if pool_atomic < min_atomic:
        raise ValueError("rain pool empty or too small")
    ideal = pool_atomic // target_claims_per_day
    if ideal >= min_atomic:
        claim = min(ideal, max_atomic)
    else:
        claim = min_atomic
    if claim > pool_atomic:
        raise ValueError("rain pool empty or too small")
    return int(claim)
