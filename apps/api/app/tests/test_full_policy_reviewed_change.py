"""Synthetic direct protocol/math risks; never runtime bank or human-study evidence."""

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_policy_change_multi import derive_multi_template_impact
from app.domain.full_policy_reviewed_change import (
    ALGORITHM,
    MARKER,
    ConfirmReviewedChangeRequest,
    ReviewedChangeRecord,
    ReviewRequest,
    SourceBasis,
    coverage,
    impact_value,
    legacy_request,
    outer_request,
    require_current_review,
    review_digest,
    review_original_text,
    verify_frozen_reviewed_policy_change_trace,
    verify_record,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_change_multi import MultiTemplatePreviewResponse
from app.services.full_policy_reviewed_change import _readback, _verify_lifecycle_original, identity
from app.tests.test_full_policy_change_multi import data


@pytest.fixture(scope="module")
def original() -> ReviewedChangeRecord:
    value = data()
    impact = derive_multi_template_impact(value)
    now = value.snapshot.as_of
    request = ReviewRequest(
        expected_version_id=value.version_id,
        expected_epoch_id=value.epoch_id,
        configuration=value.candidate_configuration,
        idempotency_key="review-1",
    )
    preview = MultiTemplatePreviewResponse(
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        source_kind=value.source_kind,
        policy_id=value.policy_id,
        expected_version_id=value.version_id,
        template_name=value.template_name,
        as_of=now,
        current_configuration_hash=configuration_hash(value.current_configuration),
        candidate_configuration_hash=configuration_hash(value.candidate_configuration),
        before_configuration=value.current_configuration,
        after_configuration=value.candidate_configuration,
        changed_fields=["amount_cents"],
        current_fact_digest="a" * 64,
        source_counts={"actions": 0},
        source_evidence_ids=[],
        source_originals={"purpose": "SYNTHETIC_DIRECT_ONLY"},
        financial_impact=impact,
        original_action_ids=[],
        original_position_ids=[],
    )
    basis = SourceBasis(
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        local_day=value.snapshot.as_of.astimezone(value.snapshot.as_of.tzinfo).date().isoformat(),
        registered_tables=["accounts", "users"],
        excluded_metadata_tables=[],
        tables={
            "users": [{"id": str(value.user_id), "timezone": "Asia/Shanghai"}],
            "accounts": [
                {"id": str(UUID(int=1)), "user_id": str(value.user_id), "balance_cents": 10000}
            ],
        },
        row_counts={"users": 1, "accounts": 1},
    )
    covered, missing, eligible = coverage(preview)
    record = ReviewedChangeRecord(
        review_id=UUID(int=9011),
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        source_kind=value.source_kind,
        policy_id=value.policy_id,
        request=request,
        captured_at=now,
        expires_at=now + timedelta(minutes=10),
        actor=LocalActorPrincipal(
            user_id=value.user_id,
            role="USER",
            session_id=UUID(int=9022),
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        ),
        source_basis=basis,
        source_basis_hash=configuration_hash(basis.model_dump(mode="json")),
        preview=preview,
        covered_scopes=covered,
        uncovered_items=missing,
        confirmation_eligible=eligible,
        review_hash="0" * 64,
    )
    return record.model_copy(update={"review_hash": review_digest(record)})


def confirm_body(record: ReviewedChangeRecord) -> ConfirmReviewedChangeRequest:
    return ConfirmReviewedChangeRequest(
        **record.request.model_dump(exclude={"idempotency_key"}),
        idempotency_key="commit-1",
        review_id=record.review_id,
        accepted=True,
        reviewed_configuration_hash=record.preview.candidate_configuration_hash,
        reviewed_review_hash=record.review_hash,
        reviewed_scope=record.covered_scopes[0],
        reason="明确复核此范围",
    )


def trace(record: ReviewedChangeRecord, confirm: bool = False, **changes: Any) -> DecisionTrace:
    body = confirm_body(record)
    if confirm:
        legacy = legacy_request(
            record.user_id,
            record.source_kind,
            record.policy_id,
            body,
            record.preview.after_configuration,
        )
        outer = outer_request(record.user_id, record.source_kind, record.policy_id, body)
        receipt = {
            "policy_id": str(record.policy_id),
            "previous_version_id": str(body.expected_version_id),
            "current_version_id": str(UUID(int=9055)),
        }
        inputs = {
            "review_phase": "CONFIRM",
            "user_id": str(record.user_id),
            "request": body.model_dump(mode="json"),
            "actor": record.actor.model_dump(mode="json"),
            "outer_request": outer,
            "outer_request_hash": configuration_hash(outer),
            "legacy_request": legacy,
            "legacy_request_hash": configuration_hash(legacy),
            "lifecycle_receipt": receipt,
        }
        outcome = {
            "lifecycle_receipt": receipt,
            "bank_authority": False,
            "financial_readback_status": "NOT_READ_AFTER_COMMIT",
        }
    else:
        inputs = {
            "review_phase": "REVIEW",
            "review_record_json": review_original_text(record),
            "review_record_hash": configuration_hash(record.model_dump(mode="json")),
        }
        outcome = {
            "review_hash": record.review_hash,
            "confirmation_eligible": True,
            "bank_authority": False,
        }
    kwargs: dict[str, Any] = dict(
        run_id=UUID(int=9066) if confirm else record.review_id,
        user_id=record.user_id,
        phase="EVALUATION",
        as_of=record.captured_at,
        parent_run_id=record.review_id if confirm else None,
        algorithm_versions={MARKER: ALGORITHM},
        inputs=inputs,
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome=outcome,
    )
    kwargs.update(changes)
    return build_trace(**kwargs)


def test_exact_1098_integer_delta_and_fresh_query_clock_no_financial_write(
    original: ReviewedChangeRecord,
) -> None:
    verify_record(original)
    now = original.captured_at + timedelta(seconds=2)
    fresh = original.preview.model_copy(update={"as_of": now, "current_fact_digest": "b" * 64})
    require_current_review(original, confirm_body(original), original.source_basis, fresh, now)
    impact = original.preview.financial_impact
    assert impact.before is not None and impact.after is not None
    assert impact.before.safe_idle_cents == 8000 and impact.after.safe_idle_cents == 7000
    assert len(impact.after.calculation_trace) == 1098 and impact.delta_safe_idle_cents == -1000
    assert not original.bank_authority and not original.independent_financial_verification
    assert "FUTURE_ACTION_DIFFERENCE_NOT_PROVEN" in original.uncovered_items


@pytest.mark.parametrize(
    "change",
    ["owner", "epoch", "day", "raw_balance", "count", "omitted_table", "duplicate", "extra_grant"],
)
def test_complete_raw_basis_and_registered_denominators_fail_closed(
    original: ReviewedChangeRecord, change: str
) -> None:
    raw = original.source_basis.model_dump()
    if change == "owner":
        raw["tables"]["accounts"][0]["user_id"] = str(UUID(int=999))
    elif change == "epoch":
        raw["epoch_id"] = UUID(int=999)
    elif change == "day":
        raw["local_day"] = "2026-02-30"
    elif change == "raw_balance":
        raw["tables"]["accounts"][0]["balance_cents"] += 1
    elif change == "count":
        raw["row_counts"]["accounts"] = 0
    elif change == "omitted_table":
        del raw["tables"]["accounts"]
    elif change == "duplicate":
        raw["tables"]["accounts"] *= 2
    else:
        raw["authority"] = True
    with pytest.raises(ValueError):
        basis = SourceBasis.model_validate(raw)
        require_current_review(
            original, confirm_body(original), basis, original.preview, original.captured_at
        )


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "owner",
        "version",
        "kind",
        "matrix_phase",
        "matrix_missing",
        "matrix_money",
        "request_config",
        "hash",
    ],
)
def test_same_actual_identity_complete_matrix_and_canonical_candidate_required(
    original: ReviewedChangeRecord, change: str
) -> None:
    record = original.model_copy(deep=True)
    fresh = original.preview.model_copy(deep=True)
    now = original.captured_at
    if change == "expiry":
        now = original.expires_at
    elif change == "owner":
        fresh = fresh.model_copy(update={"user_id": UUID(int=999)})
    elif change == "version":
        fresh = fresh.model_copy(update={"expected_version_id": UUID(int=999)})
    elif change == "kind":
        fresh = fresh.model_copy(update={"source_kind": "FULL_POLICY"})
    elif change.startswith("matrix"):
        assert fresh.financial_impact.after is not None
        points = fresh.financial_impact.after.calculation_trace
        if change == "matrix_missing":
            points.pop()
        elif change == "matrix_phase":
            points[1] = points[1].model_copy(update={"phase": "BEFORE_PAYMENT"})
        else:
            points[1] = points[1].model_copy(update={"margin_cents": points[1].margin_cents + 1})
    elif change == "request_config":
        record = record.model_copy(
            update={
                "request": record.request.model_copy(
                    update={"configuration": {"type": "emergency_buffer", "amount_cents": 1}}
                )
            }
        )
        record = record.model_copy(update={"review_hash": review_digest(record)})
    else:
        record = record.model_copy(update={"review_hash": "f" * 64})
    with pytest.raises(ValueError):
        require_current_review(record, confirm_body(original), original.source_basis, fresh, now)


