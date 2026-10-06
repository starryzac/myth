"""TOOL_TEST_ONLY synthetic files: parser/arithmetic checks, no financial effects."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts.mvp_observations import (
    BINDINGS,
    RAW_PROTOCOL,
    REG_PROTOCOL,
    RUN_PROTOCOL,
    Bundle,
    ObservationError,
    ledger_continuity,
    main,
    observe,
)

RUN = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
ACCOUNT = "33333333-3333-4333-8333-333333333333"
OPENING = "44444444-4444-4444-8444-444444444444"
ENTRY = "55555555-5555-4555-8555-555555555555"
GOAL = "66666666-6666-4666-8666-666666666666"
EPOCH = "77777777-7777-4777-8777-777777777777"
WHEN = "2026-10-05T00:00:00+00:00"


@pytest.fixture
def tmp_path() -> Path:
    """Fresh inherited-ACL workspace originals; retain TOOL_TEST_ONLY fixtures.

    Windows pytest's private 0700 temporary directory is inaccessible to this
    restricted session. These generated paths are never reused or deleted.
    """
    base = Path(__file__).resolve().parents[2] / ".runtime" / "W1-observer-tool-fixtures"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir(exist_ok=False)
    return path


def write(path: Path, value: Any) -> str:
    content = json.dumps(value, allow_nan=False, sort_keys=True).encode()
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def posting(*, sequence: int = 1) -> dict[str, Any]:
    return {
        "id": OPENING if sequence == 1 else ENTRY,
        "created_at": WHEN,
        "user_id": USER,
        "ledger_key": f"CASH:{ACCOUNT}",
        "ledger_dimension": "ECONOMIC",
        "ledger_metadata": {"provenance": "TOOL_TEST_ONLY"},
        "account_id": ACCOUNT,
        "position_id": None,
        "redemption_id": None,
        "operation_id": None,
        "external_fact_id": None if sequence == 1 else RUN,
        "leg_ref": None if sequence == 1 else "TOOL_TEST_ONLY",
        "previous_posting_id": None if sequence == 1 else OPENING,
        "sequence_number": sequence,
        "entry_kind": "OPENING" if sequence == 1 else "EXTERNAL_CREDIT",
        "balance_before_cents": 0 if sequence == 1 else 100,
        "delta_cents": 100 if sequence == 1 else 20,
        "balance_after_cents": 100 if sequence == 1 else 120,
        "occurred_at": WHEN,
    }


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        (root / "archived-source.py").write_text(
            "# TOOL_TEST_ONLY; never imported or executed\n", encoding="utf-8"
        )
        self.registrations: dict[str, dict[str, Any]] = {}
        for name in ("input", "oracle", "design", "rule", "source"):
            self.registrations[name] = {
                "protocol": REG_PROTOCOL,
                "kind": name.upper(),
                "case_id": "tool-case-only",
                "purpose": "TOOL_TEST_ONLY",
                "registration_status": "TOOL_TEST_ONLY",
            }
        self.registrations["input"]["review"] = {"fixture_note": "TOOL_TEST_ONLY"}
        self.registrations["rule"]["arm_id"] = "B0"
        self.registrations["source"]["files"] = [
            {
                "path": "archived-source.py",
                "sha256": hashlib.sha256((root / "archived-source.py").read_bytes()).hexdigest(),
            }
        ]
        self.registrations["oracle"].update(
            actor_event_ids=["actor-1"],
            decision_opportunity_ids=["decision-1"],
            recovery_opportunity_ids=["recovery-1"],
            safe_auto_opportunity_ids=[],
            due_checkpoint_ids=[],
            audit_checkpoint_ids=[],
            failure_opportunity_ids=[],
        )
        self.manifest: dict[str, Any] = {
            "protocol": RUN_PROTOCOL,
            "purpose": "TOOL_TEST_ONLY",
            "experiment_run_id": RUN,
            "case_id": "tool-case-only",
            "arm_id": "B0",
            "execution_mode": "TOOL_TEST_ONLY",
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": EPOCH,
            "user_id": USER,
            "run_status": "COMPLETE",
        }
        step_ref = {"artifact_sha256": "STEP_PLACEHOLDER", "step_id": "step-1"}
        self.payloads: dict[str, tuple[str, dict[str, Any]]] = {
            "steps": (
                "STEP_LOG",
                {
                    "steps": [
                        {
                            "step_id": "step-1",
                            "result": {
                                "fixture_note": "TOOL_TEST_ONLY, no effect or success claim"
                            },
                        }
                    ]
                },
            ),
            "actors": (
                "ACTOR_LOG",
                {
                    "capture_status": "COMPLETE",
                    "event_manifest": ["actor-1"],
                    "events": [
                        {
                            "event_id": "actor-1",
                            "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
                            "event_type": "MANUAL_CHOICE",
                            "phase": "RUNTIME_INTERVENTION",
                            "step_id": "step-1",
                            "raw_step_ref": step_ref.copy(),
                            "occurred_at": WHEN,
                        }
                    ],
                },
            ),
            "timing": (
                "TIMING_LOG",
                {
                    "clock_id": "tool-monotonic-clock-only",
                    "clock_semantics": "perf_counter_ns",
                    "capture_status": "COMPLETE",
                    "decision_samples": [
                        {
                            "opportunity_id": "decision-1",
                            "step_id": "step-1",
                            "raw_step_ref": step_ref.copy(),
                            "clock_id": "tool-monotonic-clock-only",
                            "start_ns": 10,
                            "end_ns": 30,
                        }
                    ],
                    "recovery_samples": [
                        {
                            "opportunity_id": "recovery-1",
                            "step_id": "step-1",
                            "raw_step_ref": step_ref.copy(),
                            "clock_id": "tool-monotonic-clock-only",
                            "trigger_ns": 100,
                            "observed_until_ns": 150,
                            "settled_projected_ns": None,
                            "censored": True,
                        }
                    ],
                },
            ),
        }
        for name, phase in (("before", "BEFORE"), ("after", "AFTER")):
            self.payloads[name] = (
                "BANK_SNAPSHOT",
                {
                    "checkpoint_id": "tool-checkpoint",
                    "phase": phase,
                    "captured_at": WHEN,
                    "tables": {
                        "accounts": [
                            {
                                "id": ACCOUNT,
                                "user_id": USER,
                                "account_type": "CASH",
                                "balance_cents": 100 if phase == "BEFORE" else 120,
                            }
                        ],
                        "goals": [
                            {
                                "id": GOAL,
                                "user_id": USER,
                                "allocated_cents": 0 if phase == "BEFORE" else 10,
                            }
                        ],
                        "simulated_bank_postings": [posting()]
                        + ([] if phase == "BEFORE" else [posting(sequence=2)]),
                    },
                },
            )
        self.save()

    def save(self) -> Path:
        refs = {}
        for name, original in self.registrations.items():
            digest = write(self.root / f"{name}.json", original)
            refs[name] = {"path": f"{name}.json", "sha256": digest}
            self.manifest[f"{name}_sha256"] = digest
        self.manifest["artifact_refs"] = refs
        bindings = {key: self.manifest[key] for key in BINDINGS}
        raw_refs = []
        step_digest = None
        for name, (kind, original_payload) in self.payloads.items():
            payload = copy.deepcopy(original_payload)

            def substitute(value: Any, step_sha: str | None = step_digest) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if item == "STEP_PLACEHOLDER":
                            value[key] = step_sha
                        elif item == "INPUT_PLACEHOLDER":
                            value[key] = refs["input"]["sha256"]
                        else:
                            substitute(item)
                elif isinstance(value, list):
                    for item in value:
                        substitute(item)

            substitute(payload)
            original = {
                "protocol": RAW_PROTOCOL,
                "kind": kind,
                "bindings": bindings,
                "payload": payload,
            }
            digest = write(self.root / f"{name}.json", original)
            if name == "steps":
                step_digest = digest
            raw_refs.append({"path": f"{name}.json", "sha256": digest, "kind": kind})
        self.manifest["raw_refs"] = raw_refs
        write(self.root / "run.json", self.manifest)
        return self.root / "run.json"


def test_partial_tool_observations_are_bound_and_never_financial_effect_evidence(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    result = observe(tmp_path / "run.json")
    assert result["financial_effect_evidence"] is False
    assert result["bindings"]["purpose"] == "TOOL_TEST_ONLY"
    assert result["metrics"]["E2"]["value"] == {
        "INITIAL_AUTHORIZATION": 0,
        "RUNTIME_INTERVENTION": 1,
    }
    timing = result["metrics"]["E5"]
    assert timing["value"]["decision"][0]["elapsed_ns"] == 20
    assert timing["value"]["recovery"][0]["elapsed_ns"] is None
    assert timing["value"]["recovery"][0]["lower_bound_ns"] == 50
    assert timing["denominator"] == {"decision": 1, "recovery": 1}
    for identity, metric in result["metrics"].items():
        if identity not in {"E2", "E5"}:
            assert metric["status"] == "MISSING" and metric["value"] is None
    checkpoint = result["bank_observations"]["checkpoints"][0]
    assert checkpoint["ledger_after"]["status"] == "VERIFIED"
    assert checkpoint["accounts_changes"]["value"][0]["delta_cents"] == 20
    assert checkpoint["goals_changes"]["value"][0]["delta_cents"] == 10
    assert checkpoint["original_postings_preserved"]["value"] is True
    assert fixture.manifest["execution_mode"] == "TOOL_TEST_ONLY"


@pytest.mark.parametrize(
    "binding,value",
    [
        ("case_id", "another-case"),
        ("arm_id", "P"),
        ("purpose", "MVP_FROZEN"),
        ("source_sha256", "a" * 64),
        ("experiment_run_id", EPOCH),
        ("isolated_db_epoch", RUN),
    ],
)
def test_raw_run_case_arm_purpose_source_epoch_rebinding_is_rejected(
    tmp_path: Path, binding: str, value: str
) -> None:
    fixture = Fixture(tmp_path)
    path = tmp_path / "actors.json"
    original = json.loads(path.read_bytes())
    original["bindings"][binding] = value
    digest = write(path, original)
    next(ref for ref in fixture.manifest["raw_refs"] if ref["path"] == "actors.json")["sha256"] = (
        digest
    )
    write(tmp_path / "run.json", fixture.manifest)
    with pytest.raises(ObservationError, match="binding differs"):
        observe(tmp_path / "run.json")


def test_source_hash_requires_actual_archived_bytes(tmp_path: Path) -> None:
    Fixture(tmp_path)
    (tmp_path / "archived-source.py").write_text(
        "# changed TOOL_TEST_ONLY source", encoding="utf-8"
    )
    with pytest.raises(ObservationError, match="registered digest"):
        observe(tmp_path / "run.json")


def test_original_paths_cannot_escape_run_directory(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.registrations["source"]["files"][0]["path"] = "../outside-source.py"
    fixture.save()
    with pytest.raises(ObservationError, match="escapes"):
        observe(tmp_path / "run.json")


def test_mismatched_original_hash_and_duplicate_json_are_rejected(tmp_path: Path) -> None:
    Fixture(tmp_path)
    (tmp_path / "actors.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ObservationError, match="registered digest"):
        observe(tmp_path / "run.json")
    (tmp_path / "run.json").write_text('{"protocol":"x","protocol":"y"}', encoding="utf-8")
    with pytest.raises(ObservationError, match="Duplicate JSON key"):
        observe(tmp_path / "run.json")


def test_current_invocation_detects_original_changed_after_read(tmp_path: Path) -> None:
    Fixture(tmp_path)
    bundle = Bundle(tmp_path / "run.json")
    (tmp_path / "archived-source.py").write_text("# changed TOOL_TEST_ONLY", encoding="utf-8")
    with pytest.raises(ObservationError, match="changed during"):
        bundle.unchanged()


@pytest.mark.parametrize(
    "field,value",
    [
        ("delta_cents", True),
        ("balance_after_cents", 100.0),
        ("balance_before_cents", "0"),
        ("sequence_number", 2),
    ],
)
def test_integer_ledger_rejects_nonintegers_missing_opening_and_gaps(
    field: str, value: Any
) -> None:
    row = posting()
    row[field] = value
    result = ledger_continuity([row], USER)
    assert result["status"] == "UNKNOWN" and result["value"] is None


def test_new_posting_cannot_rebind_account_or_predecessor() -> None:
    entry = posting(sequence=2)
    entry["account_id"] = GOAL
    assert ledger_continuity([posting(), entry], USER)["status"] == "UNKNOWN"
    entry = posting(sequence=2)
    entry["previous_posting_id"] = GOAL
    assert ledger_continuity([posting(), entry], USER)["status"] == "UNKNOWN"


def test_original_posting_column_changes_stay_visible(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.payloads["after"][1]["tables"]["simulated_bank_postings"][0]["created_at"] = (
        "2026-10-04T23:59:59+00:00"
    )
    fixture.save()
    result = observe(tmp_path / "run.json")
    preservation = result["bank_observations"]["checkpoints"][0]["original_postings_preserved"]
    assert preservation["status"] == "UNKNOWN"
    assert preservation["changed_or_missing_ids"] == [OPENING]


def test_missing_bank_tables_are_not_zero_observations(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    del fixture.payloads["after"][1]["tables"]["goals"]
    del fixture.payloads["after"][1]["tables"]["simulated_bank_postings"]
    fixture.save()
    result = observe(tmp_path / "run.json")
    checkpoint = result["bank_observations"]["checkpoints"][0]
    assert checkpoint["goals_changes"]["status"] == "MISSING"
    assert checkpoint["goals_changes"]["value"] is None
    assert checkpoint["ledger_after"]["value"] is None
    assert result["metrics"]["S1"]["value"] is None


def test_duplicate_actor_records_or_missing_original_step_are_missing(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    events = fixture.payloads["actors"][1]["events"]
    events.append(copy.deepcopy(events[0]))
    fixture.save()
    result = observe(tmp_path / "run.json")["metrics"]["E2"]
    assert result["status"] == "MISSING" and result["value"] is None
    assert result["denominator"] == 1 and result["applicable_units"] == ["actor-1"]
    events.pop()
    events[0]["step_id"] = "missing-step"
    fixture.save()
    assert observe(tmp_path / "run.json")["metrics"]["E2"]["status"] == "MISSING"


def test_confirmation_requires_existing_original_review_pointer(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    event = fixture.payloads["actors"][1]["events"][0]
    event["event_type"] = "AFFIRMATIVE_CONFIRMATION"
    event["exact_review_ref"] = {
        "artifact_sha256": "INPUT_PLACEHOLDER",
        "json_pointer": "/missing-review",
    }
    fixture.save()
    assert observe(tmp_path / "run.json")["metrics"]["E2"]["status"] == "MISSING"
    event["exact_review_ref"]["json_pointer"] = "/review"
    fixture.save()
    assert observe(tmp_path / "run.json")["metrics"]["E2"]["status"] == "MEASURED"


@pytest.mark.parametrize("change", ["clock", "omitted", "backwards", "success_without_bank"])
def test_timing_missing_denominator_and_partial_observations_are_preserved(
    tmp_path: Path, change: str
) -> None:
    fixture = Fixture(tmp_path)
    timing = fixture.payloads["timing"][1]
    if change == "clock":
        timing["clock_semantics"] = "simulation_datetime"
    elif change == "omitted":
        timing["recovery_samples"] = []
    elif change == "backwards":
        timing["decision_samples"][0]["end_ns"] = 1
    else:
        recovery = timing["recovery_samples"][0]
        recovery["censored"] = False
        recovery["settled_projected_ns"] = 130
    fixture.save()
    result = observe(tmp_path / "run.json")["metrics"]["E5"]
    assert result["status"] == "MISSING" and result["value"] is None
    assert result["denominator"] == {"decision": 1, "recovery": 1}
    assert result["applicable_units"] == ["decision-1", "recovery-1"]
    if change in {"omitted", "success_without_bank"}:
        assert result["partial_observations"]["decision"][0]["elapsed_ns"] == 20


def test_complete_timing_requires_bound_original_integer_bank_legs_and_receipt(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    tables = fixture.payloads["after"][1]["tables"]
    tables["action_plans"] = [{"id": RUN, "user_id": USER, "status": "RECONCILED"}]
    tables["bank_operations"] = [
        {
            "id": EPOCH,
            "user_id": USER,
            "action_plan_id": RUN,
            "operation_type": "REDEEM_ASSET",
            "status": "SETTLED",
            "available_at": WHEN,
            "settled_at": WHEN,
        }
    ]
    position_open = posting()
    position_open.update(
        id="88888888-8888-4888-8888-888888888888",
        ledger_key=f"POSITION:{GOAL}",
        account_id=None,
        position_id=GOAL,
    )
    position_debit = posting(sequence=2)
    position_debit.update(
        id="99999999-9999-4999-8999-999999999999",
        ledger_key=f"POSITION:{GOAL}",
        account_id=None,
        position_id=GOAL,
        previous_posting_id=position_open["id"],
        operation_id=EPOCH,
        external_fact_id=None,
        delta_cents=-20,
        balance_after_cents=80,
    )
    cash_credit = tables["simulated_bank_postings"][1]
    cash_credit.update(operation_id=EPOCH, external_fact_id=None)
    tables["simulated_bank_postings"] += [position_open, position_debit]
    tables["action_receipts"] = [
        {
            "id": GOAL,
            "user_id": USER,
            "action_plan_id": RUN,
            "status": "SUCCEEDED",
            "executed_cents": 20,
            "fee_cents": 0,
            "loss_cents": 0,
            "occurred_at": WHEN,
            "reconciled_at": WHEN,
            "response": {"bank_operation_id": EPOCH, "posting_ids": [ENTRY, position_debit["id"]]},
        }
    ]
    recovery = fixture.payloads["timing"][1]["recovery_samples"][0]
    recovery.update(censored=False, settled_projected_ns=130)
    fixture.save()
    recovery["completion_ref"] = {
        "artifact_sha256": hashlib.sha256((tmp_path / "after.json").read_bytes()).hexdigest(),
        "action_id": RUN,
    }
    fixture.save()
    result = observe(tmp_path / "run.json")
    assert result["financial_effect_evidence"] is False
    timing = result["metrics"]["E5"]
    assert timing["status"] == "MEASURED", timing["missing_reason"]
    assert timing["value"]["recovery"][0]["elapsed_ns"] == 30
    # A producer success string plus a lost actual debit leg cannot prove completion.
    tables["simulated_bank_postings"].pop()
    fixture.save()
    recovery["completion_ref"]["artifact_sha256"] = hashlib.sha256(
        (tmp_path / "after.json").read_bytes()
    ).hexdigest()
    fixture.save()
    assert observe(tmp_path / "run.json")["metrics"]["E5"]["status"] == "MISSING"


def test_empty_and_unrun_inputs_do_not_create_zero_safety_or_full_rates(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.registrations["oracle"].update(
        actor_event_ids=[], decision_opportunity_ids=[], recovery_opportunity_ids=[]
    )
    fixture.payloads["actors"][1].update(event_manifest=[], events=[])
    fixture.payloads["timing"][1].update(decision_samples=[], recovery_samples=[])
    fixture.save()
    result = observe(tmp_path / "run.json")
    assert (
        result["metrics"]["E2"]["status"] == result["metrics"]["E5"]["status"] == "NOT_APPLICABLE"
    )
    assert all(row["value"] is None for row in result["metrics"].values())
    fixture.manifest["run_status"] = "NOT_IMPLEMENTED"
    fixture.save()
    result = observe(tmp_path / "run.json")
    assert all(
        row["status"] == "NOT_RUN" and row["value"] is None for row in result["metrics"].values()
    )


def test_export_is_exclusive_and_does_not_change_originals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Fixture(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.iterdir()}
    output = tmp_path / "observed.json"
    assert main(["--run", str(tmp_path / "run.json"), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["financial_effect_evidence"] is False
    assert all(path.read_bytes() == content for path, content in originals.items())
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main(["--run", str(tmp_path / "run.json"), "--output", str(output)])
    assert output.read_bytes() == before
    assert "PARTIAL_OBSERVATIONS_ONLY" in capsys.readouterr().out
