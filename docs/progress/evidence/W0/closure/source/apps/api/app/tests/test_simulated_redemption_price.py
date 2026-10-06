"""Integer loss price boundaries before the real bank-backed integration chain."""

import pytest
from app.services.simulated_redemption_quote import early_loss_cents


@pytest.mark.parametrize(
    ("principal", "bps", "expected"),
    [(100_000, 50, 500), (1, 1, 1), (10_001, 1, 2), (2**63 - 1, 10_000, 2**63 - 1)],
)
def test_loss_is_conservative_and_conserved(principal: int, bps: int, expected: int) -> None:
    loss = early_loss_cents(principal, bps)
    assert loss == expected
    assert loss + (principal - loss) == principal
    assert 0 <= loss <= principal


@pytest.mark.parametrize(
    ("principal", "bps"),
    [(True, 1), (1, True), (0, 1), (-1, 1), (2**63, 1), (1, 0), (1, 10_001), (1.1, 1)],
)
def test_invalid_price_cannot_turn_into_a_money_fact(principal: int, bps: int) -> None:
    with pytest.raises(ValueError):
        early_loss_cents(principal, bps)
