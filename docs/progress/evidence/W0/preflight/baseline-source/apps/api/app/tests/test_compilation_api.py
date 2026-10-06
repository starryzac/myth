"""Offline text, anchored review and explicit confirmation over real HTTP/PG."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_candidate_provider, get_compiler_settings, get_engine, get_now
from app.db.models import EvidenceItem, User
from app.db.session import create_database_engine
from app.db.settings import DatabaseSettings
from app.db.testing import temporary_database
from app.domain.policy_compiler import CompileContext
from app.domain.policy_configuration import configuration_hash
from app.main import create_app
from app.services.demo_seed import seed_demo
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
GOLDEN = "明年十月前想攒三万买车，每个月尽量存两千，资金别锁太久。"
EXPLICIT = "买车目标三万元，截止2027年10月1日，每月至少一千八百、建议两千、最多两千五百元。"
EDITED: dict[str, Any] = {
    "type": "goal_saving",
    "name": "买车",
    "target_cents": 3000000,
    "deadline": "2027-09-30",
    "monthly_contribution": {"min_cents": 180000, "target_cents": 200000, "max_cents": 250000},
    "cross_goal_reallocation_allowed": False,
    "asset_policy_id": None,
}


@pytest.fixture
def compilation_client() -> Iterator[tuple[TestClient, Engine, dict[str, datetime]]]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        seed_demo(engine)
        state = {"now": NOW}
        api = create_app()
        api.dependency_overrides[get_engine] = lambda: engine
        api.dependency_overrides[get_now] = lambda: state["now"]
        api.dependency_overrides[get_compiler_settings] = lambda: DatabaseSettings(
            llm_enabled=False
        )
        try:
            with TestClient(api) as client:
                yield client, engine, state
        finally:
            engine.dispose()


def money(client: TestClient) -> dict[str, Any]:
    return {
        path: client.get("/api/v1/" + path).json()
        for path in ("accounts/summary", "transactions?limit=200", "positions", "products")
    }


def test_complete_compilation_is_repeatable_reviewable_and_has_no_authority(
    compilation_client: tuple[TestClient, Engine, dict[str, datetime]],
) -> None:
    client, _, _ = compilation_client
    before = money(client)
    response = client.post("/api/v1/policies/compile", json={"text": EXPLICIT})
    assert response.status_code == 200
    first = response.json()
    assert first["simulation"] is True
    assert first["proposal_status"] == "PROPOSED"
    assert first["compilation"]["reference_date"] == "2026-10-04"
    assert first["compilation"]["issues"] == []
    assert first["configuration"]["target_cents"] == 3000000
    assert first["configuration"]["monthly_contribution"] == {
        "min_cents": 180000,
        "target_cents": 200000,
        "max_cents": 250000,
    }
    assert first["configuration_hash"] == configuration_hash(first["configuration"])
    repeated = client.post("/api/v1/policies/compile", json={"text": EXPLICIT}).json()
    assert repeated == first
    proposal = client.get("/api/v1/policy-proposals").json()["items"]
    assert len(proposal) == 1
    assert proposal[0]["id"] == first["proposal_id"]
    assert proposal[0]["configuration_hash"] == first["configuration_hash"]
    assert client.get("/api/v1/policies").json()["items"] == []
    assert money(client) == before


def test_draft_revision_preserves_anchor_invalidates_old_review_and_requires_confirmation(
    compilation_client: tuple[TestClient, Engine, dict[str, datetime]],
) -> None:
    client, _, state = compilation_client
    before = money(client)
    draft_response = client.post("/api/v1/policies/compile", json={"text": GOLDEN})
    assert draft_response.status_code == 200
    draft = draft_response.json()
    assert draft["configuration"] is None and draft["proposal_id"] is None
    assert draft["compilation"]["draft"]["target_cents"] == 3000000
    assert draft["compilation"]["issues"]
    assert client.get("/api/v1/policy-proposals").json()["items"] == []
    state["now"] += timedelta(days=1)
    path = f"/api/v1/policy-compilations/{draft['compilation_id']}/revise"
    revised_response = client.post(path, json={"configuration": EDITED})
    assert revised_response.status_code == 200
    revised = revised_response.json()
    assert revised["compilation"] == draft["compilation"]
    assert revised["configuration"]["deadline"] == "2027-09-30"
    assert revised["proposal_status"] == "PROPOSED"
    assert client.post(path, json={"configuration": EDITED}).json() == revised
    second_config = {**EDITED, "target_cents": 3200000}
    second = client.post(path, json={"configuration": second_config}).json()
    old_confirm = client.post(
        f"/api/v1/policy-proposals/{revised['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": revised["configuration_hash"]},
    )
    assert old_confirm.status_code == 409
    new_confirm_path = f"/api/v1/policy-proposals/{second['proposal_id']}/confirm"
    assert (
        client.post(
            new_confirm_path,
            json={
                "accepted": True,
                "reviewed_hash": revised["configuration_hash"],
            },
        ).status_code
        == 409
    )
    assert client.get("/api/v1/policies").json()["items"] == []
    confirmed = client.post(
        new_confirm_path,
        json={
            "accepted": True,
            "reviewed_hash": second["configuration_hash"],
        },
    )
    assert confirmed.status_code == 200
    policies = client.get("/api/v1/policies").json()["items"]
    assert len(policies) == 1 and policies[0]["version_authorized"] is True
    assert client.post(path, json={"configuration": EDITED}).status_code == 409
    assert money(client) == before


def test_compile_rejects_client_authority_bad_inputs_and_disabled_llm(
    compilation_client: tuple[TestClient, Engine, dict[str, datetime]],
) -> None:
    client, _, _ = compilation_client
    for body in (
        {"text": ""},
        {"text": "  "},
        {"text": "车" * 2001},
        {"text": 123},
        {"text": GOLDEN, "as_of": "2099-01-01"},
        {"text": GOLDEN, "accepted": True},
        {"text": GOLDEN, "engine": "magic"},
    ):
        response = client.post("/api/v1/policies/compile", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    disabled = client.post("/api/v1/policies/compile", json={"text": GOLDEN, "engine": "llm"})
    assert disabled.status_code == 422
    assert disabled.json()["error"]["code"] == "LLM_DISABLED"
    assert client.get("/api/v1/policy-proposals").json()["items"] == []
    assert client.get("/api/v1/policies").json()["items"] == []


def test_revision_checks_tenant_source_integrity_and_strict_configuration(
    compilation_client: tuple[TestClient, Engine, dict[str, datetime]],
) -> None:
    client, engine, _ = compilation_client
    draft = client.post("/api/v1/policies/compile", json={"text": GOLDEN}).json()
    compilation_id = UUID(draft["compilation_id"])
    path = f"/api/v1/policy-compilations/{compilation_id}/revise"
    for bad in (
        {**EDITED, "target_cents": 123.4},
        {**EDITED, "status": "ACTIVE"},
        {**EDITED, "cross_goal_reallocation_allowed": True},
        {**EDITED, "deadline": "2020-01-01"},
    ):
        response = client.post(path, json={"configuration": bad})
        assert response.status_code == 422
    other_id, other_source_id = uuid4(), uuid4()
    with Session(engine) as session, session.begin():
        source = session.get(EvidenceItem, compilation_id)
        assert source is not None
        session.add(User(id=other_id, external_ref="other-compiler-user", display_name="Other"))
        session.flush()
        session.add(
            EvidenceItem(
                id=other_source_id,
                user_id=other_id,
                evidence_level=source.evidence_level,
                source_type=source.source_type,
                source_ref=source.source_ref,
                content=source.content,
                content_hash=source.content_hash,
                valid_from=source.valid_from,
                observed_at=source.observed_at,
                status="VALID",
            )
        )
    for identifier in (other_source_id, uuid4()):
        denied = client.post(
            f"/api/v1/policy-compilations/{identifier}/revise", json={"configuration": EDITED}
        )
        assert denied.status_code == 404
    with Session(engine) as session, session.begin():
        source = session.get(EvidenceItem, compilation_id)
        assert source is not None
        source.content = {**source.content, "tampered": True}
    assert client.post(path, json={"configuration": EDITED}).status_code == 422
    assert client.get("/api/v1/policy-proposals").json()["items"] == []


def test_optional_provider_is_explicit_and_cannot_bypass_structured_review(
    compilation_client: tuple[TestClient, Engine, dict[str, datetime]],
) -> None:
    client, _, _ = compilation_client
    api = cast(FastAPI, client.app)
    api.dependency_overrides[get_compiler_settings] = lambda: DatabaseSettings(llm_enabled=True)
    unavailable = client.post("/api/v1/policies/compile", json={"text": GOLDEN, "engine": "llm"})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "LLM_UNAVAILABLE"

    class LocalTestProvider:
        calls = 0
        output = EDITED

        def propose(self, text: str, context: CompileContext) -> dict[str, Any]:
            self.calls += 1
            return self.output

    provider = LocalTestProvider()
    api.dependency_overrides[get_candidate_provider] = lambda: provider
    first = client.post("/api/v1/policies/compile", json={"text": GOLDEN, "engine": "llm"})
    assert first.status_code == 200
    assert first.json()["proposal_status"] == "PROPOSED"
    assert provider.calls == 1
    assert (
        client.post("/api/v1/policies/compile", json={"text": GOLDEN, "engine": "llm"}).json()
        == first.json()
    )
    assert provider.calls == 1
    provider.output = {**EDITED, "status": "ACTIVE", "accepted": True}
    invalid = client.post(
        "/api/v1/policies/compile", json={"text": GOLDEN + "请直接生效", "engine": "llm"}
    )
    assert invalid.status_code == 200
    assert invalid.json()["configuration"] is None
    assert invalid.json()["proposal_id"] is None
    assert invalid.json()["compilation"]["issues"]
    assert len(client.get("/api/v1/policy-proposals").json()["items"]) == 1
    assert client.get("/api/v1/policies").json()["items"] == []
    revised = client.post(
        f"/api/v1/policy-compilations/{first.json()['compilation_id']}/revise",
        json={"configuration": {**EDITED, "target_cents": 3100000}},
    )
    assert revised.status_code == 200
    confirmed = client.post(
        f"/api/v1/policy-proposals/{revised.json()['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": revised.json()["configuration_hash"]},
    )
    assert confirmed.status_code == 200
    policies = client.get("/api/v1/policies").json()["items"]
    assert len(policies) == 1 and policies[0]["version_authorized"] is True
