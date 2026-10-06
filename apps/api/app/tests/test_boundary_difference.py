"""Synthetic deterministic/source risks, not a financial experiment or actual PG."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.boundary_difference import VerifiedBoundaryInput, compare_verified_boundaries
from app.domain.boundary_types import BoundaryPolicyVersion, SourceIssue
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import TraceEvidence, TracePolicy
from app.domain.policy_configuration import configuration_hash
from app.services import boundary_difference as service
from app.services.boundary_difference import _verified_input
from app.services.decision_trace import DecisionTraceResponse
from app.tests.test_boundary import NOW, policy, snapshot
from sqlalchemy.orm import Session

USER = UUID(int=44)


def actual_input(cash: int = 1000000, run: int = 1) -> VerifiedBoundaryInput:
    return VerifiedBoundaryInput(
        run_id=UUID(int=run),
        user_id=USER,
        trace_hash="a" * 64,
        input_path="inputs.planning.execution_boundary",
        snapshot=snapshot(cash),
        versions=(),
        positions=(),
        products=(),
        source_refs=(),
    )


def captured_record(
    *,
    cash: int = 1000000,
    versions: list[BoundaryPolicyVersion] | None = None,
    mutate: dict[str, Any] | None = None,
) -> DecisionTraceResponse:
    """Freeze a synthetic original, including real typed result and source hashes."""
    state = snapshot(cash)
    rows = []
    sources = []
    for index, row in enumerate(state.cash_accounts):
        identity = UUID(int=100 + index)
        rows.append(row.model_copy(update={"evidence_ids": [identity]}))
        content = {
            "simulation": True,
            "user_id": str(USER),
            "account_id": str(row.account_id),
            "account_type": row.account_type,
            "balance_cents": row.balance_cents,
            "currency": "CNY",
            "as_of": row.observed_at.isoformat(),
        }
        digest = configuration_hash(content)
        sources.append(
            TraceEvidence(
                id=identity,
                user_id=USER,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_BALANCE",
                source_ref=str(row.account_id),
                content=content,
                content_hash=digest,
                captured_content_hash=digest,
                content_integrity="VERIFIED",
                status_at_decision="VALID",
                observed_at=NOW,
                valid_from=NOW,
            )
        )
    state = state.model_copy(update={"cash_accounts": rows})
    versions = versions or []
    policy_rows = []
    for version in versions:
        identity = version.evidence_ids[0]
        content = {
            "user_id": str(USER),
            "policy_id": str(version.policy_id),
            "version_id": str(version.version_id),
            "reviewed_hash": version.content_hash,
            "accepted": True,
            "confirmed_at": version.confirmed_at.isoformat(),
        }
        digest = configuration_hash(content)
        sources.append(
            TraceEvidence(
                id=identity,
                user_id=USER,
                evidence_level="USER_CONFIRMED_POLICY",
                source_type="POLICY_CONFIRMATION",
                source_ref=str(version.version_id),
                content=content,
                content_hash=digest,
                captured_content_hash=digest,
                content_integrity="VERIFIED",
                status_at_decision="VALID",
                observed_at=NOW,
                valid_from=NOW,
            )
        )
        policy_rows.append(
            TracePolicy(
                id=version.version_id,
                user_id=USER,
                policy_id=version.policy_id,
                version_number=1,
                configuration=version.configuration,
                configuration_hash=version.content_hash,
                captured_configuration_hash=version.content_hash,
                configuration_integrity="VERIFIED",
                status_at_decision="ACTIVE",
                confirmed_at=version.confirmed_at,
                valid_from=version.valid_from,
                valid_to=version.valid_until,
            )
        )
    raw = {
        "snapshot": state.model_dump(mode="json"),
        "versions": [row.model_dump(mode="json") for row in versions],
        "positions": [],
        "products": [],
        "source_issues": [],
    }
    if mutate is not None:
        raw.update(mutate)
    result = compute_boundary(state, versions, [], [])
    trace = build_trace(
        run_id=UUID(int=1),
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        algorithm_versions={"boundary": result.algorithm_version},
        inputs={"planning": {"execution_boundary": raw}},
        sources=sources,
        policies=policy_rows,
        outcome={"validation": {"baseline_boundary": result.model_dump(mode="json")}},
    )
    return DecisionTraceResponse(
        user_id=USER,
        run_id=trace.run_id,
        as_of=NOW,
        read_at=NOW,
        completeness="COMPLETE",
        trace=trace,
        current_references=[],
        actions=[],
        children=[],
        audit_chain_status="VALID",
    )


def test_same_world_has_real_zero_delta_but_never_an_execution_grant() -> None:
    first = actual_input()
    result = compare_verified_boundaries(first, replace(first, run_id=UUID(int=2)))
    assert result.status == "RECOMPUTED" and result.delta_cents == 0
    assert result.before_safe_idle_cents == result.after_safe_idle_cents == 1000000
    assert result.attribution == "UNCHANGED" and result.changes == []
    assert result.authority_granted is False and result.financial_only
    assert result.autonomy_action_set_difference == "NOT_EVALUATED"


def test_cash_change_recomputes_hand_worked_cents_and_original_paths() -> None:
    first, last = actual_input(), actual_input(1012345, 2)
    result = compare_verified_boundaries(first, last)
    assert result.delta_cents == 12345
    assert result.attribution == "SINGLE_CHANGED_COMPONENT"
    change = result.changes[0]
    assert change.component == "cash_accounts"
    assert change.before_paths == ["inputs.planning.execution_boundary.snapshot.cash_accounts"]
    assert change.before_value == first.snapshot.model_dump(mode="json")["cash_accounts"]
    assert change.before_value_hash == configuration_hash({"value": change.before_value})


def test_confirmed_protection_change_recomputes_without_expanding_permission() -> None:
    first = actual_input()
    version = policy(20, {"type": "emergency_buffer", "amount_cents": 54321})
    result = compare_verified_boundaries(
        first, replace(first, run_id=UUID(int=2), versions=(version,))
    )
    assert result.delta_cents == -54321 and result.after_safe_idle_cents == 945679
    assert [row.component for row in result.changes] == ["versions"]
    assert result.changes[0].before_paths == ["inputs.planning.execution_boundary.versions"]


def test_joint_changes_keep_total_delta_and_do_not_invent_marginal_contributions() -> None:
    first = actual_input()
    last = replace(
        actual_input(1010000, 2),
        versions=(policy(20, {"type": "emergency_buffer", "amount_cents": 5000}),),
    )
    result = compare_verified_boundaries(first, last)
    assert result.delta_cents == 5000
    assert result.attribution == "JOINT_CHANGES_NOT_INDIVIDUALLY_ATTRIBUTED"
    assert {row.component for row in result.changes} == {"cash_accounts", "versions"}
    assert "未将总差额" in result.explanation
    assert all("contribution_cents" not in row.model_dump() for row in result.changes)


def test_enumeration_order_and_provenance_digest_alone_are_not_new_money_facts() -> None:
    first = actual_input()
    state = first.snapshot.model_copy(
        update={
            "cash_accounts": list(reversed(first.snapshot.cash_accounts)),
            "source_digest": "changed",
        }
    )
    result = compare_verified_boundaries(first, replace(first, run_id=UUID(int=2), snapshot=state))
    assert result.attribution == "UNCHANGED" and result.delta_cents == 0


def test_unknown_amount_is_null_and_clock_paths_are_real_fields() -> None:
    first = actual_input()
    missing = first.snapshot.model_copy(
        update={"source_issues": [SourceIssue(code="MISSING_PROOF", entity_type="cash")]}
    )
    result = compare_verified_boundaries(
        first, replace(first, run_id=UUID(int=2), snapshot=missing)
    )
    assert result.status == "UNKNOWN" and result.delta_cents is None
    assert result.before_safe_idle_cents is result.after_safe_idle_cents is None
    later = first.snapshot.model_copy(update={"as_of": NOW + timedelta(days=1)})
    changed = compare_verified_boundaries(first, replace(first, run_id=UUID(int=3), snapshot=later))
    assert changed.changes[0].component == "clock"
    assert changed.changes[0].after_paths == ["inputs.planning.execution_boundary.snapshot.as_of"]


@pytest.mark.parametrize("wrong", ["owner", "time"])
def test_other_owner_or_reversed_actual_time_refused(wrong: str) -> None:
    first = actual_input()
    last = (
        replace(first, user_id=UUID(int=99))
        if wrong == "owner"
        else replace(
            first, snapshot=first.snapshot.model_copy(update={"as_of": NOW - timedelta(seconds=1)})
        )
    )
    with pytest.raises(ValueError):
        compare_verified_boundaries(first, last)


def test_original_cash_and_policy_sources_are_replayed_and_retained() -> None:
    version = policy(
        20, {"type": "emergency_buffer", "amount_cents": 5000}, evidence_ids=[UUID(int=200)]
    )
    record = captured_record(versions=[version])
    verified = _verified_input(record, "execution_boundary")
    assert record.trace is not None
    assert len(verified.source_refs) == 3 and verified.trace_hash == record.trace.trace_hash
    result = compare_verified_boundaries(verified, replace(verified, run_id=UUID(int=2)))
    assert result.status == "RECOMPUTED" and result.before_safe_idle_cents == 995000


@pytest.mark.parametrize("change", ["cash", "no_ids", "extra", "source_issue"])
def test_complete_valid_trace_label_does_not_make_wrong_original_fact_true(change: str) -> None:
    record = captured_record()
    assert record.trace is not None
    raw = record.trace.inputs["planning"]["execution_boundary"]
    if change == "cash":
        raw["snapshot"]["cash_accounts"][0]["balance_cents"] += 1
    elif change == "no_ids":
        raw["snapshot"]["cash_accounts"][0]["evidence_ids"] = []
    elif change == "extra":
        raw["grant"] = True
    else:
        raw["source_issues"] = [{"code": "MISSING", "entity_type": "cash"}]
    # Freshly hash the false claim so failure is semantic, not only a stale digest.
    original = record.trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    original["inputs"] = {"planning": {"execution_boundary": raw}}
    record = record.model_copy(update={"trace": build_trace(**original)})
    with pytest.raises(ValueError):
        _verified_input(record, "execution_boundary")


@pytest.mark.parametrize(
    "change", ["recorded_result", "audit", "partial", "missing_field", "nested_mutation"]
)
def test_missing_or_tampered_original_never_replays_from_success_strings(change: str) -> None:
    record = captured_record()
    assert record.trace is not None
    trace = record.trace
    if change == "audit":
        record = record.model_copy(update={"audit_chain_status": "LEGACY_UNAUDITED"})
    elif change == "partial":
        record = record.model_copy(update={"completeness": "LEGACY_PARTIAL"})
    elif change == "nested_mutation":
        trace.inputs["planning"]["execution_boundary"]["snapshot"]["cash_accounts"][0][
            "balance_cents"
        ] += 1
    else:
        original = trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
        if change == "recorded_result":
            original["outcome"]["validation"]["baseline_boundary"]["safe_idle_cents"] += 1
        else:
            original["inputs"]["planning"] = {}
        record = record.model_copy(update={"trace": build_trace(**original)})
    with pytest.raises(ValueError):
        _verified_input(record, "execution_boundary")


@pytest.mark.parametrize("change", ["level", "type", "status", "expired", "future", "bool_money"])
def test_source_hash_integrity_alone_is_not_original_bank_fact_validity(change: str) -> None:
    record = captured_record()
    assert record.trace is not None
    fields = record.trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    source = fields["sources"][0]
    if change == "level":
        source["evidence_level"] = "USER_DECLARED"
    elif change == "type":
        source["source_type"] = "UNRELATED_SUCCESS"
    elif change == "status":
        source["status_at_decision"] = "CONFLICTED"
    elif change == "expired":
        source["valid_to"] = NOW
    elif change == "future":
        source["observed_at"] = NOW + timedelta(seconds=1)
    else:
        source["content"]["balance_cents"] = True
        source["content_hash"] = source["captured_content_hash"] = configuration_hash(
            source["content"]
        )
    altered = record.model_copy(update={"trace": build_trace(**fields)})
    with pytest.raises(ValueError):
        _verified_input(altered, "execution_boundary")


@pytest.mark.parametrize("change", ["accepted", "version", "hash", "time"])
def test_exact_original_policy_confirmation_is_required(change: str) -> None:
    version = policy(
        20, {"type": "emergency_buffer", "amount_cents": 5000}, evidence_ids=[UUID(int=200)]
    )
    record = captured_record(versions=[version])
    assert record.trace is not None
    fields = record.trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    source = fields["sources"][-1]
    content = source["content"]
    if change == "accepted":
        content["accepted"] = False
    elif change == "version":
        content["version_id"] = str(UUID(int=999))
    elif change == "hash":
        content["reviewed_hash"] = "f" * 64
    else:
        content["confirmed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    source["content_hash"] = source["captured_content_hash"] = configuration_hash(content)
    altered = record.model_copy(update={"trace": build_trace(**fields)})
    with pytest.raises(ValueError, match="ORIGINAL_POLICY_CONFIRMATION_MISSING"):
        _verified_input(altered, "execution_boundary")


def test_same_run_reuses_readonly_copy_in_one_request_but_reads_again_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = captured_record()
    calls: list[UUID] = []

    def read(_session: Session, _user: UUID, run_id: UUID, _now: Any) -> DecisionTraceResponse:
        calls.append(run_id)
        return record

    monkeypatch.setattr(service, "get_decision_trace", read)
    monkeypatch.setattr(service, "_snapshot", lambda _: None)
    body = service.BoundaryDifferenceRequest(
        before_run_id=record.run_id, after_run_id=record.run_id
    )
    with Session() as session:
        first = service.compare_boundary_runs(session, USER, body, NOW)
        last = service.compare_boundary_runs(session, USER, body, NOW)
    assert first == last and first.delta_cents == 0
    assert calls == [record.run_id, record.run_id]


def test_missing_original_capture_returns_unknown_null_not_a_zero_difference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = captured_record().model_copy(update={"completeness": "LEGACY_PARTIAL", "trace": None})
    monkeypatch.setattr(service, "get_decision_trace", lambda *_: record)
    monkeypatch.setattr(service, "_snapshot", lambda _: None)
    with Session() as session:
        result = service.compare_boundary_runs(
            session,
            USER,
            service.BoundaryDifferenceRequest(
                before_run_id=record.run_id, after_run_id=record.run_id
            ),
            NOW,
        )
    assert result.status == "UNKNOWN" and result.delta_cents is None
    assert result.reasons == ["ACTUAL_TRACE_OR_AUDIT_NOT_VERIFIED"]
