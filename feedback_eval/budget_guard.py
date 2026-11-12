"""Conservative budget arithmetic for future integration; no API calls."""

from decimal import Decimal, InvalidOperation


class BudgetUnverified(ValueError):
    """Budget cannot be safely evaluated."""


def _money(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BudgetUnverified("invalid amount") from None
    if not number.is_finite() or number < 0:
        raise BudgetUnverified("amount must be finite and non-negative")
    return number


def remaining_cny(cap, prior, spent, pending):
    """Compute conservative remaining funds; unknown is never treated as zero."""
    if any(value is None for value in (cap, prior, spent, pending)):
        raise BudgetUnverified("unknown cost or pending hold")
    return _money(cap) - _money(prior) - _money(spent) - _money(pending)


def remaining_wallet(cap, wallet, spent, pending):
    """Remaining this-run funds from a live wallet.

    `wallet` is the provider remaining balance, which already nets historical
    spend. Do not pass a forged historical spent=0; pass the live wallet and
    only subtract this-run `spent` and `pending` holds.
    """
    if any(value is None for value in (cap, wallet, spent, pending)):
        raise BudgetUnverified("unknown wallet, cap, spend, or pending hold")
    ceiling = min(_money(cap), _money(wallet))
    return ceiling - _money(spent) - _money(pending)


def allow_reservation(cap, prior, spent, pending, worst_case):
    """Check a conservative reservation before a call; callers must lock and persist."""
    available = remaining_cny(cap, prior, spent, pending)
    return _money(worst_case) <= available


def allow_wallet_reservation(cap, wallet, spent, pending, worst_case):
    """Reserve one call against min(cap, live wallet) minus this-run spend."""
    available = remaining_wallet(cap, wallet, spent, pending)
    return _money(worst_case) <= available
