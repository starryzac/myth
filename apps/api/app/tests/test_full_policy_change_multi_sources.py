"""Exact source-chain service risks with synthetic ORM originals; no PostgreSQL calls."""

from collections import deque
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import EvidenceItem, PolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    _mvp_history,
    preview_multi_template_financial_change,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.boundary_display_cases import NOW
from sqlalchemy.orm import Session


class Rows:
    def __init__(self, values: list[list[Any]]) -> None:
        self.values = deque(values)

    def scalars(self, query: Any) -> list[Any]:
        return self.values.popleft()


def originals() -> tuple[list[PolicyVersion], list[list[EvidenceItem]]]:
    versions = []
    evidence = []
    previous = None
    for number in (1, 2):
        config = validate_configuration({"type": "emergency_buffer", "amount_cents": number * 100})
        digest = configuration_hash(config)
        proof_id, version_id = UUID(int=100 + number), UUID(int=200 + number)
        confirmation = {
            "accepted": True,
            "user_id": str(UUID(int=900)),
            "policy_id": str(UUID(int=40)),
            "version_id": str(version_id),
            "reviewed_hash": digest,
            "confirmed_at": NOW.isoformat(),
        }
        versions.append(
            PolicyVersion(
                id=version_id,
                user_id=UUID(int=900),
                policy_id=UUID(int=40),
                version_number=number,
                configuration=config,
                content_hash=digest,
                previous_hash=previous,
                confirmation=confirmation,
                confirmed_at=NOW,
                created_at=NOW,
                valid_from=NOW,
                evidence_ids=[str(proof_id)],
                impact_analysis={},
            )
        )
        evidence.append(
            [
                EvidenceItem(
                    id=proof_id,
                    user_id=UUID(int=900),
                    created_at=NOW,
                    evidence_level="USER_CONFIRMED_POLICY",
                    source_type="POLICY_CONFIRMATION",
                    source_ref=str(version_id),
                    status="VALID",
                    content=confirmation,
                    content_hash=configuration_hash(confirmation),
                    observed_at=NOW,
                    valid_from=NOW,
                )
            ]
        )
        previous = digest
    return versions, evidence


def test_exact_two_version_history_retains_both_original_confirmations_without_writes() -> None:
    versions, evidence = originals()
    session = Rows([versions, *evidence])
    value = _mvp_history(cast(Session, session), UUID(int=900), UUID(int=40), NOW)
    assert len(value["versions"]) == len(value["evidence"]) == 2
    assert value["versions"][1]["previous_hash"] == versions[0].content_hash
    assert {p["id"] for p in value["evidence"]} == {str(e[0].id) for e in evidence}
    assert not session.values


@pytest.mark.parametrize(
    "change", ["prior_hash", "gap", "owner", "confirmation", "missing", "raw_hash", "conflicted"]
)
def test_bad_old_history_or_original_evidence_cannot_be_silently_reused(change: str) -> None:
    versions, evidence = originals()
    if change == "prior_hash":
        versions[1].previous_hash = "f" * 64
    elif change == "gap":
        versions[1].version_number = 3
    elif change == "owner":
        versions[0].confirmation = {**versions[0].confirmation, "user_id": str(UUID(int=999))}
    elif change == "confirmation":
        evidence[0][0].content = {**evidence[0][0].content, "accepted": False}
        evidence[0][0].content_hash = configuration_hash(evidence[0][0].content)
    elif change == "missing":
        evidence[0] = []
    elif change == "raw_hash":
        evidence[0][0].content_hash = "0" * 64
    else:
        evidence[0][0].status = "CONFLICTED"
    with pytest.raises(ValueError):
        _mvp_history(cast(Session, Rows([versions, *evidence])), UUID(int=900), UUID(int=40), NOW)


class DirtySession:
    new = (object(),)
    dirty: tuple[object, ...] = ()
    deleted: tuple[object, ...] = ()

    def connection(self) -> Any:
        pytest.fail("Dirty source gate must reject before a connection")


def test_dirty_snapshot_rejects_before_any_financial_or_candidate_read() -> None:
    request = MultiTemplatePreviewRequest(
        expected_epoch_id=UUID(int=901),
        expected_version_id=UUID(int=201),
        configuration={"type": "emergency_buffer", "amount_cents": 1},
    )
    with pytest.raises(PolicyLifecycleError) as failure:
        preview_multi_template_financial_change(
            cast(Session, DirtySession()), UUID(int=900), "MVP_POLICY", UUID(int=40), request, NOW
        )
    assert failure.value.code == "INVALID_READ_SNAPSHOT"
