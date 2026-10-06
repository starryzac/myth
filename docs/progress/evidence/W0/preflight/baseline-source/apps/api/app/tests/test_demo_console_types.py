"""The public console accepts presets and consent, never arbitrary bank results."""

import pytest
from app.services.demo_console import TEMPLATE_KINDS, template_configuration
from app.services.demo_console_types import DemoEventRequest, DemoResetRequest
from pydantic import ValidationError

EPOCH = "079f02bf-89bd-4bd8-bdb1-f950dc11fedd"


@pytest.mark.parametrize(
    "extra", ["amount_cents", "occurred_at", "user_id", "bank_status", "result"]
)
def test_event_rejects_browser_financial_payload(extra: str) -> None:
    with pytest.raises(ValidationError):
        DemoEventRequest.model_validate_json(
            '{"event_kind":"SALARY_RECEIVED","expected_epoch_id":"' + EPOCH + '","' + extra + '":1}'
        )


def test_reset_requires_true_consent_and_supports_absent_initial_epoch() -> None:
    request = DemoResetRequest.model_validate_json(
        '{"reset_key":"first","expected_epoch_id":null,"accepted":true}'
    )
    assert request.expected_epoch_id is None
    for value in ["false", "1", '"true"']:
        with pytest.raises(ValidationError):
            DemoResetRequest.model_validate_json(
                '{"reset_key":"first","expected_epoch_id":null,"accepted":' + value + "}"
            )


def test_template_terms_are_complete_bounded_and_penalty_never_silently_authorized() -> None:
    configurations = {kind: template_configuration(kind) for kind in TEMPLATE_KINDS}
    assert configurations["CAR_GOAL"]["cross_goal_reallocation_allowed"] is False
    assert configurations["LIQUID_ASSET"]["single_action_cap_cents"] == 250_000
    assert configurations["LIQUID_ASSET"]["allow_early_withdrawal_with_penalty"] is False
    fixed = configurations["FIXED_ASSET"]
    assert fixed["allowed_asset_classes"] == ["FIXED_DEPOSIT"]
    assert fixed["max_lock_days"] == 30
    assert fixed["single_action_cap_cents"] == 50_000
    assert fixed["allow_early_withdrawal_with_penalty"] is True
    assert configurations["RENT"]["auto_execute"] is False
