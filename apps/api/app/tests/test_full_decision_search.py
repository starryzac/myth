"""Synthetic read-only search risks. No database, financial execution or acceptance proof."""

import copy
from datetime import timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_decision_search as api
from app.db.full_models import FullPolicyVersion
from app.db.models import ActionPlan, DecisionRun, PolicyVersion, User
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_decision_search import DecisionSearchQuery, typed_policy_references
from app.domain.policy_configuration import configuration_hash
from app.services import full_decision_search as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_decision_trace_domain import ACTION, NOW, RUN, USER, VERSION, fields
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session


def trace() -> DecisionTrace:
    return build_trace(**fields())


def original(saved: DecisionTrace, **changes: Any) -> DecisionRun:
    snapshot = {"decision_trace": saved.model_dump(mode="json")}
    return DecisionRun(
        **{
            "id": saved.run_id,
            "user_id": saved.user_id,
            "created_at": saved.as_of,
            "as_of": saved.as_of,
            "completed_at": saved.as_of,
            "idempotency_key": "SYNTHETIC_ONLY",
            "trigger_type": "TRACE_PREPARE",
            "algorithm_version": "decision-trace-v1",
            "subject_action_plan_id": saved.action_id,
            "parent_run_id": None,
            "input_snapshot": snapshot,
            "snapshot_hash": configuration_hash(snapshot),
            "policy_version_ids": [str(VERSION)],
            "evidence_ids": [],
            "result": {},
            "status": "SUCCEEDED",
            **changes,
        }
    )


def setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: bool = False,
    saved: DecisionTrace | None = None,
    completeness: str = "COMPLETE",
    rows: list[DecisionRun] | None = None,
    capacity: bool = False,
    action: ActionPlan | None = None,
    full_version: FullPolicyVersion | None = None,
) -> Session:
    saved = saved or trace()
    values: list[Any] = [USER]
    if action is not None:
        values.append(action)
    if version:
        values.extend(
            [
                PolicyVersion(
                    id=VERSION,
                    configuration={"synthetic": True},
                    content_hash=configuration_hash({"synthetic": True}),
                ),
                None,
            ]
        )
    if full_version is not None:
        values.extend([None, full_version])
    values.extend([2, 1, 1025 if capacity else 1, 0, 0, 1024, 0, 0])
    session = MagicMock(spec=Session)
    session.scalar.side_effect = values
    if capacity:
        session.scalars.side_effect = AssertionError("Oversize source must not be hydrated")
    else:
        session.scalars.side_effect = [
            MagicMock(__iter__=lambda _: iter(rows or [original(saved)])),
            MagicMock(__iter__=lambda _: iter([])),
            MagicMock(__iter__=lambda _: iter([])),
        ]
    monkeypatch.setattr(service, "readonly", lambda _session: None)
    monkeypatch.setattr(
        service,
        "_stored_trace",
        lambda *_: (completeness, saved if completeness == "COMPLETE" else None),
    )
    return cast(Session, session)


def original_action() -> ActionPlan:
    request = {"synthetic": "NO_EXECUTION_TEST_ONLY"}
    return ActionPlan(
        id=ACTION,
        user_id=USER,
        created_at=NOW,
        decision_run_id=RUN,
        idempotency_key="exact + / : % original",
        request=request,
        request_hash=configuration_hash(request),
    )


def test_exact_action_key_uses_owned_row_and_original_fk(monkeypatch: pytest.MonkeyPatch) -> None:
    action = original_action()
    query = DecisionSearchQuery(action_key=action.idempotency_key)
    result = service.search_decisions(setup(monkeypatch, action=action), USER, query, NOW)
    assert result.resolved_action_id == ACTION and result.items[0].run_id == RUN
    assert result.items[0].action_ids == [ACTION] and result.verified_match_count == 1
    assert result.query.action_key == action.idempotency_key


def test_exact_action_ref_does_not_upgrade_partial_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    action = original_action()
    result = service.search_decisions(
        setup(monkeypatch, action=action, completeness="LEGACY_PARTIAL"),
        USER,
        DecisionSearchQuery(action_id=ACTION),
        NOW,
    )
    assert result.state == "UNKNOWN" and result.total_match_count is None
    assert result.items[0].match_state == "MATCHED" and result.verified_match_count == 0
    assert result.inventory.unverifiable_count == 1


