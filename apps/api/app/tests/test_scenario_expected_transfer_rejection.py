"""Actual original DTO rejection and continued read-only observation, not frozen T02."""

import hashlib
import json
from pathlib import Path

import pytest
from app.services.demo_seed import DEMO_USER_ID, SEED_VERSION, seed_demo
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import ScenarioStep
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_fixed_purchase_clock import NOW
from app.tests.test_scenario_service_steps import case, original_rows
from sqlalchemy.engine import Engine

from scripts.mvp_native_schema import SOURCES, NativeAdapter, canonical

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.integration
def test_missing_transfer_destination_is_actual_422_and_all24_tables_keep_originals(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    steps = [
        ScenarioStep(
            step_id="cash",
            kind="LOOKUP_ACCOUNT",
            at=NOW,
            inputs={"external_ref": SEED_VERSION + ":account:cash"},
        ),
        ScenarioStep.model_validate(
            {
                "step_id": "ambiguous",
                "kind": "PREPARE_ACTION",
                "at": NOW,
                "inputs": {
                    "idempotency_key": "DEVELOPMENT_missing_transfer_destination",
                    "intent": {
                        "kind": "transfer_internal",
                        "source_account_id": {
                            "$ref": {"step_id": "cash", "pointer": "/result/account/id"}
                        },
                        "destination_account_id": None,
                        "amount_cents": 47143,
                    },
                },
                "expected_error": {"code": "INVALID_SCENARIO_STEP", "status_code": 422},
            }
        ),
        ScenarioStep(step_id="after", kind="SNAPSHOT", at=NOW, inputs={}),
    ]
    scenario = case(demo_engine, steps, "DEVELOPMENT_negative_transfer_original_service")
    names = set(SOURCES) | {"scripts/mvp_native_schema.py", "scripts/mvp_corpus_v2.py"}
    inventory = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    registration = NativeAdapter(ROOT, inventory).validate_execution(
        canonical(json.loads(scenario.model_dump_json())),
        "DEVELOPMENT",
    )
    assert registration["status"] == "EXPECTED_DTO_REJECTION_PENDING"
    assert registration["expected_dto_rejections"][0]["runtime_rejection_observed"] is False
    before = original_rows(demo_engine)
    actual = ScenarioRunner(demo_engine, DEMO_USER_ID).run(scenario)
    assert actual.status == "EXECUTED" and len(actual.steps) == 3
    rejected = actual.steps[1]
    assert rejected["kind"] == "PREPARE_ACTION"
    assert rejected["error"]["code"] == "INVALID_SCENARIO_STEP"
    assert rejected["error"]["status_code"] == 422
    assert "destination_account_id" in rejected["error"]["message"]
    assert rejected["expected_error_matched"] is True and "result" not in rejected
    assert actual.steps[2]["kind"] == "SNAPSHOT" and "result" in actual.steps[2]
    assert original_rows(demo_engine) == before
    assert before["action_plans"] == before["action_receipts"] == []
    assert len(before) == 24
    assert registration["expected_dto_rejections"][0]["runtime_rejection_observed"] is False
