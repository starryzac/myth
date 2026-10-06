"""Synthetic direct source-denominator risks only; no real grant or banking evidence."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from app.domain.boundary_types import BoundarySnapshot, CashFact, GoalOwnership, UnassignedGoalCash
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import TraceEvidence
from app.domain.full_action_set_release_producers import (
    ALGORITHM,
    ReleaseActionSetInput,
    ReleaseAuthorizationOriginal,
    ReleaseOriginalCommand,
    ReleaseProducerInput,
    _allocation_originals,
    _authorization,
    _one,
    _product_originals,
    _protection,
    derive_release_producers,
    release_action_ids,
    release_authorization_ids,
    release_policy_ids,
    release_producer_keys,
)
from app.domain.full_goal_reallocation import decide_cash_reallocation
from app.domain.full_goal_release_authorization import (
    GoalReleaseAuthorization,
    GoalReleaseBinding,
    GoalReleaseScope,
    ReleaseAuthorizationConfirmation,
    release_authorization_identity,
    release_authorization_request_hash,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_release_producers as service
from app.services.full_goal_release_authorization import ReleaseAuthorizationResponse
from app.services.full_goal_release_execution import (
    GoalReleasePrepareRequest,
    GoalReleaseProtectionCheck,
)
from app.services.full_goals import FullGoalModelContent, _request_hash
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.tests.test_full_action_set_boundary_actual import coverage, trace_for
from app.tests.test_full_action_set_boundary_actual import fixture as actual_fixture
from app.tests.test_full_dynamic_goal_execution import EPOCH, NOW, USER
from app.tests.test_full_goal_reallocation import configuration
from app.tests.test_full_goal_reallocation import facts as repair_fixture
from app.tests.test_full_goal_release_execution import basis
from app.tests.test_goal_release_provenance import fixture as provenance_fixture
from pydantic import ValidationError

POLICY, VERSION, PROOF = (UUID(int=value) for value in (7801, 7802, 7803))


def fixture(*, policy: bool = True) -> ReleaseActionSetInput:
    actual = actual_fixture()
    if not policy:
        return ReleaseActionSetInput(
            original_actual_input=actual,
            expected_full_policy_ids=[],
            original_authorization_source_ids=[],
            original_action_ids=[],
            authorizations=[],
            original_commands=[],
            producers=[],
        )
    raw = copy.deepcopy(actual.base.original_inventory)
    goal = UUID(raw["goals"][0]["id"])
    canonical = configuration(source_goal_ids=[str(goal)]).model_dump(mode="json")
    digest = configuration_hash(canonical)
    consent = {"accepted": True, "reviewed_hash": digest}
    version = FullVersionView(
        version_id=VERSION,
        policy_id=POLICY,
        version_number=1,
        configuration=canonical,
        content_hash=digest,
        previous_hash=None,
        summary="synthetic",
        confirmation=consent,
        confirmed_at=NOW,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        change_reason="synthetic source-shaped fixture",
        evidence_ids=[PROOF],
        impact_analysis={"reference_snapshots": []},
        confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
    )
    full = FullPolicyView(
        policy_id=POLICY,
        epoch_id=EPOCH,
        template_name="CrossGoalReallocationPolicy",
        name="synthetic",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=version,
        updated_at=NOW,
    )
    raw["full_policies"] = [
        {
            "id": str(POLICY),
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "template_name": full.template_name,
            "status": "ACTIVE",
        }
    ]
    raw["full_policy_versions"] = [
        {
            "id": str(VERSION),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "version_number": 1,
            "configuration": canonical,
            "content_hash": digest,
            "confirmation": consent,
            "previous_hash": None,
            "confirmed_at": NOW.isoformat(),
            "valid_from": NOW.isoformat(),
            "valid_until": version.valid_until.isoformat() if version.valid_until else None,
            "evidence_ids": [str(PROOF)],
            "impact_analysis": {"reference_snapshots": []},
        }
    ]
    raw["evidence_items"].append(
        {
            "id": str(PROOF),
            "user_id": str(USER),
            "status": "VALID",
            "source_type": "FULL_POLICY_CONFIRMATION",
            "source_ref": str(VERSION),
            "content": consent,
            "content_hash": configuration_hash(consent),
            "evidence_level": "USER_CONFIRMED_POLICY",
            "observed_at": NOW.isoformat(),
            "valid_from": NOW.isoformat(),
            "valid_to": None,
        }
    )
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(update={"original_inventory": raw}),
            "table_coverage": coverage(raw),
        }
    )
    producers = [
        ReleaseProducerInput(
            full_policy_id=pid, source_goal_id=gid, destination_account_id=aid, full_policy=full
        )
        for pid, gid, aid in release_producer_keys(actual)
    ]
    return ReleaseActionSetInput(
        original_actual_input=actual,
        expected_full_policy_ids=[POLICY],
        original_authorization_source_ids=[],
        original_action_ids=[],
        authorizations=[],
        original_commands=[],
        producers=producers,
    )


def refresh(
    data: ReleaseActionSetInput, raw: dict[str, list[dict[str, Any]]]
) -> ReleaseActionSetInput:
    original = data.original_actual_input
    return data.model_copy(
        update={
            "original_actual_input": original.model_copy(
                update={
                    "base": original.base.model_copy(update={"original_inventory": raw}),
                    "table_coverage": coverage(raw),
                }
            )
        }
    )


def test_true_empty_family_preserves_actual_original_and_no_authority() -> None:
    data = fixture(policy=False)
    before = data.model_dump_json()
    result = derive_release_producers(data)
    assert result.release_family_complete and result.expected_candidate_keys == []
    assert result.original_actual_input_hash == configuration_hash(
        data.original_actual_input.model_dump(mode="json")
    )
    assert result.result_hash == configuration_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
    assert result.bank_authority is result.grants_authority is result.financial_write is False
    assert result.full_global_adapter_installed is False
    assert data.model_dump_json() == before


def test_all_owned_cash_destinations_and_listed_goal_no_scope_are_known_excluded() -> None:
    data = fixture()
    result = derive_release_producers(data)
    assert result.status == "COMPLETE_REGISTERED_RELEASE_FAMILY", result.reasons
    assert len(result.results) == 1 and result.results[0].view.state == "EXCLUDED"
    assert result.results[0].view.reasons == ["COMPLETE_NO_CURRENT_DEDICATED_RELEASE_SCOPE"]
    assert result.results[0].view.amount_cents is None
    assert result.results[0].view.signature is None
    assert result.results[0].shadow_original_candidate_key is None
    assert result.results[0].requires_new_exact_user_confirmation is True
    assert result.handled_unsupported_codes == [
        "FULL_PRODUCER_ADAPTER_MISSING:CrossGoalReallocationPolicy:" + str(POLICY)
    ]


@pytest.mark.parametrize(
    "change",
    [
        "policy",
        "goal",
        "destination",
        "duplicate",
        "version",
        "confirmation",
        "evidence",
        "source",
        "capacity",
    ],
)
def test_incomplete_current_family_does_not_become_an_empty_complete_set(change: str) -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    if change == "policy":
        data = data.model_copy(update={"expected_full_policy_ids": []})
    elif change == "goal":
        raw["goals"] = []
    elif change == "destination":
        data = data.model_copy(update={"producers": []})
    elif change == "duplicate":
        data = data.model_copy(update={"producers": data.producers * 2})
    elif change == "version":
        raw["full_policy_versions"] = []
    elif change == "confirmation":
        raw["full_policy_versions"][0]["confirmation"]["accepted"] = False
    elif change == "evidence":
        raw["evidence_items"] = [row for row in raw["evidence_items"] if row["id"] != str(PROOF)]
    elif change == "source":
        data = data.model_copy(update={"source_reasons": ["ACTUAL_INVENTORY_TRUNCATED"]})
    else:
        raw["accounts"].extend(
            {
                "id": str(UUID(int=8000 + i)),
                "user_id": str(USER),
                "account_type": "CASH",
                "balance_cents": 0,
            }
            for i in range(65)
        )
    result = derive_release_producers(refresh(data, raw))
    assert not result.release_family_complete and result.status == "UNKNOWN"
    assert result.handled_unsupported_codes == []


@pytest.mark.parametrize("status", ["SUSPENDED", "REVOKED", "EXPIRED"])
def test_current_disabled_policy_has_no_bank_grant_or_amount(status: str) -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["full_policies"][0]["status"] = status
    full = data.producers[0].full_policy
    assert full is not None
    data = refresh(data, raw).model_copy(
        update={
            "producers": [
                data.producers[0].model_copy(
                    update={
                        "full_policy": full.model_copy(
                            update={"status": status, "effective_status": status}
                        )
                    }
                )
            ]
        }
    )
    result = derive_release_producers(data)
    assert result.release_family_complete, result.reasons
    assert (
        result.results[0].view.state == "EXCLUDED" and result.results[0].view.amount_cents is None
    )


@pytest.mark.parametrize(
    "state",
    ["PLANNED", "AUTHORIZED", "SUBMITTED", "UNKNOWN", "SUCCEEDED", "RECONCILED", "INVALIDATED"],
)
def test_missing_original_prepare_trace_keeps_each_historical_action_unresolved(state: str) -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    identity = UUID(int=7900)
    raw["action_plans"].append(
        {
            "id": str(identity),
            "user_id": str(USER),
            "action_type": "RELEASE_GOAL",
            "status": state,
            "request": {},
        }
    )
    data = refresh(data, raw).model_copy(
        update={
            "original_action_ids": [identity],
            "original_commands": [ReleaseOriginalCommand(action_id=identity)],
        }
    )
    result = derive_release_producers(data)
    assert not result.release_family_complete
    assert result.unresolved_original_action_ids == [identity]
    assert result.results[0].view.state == "UNKNOWN"
    assert "RELEASE_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED" in result.results[0].view.reasons


def test_orphan_bank_and_unregistered_authorization_original_are_not_filtered() -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["bank_operations"].append(
        {
            "id": str(UUID(int=7900)),
            "user_id": str(USER),
            "action_plan_id": str(UUID(int=7901)),
            "operation_type": "RELEASE_GOAL",
            "request": {},
        }
    )
    result = derive_release_producers(refresh(data, raw))
    assert not result.release_family_complete and result.handled_unsupported_codes == []
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["evidence_items"].append(
        {
            "id": str(UUID(int=7902)),
            "user_id": str(USER),
            "source_type": "FULL_GOAL_RELEASE_AUTHORIZATION",
            "content": {"status": "CURRENT"},
        }
    )
    assert release_authorization_ids(refresh(data, raw).original_actual_input) == [UUID(int=7902)]
    assert not derive_release_producers(refresh(data, raw)).release_family_complete


def test_all_historical_release_markers_and_sources_have_literal_denominators() -> None:
    data = fixture(policy=False)
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["action_plans"] = [
        {
            "id": str(UUID(int=1)),
            "user_id": str(USER),
            "action_type": "TRANSFER",
            "request": {"goal_release_execution": {}},
        }
    ]
    assert release_action_ids(refresh(data, raw).original_actual_input) == [UUID(int=1)]
    assert release_policy_ids(data.original_actual_input) == []


def test_assigned_amount_cannot_become_available_without_original_allocation() -> None:
    source = provenance_fixture()
    assert source.income is not None and source.income.fragments[0].assigned_cents == 500
    with pytest.raises(StopIteration):
        _allocation_originals(fixture(policy=False).original_actual_input, source.income, basis())


def test_original_product_missing_is_not_a_zero_principal_future_fact() -> None:
    data = fixture(policy=False)
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["asset_products"].append(
        {"id": str(UUID(int=7904)), "effective_from": NOW.isoformat(), "effective_until": None}
    )
    with pytest.raises(ValueError, match="PRODUCT_DENOMINATOR"):
        _product_originals(refresh(data, raw).original_actual_input, [])


@pytest.mark.parametrize(
    "field", ["amount_cents", "bank_authority", "now", "permission", "scope", "result"]
)
def test_typed_internal_input_forbids_unregistered_top_level_claims(field: str) -> None:
    with pytest.raises(ValidationError):
        ReleaseActionSetInput.model_validate_json(
            json.dumps(fixture().model_dump(mode="json") | {field: True})
        )


def test_frozen_algorithm_is_not_the_old_global_or_old_release_algorithm() -> None:
    with pytest.raises(ValueError, match="exact release"):
        service.verify_frozen_release_producers(
            trace_for(fixture(policy=False).original_actual_input)
        )
    assert ALGORITHM == "full-policy-release-action-producers-v1"


def test_missing_authorization_cannot_skip_original_source_and_trace_binding() -> None:
    data = fixture()
    identity = UUID(int=7905)
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["evidence_items"].append(
        {
            "id": str(identity),
            "user_id": str(USER),
            "source_type": "FULL_GOAL_RELEASE_AUTHORIZATION",
            "content": {},
        }
    )
    supplied = refresh(data, raw).model_copy(
        update={
            "original_authorization_source_ids": [identity],
            "authorizations": [ReleaseAuthorizationOriginal(source_id=identity)],
        }
    )
    result = derive_release_producers(supplied)
    assert not result.release_family_complete
    assert any(
        "AUTHORIZATION_ORIGINAL_TRACE_OR_CURRENT_SOURCE_MISSING" in reason
        for reason in result.reasons
    )


def authorization_fixture() -> tuple[ReleaseActionSetInput, ReleaseAuthorizationOriginal]:
    """Exact synthetic scope original, never a real actor or finance observation."""
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    goal = raw["goals"][0]
    model_source = next(
        row for row in raw["evidence_items"] if row["source_type"] == "FULL_GOAL_MODEL_V1"
    )
    model = FullGoalModelContent.model_validate_json(json.dumps(model_source["content"]))
    goal.update(
        policy_version_id=str(model.base_policy_version_id),
        account_id=str(UUID(int=6)),
        minimum_protection_cents=0,
    )
    model = model.model_copy(
        update={
            "request_hash": _request_hash(
                model.user_id,
                model.epoch_id,
                model.goal_id,
                model.expected_version_id,
                model.full_configuration,
                model.reason,
                model.idempotency_key,
            )
        }
    )
    model_source.update(
        content=model.model_dump(mode="json"),
        content_hash=configuration_hash(model.model_dump(mode="json")),
        created_at=model.confirmed_at.isoformat(),
        observed_at=model.confirmed_at.isoformat(),
        valid_from=model.confirmed_at.isoformat(),
        source_ref=goal["id"],
    )
    full = data.producers[0].full_policy
    assert full is not None and full.current_version.valid_until is not None
    config = full.current_version.configuration
    scope = GoalReleaseScope(
        user_id=USER,
        epoch_id=EPOCH,
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_configuration_hash=full.current_version.content_hash,
        source_goals=[
            GoalReleaseBinding(
                goal_id=UUID(goal["id"]),
                original_policy_id=model.policy_id,
                original_policy_version_id=model.base_policy_version_id,
                full_model_evidence_id=UUID(model_source["id"]),
                full_model_evidence_hash=model_source["content_hash"],
                full_configuration_hash=model.full_hash,
                minimum_guarantee_cents=0,
            )
        ],
        emergency_conditions=sorted(config["emergency_conditions"]),
        single_action_cap_cents=config["single_action_cap_cents"],
        total_cap_cents=config["total_cap_cents"],
        valid_from=NOW,
        valid_until=full.current_version.valid_until,
    )
    body = ReleaseAuthorizationConfirmation(
        expected_epoch_id=EPOCH,
        expected_policy_version_id=VERSION,
        reviewed_scope_hash=configuration_hash(scope.model_dump(mode="json")),
        accepted=True,
        idempotency_key="synthetic-source-only",
    )
    saved = GoalReleaseAuthorization(
        authorization_id=release_authorization_identity(USER, EPOCH, body.idempotency_key),
        user_id=USER,
        epoch_id=EPOCH,
        policy_id=POLICY,
        policy_version_id=VERSION,
        scope=scope,
        scope_hash=body.reviewed_scope_hash,
        accepted=True,
        idempotency_key=body.idempotency_key,
        original_request=body,
        request_hash=release_authorization_request_hash(USER, POLICY, body),
        confirmed_at=NOW,
        valid_until=scope.valid_until,
    )
    identity = uuid5(saved.authorization_id, "authorization-evidence")
    content = saved.model_dump(mode="json")
    digest = configuration_hash(content)
    raw["evidence_items"].append(
        {
            "id": str(identity),
            "user_id": str(USER),
            "source_type": "FULL_GOAL_RELEASE_AUTHORIZATION",
            "source_ref": str(saved.authorization_id),
            "status": "VALID",
            "evidence_level": "USER_CONFIRMED_POLICY",
            "content": content,
            "content_hash": digest,
            "created_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "valid_from": NOW.isoformat(),
            "valid_to": scope.valid_until.isoformat(),
        }
    )
    copied = TraceEvidence(
        id=identity,
        user_id=USER,
        evidence_level="USER_CONFIRMED_POLICY",
        source_type="FULL_GOAL_RELEASE_AUTHORIZATION",
        source_ref=str(saved.authorization_id),
        content=content,
        content_hash=digest,
        captured_content_hash=digest,
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=scope.valid_until,
    )
    trace = build_trace(
        run_id=saved.authorization_id,
        user_id=USER,
        as_of=NOW,
        phase="EVALUATION",
        algorithm_versions={"goal_release_authorization": saved.protocol},
        inputs={"original_request": body.model_dump(mode="json")},
        sources=[copied],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={"goal_release_authorization": content},
    )
    current = ReleaseAuthorizationResponse(
        original_authorization=saved,
        evidence_id=identity,
        evidence_hash=digest,
        original_trace_hash=trace.trace_hash,
        idempotent_replay=True,
        current_scope_status="CURRENT",
    )
    return refresh(data, raw), ReleaseAuthorizationOriginal(
        source_id=identity, original_trace=trace, current_original=current
    )


def test_scope_current_label_requires_the_complete_original_full_goal_binding() -> None:
    data, original = authorization_fixture()
    before = data.model_dump_json()
    saved = _authorization(data.original_actual_input, original)
    assert saved.scope.principal_release_allowed is False
    assert saved.scope.creates_new_income is False
    assert data.model_dump_json() == before


@pytest.mark.parametrize(
    "change",
    ["unknown", "label", "model", "modelhash", "modelrequest", "owner", "clock", "scopeversion"],
)
def test_scope_labels_cannot_replace_original_current_sources(change: str) -> None:
    data, original = authorization_fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    current = original.current_original
    assert current is not None
    model = next(row for row in raw["evidence_items"] if row["source_type"] == "FULL_GOAL_MODEL_V1")
    if change in {"unknown", "label"}:
        original = original.model_copy(
            update={
                "current_original": current.model_copy(
                    update={"current_scope_status": "UNKNOWN" if change == "unknown" else "STALE"}
                )
            }
        )
    elif change == "model":
        raw["evidence_items"].remove(model)
    elif change == "modelhash":
        model["content_hash"] = "0" * 64
    elif change == "modelrequest":
        model["content"]["request_hash"] = "0" * 64
        model["content_hash"] = configuration_hash(model["content"])
    elif change == "owner":
        model["content"]["user_id"] = str(UUID(int=9001))
    elif change == "clock":
        model["observed_at"] = (NOW + timedelta(days=1)).isoformat()
    else:
        raw["full_policy_versions"][0]["content_hash"] = "0" * 64
    with pytest.raises((ValueError, StopIteration)):
        _authorization(refresh(data, raw).original_actual_input, original)


def protection_fixture() -> tuple[ReleaseActionSetInput, ReleaseProducerInput]:
    """A private math-helper unit fixture. No family, authorization or bank is asserted."""
    data = fixture(policy=False)
    goal, source, target = UUID(int=2), UUID(int=6), UUID(int=5)
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        horizon_days=365,
        source_digest="e" * 64,
        cash_accounts=[
            CashFact(account_id=source, account_type="GOAL", balance_cents=500, observed_at=NOW),
            CashFact(account_id=target, account_type="CASH", balance_cents=500, observed_at=NOW),
        ],
        goals=[
            GoalOwnership(
                goal_id=goal,
                policy_id=UUID(int=3),
                account_id=source,
                cash_owned_cents=300,
                principal_owned_cents=0,
                allocated_cents=300,
            )
        ],
        unassigned_goal_cash=[UnassignedGoalCash(account_id=source, amount_cents=200)],
    )
    inputs = FullProtectionProjectionInput(
        snapshot=snapshot, boundary_versions=[], positions=[], boundary_products=[], policies=[]
    )
    before = project_full_protection(inputs)
    after_snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 400}),
                snapshot.cash_accounts[1].model_copy(update={"balance_cents": 600}),
            ],
            "goals": [
                snapshot.goals[0].model_copy(
                    update={"cash_owned_cents": 200, "allocated_cents": 200}
                )
            ],
        }
    )
    after = project_full_protection(inputs.model_copy(update={"snapshot": after_snapshot}))
    assert before.full_annual_projection is not None and after.full_annual_projection is not None
    # The remaining 200 unassigned GOAL cents remain protected after the release.
    assert before.full_annual_projection.calculation_trace[0].margin_cents == 500
    assert after.full_annual_projection.calculation_trace[0].margin_cents == 600
    digest = configuration_hash(
        {"original_financial_digest": snapshot.source_digest, "full_input_hash": before.input_hash}
    )
    check = GoalReleaseProtectionCheck(
        status="VERIFIED_NONWORSENING",
        actual_before_input_hash=before.input_hash,
        hypothetical_after_input_hash=after.input_hash,
        compared_point_count=1098,
        reasons=[],
        source_hash=configuration_hash(
            {
                "full_input": digest,
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
                "amount": 100,
                "destination": str(target),
            }
        ),
    )
    # Only the helper's narrow inputs are supplied; this object is never passed to
    # derive_release_producers or claimed as a validated GoalReleaseCandidate.
    item = cast(
        ReleaseProducerInput,
        SimpleNamespace(
            source_goal_id=goal,
            destination_account_id=target,
            protection_inputs=inputs,
            full_input_digest=digest,
            actual_candidate=SimpleNamespace(
                amount_cents=100,
                protection=check,
                actual_preview=SimpleNamespace(full_protection_input_hash=digest),
            ),
        ),
    )
    financial = {
        "user_id": str(USER),
        "as_of": NOW.isoformat(),
        "snapshot": snapshot.model_dump(mode="json"),
        "versions": [],
        "positions": [],
        "products": [],
    }
    base = data.original_actual_input.base.model_copy(
        update={"financial_basis": financial, "financial_input_hash": configuration_hash(financial)}
    )
    return data.model_copy(
        update={
            "original_actual_input": data.original_actual_input.model_copy(update={"base": base})
        }
    ), item


def test_service_blocked_label_with_missing_financial_originals_is_not_an_empty_set() -> None:
    data, authorization = authorization_fixture()
    item = data.producers[0]
    full, current = item.full_policy, authorization.current_original
    assert full is not None and current is not None
    scope = current.original_authorization
    repair = repair_fixture().model_copy(update={"user_id": USER, "epoch_id": EPOCH, "as_of": NOW})
    body = GoalReleasePrepareRequest(
        policy_id=item.full_policy_id,
        source_goal_id=item.source_goal_id,
        expected_policy_version_id=full.current_version.version_id,
        expected_goal_policy_version_id=scope.scope.source_goals[0].original_policy_version_id,
        expected_epoch_id=EPOCH,
        authorization_epoch_id=EPOCH,
        authorization_idempotency_key=scope.idempotency_key,
        destination_account_id=item.destination_account_id,
        idempotency_key="synthetic-refusal-key",
    )
    # Narrow private-helper negative. This is intentionally not a validated
    # financial candidate and never enters the public typed derivation.
    preview = SimpleNamespace(
        policy=full, decision=decide_cash_reallocation(repair), source_binding_hash="a" * 64
    )
    inventory = SimpleNamespace(source_binding_hash="b" * 64)
    candidate = SimpleNamespace(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        original_request=body,
        actual_preview=preview,
        actual_inventory=inventory,
        original_authorization=current,
        protection=None,
        state="BLOCKED",
        reasons=[
            "NO_CURRENT_EMERGENCY",
            "CURRENT_COMPLETE_SOURCE_BANK_AND_APPLICATION_NOT_MATCHED",
        ],
    )
    candidate.input_hash = configuration_hash(
        {
            "request": body.model_dump(mode="json"),
            "preview": preview.source_binding_hash,
            "inventory": inventory.source_binding_hash,
            "authorization": current.model_dump(mode="json"),
            "protection": None,
            "reasons": candidate.reasons,
            "as_of": NOW.isoformat(),
        }
    )
    supplied = cast(
        ReleaseProducerInput,
        SimpleNamespace(
            **item.model_dump(
                exclude={"actual_candidate", "repair_inputs", "authorization_source_id"}
            ),
            actual_candidate=candidate,
            repair_inputs=repair,
            authorization_source_id=authorization.source_id,
        ),
    )
    supplied.full_policy = full
    result = _one(
        data.original_actual_input, supplied, {authorization.source_id: authorization}, []
    )
    assert result.view.state == "UNKNOWN" and result.view.amount_cents is None
    assert result.view.reasons[0].startswith("RELEASE_SERVICE_REFUSAL_NOT_INDEPENDENTLY_REPLAYED")


def test_hand_worked_release_preserves_cash_and_1098_protection_points_without_grant() -> None:
    data, item = protection_fixture()
    _protection(data.original_actual_input, item)
    assert item.protection_inputs is not None
    assert sum(row.balance_cents for row in item.protection_inputs.snapshot.cash_accounts) == 1000


@pytest.mark.parametrize("change", ["cash", "clock", "hash", "denominator", "amount"])
def test_private_protection_check_rejects_changed_real_basis_or_fake_verified_label(
    change: str,
) -> None:
    data, item = protection_fixture()
    assert item.protection_inputs is not None and item.actual_candidate is not None
    if change in {"cash", "clock"}:
        snapshot = item.protection_inputs.snapshot
        snapshot = snapshot.model_copy(
            update={"as_of": NOW + timedelta(seconds=1)}
            if change == "clock"
            else {
                "cash_accounts": [
                    snapshot.cash_accounts[0].model_copy(update={"balance_cents": 600}),
                    snapshot.cash_accounts[1],
                ]
            }
        )
        item.protection_inputs = item.protection_inputs.model_copy(update={"snapshot": snapshot})
    elif change == "hash":
        item.full_input_digest = "0" * 64
    elif change == "amount":
        item.actual_candidate.amount_cents = 301
    else:
        assert item.actual_candidate.protection is not None
        item.actual_candidate.protection = item.actual_candidate.protection.model_copy(
            update={"compared_point_count": 1097}
        )
    with pytest.raises(ValueError):
        _protection(data.original_actual_input, item)