def test_unknown_and_partial_are_not_promoted_to_all_financial_verification(
    original: ReviewedChangeRecord,
) -> None:
    impact = original.preview.financial_impact.model_copy(
        update={"status": "UNKNOWN", "after": None}
    )
    unknown = original.preview.model_copy(update={"financial_impact": impact})
    assert coverage(unknown)[0] == [] and coverage(unknown)[2] is False
    partial = original.preview.financial_impact.model_copy(
        update={
            "status": "PARTIAL",
            "scope": "INDIVIDUAL_PRODUCT_CAPACITY",
            "reasons": ["WHOLE_PORTFOLIO_UNSUPPORTED"],
        }
    )
    candidate = original.preview.model_copy(
        update={"financial_impact": partial, "source_counts": {"asset_catalogue": 0}}
    )
    covered, uncovered, eligible = coverage(candidate)
    assert (
        eligible
        and covered == ["INDIVIDUAL_PRODUCT_CAPACITY"]
        and "WHOLE_PORTFOLIO_UNSUPPORTED" in uncovered
    )
    # An omitted catalogue member must block this partial scope too.
    assert (
        coverage(candidate.model_copy(update={"source_counts": {"asset_catalogue": 1}}))[2] is False
    )


@pytest.mark.parametrize("phase", [False, True])
def test_new_exact_algorithm_original_envelope_frozen_metadata_only(
    original: ReviewedChangeRecord, phase: bool
) -> None:
    value = trace(original, phase)
    verify_frozen_reviewed_policy_change_trace(value)
    assert value.outcome["bank_authority"] is False
    assert (
        value.inputs.get("review_phase") != "CONFIRM"
        or value.outcome["financial_readback_status"] == "NOT_READ_AFTER_COMMIT"
    )


