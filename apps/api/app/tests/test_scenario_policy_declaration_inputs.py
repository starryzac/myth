"""Only configuration/identity/DSN guards; no database operation is executed."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from app.db.models import EvidenceItem, PolicyProposal
from app.services import scenario_policy_declaration as candidate
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 5, 7, 10, tzinfo=UTC)


def config() -> dict[str, Any]:
    return {
        "type": "asset_authorization",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH_MGMT_T0"],
        "max_auto_managed_cents": 159117,
        "single_action_cap_cents": 67003,
        "max_redemption_delay_days": 0,
        "max_lock_days": 0,
        "allow_auto_recovery_without_penalty": True,
    }


def test_actual_original_general_validation_expands_defaults_and_retains_explicit_caps() -> None:
    canonical, digest, clock = candidate.canonical_inputs(
        config(), "native-structured-declaration", NOW
    )
    assert canonical["single_action_cap_cents"] == 67003
    assert canonical["max_auto_managed_cents"] == 159117
    assert canonical["max_principal_risk_level"] == 0
    assert canonical["allow_early_withdrawal_with_penalty"] is False
    assert len(digest) == 64 and clock == NOW


@pytest.mark.parametrize(
    "key,value",
    [
        ("single_action_cap_cents", True),
        ("max_auto_managed_cents", -1),
        ("single_action_cap_cents", 159118),
        ("autonomy_level", "AUTO_EXECUTE"),
        ("nested_caps", {}),
        ("goal_id", str(uuid4())),
        ("allowed_asset_classes", ["FAKE_RISK_CLASS"]),
    ],
)
def test_no_nested_caps_autonomy_or_invalid_money_can_bypass_complete_original_schema(
    key: str, value: Any
) -> None:
    with pytest.raises(PolicyLifecycleError) as error:
        candidate.canonical_inputs({**config(), key: value}, "fixed-key", NOW)
    assert error.value.code == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("key", ["", " leading", "line\nbreak", "nonASCII中文", True, "a" * 161])
def test_strict_key_refuses_aliases_and_non_plain_inputs(key: Any) -> None:
    with pytest.raises(PolicyLifecycleError) as error:
        candidate.canonical_inputs(config(), key, NOW)
    assert error.value.code == "INVALID_DECLARATION_KEY"


def test_uuid5_is_bound_to_user_epoch_and_original_key_and_separates_evidence_proposal() -> None:
    user, epoch = uuid4(), uuid4()
    actual = candidate.declaration_identity(user, epoch, "native-key")
    assert actual == candidate.declaration_identity(user, epoch, "native-key")
    assert actual[0] != actual[1] and len(actual[2] + ":proposal") <= 160
    assert actual != candidate.declaration_identity(uuid4(), epoch, "native-key")
    assert actual != candidate.declaration_identity(user, uuid4(), "native-key")
    assert actual != candidate.declaration_identity(user, epoch, "different-key")


def test_naive_clock_cannot_enter_a_declaration() -> None:
    with pytest.raises(PolicyLifecycleError) as error:
        candidate.canonical_inputs(config(), "native-key", NOW.replace(tzinfo=None))
    assert error.value.code == "INVALID_CLOCK"


@pytest.mark.parametrize(
    "configuration",
    [
        {
            "type": "goal_saving",
            "name": "工具合成目标",
            "target_cents": 100001,
            "deadline": "2027-10-01",
            "monthly_contribution": {"min_cents": 1001, "target_cents": 2003, "max_cents": 3007},
        },
        {
            "type": "recurring_obligation",
            "name": "工具合成房租",
            "payee_id": "synthetic-landlord-001",
            "amount_rule": {"kind": "exact", "amount_cents": 150001},
            "due_day": 1,
            "auto_execute": False,
        },
    ],
)
def test_goal_and_rent_use_complete_original_schema_without_fixed_template(
    configuration: dict[str, Any],
) -> None:
    canonical, digest, _ = candidate.canonical_inputs(configuration, "strict-other-policy", NOW)
    assert canonical["type"] == configuration["type"] and len(digest) == 64


@pytest.mark.parametrize(
    "mutation",
    [
        "proposal_bool_as_int",
        "evidence_bool_as_int",
        "first_version_bool_as_int",
        "first_version_hash",
    ],
)
def test_pure_original_metadata_rejects_bool_integer_alias_without_sql(mutation: str) -> None:
    # TOOL_ONLY declaration-input metadata, never a mock settled financial result.
    user, epoch = uuid4(), uuid4()
    canonical, digest, clock = candidate.canonical_inputs(config(), "original-key", NOW)
    proposal_id, evidence_id, source_ref = candidate.declaration_identity(
        user, epoch, "original-key"
    )
    content = {
        "simulation": True,
        "protocol": candidate.PROTOCOL,
        "user_id": str(user),
        "epoch_id": str(epoch),
        "idempotency_key": "original-key",
        "admitted_at": clock.isoformat(),
        "configuration": canonical,
        "configuration_hash": digest,
    }
    from app.domain.policy_configuration import configuration_hash

    evidence = SimpleNamespace(
        id=evidence_id,
        user_id=user,
        created_at=clock,
        evidence_level="USER_DECLARED",
        source_type=candidate.SOURCE_TYPE,
        source_ref=source_ref,
        status="VALID",
        valid_to=None,
        supersedes_id=None,
        observed_at=clock,
        valid_from=clock,
        content=deepcopy(content),
        content_hash=configuration_hash(content),
    )
    proposal = SimpleNamespace(
        id=proposal_id,
        user_id=user,
        source_type=candidate.SOURCE_TYPE,
        source_text=candidate.SOURCE_TEXT,
        compiler_version=candidate.PROTOCOL,
        idempotency_key=source_ref + ":proposal",
        proposed_configuration=deepcopy(canonical),
        evidence_ids=[str(evidence_id)],
        created_at=clock,
        status="PROPOSED",
        confirmed_policy_id=None,
    )
    metadata_session: Any = None
    if mutation == "proposal_bool_as_int":
        proposal.proposed_configuration["allow_auto_recovery_without_penalty"] = 1
    elif mutation == "evidence_bool_as_int":
        evidence.content["simulation"] = 1
    else:
        # Pure hostile metadata only: no actual authorization, database row,
        # execution or bank outcome is represented by these namespaces.
        policy_id = uuid4()
        proposal.status = "CONFIRMED"
        proposal.confirmed_policy_id = policy_id
        first = SimpleNamespace(
            configuration=deepcopy(canonical),
            content_hash=digest,
            evidence_ids=[str(evidence_id)],
            confirmed_at=clock,
            valid_from=clock,
        )
        if mutation == "first_version_bool_as_int":
            first.configuration["allow_auto_recovery_without_penalty"] = 1
        else:
            first.content_hash = "0" * 64
        metadata_session = SimpleNamespace(
            get=lambda *args, **kwargs: SimpleNamespace(id=policy_id, user_id=user),
            scalar=lambda *args, **kwargs: first,
        )
    with pytest.raises(PolicyLifecycleError) as error:
        candidate._original(
            cast(Session, metadata_session),
            user,
            epoch,
            "original-key",
            canonical,
            digest,
            NOW,
            cast(PolicyProposal, proposal),
            cast(EvidenceItem, evidence),
            source_ref,
            proposal_id,
            evidence_id,
        )
    assert error.value.code == "DECLARATION_ORIGINAL_CONFLICT"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://synthetic@127.0.0.1:54329/bounded_funds",
        "postgresql+psycopg://synthetic@remote.invalid:54329/bf_test_" + "a" * 32,
        "postgresql+psycopg://synthetic@127.0.0.1:5432/bf_test_" + "a" * 32,
    ],
)
def test_dsn_gate_refuses_formal_remote_and_wrong_port_without_connecting(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(url)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Connection was attempted by a pure isolation guard")

    monkeypatch.setattr(engine, "connect", forbidden)
    try:
        with pytest.raises(PolicyLifecycleError) as error:
            candidate.require_isolated(engine)
        assert error.value.code == "INVALID_SCENARIO_INPUT"
    finally:
        engine.dispose()
