"""Root runs disposable PG classification→real suggestions, never human research."""

import json
import traceback
from uuid import UUID

import pytest
from app.api.dependencies import get_engine, get_now
from app.api.v1 import transaction_category as category_routes
from app.db.models import EvidenceItem, Transaction
from app.main import create_app
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.transaction_category import confirm_category
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_explicit_categories_restore_patterns_without_changing_bank_facts(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def observed_confirm(*args: object, **kwargs: object) -> object:
        try:
            return confirm_category(*args, **kwargs)  # type: ignore[arg-type]
        except PolicyLifecycleError:
            raise
        except Exception:
            # Production preserves its generic HTTP 500. This isolated test logs
            # the original exception once so a failed real write is diagnosable.
            print(traceback.format_exc())
            raise

    monkeypatch.setattr(category_routes, "confirm_category", observed_confirm)
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: boundary_engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    with Session(boundary_engine) as session:
        identities = list(
            session.scalars(
                select(Transaction.id)
                .where(Transaction.user_id == DEMO_USER_ID, Transaction.category == "rent")
                .order_by(Transaction.id)
            )
        )
    assert len(identities) == 2
    before = json.loads(physical_snapshot(boundary_engine))
    with TestClient(app) as client:
        initial = client.get("/api/v1/policy-suggestions/periodic")
        assert initial.status_code == 200
        assert not initial.json()["history_proof"]["verified"]
        assert "INVALID_CATEGORY_CONFIRMATION" in initial.json()["history_proof"]["reason_codes"]
        for identity in identities:
            path = f"/api/v1/transactions/{identity}"
            state = physical_snapshot(boundary_engine)
            reviewed = client.get(path + "/category-review")
            assert reviewed.status_code == 200, reviewed.text
            review = reviewed.json()
            assert review["first_confirmation_supported"] is True
            assert physical_snapshot(boundary_engine) == state
            body = {
                "category": "rent",
                "accepted": True,
                "reviewed_transaction_hash": review["reviewed_transaction_hash"],
                "reason": "隔离模拟测试明确确认此原租金分类，不是真人研究",
                "idempotency_key": f"category:{identity}",
                "expected_epoch_id": review["epoch_id"],
            }
            assert (
                client.post(
                    path + "/category-confirmation", json=body | {"accepted": 1}
                ).status_code
                == 422
            )
            assert (
                client.post(
                    path + "/category-confirmation",
                    json=body | {"reviewed_transaction_hash": "f" * 64},
                ).status_code
                == 409
            )
            assert physical_snapshot(boundary_engine) == state
            confirmed = client.post(path + "/category-confirmation", json=body)
            assert confirmed.status_code == 200, confirmed.text
            original = confirmed.json()
            assert original["status"] == "RECORDED" and original["replayed_original"] is False
            assert original["bank_facts_changed"] is False and original["grants_authority"] is False
            saved = physical_snapshot(boundary_engine)
            lookup = client.get(
                path
                + f"/category-confirmations/{review['epoch_id']}/by-key/{body['idempotency_key']}"
            )
            assert lookup.status_code == 200, lookup.text
            assert lookup.json()["original_command"] == original["original_command"]
            assert lookup.json()["original_receipt"] == original["original_receipt"]
            replay = client.post(path + "/category-confirmation", json=body)
            assert replay.status_code == 200 and replay.json()["replayed_original"] is True
            assert replay.json()["audit_event_hash"] == original["audit_event_hash"]
            assert physical_snapshot(boundary_engine) == saved
            assert (
                client.post(
                    path + "/category-confirmation", json=body | {"reason": "changed"}
                ).status_code
                == 409
            )
            assert physical_snapshot(boundary_engine) == saved
        frozen = physical_snapshot(boundary_engine)
        patterns = client.get("/api/v1/policy-suggestions/periodic")
        assert patterns.status_code == 200, patterns.text
        actual = patterns.json()
        assert actual["history_proof"]["verified"] and actual["source_issues"] == []
        assert {row["kind"] for row in actual["patterns"]} == {
            "FIXED_TRANSFER",
            "RENT",
            "CREDIT_CARD_BILL",
        }
        assert all(
            row["status"] == "READY" and row["sample_count"] == 2 for row in actual["patterns"]
        )
        assert all(
            row["candidate_configuration"]["auto_execute"] is False for row in actual["patterns"]
        )
        assert physical_snapshot(boundary_engine) == frozen
        after = json.loads(frozen)
        for name in before:
            if name not in {
                "transactions",
                "evidence_items",
                "audit_events",
                "audit_epochs",
                "audit_subject_snapshots",
            }:
                assert after[name] == before[name], name
        for original in before["evidence_items"]:
            assert original in after["evidence_items"]
        evidence_id = confirmed.json()["evidence_id"]
        with Session(boundary_engine) as session, session.begin():
            declaration = session.get(EvidenceItem, UUID(evidence_id))
            assert declaration is not None
            declaration.content_hash = "f" * 64
        changed = physical_snapshot(boundary_engine)
        refused = client.get("/api/v1/policy-suggestions/periodic")
        assert refused.status_code == 200 and not refused.json()["history_proof"]["verified"]
        assert all(row["candidate_configuration"] is None for row in refused.json()["patterns"])
        assert physical_snapshot(boundary_engine) == changed
