"""Original-record service risk doubles; no database or produced financial success."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from app.db.models import EvidenceItem
from app.domain.decision_trace import build_trace
from app.domain.full_seasonal_adoption import (
    PROTOCOL,
    SOURCE,
    SeasonalAdoptionPreviewRequest,
    verify_frozen_seasonal_adoption_trace,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_seasonal_adoption as service
from app.services.decision_trace import evidence_copy
from app.services.full_policy_lifecycle import FullPolicyView
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.policy_suggestions import SeasonalSuggestions
from app.tests.test_full_seasonal_adoption import (
    EPOCH,
    POLICY,
    USER,
    original_fixture,
    scope_fixture,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class Memory:
    def __init__(self) -> None:
        self.new: set[Any] = set()
        self.dirty: set[Any] = set()
        self.deleted: set[Any] = set()
        self.original = original_fixture()
        self.now = self.original.recorded_at
        original = self.original
        self.proof = EvidenceItem(
            id=uuid5(original.command_id, "evidence"),
            user_id=USER,
            created_at=self.now,
            source_type=SOURCE,
            source_ref=str(original.command_id),
            status="VALID",
            evidence_level="USER_CONFIRMED_POLICY",
            content=original.model_dump(mode="json"),
            content_hash=configuration_hash(original.model_dump(mode="json")),
            observed_at=self.now,
            valid_from=self.now,
            valid_to=None,
        )
        self.evidence: dict[UUID, EvidenceItem] = {}
        for raw in original.scope.source_evidence_originals:
            self.evidence[UUID(raw["id"])] = EvidenceItem(
                **{
                    key: value
                    for key, value in raw.items()
                    if key
                    not in {"created_at", "observed_at", "valid_from", "valid_to", "id", "user_id"}
                },
                id=UUID(raw["id"]),
                user_id=USER,
                created_at=self.now,
                observed_at=self.now,
                valid_from=self.now,
                valid_to=None,
            )
        self.evidence[self.proof.id] = self.proof
        from datetime import datetime

        for raw in original.scope.source_evidence_originals:
            source = self.evidence[UUID(raw["id"])]
            source.observed_at = datetime.fromisoformat(raw["observed_at"])
            source.valid_from = datetime.fromisoformat(raw["valid_from"])
        self.connection_state = "REPEATABLE READ"
        self.readonly = "on"

    def get(self, model: Any, identity: UUID) -> Any:
        if model is EvidenceItem:
            return self.evidence.get(identity)
        return SimpleNamespace(id=USER, is_simulated=True, timezone="Asia/Shanghai")

    def connection(self) -> Any:
        return SimpleNamespace(get_isolation_level=lambda: self.connection_state)

    def scalar(self, query: Any) -> Any:
        assert str(query) == "SHOW transaction_read_only"
        return self.readonly

    @property
    def no_autoflush(self) -> nullcontext[None]:
        return nullcontext()

    def scalars(self, _query: Any) -> list[EvidenceItem]:
        return [self.proof] if self.proof.id in self.evidence else []

    def session(self) -> Session:
        return cast(Session, self)

    def trace(self) -> Any:
        original = self.original
        trace = build_trace(
            run_id=original.command_id,
            user_id=USER,
            phase="EVALUATION",
            as_of=self.now,
            action_id=None,
            parent_run_id=None,
            algorithm_versions={"trace": "decision-trace-v1", "seasonal_adoption": PROTOCOL},
            inputs={
                "original_request": original.original_request.model_dump(mode="json"),
                "request_hash": original.request_hash,
                "reviewed_hash": original.reviewed_hash,
            },
            sources=[evidence_copy(row) for row in self.evidence.values()],
            policies=[],
            outcome={"seasonal_adoption_original": original.model_dump(mode="json")},
        )
        return SimpleNamespace(completeness="COMPLETE", audit_chain_status="VALID", trace=trace)


def patch_reads(monkeypatch: pytest.MonkeyPatch, memory: Memory) -> None:
    monkeypatch.setattr(
        service, "verify_audit_chain", lambda *_args: SimpleNamespace(status="VALID")
    )
    monkeypatch.setattr(service, "audit_read_scope", lambda _session: nullcontext())
    monkeypatch.setattr(service, "get_decision_trace", lambda *_args: memory.trace())
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_args: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(
        service,
        "read_full_policy",
        lambda *_args: FullPolicyView.model_validate_json(
            __import__("json").dumps(memory.original.scope.full_policy_original)
        ),
    )


def test_exact_key_read_preserves_original_request_receipt_and_nonfinal_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = Memory()
    patch_reads(monkeypatch, memory)
    result = service.read_seasonal_adoption_command(
        memory.session(), USER, EPOCH, memory.original.idempotency_key, memory.now
    )
    assert result.status == "RECORDED" and result.original_receipt
    assert result.original_receipt.original == memory.original
    assert not result.bank_authority
    memory.evidence.pop(memory.proof.id)
    missing = service.read_seasonal_adoption_command(
        memory.session(), USER, EPOCH, memory.original.idempotency_key, memory.now
    )
    assert missing.status == "NOT_FOUND_NOT_FINAL" and missing.original_receipt is None


@pytest.mark.parametrize(
    "case", ["level", "status", "time", "hash", "owner", "source", "trace", "denominator"]
)
def test_rehashed_or_metadata_changed_originals_do_not_recover_as_success(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    memory = Memory()
    patch_reads(monkeypatch, memory)
    if case == "level":
        memory.proof.evidence_level = "USER_DECLARED"
    elif case == "status":
        memory.proof.status = "SUPERSEDED"
    elif case == "time":
        memory.proof.observed_at = memory.now.replace(year=2027)
    elif case == "hash":
        memory.proof.content = deepcopy(memory.proof.content)
        memory.proof.content["scope"]["adopted_adjustment_cents"] += 1
        memory.proof.content_hash = configuration_hash(memory.proof.content)
    elif case == "owner":
        memory.proof.user_id = UUID(int=99)
    elif case == "source":
        memory.proof.source_type = "CLIENT_RESULT"
    elif case == "trace":
        monkeypatch.setattr(
            service,
            "get_decision_trace",
            lambda *_args: SimpleNamespace(
                completeness="UNSUPPORTED_VERSION", audit_chain_status="VALID", trace=None
            ),
        )
    else:
        row = next(iter(memory.evidence.values()))
        memory.evidence.pop(row.id)
    with pytest.raises((PolicyLifecycleError, ValueError)):
        service.read_seasonal_adoption_command(
            memory.session(), USER, EPOCH, memory.original.idempotency_key, memory.now
        )


@pytest.mark.parametrize("case", ["dirty", "isolation", "writable"])
def test_original_get_still_requires_a_clean_actual_readonly_snapshot(case: str) -> None:
    memory = Memory()
    if case == "dirty":
        memory.dirty.add(object())
    elif case == "isolation":
        memory.connection_state = "READ COMMITTED"
    else:
        memory.readonly = "off"
    with pytest.raises(PolicyLifecycleError):
        service.read_seasonal_adoption_command(
            memory.session(), USER, EPOCH, "original", memory.now
        )


def test_agent_cannot_reach_a_write_even_with_a_fully_valid_original_review() -> None:
    original = original_fixture()
    with pytest.raises(PolicyLifecycleError) as failed:
        service.confirm_seasonal_adoption(
            cast(Engine, None),
            USER,
            POLICY,
            original.original_request,
            original.principal_at_command.model_copy(update={"role": "AGENT"}),
            original.recorded_at,
        )
    assert failed.value.code == "LOCAL_USER_SESSION_REQUIRED"


def test_current_get_never_turns_changed_source_into_zero_or_old_period_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = Memory()
    patch_reads(monkeypatch, memory)
    monkeypatch.setattr(service, "_scope", lambda *_args: memory.original.scope)
    current = service.read_current_seasonal_adoption(memory.session(), USER, POLICY, memory.now)
    assert current.status == "VERIFIED" and current.original
    assert current.original.scope.adopted_adjustment_cents == 1800

    def changed(*_args: Any) -> Any:
        raise PolicyLifecycleError("SOURCE_CHANGED", "original coverage changed", 409)

    monkeypatch.setattr(service, "_scope", changed)
    missing = service.read_current_seasonal_adoption(memory.session(), USER, POLICY, memory.now)
    assert missing.status == "UNKNOWN" and missing.current_scope is None
    assert missing.original == memory.original  # unchanged original is retained
    memory.evidence.pop(memory.proof.id)
    advice = service.read_current_seasonal_adoption(memory.session(), USER, POLICY, memory.now)
    assert advice.status == "ADVICE_ONLY" and advice.original is None


def test_scope_uses_actual_confirmed_config_and_refuses_bad_bank_or_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = Memory()
    patch_reads(monkeypatch, memory)
    scope = scope_fixture()
    monkeypatch.setattr(
        service, "verify_audit_chain", lambda *_args: SimpleNamespace(status="VALID")
    )
    monkeypatch.setattr(
        service,
        "load_verified_financial_context",
        lambda *_args: (SimpleNamespace(sources=SimpleNamespace(issues=[])), True, []),
    )
    monkeypatch.setattr(
        service,
        "seasonal_suggestions",
        lambda *_args: SeasonalSuggestions.model_validate_json(
            __import__("json").dumps(scope.suggestion_original)
        ),
    )
    # Restore the exact captured row metadata for the scope constructor.
    for raw in scope.source_evidence_originals:
        from datetime import datetime

        row = memory.evidence[UUID(raw["id"])]
        row.observed_at = datetime.fromisoformat(raw["observed_at"])
        row.valid_from = datetime.fromisoformat(raw["valid_from"])
    body = SeasonalAdoptionPreviewRequest(
        expected_version_id=scope.version_id, window_id=scope.window_id
    )
    result = service._scope(memory.session(), USER, POLICY, body, memory.now)
    assert result.adopted_adjustment_cents == scope.adopted_adjustment_cents
    monkeypatch.setattr(
        service,
        "load_verified_financial_context",
        lambda *_args: (SimpleNamespace(sources=SimpleNamespace(issues=[])), False, []),
    )
    with pytest.raises(PolicyLifecycleError):
        service._scope(memory.session(), USER, POLICY, body, memory.now)
    monkeypatch.setattr(
        service, "verify_audit_chain", lambda *_args: SimpleNamespace(status="INTEGRITY_ERROR")
    )
    with pytest.raises(PolicyLifecycleError):
        service._scope(memory.session(), USER, POLICY, body, memory.now)


def test_frozen_registered_trace_replays_exact_original_and_rehashed_metadata_is_rejected() -> None:
    memory = Memory()
    trace = memory.trace().trace
    assert verify_frozen_seasonal_adoption_trace(trace) == memory.original
    first = trace.sources[0].model_copy(update={"source_ref": "rehashed-different-source"})
    changed = build_trace(
        run_id=trace.run_id,
        user_id=trace.user_id,
        phase=trace.phase,
        as_of=trace.as_of,
        action_id=None,
        parent_run_id=None,
        algorithm_versions=trace.algorithm_versions,
        inputs=trace.inputs,
        sources=[first, *trace.sources[1:]],
        policies=[],
        outcome=trace.outcome,
    )
    with pytest.raises(ValueError, match="metadata/content"):
        verify_frozen_seasonal_adoption_trace(changed)


def test_empty_owner_filtered_adoption_list_cannot_hide_an_original_audit_integrity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = Memory()
    patch_reads(monkeypatch, memory)
    memory.evidence.pop(memory.proof.id)
    monkeypatch.setattr(
        service, "verify_audit_chain", lambda *_args: SimpleNamespace(status="INTEGRITY_ERROR")
    )
    with pytest.raises(PolicyLifecycleError) as failed:
        service.read_current_seasonal_adoption(memory.session(), USER, POLICY, memory.now)
    assert failed.value.code == "SEASONAL_ADOPTION_INTEGRITY_ERROR"
