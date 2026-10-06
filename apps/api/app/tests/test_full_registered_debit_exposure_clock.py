"""Durable observation clocks are distinct from current original validation clocks."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.asset_exposure import AssetExposure
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.tests.test_execution_domain import NOW, A, context
from app.tests.test_full_registered_account_debits import original_inputs


@pytest.mark.parametrize("clock", ["verified-original", "missing-original", "future"])
def test_durable_exposure_preserves_its_clock_and_claims_or_remains_unknown(clock: str) -> None:
    stamp = NOW + timedelta(seconds=1) if clock == "future" else NOW - timedelta(days=1)
    exposure = AssetExposure(
        as_of=stamp,
        scope="general_idle_funds",
        managed_principal_cents=0,
        pending_purchase_cents=0,
        reserved_cash_by_account={A: 100},
        evidence_ids=[] if clock == "missing-original" else [UUID(int=300)],
    )
    original = context().model_copy(update={"exposure": exposure})
    proof = derive_full_account_debit_bounds(*original_inputs(original=original))
    if clock == "verified-original":
        assert proof.status == "PASSED"
        assert proof.accounts[0].other_claims_cents == 100
        assert proof.accounts[0].minimum_account_cash_lower_bound_cents == 480
        assert original.exposure is not None and original.exposure.as_of == stamp
    else:
        assert proof.status == "UNKNOWN"
        assert proof.accounts[0].minimum_account_cash_lower_bound_cents is None
        assert proof.reasons == [
            "OTHER_EXPOSURE_CURRENT_ROW_PROOF_MISSING"
            if clock == "missing-original"
            else "OTHER_EXPOSURE_CLOCK_NOT_PROVEN"
        ]
