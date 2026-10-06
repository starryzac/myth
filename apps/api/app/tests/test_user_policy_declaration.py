"""Production declaration request risks; original isolated driver guard remains strict."""

from typing import Any
from uuid import uuid4

import pytest
from app.services import user_policy_declaration as service
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import ValidationError


def body(**changes: Any) -> service.UserDeclarationRequest:
    return service.UserDeclarationRequest.model_validate(
        {
            "configuration": {"type": "emergency_buffer", "amount_cents": 100003},
            "idempotency_key": "user-declaration-original",
            "expected_epoch_id": uuid4(),
            **changes,
        }
    )


@pytest.mark.parametrize("amount", [True, 1000.01, "1000", -1])
def test_money_must_be_exact_nonnegative_integer_cents(amount: Any) -> None:
    with pytest.raises(PolicyLifecycleError):
        service.candidate(body(configuration={"type": "emergency_buffer", "amount_cents": amount}))


@pytest.mark.parametrize("field", ["user_id", "now", "accepted", "bank_authority", "status"])
def test_candidate_request_cannot_supply_identity_clock_or_confirmation(field: str) -> None:
    with pytest.raises(ValidationError):
        body(**{field: "not server metadata"})


def test_complete_original_request_is_bound_even_when_canonical_config_matches() -> None:
    first = body()
    second = first.model_copy(
        update={"configuration": first.configuration | {"name": "自设应急名"}}
    )
    canonical, first_hash = service.candidate(first)
    changed, second_hash = service.candidate(second)
    assert canonical != changed and first_hash != second_hash
    identities = service.identity(uuid4(), first.expected_epoch_id, "k" * 160)
    assert len(identities[2]) <= 160 and identities[0] != identities[1]