def test_tampered_original_action_key_payload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    action = original_action()
    action.request["changed"] = "must reject"
    with pytest.raises(PolicyLifecycleError) as error:
        service.search_decisions(
            setup(monkeypatch, action=action),
            USER,
            DecisionSearchQuery(action_key=action.idempotency_key),
            NOW,
        )
    assert error.value.code == "ACTION_SEARCH_INTEGRITY_ERROR"


def test_owned_full_version_is_explicit_unknown_not_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = service.search_decisions(
        setup(monkeypatch, full_version=FullPolicyVersion(id=VERSION)),
        USER,
        DecisionSearchQuery(policy_version_id=VERSION),
        NOW,
    )
    assert result.state == "UNKNOWN" and result.version_family == "FULL_UNSUPPORTED"
    assert result.total_match_count is None and result.items[0].match_state == "UNVERIFIABLE"
    assert result.inventory.selected_scope_count == result.inventory.verified_typed_count == 1
    assert "FULL_VERSION_TYPED_FAMILIES_NOT_ADAPTED" in result.issues


def test_typed_policy_and_constraint_refs_match_without_uuid_scanning() -> None:
    saved = trace()
    refs = typed_policy_references(saved)
    assert {ref.identity for ref in refs} == {VERSION}
    assert {ref.pointer for ref in refs} == {
        "/decision_trace/policies/0/id",
        "/decision_trace/constraints/0/policy_version_id",
    }
    data = fields()
    data["inputs"]["unregistered_uuid"] = str(UUID(int=777))
    assert UUID(int=777) not in {
        ref.identity for ref in typed_policy_references(build_trace(**data))
    }


def test_rehashed_arbitrary_version_text_does_not_become_a_version_ref() -> None:
    data = fields()
    data["policies"] = []
    data["constraints"] = []
    data["inputs"]["policy_version_id"] = str(VERSION)
    assert typed_policy_references(build_trace(**data)) == []


def test_nested_trace_mutation_is_rejected_before_matching() -> None:
    saved = trace()
    saved.inputs["unexpected"] = str(VERSION)
    with pytest.raises(ValueError):
        typed_policy_references(saved)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"epoch_id": str(UUID(int=9))},
        {"action_id": str(ACTION), "action_key": "old"},
        {"action_key": " "},
        {"action_key": "x" * 161},
        {"action_key": "a\x00b"},
        {"action_id": "../other"},
        {"action_id": str(ACTION), "amount_cents": 123},
        {"action_id": str(ACTION), "limit": True},
        {"action_id": str(ACTION), "offset": -1},
    ],
)
def test_request_rejects_missing_or_forged_filters(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DecisionSearchQuery.model_validate_json(__import__("json").dumps(bad))


def test_verified_exact_filter_keeps_original_denominators_and_non_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = service.search_decisions(
        setup(monkeypatch, version=True), USER, DecisionSearchQuery(policy_version_id=VERSION), NOW
    )
    assert result.state == "SEARCHED" and result.total_match_count == 1
    assert result.inventory.actual_owned_decision_count == 2
    assert result.inventory.known_decision_count == 1
    assert result.inventory.selected_scope_count == result.inventory.captured_scope_count == 1
    assert result.inventory.verified_typed_count == result.verified_match_count == 1
    assert result.items[0].run_id == RUN and result.items[0].trace_hash == trace().trace_hash
    assert result.items[0].action_ids == [ACTION]
    assert result.source_hash is not None
    assert not result.absence_is_final and not result.grants_authority
    assert not result.audit_chain_verified and not result.financial_success_inferred


def test_old_partial_counts_as_unknown_not_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    result = service.search_decisions(
        setup(monkeypatch, version=True, completeness="LEGACY_PARTIAL"),
        USER,
        DecisionSearchQuery(policy_version_id=VERSION),
        NOW,
    )
    assert result.state == "UNKNOWN" and result.total_match_count is None
    assert result.inventory.unverifiable_count == 1 and result.inventory.selected_scope_count == 1
    assert result.items[0].match_state == "UNVERIFIABLE"
    assert result.items[0].trace_hash is None and not result.items[0].grants_authority


def test_unsupported_version_and_invalid_trace_do_not_shrink_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch, version=True)

    def fail(*_: Any) -> None:
        raise PolicyLifecycleError("DECISION_TRACE_INTEGRITY_ERROR", "raw invalid")

    monkeypatch.setattr(service, "_stored_trace", fail)
    result = service.search_decisions(
        session, USER, DecisionSearchQuery(policy_version_id=VERSION), NOW
    )
    assert result.state == "UNKNOWN" and result.total_match_count is None
    assert result.items[0].completeness == "INVALID" and result.inventory.unverifiable_count == 1


