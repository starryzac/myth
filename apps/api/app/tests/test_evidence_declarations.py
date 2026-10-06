"""User corrections stay append-only and cannot manufacture bank authority."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.main import create_app
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.evidence_declarations import DeclarationRequest
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_scenario_service_steps import original_rows
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def declaration(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "EXPENSE",
        "source_ref": "late-correction:original",
        "content": {"amount_cents": 101137},
        "expected_epoch_id": str(uuid4()),
        "idempotency_key": "original-declaration",
        "valid_from": SEED_AS_OF.isoformat(),
        **extra,
    }


@pytest.mark.parametrize(
    "field", ["user_id", "evidence_level", "source_type", "observed_at", "status", "content_hash"]
)
def test_client_cannot_mint_observation_or_bank_authority(field: str) -> None:
    with pytest.raises(ValidationError):
        DeclarationRequest.model_validate(declaration(**{field: "BANK_CONFIRMED"}))


def test_declaration_bound_and_nonempty_interval_are_enforced() -> None:
    with pytest.raises(ValidationError):
        DeclarationRequest.model_validate(declaration(content={"note": "长" * 11000}))
    with pytest.raises(ValidationError):
        DeclarationRequest.model_validate(declaration(valid_to=SEED_AS_OF.isoformat()))


@pytest.mark.integration
def test_actual_declaration_replay_late_correction_and_financial_zero_writes(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
    before = original_rows(demo_engine)
    clock = [SEED_AS_OF + timedelta(seconds=1)]
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: demo_engine
    api.dependency_overrides[get_now] = lambda: clock[0]
    body = declaration(expected_epoch_id=str(epoch_id))
    with TestClient(api) as client:
        first = client.post("/api/v1/evidence/declarations", json=body)
        assert first.status_code == 200
        stored = first.json()
        assert stored["bank_verified"] is False and stored["grants_authority"] is False
        assert stored["evidence"]["evidence_level"] == "USER_DECLARED"
        first_id = stored["evidence"]["id"]
        after_first = original_rows(demo_engine)
        assert len(after_first["evidence_items"]) == len(before["evidence_items"]) + 1
        for name in before:
            if name != "evidence_items":
                assert after_first[name] == before[name]
        assert all(row in after_first["evidence_items"] for row in before["evidence_items"])
        clock[0] += timedelta(seconds=1)
        assert client.post("/api/v1/evidence/declarations", json=body).json() == stored
        assert original_rows(demo_engine) == after_first
        assert (
            client.post(
                "/api/v1/evidence/declarations", json=body | {"content": {"amount_cents": 109139}}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/evidence/declarations", json=body | {"evidence_level": "BANK_CONFIRMED"}
            ).status_code
            == 422
        )
        assert original_rows(demo_engine) == after_first
        clock[0] += timedelta(seconds=1)
        correction = client.post(
            "/api/v1/evidence/declarations",
            json=body
            | {
                "idempotency_key": "correction",
                "supersedes_id": first_id,
                "content": {"amount_cents": 109139},
            },
        )
        assert correction.status_code == 200
        query = {
            "source_type": "USER_DECLARED_EXPENSE",
            "source_ref": body["source_ref"],
            "valid_at": SEED_AS_OF.isoformat(),
        }
        old = client.get(
            "/api/v1/evidence/facts",
            params=query | {"known_at": (clock[0] - timedelta(seconds=1)).isoformat()},
        ).json()
        current = client.get("/api/v1/evidence/facts", params=query).json()
        assert old["groups"][0]["evidence_ids"] == [first_id]
        assert current["groups"][0]["evidence_ids"] == [correction.json()["evidence"]["id"]]
        after = original_rows(demo_engine)
        for name in before:
            if name != "evidence_items":
                assert after[name] == before[name]
        assert all(row in after["evidence_items"] for row in before["evidence_items"])
        assert len(after["evidence_items"]) == len(before["evidence_items"]) + 2