@pytest.mark.parametrize(
    "change",
    [
        "algorithm",
        "parent",
        "outer_owner",
        "legacy_reason",
        "legacy_hash",
        "receipt",
        "extra",
        "outcome",
    ],
)
def test_confirmation_trace_cannot_relabel_new_body_or_legacy_key_as_success(
    original: ReviewedChangeRecord, change: str
) -> None:
    value = trace(original, True)
    inputs, outcome = deepcopy(value.inputs), deepcopy(value.outcome)
    changes: dict[str, Any] = {"inputs": inputs, "outcome": outcome}
    if change == "algorithm":
        changes["algorithm_versions"] = {MARKER: "unknown-version"}
    elif change == "parent":
        changes["parent_run_id"] = UUID(int=999)
    elif change == "outer_owner":
        inputs["outer_request"]["user_id"] = str(UUID(int=999))
        inputs["outer_request_hash"] = configuration_hash(inputs["outer_request"])
    elif change == "legacy_reason":
        inputs["legacy_request"]["reason"] = "different"
        inputs["legacy_request_hash"] = configuration_hash(inputs["legacy_request"])
    elif change == "legacy_hash":
        inputs["legacy_request_hash"] = "f" * 64
    elif change == "receipt":
        inputs["lifecycle_receipt"]["previous_version_id"] = str(UUID(int=999))
        outcome["lifecycle_receipt"] = inputs["lifecycle_receipt"]
    elif change == "extra":
        inputs["financial_success"] = True
    else:
        outcome["financial_readback_status"] = "VERIFIED"
    with pytest.raises(ValueError):
        verify_frozen_reviewed_policy_change_trace(trace(original, True, **changes))