def test_capacity_unknown_does_not_hydrate_or_label_zero_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch, version=True, capacity=True)
    result = service.search_decisions(
        session, USER, DecisionSearchQuery(policy_version_id=VERSION), NOW
    )
    assert (
        result.state == "UNKNOWN"
        and result.total_match_count is None
        and result.source_hash is None
    )
    assert (
        result.inventory.selected_scope_count == 1025 and result.inventory.captured_scope_count == 0
    )
    assert result.items == [] and result.issues == ["SOURCE_CAPACITY_EXCEEDED"]


def test_unknown_owner_or_foreign_filter_is_not_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "readonly", lambda _: None)
    for values in ([None], [USER, None], [USER, None, None]):
        session = MagicMock(spec=Session)
        session.scalar.side_effect = values
        query = (
            DecisionSearchQuery(action_id=ACTION)
            if len(values) < 3
            else DecisionSearchQuery(policy_version_id=VERSION)
        )
        with pytest.raises(PolicyLifecycleError) as error:
            service.search_decisions(cast(Session, session), USER, query, NOW)
        assert error.value.status_code == 404


def test_future_or_foreign_row_is_rejected_even_after_source_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for row in (
        original(trace(), user_id=UUID(int=999)),
        original(trace(), as_of=NOW + timedelta(seconds=1)),
    ):
        with pytest.raises(PolicyLifecycleError) as error:
            service.search_decisions(
                setup(monkeypatch, version=True, rows=[row]),
                USER,
                DecisionSearchQuery(policy_version_id=VERSION),
                NOW,
            )
        assert error.value.code == "SEARCH_OWNER_OR_CLOCK_MISMATCH"


def test_dirty_session_guard_and_naive_clock_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    session = setup(monkeypatch)
    with pytest.raises(PolicyLifecycleError) as error:
        service.search_decisions(
            session, USER, DecisionSearchQuery(action_id=ACTION), NOW.replace(tzinfo=None)
        )
    assert error.value.code == "INVALID_SEARCH_CLOCK"

    def dirty(_: Session) -> None:
        raise PolicyLifecycleError("DIRTY_READ_SESSION", "uncommitted source")

    monkeypatch.setattr(service, "readonly", dirty)
    with pytest.raises(PolicyLifecycleError, match="uncommitted"):
        service.search_decisions(session, USER, DecisionSearchQuery(action_id=ACTION), NOW)


def test_http_strict_query_transport_and_original_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    session = setup(monkeypatch, version=True)
    expected = service.search_decisions(
        session, USER, DecisionSearchQuery(policy_version_id=VERSION), NOW
    )
    calls: list[DecisionSearchQuery] = []

    def read(_session: Session, owner: UUID, query: DecisionSearchQuery, now: Any) -> Any:
        assert owner == USER and now == NOW
        calls.append(query)
        return expected.model_copy(update={"query": query})

    monkeypatch.setattr(api, "search_decisions", read)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/decision-search", params={"policy_version_id": str(VERSION), "limit": "1"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["query"]["policy_version_id"] == str(VERSION)
        for bad in [
            "",
            "action_id=bad",
            f"action_id={ACTION}&now=2030-01-01",
            f"action_id={ACTION}&limit=true",
            f"action_id={ACTION}&offset=01",
            f"action_id={ACTION}&user_id={USER}",
            f"action_id={ACTION}&action_id={ACTION}",
            f"action_id={ACTION}&permission=AUTO_EXECUTE",
        ]:
            assert client.get("/api/v1/decision-search?" + bad).status_code == 422
    assert len(calls) == 1 and calls[0].limit == 1


def test_original_query_mutation_is_revalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    query = DecisionSearchQuery(action_id=ACTION).model_copy(update={"limit": True})
    with pytest.raises(ValidationError):
        service.search_decisions(setup(monkeypatch), USER, query, NOW)


def test_saved_query_json_preserves_exact_key() -> None:
    query = DecisionSearchQuery.model_validate_json('{"action_key":"a + / :% key"}')
    assert copy.deepcopy(query).action_key == "a + / :% key"
