"""Actual isolated PostgreSQL risks for declaration and original explicit confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import EvidenceItem, Policy, PolicyProposal, PolicyVersion, User
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services import scenario_policy_declaration as candidate
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import ExpectedScenarioError, InitialState, Scenario, ScenarioStep
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 5, 7, 10, tzinfo=UTC)


def config() -> dict[str, Any]:
    return {
        "type": "asset_authorization",
        "name": "GENERAL明确声明工具fixture",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH_MGMT_T0"],
        "max_auto_managed_cents": 159117,
        "single_action_cap_cents": 67003,
        "max_redemption_delay_days": 0,
        "max_lock_days": 0,
        "max_principal_risk_level": 0,
        "allow_auto_recovery_without_penalty": True,
        "allow_early_withdrawal_with_penalty": False,
    }


def epoch(engine: Engine) -> UUID:
    with Session(engine) as session:
        row = current_audit_epoch(session, DEMO_USER_ID)
        assert row is not None
        return row.id


def declare(
    engine: Engine,
    *,
    key: str = "general-original",
    configuration: dict[str, Any] | None = None,
    now: datetime = NOW,
    expected: UUID | None = None,
) -> candidate.DeclarationResult:
    return candidate.prepare_declared_policy(
        engine,
        DEMO_USER_ID,
        config() if configuration is None else configuration,
        key,
        epoch(engine) if expected is None else expected,
        now,
    )


def test_declaration_has_no_authority_and_same_key_replay_is_all_rows_read_only(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    original_epoch = epoch(demo_engine)
    before = database_snapshot(demo_engine)
    actual = declare(demo_engine)
    assert actual.status == "PROPOSED" and actual.grants_authority is False
    assert actual.configuration == validate_configuration(config())
    assert actual.configuration_hash == configuration_hash(actual.configuration)
    with Session(demo_engine) as session:
        assert session.scalars(select(Policy)).all() == []
        assert session.scalars(select(PolicyVersion)).all() == []
        proof = session.get(EvidenceItem, actual.evidence_id)
        proposal = session.get(PolicyProposal, actual.proposal_id)
        assert proof and proposal
        assert proof.evidence_level == "USER_DECLARED" and proof.status == "VALID"
        assert proof.content["epoch_id"] == str(original_epoch)
        assert proof.content["configuration_hash"] == actual.configuration_hash
        assert proposal.evidence_ids == [str(proof.id)] and proposal.confirmed_policy_id is None
        assert verify_audit_chain(session, DEMO_USER_ID, original_epoch).status == "VALID"
    after = database_snapshot(demo_engine)
    assert after != before
    replay = declare(demo_engine, now=NOW + timedelta(seconds=10))
    assert replay == actual
    assert database_snapshot(demo_engine) == after


def test_only_explicit_confirm_gains_authority_then_declaration_replay_preserves_all_rows(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    actual = declare(demo_engine)
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(
                session,
                DEMO_USER_ID,
                actual.proposal_id,
                actual.configuration_hash,
                False,
                NOW + timedelta(seconds=1),
            )
        assert error.value.code == "CONFIRMATION_REQUIRED"
    assert database_snapshot(demo_engine) == before
    with Session(demo_engine) as session, session.begin():
        confirmed = confirm_proposal(
            session,
            DEMO_USER_ID,
            actual.proposal_id,
            actual.configuration_hash,
            True,
            NOW + timedelta(seconds=2),
        )
    after = database_snapshot(demo_engine)
    replay = declare(demo_engine, now=NOW + timedelta(seconds=3))
    assert replay.status == "CONFIRMED" and replay.grants_authority is False
    assert replay.proposal_id == actual.proposal_id and replay.evidence_id == actual.evidence_id
    assert replay.configuration == actual.configuration and replay.admitted_at == actual.admitted_at
    assert database_snapshot(demo_engine) == after
    with Session(demo_engine) as session:
        version = session.get(PolicyVersion, confirmed.current_version_id)
        assert version and version.configuration == actual.configuration
        assert str(actual.evidence_id) in version.evidence_ids
        assert verify_audit_chain(session, DEMO_USER_ID, epoch(demo_engine)).status == "VALID"


def test_same_epoch_key_different_config_rejects_without_rewriting_original_fields(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    declare(demo_engine)
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as error:
        declare(demo_engine, configuration={**config(), "single_action_cap_cents": 53039})
    assert error.value.code == "DECLARATION_ORIGINAL_CONFLICT"
    assert database_snapshot(demo_engine) == before


@pytest.mark.parametrize(
    "mutation", ["status", "source", "owner", "hash", "missing_proposal", "missing_evidence"]
)
def test_same_id_original_conflict_is_refused_atomically_and_retains_all_fields(
    demo_engine: Engine,
    mutation: str,
) -> None:
    seed_demo(demo_engine)
    actual = declare(demo_engine)
    with Session(demo_engine) as session, session.begin():
        proof = session.get(EvidenceItem, actual.evidence_id)
        proposal = session.get(PolicyProposal, actual.proposal_id)
        assert proof and proposal
        if mutation == "status":
            proof.status = "SUPERSEDED"
        elif mutation == "source":
            proof.source_type = "DIFFERENT_SOURCE"
        elif mutation == "owner":
            other = User(
                id=uuid4(), external_ref="other-declaration-owner", display_name="synthetic"
            )
            session.add(other)
            session.flush()
            proof.user_id = other.id
        elif mutation == "hash":
            proof.content_hash = "0" * 64
        elif mutation == "missing_proposal":
            session.delete(proposal)
        else:
            session.delete(proof)
    # This is an explicitly corrupted fixture in a disposable generated bf_test DB.
    # The subsequent action must retain it, never repair/replace a historical row.
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as error:
        declare(demo_engine, now=NOW + timedelta(seconds=1))
    assert error.value.code == "DECLARATION_ORIGINAL_CONFLICT"
    assert database_snapshot(demo_engine) == before


def test_wrong_epoch_refused_without_any_new_originals(demo_engine: Engine) -> None:
    seed_demo(demo_engine)
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as error:
        declare(demo_engine, expected=uuid4())
    assert error.value.code == "STALE_DEMO_EPOCH"
    assert database_snapshot(demo_engine) == before


@pytest.mark.parametrize("mutation", ["bool_integer_alias", "wrong_hash"])
def test_confirmed_original_store_refuses_configuration_tamper_before_replay(
    demo_engine: Engine, mutation: str
) -> None:
    seed_demo(demo_engine)
    actual = declare(demo_engine)
    with Session(demo_engine) as session, session.begin():
        confirmed = confirm_proposal(
            session,
            DEMO_USER_ID,
            actual.proposal_id,
            actual.configuration_hash,
            True,
            NOW + timedelta(seconds=1),
        )
    before = database_snapshot(demo_engine)
    with pytest.raises(IntegrityError, match="PolicyVersion is immutable"):
        with Session(demo_engine) as session, session.begin():
            version = session.get(PolicyVersion, confirmed.current_version_id)
            assert version is not None
            values = (
                {
                    "configuration": {
                        **version.configuration,
                        "allow_auto_recovery_without_penalty": 1,
                    }
                }
                if mutation == "bool_integer_alias"
                else {"content_hash": "0" * 64}
            )
            # Core UPDATE exercises the actual immutable trigger even when Python
            # compares the JSON bool/int aliases as equal and ORM sees no change.
            session.execute(
                update(PolicyVersion).where(PolicyVersion.id == version.id).values(**values)
            )
    assert database_snapshot(demo_engine) == before
    replay = declare(demo_engine, now=NOW + timedelta(seconds=2))
    assert replay.status == "CONFIRMED" and replay.grants_authority is False
    assert database_snapshot(demo_engine) == before


def test_scenario_declaration_requires_actual_separate_confirmation_and_original_refs(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    original_epoch = epoch(demo_engine)
    previous = {"$ref": {"step_id": "declare", "pointer": "/result/proposal_id"}}
    reviewed = {"$ref": {"step_id": "declare", "pointer": "/result/configuration_hash"}}
    case = Scenario(
        scenario_id="DEVELOPMENT-original-declaration",
        purpose="DEVELOPMENT",
        dataset_id="DEVELOPMENT-not-formal-24",
        family_id="DECLARATION_RISKS",
        initial_state=InitialState(expected_epoch_id=original_epoch),
        steps=[
            ScenarioStep(
                step_id="declare",
                kind="DECLARE_POLICY",
                at=NOW,
                inputs={
                    "configuration": config(),
                    "idempotency_key": "scenario-native",
                    "expected_epoch_id": str(original_epoch),
                },
            ),
            ScenarioStep(step_id="before", kind="SNAPSHOT", at=NOW, inputs={}),
            ScenarioStep(
                step_id="refuse",
                kind="CONFIRM_POLICY",
                at=NOW + timedelta(seconds=1),
                inputs={"proposal_id": previous, "reviewed_hash": reviewed, "accepted": False},
                expected_error=ExpectedScenarioError(code="CONFIRMATION_REQUIRED", status_code=422),
            ),
            ScenarioStep(
                step_id="confirm",
                kind="CONFIRM_POLICY",
                at=NOW + timedelta(seconds=2),
                inputs={"proposal_id": previous, "reviewed_hash": reviewed, "accepted": True},
            ),
            ScenarioStep(
                step_id="after", kind="SNAPSHOT", at=NOW + timedelta(seconds=3), inputs={}
            ),
        ],
        expected_properties=["BANK_LEDGER_VALID", "AUDIT_VALID"],
    )
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(case)
    assert result.status == "EXECUTED" and len(result.steps) == 5, [
        {
            key: value
            for key, value in row.items()
            if key in {"step_id", "error", "expectation_failure"}
        }
        for row in result.steps
    ]
    declaration = result.steps[0]["result"]
    assert declaration["grants_authority"] is False and declaration["status"] == "PROPOSED"
    before = result.steps[1]["result"]["tables"]
    after = result.steps[4]["result"]["tables"]
    assert before["policies"] == before["policy_versions"] == []
    assert result.steps[2]["expected_error_matched"] is True
    assert len(after["policies"]) == len(after["policy_versions"]) == 1
    version = after["policy_versions"][0]
    assert version["configuration"] == validate_configuration(config())
    assert version["content_hash"] == declaration["configuration_hash"]
    assert version["confirmation"]["accepted"] is True
    assert result.steps[3]["resolved_inputs"]["proposal_id"] == declaration["proposal_id"]
    for table in (
        "accounts",
        "asset_positions",
        "bank_operations",
        "simulated_bank_postings",
        "action_plans",
        "action_receipts",
    ):
        assert before[table] == after[table]