def post_commit(
    original: ReviewedChangeRecord,
) -> tuple[
    ConfirmReviewedChangeRequest, dict[str, Any], dict[str, Any], MultiTemplatePreviewResponse
]:
    body = confirm_body(original)
    legacy = legacy_request(
        original.user_id,
        original.source_kind,
        original.policy_id,
        body,
        original.preview.after_configuration,
    )
    version_id = UUID(int=9099)
    receipt = {
        "policy_id": str(original.policy_id),
        "current_version_id": str(version_id),
        "previous_version_id": str(body.expected_version_id),
    }
    impact = original.preview.financial_impact.model_copy(
        update={
            "before": original.preview.financial_impact.after,
            "delta_safe_idle_cents": 0,
            "delta_minimum_margin_cents": 0,
        }
    )
    fresh = original.preview.model_copy(
        update={
            "expected_version_id": version_id,
            "before_configuration": original.preview.after_configuration,
            "current_configuration_hash": original.preview.candidate_configuration_hash,
            "financial_impact": impact,
            "source_originals": {
                "mvp_history": {
                    "versions": [
                        {
                            "id": str(version_id),
                            "user_id": str(original.user_id),
                            "policy_id": str(original.policy_id),
                            "configuration": original.preview.after_configuration,
                            "content_hash": body.reviewed_configuration_hash,
                            "confirmation": {
                                "request_key": "change:" + body.idempotency_key,
                                "request_hash": configuration_hash(legacy),
                            },
                            "impact_analysis": {"lifecycle_result": receipt},
                        }
                    ]
                }
            },
        }
    )
    return body, legacy, receipt, fresh


@pytest.mark.parametrize("failure", [False, True, "wrong_key", "missing_receipt"])
def test_committed_original_key_retained_when_independent_readback_missing_or_differs(
    original: ReviewedChangeRecord, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    from app.services import full_policy_reviewed_change as service

    body, legacy, receipt, fresh = post_commit(original)

    def capture(*args: Any) -> tuple[MultiTemplatePreviewResponse, SourceBasis]:
        if failure is True:
            raise TimeoutError("actual fresh read absent")
        if failure == "wrong_key":
            fresh.source_originals["mvp_history"]["versions"][0]["confirmation"]["request_key"] = (
                "change:another-key"
            )
        return fresh, original.source_basis

    if failure == "missing_receipt":
        receipt = {"policy_id": str(original.policy_id)}
    monkeypatch.setattr(service, "_capture", capture)
    response = _readback(
        None,
        original.user_id,
        original.source_kind,
        original.policy_id,
        body,
        original,
        legacy,
        receipt,
        original.captured_at,
    )  # type: ignore[arg-type]
    assert response.commit_state == "COMMITTED" and response.lifecycle_receipt == receipt
    assert response.idempotency_key == body.idempotency_key and response.original_request == body
    assert not response.bank_authority and not response.full_financial_effects_verified
    if failure:
        assert response.status == "COMMITTED_BUT_FINANCIAL_UNKNOWN" and response.reasons
    else:
        assert (
            response.status == "COMMITTED_REVIEW_SCOPE_MATCHED"
            and response.actual_delta_safe_idle_cents == -1000
        )
        _verify_lifecycle_original(fresh, body, legacy, receipt)


@pytest.mark.parametrize("key", ["", "x" * 161, "white space", "a/b", "银行"])
def test_internal_key_is_closed_not_just_http_validation(
    original: ReviewedChangeRecord, key: str
) -> None:
    with pytest.raises(ValueError):
        identity(original.user_id, "CONFIRM", key)


def test_json_request_strict_confirmation_amount_and_unknown_fields(
    original: ReviewedChangeRecord,
) -> None:
    body = confirm_body(original).model_dump(mode="json")
    assert (
        ConfirmReviewedChangeRequest.model_validate_json(__import__("json").dumps(body)).review_id
        == original.review_id
    )
    for changed in (
        {"accepted": 1},
        {"accepted": False},
        {"review_id": "bad"},
        {"amount_cents": 2},
        {"role": "USER"},
        {"now": "2030-01-01"},
    ):
        with pytest.raises(ValueError):
            ConfirmReviewedChangeRequest.model_validate_json(
                __import__("json").dumps({**body, **changed})
            )
