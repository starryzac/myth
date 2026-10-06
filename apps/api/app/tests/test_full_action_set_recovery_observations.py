"""Synthetic frozen mathematics and boundary risks; no real PG writes or bank result."""

from uuid import uuid5

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_composed import ComposedActionSetInput
from app.domain.full_action_set_boundary_recovery_composed import (
    ALGORITHM,
    RecoveryComposedActionSetInput,
    RecoveryComposedActionSetSnapshot,
    RecoveryComposedGlobalObservation,
    compare_recovery_composed_action_sets,
    derive_recovery_composed_action_set,
)
from app.domain.full_action_set_recovery_producers import RecoveryActionSetInput
from app.domain.policy_configuration import configuration_hash
from app.services.full_action_set_recovery_observations import (
    NAMESPACE,
    verify_frozen_recovery_composed_observation,
)
from app.tests.test_full_action_set_boundary_recovery_composed import fixture
from app.tests.test_full_action_set_payment_producers import fixture as periodic_fixture
from app.tests.test_full_action_set_recovery_producers import original_trace


def complete(*, auto: bool = True) -> RecoveryComposedActionSetSnapshot:
    periodic = periodic_fixture(auto=auto)
    recovery = RecoveryActionSetInput(
        original_actual_input=periodic.original_actual_input,
        expected_full_policy_ids=[],
        original_position_ids=[],
        original_action_ids=[],
        original_commands=[],
        producers=[],
    )
    return derive_recovery_composed_action_set(
        RecoveryComposedActionSetInput(
            composed=ComposedActionSetInput(periodic=periodic), recovery=recovery
        )
    )


def test_comparison_keeps_same_action_set_quiet_despite_changed_source_hash() -> None:
    before = complete()
    # A synthetic snapshot comparison, not an actual cash change or bank observation.
    after = before.model_copy(update={"financial_input_hash": "1" * 64, "snapshot_hash": "2" * 64})
    assert compare_recovery_composed_action_sets(None, before) == ("BoundaryObserved", None)
    kind, semantic = compare_recovery_composed_action_sets(before, after)
    assert kind == "BoundaryObserved" and semantic is not None


def test_recomputed_permission_change_crosses_but_unknown_family_never_does() -> None:
    before, after = complete(), complete(auto=False)
    assert before.global_action_set_complete and after.global_action_set_complete
    kind, semantic = compare_recovery_composed_action_sets(before, after)
    assert kind == "BoundaryCrossed" and semantic is not None
    unknown = derive_recovery_composed_action_set(fixture(delay=1))
    assert compare_recovery_composed_action_sets(before, unknown) == (None, None)
    assert compare_recovery_composed_action_sets(unknown, before) == (None, None)


@pytest.fixture(scope="module")
def frozen() -> DecisionTrace:
    data = fixture()
    snapshot = derive_recovery_composed_action_set(data)
    copies = original_trace(data.recovery)
    body = GlobalBoundaryObserveRequest(
        expected_epoch_id=snapshot.epoch_id,
        idempotency_key="synthetic-recovery-v4",
        previous_observation_run_id=None,
    )
    identity = uuid5(NAMESPACE, f"{snapshot.user_id}:{snapshot.epoch_id}:{body.idempotency_key}")
    kind, semantic = compare_recovery_composed_action_sets(None, snapshot)
    value = RecoveryComposedGlobalObservation(
        user_id=snapshot.user_id,
        epoch_id=snapshot.epoch_id,
        observation_run_id=identity,
        previous_observation_run_id=None,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        snapshot=snapshot,
        kind=kind,
        semantic_key=semantic,
        requires_user_attention=False,
        previous_snapshot_hash=None,
        previous_action_set_signature=None,
        global_action_set_complete=snapshot.global_action_set_complete,
    )
    return build_trace(
        run_id=identity,
        user_id=snapshot.user_id,
        phase="EVALUATION",
        as_of=snapshot.as_of,
        parent_run_id=None,
        algorithm_versions={"global_action_set": ALGORITHM},
        inputs={
            "original_request": body.model_dump(mode="json"),
            "recovery_composed_action_set_input": data.model_dump(mode="json"),
            "previous_snapshot": None,
        },
        sources=copies.sources,
        policies=copies.policies,
        outcome={
            "recovery_composed_global_observation": value.model_dump(mode="json"),
            "decision_status": "UNKNOWN",
        },
    )


def test_frozen_observation_recomputes_all_sources_and_keeps_original_unknown(
    frozen: DecisionTrace,
) -> None:
    value = verify_frozen_recovery_composed_observation(frozen)
    assert value.kind is None and not value.global_action_set_complete
    assert value.snapshot.recovery_family.recovery_family_complete
    assert value.notification_support == "NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"


@pytest.mark.parametrize(
    "change",
    [
        "missing-source",
        "result",
        "old-algorithm",
        "decision-status",
        "request-key",
        "fake-parent",
        "extra-input",
    ],
)
def test_rehashed_trace_cannot_drop_originals_or_forge_an_observation(
    frozen: DecisionTrace, change: str
) -> None:
    fields = frozen.model_dump(exclude={"input_hash", "outcome_hash", "trace_hash"})
    result = fields["outcome"]["recovery_composed_global_observation"]
    if change == "missing-source":
        fields["sources"] = []
    elif change == "result":
        result["snapshot"]["candidates"][0]["amount_cents"] = 10000
    elif change == "old-algorithm":
        fields["algorithm_versions"] = {
            "global_action_set": "full-policy-action-set-boundary-actual-v2"
        }
    elif change == "decision-status":
        fields["outcome"]["decision_status"] = "COMPUTED"
    elif change == "request-key":
        fields["inputs"]["original_request"]["idempotency_key"] = "another-key"
        result["original_request"]["idempotency_key"] = "another-key"
        result["request_hash"] = configuration_hash(result["original_request"])
    elif change == "fake-parent":
        fields["inputs"]["previous_snapshot"] = result["snapshot"]
        result["previous_snapshot_hash"] = result["snapshot"]["snapshot_hash"]
    else:
        fields["inputs"]["fake_success"] = True
    with pytest.raises(ValueError):
        verify_frozen_recovery_composed_observation(build_trace(**fields))
