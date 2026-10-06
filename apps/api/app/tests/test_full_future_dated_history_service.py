"""Synthetic adapter seams only: actual PostgreSQL proof belongs to Root's one chain."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.full_models import FullPolicy
from app.db.models import AuditEpoch, User
from app.domain.full_future_dated_history import validate_future_dated_history
from app.services import full_future_dated_history as service
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_future_dated_history import EPOCH, NOW, POLICY, USER, originals
from sqlalchemy.orm import Session


def view() -> FullPolicyView:
    proof = originals()
    raw = proof.versions[-1]
    return FullPolicyView(
        policy_id=POLICY,
        epoch_id=EPOCH,
        template_name="DatedExpensePolicy",
        name="synthetic dated",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        updated_at=datetime.fromisoformat(proof.commands[-1]["created_at"]),
        current_version=FullVersionView(
            version_id=UUID(raw["id"]),
            policy_id=POLICY,
            version_number=2,
            configuration=raw["configuration"],
            content_hash=raw["content_hash"],
            previous_hash=raw["previous_hash"],
            summary="synthetic current",
            confirmation=raw["confirmation"],
            confirmed_at=datetime.fromisoformat(raw["confirmed_at"]),
            valid_from=datetime.fromisoformat(raw["valid_from"]),
            valid_until=None,
            change_reason="synthetic",
            evidence_ids=[UUID(v) for v in raw["evidence_ids"]],
            impact_analysis=raw["impact_analysis"],
            confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
        ),
    )


class Rows:
    def __init__(
        self, *, count: int = 2, missing_evidence: bool = False, readonly: bool = True
    ) -> None:
        proof = originals()
        self.new: set[Any] = set()
        self.dirty: set[Any] = set()
        self.deleted: set[Any] = set()
        self.no_autoflush = nullcontext()
        self.version_count, self.readonly = count, readonly
        self.calls: list[str] = []
        self.user = SimpleNamespace(
            id=USER, timezone="Asia/Shanghai", is_simulated=True, raw=proof.user_original
        )
        self.epoch = SimpleNamespace(
            id=EPOCH, user_id=USER, status="OPEN", raw=proof.epoch_original
        )
        self.policy = SimpleNamespace(id=POLICY, raw=proof.policy_original)
        self.versions = [
            SimpleNamespace(id=UUID(v["id"]), evidence_ids=v["evidence_ids"], raw=v)
            for v in proof.versions
        ]
        self.commands = [SimpleNamespace(id=UUID(v["id"]), raw=v) for v in proof.commands]
        self.evidence = [SimpleNamespace(id=UUID(v["id"]), raw=v) for v in proof.evidence_originals]
        if missing_evidence:
            self.evidence.pop()

    def connection(self) -> Any:
        return SimpleNamespace(get_isolation_level=lambda: "REPEATABLE READ")

    def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        self.calls.append(sql)
        if sql == "SHOW transaction_read_only":
            return "on" if self.readonly else "off"
        assert "user_id" not in sql, "Complete policy history count must not hide other-owner rows"
        return self.version_count if "full_policy_versions" in sql else 2

    def scalars(self, statement: Any) -> Any:
        sql = str(statement)
        self.calls.append(sql)
        if "FROM full_policy_versions" in sql:
            return self.versions
        if "FROM full_policy_commands" in sql:
            return self.commands
        if "FROM evidence_items" in sql:
            return self.evidence
        raise AssertionError(sql)

    def get(self, model: Any, identifier: UUID) -> Any:
        return {User: self.user, AuditEpoch: self.epoch, FullPolicy: self.policy}[model]


def patch_reads(monkeypatch: pytest.MonkeyPatch, rows: Rows) -> FullPolicyView:
    current = view()
    monkeypatch.setattr(service, "read_full_policy", lambda *_: current)
    monkeypatch.setattr(
        service,
        "list_full_versions",
        lambda *_: SimpleNamespace(items=[SimpleNamespace(version_id=v.id) for v in rows.versions]),
    )
    monkeypatch.setattr(
        service,
        "list_full_commands",
        lambda *_: SimpleNamespace(items=[SimpleNamespace(command_id=v.id) for v in rows.commands]),
    )
    monkeypatch.setattr(service, "current_audit_epoch", lambda *_: rows.epoch)
    monkeypatch.setattr(service, "row_copy", lambda row: deepcopy(row.raw))
    return current


def test_rrro_verified_lists_original_counts_and_evidence_produce_bound_calendar_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = Rows()
    current = patch_reads(monkeypatch, rows)
    result = service.prove_current_future_dated_history(cast(Session, rows), USER, current, NOW)
    assert result.status == "VERIFIED_FUTURE_DATED_HISTORY" and validate_future_dated_history(
        result
    )
    assert result.actual_version_count == result.captured_version_count == 2
    assert result.actual_command_count == result.captured_command_count == 2
    assert result.expected_evidence_count == result.captured_evidence_count == 2
    assert all("INSERT" not in q and "UPDATE" not in q and "DELETE" not in q for q in rows.calls)


@pytest.mark.parametrize(
    "failure", ["count", "missing_evidence", "stale_view", "archived", "dirty", "writable"]
)
def test_missing_denominator_evidence_changed_chain_or_non_rrro_never_upgrades(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    rows = Rows(
        count=3 if failure == "count" else 2,
        missing_evidence=failure == "missing_evidence",
        readonly=failure != "writable",
    )
    current = patch_reads(monkeypatch, rows)
    if failure == "stale_view":
        current = current.model_copy(
            update={
                "current_version": current.current_version.model_copy(
                    update={"version_id": UUID(int=999)}
                )
            }
        )
    if failure == "archived":
        rows.epoch.status = "SEALED"
    if failure == "dirty":
        rows.dirty.add(object())
    if failure in {"dirty", "writable"}:
        with pytest.raises(PolicyLifecycleError) as captured:
            service.prove_current_future_dated_history(cast(Session, rows), USER, current, NOW)
        assert captured.value.code == "INVALID_READ_SNAPSHOT"
        return
    result = service.prove_current_future_dated_history(cast(Session, rows), USER, current, NOW)
    assert result.status == "UNKNOWN" and result.reasons and result.unpaid_amount_proven is False
    if failure == "count":
        assert result.actual_version_count == 3
    if failure == "missing_evidence":
        assert result.expected_evidence_count == 2 and result.captured_evidence_count == 1
