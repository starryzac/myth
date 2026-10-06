"""Bitemporal and provenance behavior; real PostgreSQL writes only isolated fixtures."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import EvidenceItem
from app.domain.policy_configuration import configuration_hash
from app.main import create_app
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.evidence_graph import FactView, clocks, node_references, select_facts
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_scenario_service_steps import original_rows
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

AT = datetime(2026, 10, 4, tzinfo=UTC)
OWNER = uuid4()


def fact(amount: int, observed: datetime = AT, **extra: object) -> FactView:
    content = {"amount_cents": amount}
    return FactView.model_validate(
        {
            "id": uuid4(),
            "user_id": OWNER,
            "evidence_level": "USER_DECLARED",
            "source_type": "FULL101_DECLARED_FACT",
            "source_ref": "expense:one",
            "content": content,
            "content_hash": configuration_hash(content),
            "valid_from": AT,
            "valid_to": None,
            "observed_at": observed,
            "supersedes_id": None,
            "status": "VALID",
            **extra,
        }
    )


def test_late_observation_cannot_rewrite_earlier_knowledge() -> None:
    first = fact(101137)
    late = fact(109139, AT + timedelta(days=2), supersedes_id=first.id)
    old, old_issues = select_facts([first, late], AT + timedelta(hours=1), AT + timedelta(days=1))
    new, new_issues = select_facts([first, late], AT + timedelta(hours=1), AT + timedelta(days=3))
    assert old_issues == new_issues == []
    assert old[0]["evidence_ids"] == [str(first.id)]
    assert old[0]["originals"][0]["content"]["amount_cents"] == 101137
    assert new[0]["evidence_ids"] == [str(late.id)]
    assert new[0]["originals"][0]["content"]["amount_cents"] == 109139
    assert first.status == "VALID" and first.content["amount_cents"] == 101137


def test_unlinked_contradictions_remain_conflicted() -> None:
    groups, issues = select_facts([fact(101137), fact(109139)], AT, AT)
    assert issues == [] and groups[0]["state"] == "CONFLICTED"
    assert len(groups[0]["originals"]) == 2 and not groups[0]["execution_authority"]


def test_partial_valid_window_does_not_suppress_an_earlier_period() -> None:
    first = fact(101137)
    revision = fact(109139, supersedes_id=first.id, valid_from=AT + timedelta(days=10))
    groups, issues = select_facts([first, revision], AT, AT + timedelta(days=20))
    assert issues == [] and groups[0]["evidence_ids"] == [str(first.id)]
    expired, _ = select_facts([first.model_copy(update={"valid_to": AT})], AT, AT)
    assert expired == []


@pytest.mark.parametrize("status", ["UNKNOWN", "SUPERSEDED"])
def test_missing_mutable_status_history_is_not_guessed(status: str) -> None:
    groups, issues = select_facts([fact(101137, status=status)], AT, AT)
    assert groups[0]["state"] == "UNKNOWN"
    assert issues[0]["code"] == "STATUS_HISTORY_NOT_PROVEN"


def test_hash_mismatch_and_cross_owner_correction_are_explicit() -> None:
    first = fact(101137)
    invalid = fact(109139, supersedes_id=first.id, user_id=uuid4(), content_hash="a" * 64)
    groups, issues = select_facts([first, invalid], AT, AT)
    assert groups[0]["state"] == "CONFLICTED"
    assert {issue["code"] for issue in issues} == {"INVALID_SUPERSESSION", "CONTENT_HASH_MISMATCH"}


def test_supersession_cycles_are_rejected_without_discarding_both_originals() -> None:
    first, second = fact(101137), fact(109139)
    first = first.model_copy(update={"supersedes_id": second.id})
    second = second.model_copy(update={"supersedes_id": first.id})
    groups, issues = select_facts([first, second], AT, AT)
    assert len(groups[0]["originals"]) == 2
    assert {issue["code"] for issue in issues} == {"SUPERSESSION_CYCLE"}


def test_future_knowledge_and_naive_clocks_are_rejected() -> None:
    with pytest.raises(PolicyLifecycleError, match="不能查询"):
        clocks(AT, AT + timedelta(seconds=1), AT)
    with pytest.raises(PolicyLifecycleError, match="时区"):
        clocks(AT.replace(tzinfo=None), AT, AT)


def test_receipt_references_original_action_and_no_guessed_causal_edge() -> None:
    action = uuid4()
    assert node_references("RECEIPT", SimpleNamespace(action_plan_id=action)) == [
        ("ACTION", action, "EXECUTION_OF")
    ]


@pytest.mark.integration
def test_real_api_late_correction_and_all24_zero_read_writes(demo_engine: Engine) -> None:
    seed_demo(demo_engine)
    content = {"amount_cents": 101137}
    revised = {"amount_cents": 109139}
    with Session(demo_engine) as session, session.begin():
        first = EvidenceItem(
            user_id=DEMO_USER_ID,
            evidence_level="USER_DECLARED",
            source_type="FULL101_READ_TEST",
            source_ref="expense:late-original",
            content=content,
            content_hash=configuration_hash(content),
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF,
            status="VALID",
        )
        session.add(first)
        session.flush()
        first_id = first.id
        second = EvidenceItem(
            user_id=DEMO_USER_ID,
            evidence_level="USER_DECLARED",
            source_type=first.source_type,
            source_ref=first.source_ref,
            content=revised,
            content_hash=configuration_hash(revised),
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF + timedelta(days=2),
            supersedes_id=first.id,
            status="VALID",
        )
        session.add(second)
        session.flush()
        second_id = second.id
    before = original_rows(demo_engine)
    now = SEED_AS_OF + timedelta(days=3)
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: demo_engine
    api.dependency_overrides[get_now] = lambda: now
    with TestClient(api) as client:
        old = client.get(
            "/api/v1/evidence/facts",
            params={
                "valid_at": SEED_AS_OF.isoformat(),
                "known_at": (SEED_AS_OF + timedelta(days=1)).isoformat(),
                "source_type": "FULL101_READ_TEST",
                "source_ref": "expense:late-original",
            },
        )
        assert old.status_code == 200, old.text
        assert old.json()["groups"][0]["evidence_ids"] == [str(first_id)]
        current = client.get(
            "/api/v1/evidence/facts",
            params={
                "valid_at": SEED_AS_OF.isoformat(),
                "source_type": "FULL101_READ_TEST",
                "source_ref": "expense:late-original",
            },
        )
        assert current.status_code == 200, current.text
        assert current.json()["groups"][0]["evidence_ids"] == [str(second_id)]
        assert not current.json()["execution_authority"]
        graph = client.get(f"/api/v1/evidence/graph/EVIDENCE/{second_id}")
        assert graph.status_code == 200, graph.text
        assert graph.json()["state"] == "REFERENCES_RESOLVED"
        assert len(graph.json()["nodes"]) == 2 and len(graph.json()["edges"]) == 1
        assert graph.json()["edges"][0]["relation"] == "SUPERSEDES"
        assert client.get(f"/api/v1/evidence/graph/EVIDENCE/{uuid4()}").status_code == 404
        assert (
            client.get(
                "/api/v1/evidence/facts",
                params={"known_at": (now + timedelta(seconds=1)).isoformat()},
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v1/evidence/facts", params={"source_type": "ABSENT"}).json()["state"]
            == "UNKNOWN"
        )
    assert original_rows(demo_engine) == before
