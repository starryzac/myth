"""One real isolated mixed-flow candidate; parent owns actual PG execution.

Not a formal family, seven-arm result, or independent financial metric proof.
"""

import json
from uuid import UUID

import pytest
from app.domain.demo_identity import DEMO_USER_ID
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.full_native_cases import FullNativeCaseRunner
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_existing_mixed_full_api_and_original_mvp_reads(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = annual_client
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", "SYNTHETIC_MIXED_NATIVE_FULL_USER_804")
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ac" * 32)
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None and epoch.status == "OPEN"
        identity = epoch.id
    original = {
        "protocol": "full-family-case-input-v1",
        "profile": "FULL",
        "purpose": "DEVELOPMENT",
        "family_id": "DIRECT_RISK_NOT_FORMAL",
        "scenario_id": "mixed-original-real-candidate",
        "initial_state": {"mode": "EXISTING", "expected_epoch_id": str(identity)},
        "steps": [
            {
                "step_id": "cash",
                "kind": "LOOKUP_ACCOUNT",
                "at": NOW.isoformat(),
                "inputs": {"external_ref": "mvp-301-v6:account:cash"},
            },
            {
                "step_id": "validate",
                "kind": "FULL_POLICY_VALIDATE",
                "at": NOW.isoformat(),
                "inputs": {
                    "body": {
                        "template_name": "DatedExpensePolicy",
                        "configuration": {
                            "type": "dated_expense",
                            "name": "实际混合原路由候选",
                            "window": {"start": "2026-10-15", "end": "2026-10-17"},
                            "amount": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
                        },
                    }
                },
            },
            {
                "step_id": "confirm",
                "kind": "FULL_POLICY_CONFIRM",
                "at": NOW.isoformat(),
                "inputs": {
                    "body": {
                        "template_name": "DatedExpensePolicy",
                        "configuration": {
                            "$ref": {
                                "step_id": "validate",
                                "pointer": "/result/normalized_configuration",
                            }
                        },
                        "reviewed_hash": {
                            "$ref": {"step_id": "validate", "pointer": "/result/configuration_hash"}
                        },
                        "accepted": True,
                        "reason": "实际原返回复核",
                        "idempotency_key": "mixed-native-full-original",
                    }
                },
            },
            {
                "step_id": "read",
                "kind": "FULL_POLICY_READ",
                "at": NOW.isoformat(),
                "inputs": {
                    "policy_id": {"$ref": {"step_id": "confirm", "pointer": "/result/policy_id"}}
                },
            },
            {
                "step_id": "cash-again",
                "kind": "LOOKUP_ACCOUNT",
                "at": NOW.isoformat(),
                "inputs": {"external_ref": "mvp-301-v6:account:cash"},
            },
        ],
    }
    before = json.loads(physical_snapshot(engine))
    result = FullNativeCaseRunner(engine, DEMO_USER_ID).run(
        json.dumps(original, ensure_ascii=False).encode(), identity
    )
    assert result["status"] == "ACTUAL_SERVICE_OBSERVATIONS_NOT_ECONOMIC_ACCEPTANCE", result
    assert result["returned_step_count"] == result["original_step_count"] == 5
    assert result["steps"][0]["result"] == result["steps"][4]["result"]
    policy = UUID(result["steps"][2]["result"]["policy_id"])
    assert result["steps"][3]["result"]["policy_id"] == str(policy)
    assert result["steps"][3]["native_response"]["status_code"] == 200
    assert result["financial_acceptance"] is False and result["metric_results"] is None
    metadata = {
        "full_policies",
        "full_policy_versions",
        "full_policy_commands",
        "evidence_items",
        "decision_runs",
        "audit_events",
        "audit_subject_snapshots",
        "audit_heads",
        "audit_epochs",
        "intervention_outbox",
        "intervention_inbox",
    }
    after = json.loads(physical_snapshot(engine))
    assert {k: v for k, v in before.items() if k not in metadata} == {
        k: v for k, v in after.items() if k not in metadata
    }
    with Session(engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
