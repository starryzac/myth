"""Missed notification/restart risks with synthetic original heads; no PG proof."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.services import question_intervention_recovery as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.question_intervention_producer import QuestionProducerResult
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_intervention_producer import prepared, response
from app.tests.test_question_workflow import revision
from sqlalchemy.engine import Engine


def heads(count: int = 3) -> list[Any]:
    return [revision().model_copy(update={"session_id": UUID(int=900 + i)}) for i in range(count)]


def result(identity: UUID, status: str = "NOT_PENDING") -> QuestionProducerResult:
    return QuestionProducerResult.model_validate(
        {"user_id": USER, "session_id": identity, "status": status}
    )


def test_complete_discovery_reaches_later_heads_by_cursor_without_reusing_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[datetime] = []
    calls: list[UUID] = []
    inventory = heads(5)[::-1]

    def discover(_engine: Engine, _user: UUID, now: datetime) -> list[Any]:
        reads.append(now)
        return inventory

    def observe(
        _engine: Engine, _user: UUID, identity: UUID, _now: datetime
    ) -> QuestionProducerResult:
        calls.append(identity)
        return result(identity)

    monkeypatch.setattr(service, "_pending_heads", discover)
    monkeypatch.setattr(service, "produce_current_question_intervention", observe)
    engine = MagicMock(spec=Engine)
    cursor = None
    for expected in ("MORE_PENDING", "MORE_PENDING", "COMPLETE_SCAN"):
        report = service.recover_current_question_notifications(
            engine, USER, NOW, batch_size=2, after_session_id=cursor
        )
        assert report.status == expected
        assert report.complete_registered_inventory and report.pending_original_sessions == 5
        assert not report.authority_granted and not report.delivered and not report.acknowledged
        cursor = report.next_session_cursor
    assert calls == sorted(head.session_id for head in inventory)
    assert len(reads) == 3 and cursor is None
    # A process restart rediscovers the inventory. It has no cached grant or
    # persisted assumption that an earlier notification attempt succeeded.
    service.recover_current_question_notifications(engine, USER, NOW, batch_size=1)
    assert len(reads) == 4 and calls[-1] == UUID(int=900)


def test_restart_delegates_same_original_notification_key_without_financial_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = prepared()
    original_response = response(original)
    state = revision()
    observed = QuestionProducerResult(
        user_id=USER,
        session_id=state.session_id,
        status="OBSERVED",
        request=original.body,
        request_hash=original.request_hash,
        original_response=original_response,
        observation_attempted=True,
    )
    producer = MagicMock(return_value=observed)
    discovery = MagicMock(return_value=[state])
    monkeypatch.setattr(service, "_pending_heads", discovery)
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    engine = MagicMock(spec=Engine)
    reports = [service.recover_current_question_notifications(engine, USER, NOW) for _ in range(2)]
    assert discovery.call_count == producer.call_count == 2
    assert reports[0].items == reports[1].items
    assert reports[0].items[0].idempotency_key == original.body.idempotency_key
    assert reports[0].items[0].request_hash == original.request_hash
    assert reports[0].items[0].message_id == original_response.original_receipt.message_id
    assert all(not report.answers_question and not report.execution_eligible for report in reports)


@pytest.mark.parametrize(
    "failure", [ValueError("postgresql://secret"), PolicyLifecycleError("DIRTY", "secret")]
)
def test_unverified_or_incomplete_inventory_does_not_observe_or_claim_empty(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    discovery = MagicMock(side_effect=failure)
    producer = MagicMock()
    monkeypatch.setattr(service, "_pending_heads", discovery)
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    report = service.recover_current_question_notifications(MagicMock(spec=Engine), USER, NOW)
    assert report.status == "SOURCE_UNVERIFIED" and not report.complete_registered_inventory
    assert report.pending_original_sessions is None and not report.items
    assert "secret" not in report.model_dump_json()
    producer.assert_not_called()


def test_duplicate_inventory_fails_whole_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_pending_heads", MagicMock(return_value=[revision(), revision()]))
    producer = MagicMock()
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    report = service.recover_current_question_notifications(MagicMock(spec=Engine), USER, NOW)
    assert report.status == "SOURCE_UNVERIFIED" and report.pending_original_sessions is None
    producer.assert_not_called()


def test_item_failure_does_not_suppress_later_pending_question_and_is_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = heads()
    monkeypatch.setattr(service, "_pending_heads", MagicMock(return_value=inventory))
    producer = MagicMock(
        side_effect=[
            RuntimeError("driver password"),
            result(inventory[1].session_id),
            result(inventory[2].session_id, "OBSERVATION_OUTCOME_UNKNOWN"),
        ]
    )
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    report = service.recover_current_question_notifications(MagicMock(spec=Engine), USER, NOW)
    assert report.status == "COMPLETE_SCAN" and report.complete_registered_inventory
    assert [item.status for item in report.items] == [
        "SOURCE_UNVERIFIED",
        "NOT_PENDING",
        "OBSERVATION_OUTCOME_UNKNOWN",
    ]
    assert "password" not in report.model_dump_json() and producer.call_count == 3
    assert all(item.idempotency_key is None for item in report.items)


@pytest.mark.parametrize(
    "field,value",
    [("user_id", UUID(int=22)), ("session_id", UUID(int=33)), ("authority_granted", True)],
)
def test_wrong_owner_session_or_copied_authority_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    state = revision()
    monkeypatch.setattr(service, "_pending_heads", MagicMock(return_value=[state]))
    producer = MagicMock(return_value=result(state.session_id).model_copy(update={field: value}))
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    report = service.recover_current_question_notifications(MagicMock(spec=Engine), USER, NOW)
    assert report.items[0].status == "SOURCE_UNVERIFIED"
    assert not report.authority_granted and report.items[0].message_id is None


@pytest.mark.parametrize("size", [True, False, 0, 33, 1.5, "2"])
def test_batch_limit_is_exact_and_not_boolean(monkeypatch: pytest.MonkeyPatch, size: Any) -> None:
    discovery = MagicMock()
    monkeypatch.setattr(service, "_pending_heads", discovery)
    with pytest.raises(ValueError):
        service.recover_current_question_notifications(
            MagicMock(spec=Engine), USER, NOW, batch_size=size
        )
    discovery.assert_not_called()


def test_empty_verified_inventory_is_explicit_and_no_notification_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "_pending_heads", MagicMock(return_value=[]))
    producer = MagicMock()
    monkeypatch.setattr(service, "produce_current_question_intervention", producer)
    report = service.recover_current_question_notifications(MagicMock(spec=Engine), USER, NOW)
    assert report.status == "COMPLETE_SCAN" and report.pending_original_sessions == 0
    assert report.complete_registered_inventory and report.items == []
    producer.assert_not_called()
