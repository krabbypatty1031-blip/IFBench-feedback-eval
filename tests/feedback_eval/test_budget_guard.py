from decimal import Decimal

import pytest

from feedback_eval.budget_guard import (
    BudgetUnverified,
    allow_reservation,
    allow_wallet_reservation,
    remaining_cny,
    remaining_wallet,
)


def test_remaining_includes_prior_and_pending():
    assert remaining_cny("50", "0.2", "1.1", "0.3") == Decimal("48.4")
    assert allow_reservation("50", "0.2", "1.1", "0.3", "48.4")
    assert not allow_reservation("50", "0.2", "1.1", "0.3", "48.41")


@pytest.mark.parametrize("unknown", [None, "NaN", "Infinity", "-1"])
def test_unknown_or_invalid_spend_blocks(unknown):
    with pytest.raises(BudgetUnverified):
        allow_reservation("50", unknown, "0", "0", "1")


def test_wallet_reservation_uses_live_balance_not_forged_prior():
    # min(40, 54.24) - 1.1 - 0 = 38.9; a 38.9 hold fits, 39.0 does not.
    assert remaining_wallet("40", "54.24", "1.1", "0") == Decimal("38.9")
    assert allow_wallet_reservation("40", "54.24", "1.1", "0", "38.9")
    assert not allow_wallet_reservation("40", "54.24", "1.1", "0", "39")
    with pytest.raises(BudgetUnverified):
        remaining_wallet("40", None, "0", "0")
