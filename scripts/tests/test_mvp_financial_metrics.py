"""Pure TOOL_TEST_ONLY originals: parser/arithmetic evidence, never financial effects."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import pytest

from scripts import mvp_financial_metrics as metrics
from scripts.mvp_financial_oracles import TABLES
from scripts.mvp_observations import (
    BINDINGS,
    RAW_PROTOCOL,
    REG_PROTOCOL,
    RUN_PROTOCOL,
    ObservationError,
)
from scripts.mvp_trace_metrics import EFFECT_FIELDS, Missing, digest_value
from scripts.tests.test_mvp_financial_oracles import checkpoint, complete_fixture

ROOT = Path(__file__).resolve().parents[2]
USER = "22222222-2222-4222-8222-222222222222"
RUN = "11111111-1111-4111-8111-111111111111"
EPOCH = "77777777-7777-4777-8777-777777777777"
SOURCE = "33333333-3333-4333-8333-333333333333"
DESTINATION = "44444444-4444-4444-8444-444444444444"
ACTION = "55555555-5555-4555-8555-555555555555"
WHEN = "2026-10-05T00:00:00+00:00"
END = "2026-10-06T00:00:00+00:00"
POLICY = "88888888-8888-4888-8888-888888888888"
VERSION = "99999999-9999-4999-8999-999999999999"


@pytest.fixture
def tmp_path() -> Path:
    base = ROOT / ".runtime/W1-financial-metrics-tool-fixtures"
    base.mkdir(parents=True, exist_ok=True)
    target = base / uuid4().hex
    target.mkdir(exist_ok=False)
    return target


def write(path: Path, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def entry(account: str, *, opening: bool, before: int, delta: int, leg: str) -> dict[str, Any]:
    return {
        "id": str(
            uuid5(UUID(account if opening else ACTION), "opening" if opening else "posting:" + leg)
        ),
        "created_at": WHEN,
        "user_id": USER,
        "ledger_key": "CASH:" + account,
        "ledger_dimension": "ECONOMIC",
        "ledger_metadata": {"purpose": "TOOL_TEST_ONLY"},
        "account_id": account,
        "position_id": None,
        "redemption_id": None,
        "operation_id": None if opening else ACTION,
        "external_fact_id": None,
        "leg_ref": None if opening else leg,
        "previous_posting_id": None if opening else str(uuid5(UUID(account), "opening")),
        "sequence_number": 1 if opening else 2,
        "entry_kind": "OPENING" if opening else "TRANSFER",
        "balance_before_cents": before,
        "delta_cents": delta,
        "balance_after_cents": before + delta,
        "occurred_at": WHEN,
    }


class Fixture:
    def __init__(self, root: Path, *, consent: bool = True, transfer: bool = True):
        self.root = root
        self.sources = []
        for index, original in enumerate(
            (
                "mvp_financial_metrics",
                "mvp_financial_oracles",
                "mvp_observations",
                "mvp_trace_metrics",
            )
        ):
            original_path = "scripts/" + original + ".py"
            content = (ROOT / original_path).read_bytes()
            path = f"source-{index}.bin"
            (root / path).write_bytes(content)
            self.sources.append(
                {
                    "original_path": original_path,
                    "path": path,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        self.registration: dict[str, Any] = {
            "protocol": metrics.REGISTRATION,
            "calculator_sources": self.sources,
            "safe_auto_opportunity_ids": [],
            "version_consumption_ids": [],
            "protection_checkpoints": [],
            "due_checkpoints": [],
            "deployment_checkpoints": [],
            "ask_requirements": [],
        }
        self.manifest: dict[str, Any] = {
            "protocol": RUN_PROTOCOL,
            "purpose": "TOOL_TEST_ONLY",
            "experiment_run_id": RUN,
            "case_id": "financial-parser-tool-only",
            "arm_id": "B0",
            "execution_mode": "TOOL_TEST_ONLY",
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": EPOCH,
            "user_id": USER,
            "run_status": "COMPLETE",
        }
        effect: dict[str, Any] = dict.fromkeys(EFFECT_FIELDS)
        effect.update(
            simulation=True,
            operation_id=ACTION,
            user_id=USER,
            business_key="tool-only-transfer",
            action_type="TRANSFER_INTERNAL",
            amount_cents=100,
            cash_uses=[{"account_id": SOURCE, "amount_cents": 100}],
            income_uses=[],
            policy_version_ids=[],
            fee_cents=0,
            loss_cents=0,
            settlement_delay_days=0,
            destination_account_id=DESTINATION,
            valid_from=WHEN,
            expires_at=END,
        )
        self.effect = effect
        command: dict[str, Any] = {"effect": effect, "effect_hash": digest_value(effect)}
        confirmation_id = str(uuid5(UUID(ACTION), "confirmation:" + command["effect_hash"]))
        request = {"execution": command, "confirmation_evidence_id": confirmation_id}
        self.action: dict[str, Any] = {
            "id": ACTION,
            "user_id": USER,
            "action_type": "TRANSFER_INTERNAL",
            "amount_cents": 100,
            "request": request,
            "request_hash": digest_value(request),
            "idempotency_key": "tool-only-idempotency",
            "status": "SUCCEEDED",
            "authorized_at": WHEN,
            "created_at": WHEN,
        }
        consent_content = {
            "simulation": True,
            "user_id": USER,
            "action_id": ACTION,
            "effect_hash": command["effect_hash"],
            "accepted": True,
            "confirmed_at": WHEN,
            "valid_until": END,
        }
        self.consent: dict[str, Any] = {
            "id": confirmation_id,
            "user_id": USER,
            "source_type": "USER_ACTION_CONFIRMATION",
            "source_ref": ACTION,
            "evidence_level": "USER_CONFIRMED_ACTION",
            "status": "VALID",
            "content": consent_content,
            "content_hash": digest_value(consent_content),
            "observed_at": WHEN,
            "valid_from": WHEN,
            "valid_to": END,
            "created_at": WHEN,
        }
        openings = [
            entry(SOURCE, opening=True, before=0, delta=1000, leg=""),
            entry(DESTINATION, opening=True, before=0, delta=0, leg=""),
        ]
        legs = [
            entry(SOURCE, opening=False, before=1000, delta=-100, leg="cash-out"),
            entry(DESTINATION, opening=False, before=0, delta=100, leg="cash-in"),
        ]
        self.bank: dict[str, Any] = {
            "id": ACTION,
            "user_id": USER,
            "action_plan_id": ACTION,
            "operation_type": "TRANSFER_INTERNAL",
            "business_key": effect["business_key"],
            "amount_cents": 100,
            "idempotency_key": self.action["idempotency_key"],
            "request": copy.deepcopy(command),
            "request_hash": digest_value(command),
            "requested_at": WHEN,
            "available_at": WHEN,
            "settled_at": WHEN,
            "status": "SETTLED",
        }
        self.receipt: dict[str, Any] = {
            "id": str(uuid5(UUID(ACTION), "receipt")),
            "user_id": USER,
            "action_plan_id": ACTION,
            "status": "SUCCEEDED",
            "executed_cents": 100,
            "fee_cents": 0,
            "loss_cents": 0,
            "occurred_at": WHEN,
            "response": {"bank_operation_id": ACTION, "posting_ids": [leg["id"] for leg in legs]},
        }
        self.before: dict[str, list[Any]] = {table: [] for table in TABLES}
        self.before["accounts"] = [
            {"id": SOURCE, "user_id": USER, "account_type": "CASH", "balance_cents": 1000},
            {"id": DESTINATION, "user_id": USER, "account_type": "CASH", "balance_cents": 0},
        ]
        self.before["simulated_bank_postings"] = openings
        if transfer:
            self.before["action_plans"] = [self.action]
            if consent:
                self.before["evidence_items"] = [self.consent]
        self.after = copy.deepcopy(self.before)
        if transfer:
            self.after["bank_operations"] = [self.bank]
            self.after["action_receipts"] = [self.receipt]
            self.after["simulated_bank_postings"] += legs
            self.after["accounts"][0]["balance_cents"] = 900
            self.after["accounts"][1]["balance_cents"] = 100
        self.actions: list[dict[str, Any]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.consumptions: list[dict[str, Any]] = []
        self.actors: list[dict[str, Any]] = []
        self.asks: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.input_requirements: list[dict[str, Any]] = []
        self.policy_events: list[dict[str, Any]] = []
        self.include_actor = self.include_ask = True
        self.capture: dict[str, Any] = {
            "complete": True,
            "action_ids": [ACTION] if transfer else [],
            "version_bank_operation_ids": [],
            "version_decision_run_ids": [],
        }
        self.transfer = transfer

    def raw(self, name: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = name + ".json"
        original = {
            "protocol": RAW_PROTOCOL,
            "kind": kind,
            "bindings": self.bindings,
            "payload": payload,
        }
        digest = write(self.root / path, original)
        self.raws.append({"path": path, "sha256": digest, "kind": kind})
        return {
            "artifact_sha256": digest,
            "json_pointer": "/payload/facts",
            "value_sha256": digest_value(payload.get("facts")),
        }

    def facts(self, name: str, tables: dict[str, list[Any]]) -> dict[str, Any]:
        basis = {
            "protocol": RAW_PROTOCOL,
            "kind": "FINANCIAL_BASIS",
            "bindings": self.bindings,
            "payload": {"tables": tables, "events": self.policy_events},
        }
        content = json.dumps(basis, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256(content.encode()).hexdigest()
        if not any(raw["sha256"] == digest for raw in self.raws):
            path = "basis-" + digest + ".json"
            (self.root / path).write_bytes(content.encode())
            self.raws.append({"path": path, "sha256": digest, "kind": "FINANCIAL_BASIS"})
        inventory = {
            table: {
                "row_ids": sorted(row["id"] for row in rows),
                "sha256": digest_value(sorted(rows, key=lambda row: row["id"])),
                "source_ref": {
                    "artifact_sha256": digest,
                    "json_pointer": "/payload/tables/" + table,
                    "value_sha256": digest_value(rows),
                },
            }
            for table, rows in tables.items()
        }
        value = {
            "protocol": "mvp-financial-facts-v1",
            "user_id": USER,
            "timezone": "Asia/Shanghai",
            "as_of": WHEN,
            "complete": True,
            "tables": tables,
            "policy_state_events": [
                {
                    **event,
                    "source_ref": {
                        "artifact_sha256": digest,
                        "json_pointer": "/payload/events/" + str(index),
                        "value_sha256": digest_value(event),
                    },
                }
                for index, event in enumerate(self.policy_events)
            ],
            "policy_state_events_source_ref": {
                "artifact_sha256": digest,
                "json_pointer": "/payload/events",
                "value_sha256": digest_value(self.policy_events),
            },
            "inventory": inventory,
            "artifact_originals": {digest: {"utf8": content}},
            "expense_history": {"protocol": "mvp-expense-coverage-v1", "complete": False},
            "income_payload": {"protocol": "new-funds-ledger-v2", "complete": False},
        }
        return self.raw(name, "FINANCIAL_FACTS", {"facts": value, "capture_identity": name})

    def emit(self) -> Path:
        registrations = {}
        for name in ("input", "oracle", "design", "rule", "source"):
            original: dict[str, Any] = {
                "protocol": REG_PROTOCOL,
                "kind": name.upper(),
                "case_id": self.manifest["case_id"],
                "purpose": "TOOL_TEST_ONLY",
                "registration_status": "TOOL_TEST_ONLY",
            }
            if name == "oracle":
                original["financial_metrics"] = self.registration
            if name == "input":
                original["confirmation_requirements"] = self.input_requirements
            if name == "source":
                original["files"] = [
                    {"path": ref["path"], "sha256": ref["sha256"]} for ref in self.sources
                ]
            if name == "rule":
                original["arm_id"] = "B0"
            path = name + ".json"
            digest = write(self.root / path, original)
            registrations[name] = {"path": path, "sha256": digest}
            self.manifest[name + "_sha256"] = digest
        self.manifest["artifact_refs"] = registrations
        self.bindings = {key: self.manifest[key] for key in BINDINGS}
        self.raws: list[dict[str, Any]] = []
        before = self.facts("before", self.before)
        after = self.facts("after", self.after)
        points = copy.deepcopy(self.checkpoints)
        for point in points:
            if point.get("facts_ref") == "AFTER":
                point["facts_ref"] = after
        consumptions = copy.deepcopy(self.consumptions)
        for record in consumptions:
            if record.get("facts_ref") == "AFTER":
                record["facts_ref"] = after
        actions = copy.deepcopy(self.actions)
        if self.transfer:
            actions.insert(
                0,
                {
                    "action_id": ACTION,
                    "opportunity_id": "transfer-only",
                    "before_facts_ref": before,
                    "after_facts_ref": after,
                },
            )
        capture = {**self.capture, "run_ref": self.bindings}
        self.raw(
            "observations",
            "FINANCIAL_OBSERVATIONS",
            {
                "final_facts_ref": after,
                "capture": capture,
                "actions": actions,
                "opportunities": self.opportunities,
                "checkpoints": points,
                "version_consumptions": consumptions,
            },
        )
        if self.include_actor:
            self.raw(
                "actors",
                "ACTOR_LOG",
                {
                    "capture_status": "COMPLETE",
                    "event_manifest": [row["event_id"] for row in self.actors],
                    "events": self.actors,
                },
            )
        if self.include_ask:
            asks = copy.deepcopy(self.asks)
            if self.questions:
                self.raw("questions", "QUESTIONS", {"questions": self.questions})
                for index, event in enumerate(asks):
                    event["question_ref"] = {
                        "artifact_sha256": self.raws[-1]["sha256"],
                        "json_pointer": "/payload/questions/" + str(index),
                        "value_sha256": digest_value(self.questions[index]),
                    }
            self.raw(
                "asks",
                "ASK_LOG",
                {
                    "capture_status": "COMPLETE",
                    "event_manifest": [row["ask_event_id"] for row in self.asks],
                    "events": asks,
                },
            )
        self.manifest["raw_refs"] = self.raws
        write(self.root / "run.json", self.manifest)
        return self.root / "run.json"


def test_bound_transfer_requires_exact_consent(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    result = metrics.observe(fixture.emit())
    assert result["financial_effect_evidence"] is False
    assert result["metrics"]["S2"]["status"] == "MEASURED"
    assert result["metrics"]["S2"]["value"] == 0
    assert result["metrics"]["S2"]["denominator"] == 1
    assert result["metrics"]["S1"]["status"] == "MISSING"
    assert result["metrics"]["E1"]["status"] == "NOT_APPLICABLE"


def test_actual_transfer_without_consent_is_unauthorized(tmp_path: Path) -> None:
    result = metrics.observe(Fixture(tmp_path, consent=False).emit())
    assert result["metrics"]["S2"]["value"] == 1


@pytest.mark.parametrize(
    "fault", ["receipt", "bank_hash", "bank_type", "bank_time", "unknown", "old_history"]
)
def test_actual_original_fault_is_missing_never_safe_zero(tmp_path: Path, fault: str) -> None:
    fixture = Fixture(tmp_path)
    if fault == "receipt":
        fixture.receipt["response"]["posting_ids"].pop()
    elif fault == "bank_hash":
        fixture.bank["request_hash"] = "a" * 64
    elif fault == "bank_type":
        fixture.bank["operation_type"] = "PURCHASE_ASSET"
    elif fault == "bank_time":
        fixture.bank["requested_at"] = END
    elif fault == "unknown":
        fixture.bank["status"] = "UNKNOWN"
    else:
        fixture.after["simulated_bank_postings"][0]["ledger_metadata"] = {"changed": True}
    metric = metrics.observe(fixture.emit())["metrics"]["S2"]
    assert metric["status"] == "MISSING"
    assert metric["value"] is None and metric["denominator"] == 1


def test_conserved_wrong_amount_is_not_success(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    for row in fixture.after["simulated_bank_postings"][2:]:
        row["delta_cents"] *= 2
        row["balance_after_cents"] = row["balance_before_cents"] + row["delta_cents"]
    result = metrics.observe(fixture.emit())["metrics"]["S2"]
    assert result["status"] == "MISSING" and "full original effect" in result["missing_reason"]


def test_missing_registered_opportunity_retains_denominator(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, transfer=False)
    fixture.registration["safe_auto_opportunity_ids"] = ["op-1", "op-2"]
    metric = metrics.observe(fixture.emit())["metrics"]["E1"]
    assert metric["status"] == "MISSING" and metric["denominator"] == 2
    assert metric["value"] is None and metric["numerator"] == 0


def test_absent_question_capture_is_missing_even_empty(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, transfer=False)
    fixture.include_ask = False
    result = metrics.observe(fixture.emit())["metrics"]["E3"]
    assert result["status"] == "MISSING" and result["value"] is None


def test_explicit_complete_zero_denominators_are_not_applicable(tmp_path: Path) -> None:
    result = metrics.observe(Fixture(tmp_path, transfer=False).emit())
    assert all(metric["status"] == "NOT_APPLICABLE" for metric in result["metrics"].values())
    assert all(metric["value"] is None for metric in result["metrics"].values())


def test_actual_action_missing_record_cannot_disappear(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    run = fixture.emit()
    path = tmp_path / "observations.json"
    body = json.loads(path.read_text())
    body["payload"]["actions"] = []
    digest = write(path, body)
    next(ref for ref in fixture.manifest["raw_refs"] if ref["kind"] == "FINANCIAL_OBSERVATIONS")[
        "sha256"
    ] = digest
    write(run, fixture.manifest)
    metric = metrics.observe(run)["metrics"]["S2"]
    assert metric["status"] == "MISSING" and metric["denominator"] == 1


def test_incomplete_capture_prevents_empty_success(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, transfer=False)
    fixture.capture["complete"] = False
    result = metrics.observe(fixture.emit())
    assert result["metrics"]["S2"]["status"] == "MISSING"
    assert result["metrics"]["S5"]["status"] == "MISSING"


def test_source_archive_must_match_current_calculator(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    raw = b"# TOOL_TEST_ONLY outdated calculator\n"
    (tmp_path / fixture.sources[0]["path"]).write_bytes(raw)
    fixture.sources[0]["sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ObservationError, match="source differs"):
        metrics.observe(fixture.emit())


def test_not_run_does_not_output_zero_success(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.manifest["run_status"] = "NOT_RUN"
    result = metrics.observe(fixture.emit())
    assert all(
        metric["status"] == "NOT_RUN" and metric["value"] is None and metric["numerator"] is None
        for metric in result["metrics"].values()
    )


def test_exclusive_output_preserves_existing_bytes(tmp_path: Path) -> None:
    run = Fixture(tmp_path).emit()
    target = tmp_path / "result.json"
    target.write_bytes(b"ORIGINAL_FAILURE_EVIDENCE")
    with pytest.raises(SystemExit):
        metrics.main(["--run", str(run), "--output", str(target)])
    assert target.read_bytes() == b"ORIGINAL_FAILURE_EVIDENCE"


def policy_facts() -> dict[str, Any]:
    config = {"type": "emergency_buffer", "version": 1, "amount_cents": 10}
    confirmation = {
        "accepted": True,
        "user_id": USER,
        "policy_id": POLICY,
        "version_id": VERSION,
        "confirmed_at": WHEN,
        "reviewed_hash": digest_value(config),
        "effective_from": WHEN,
        "effective_until": END,
    }
    proof = {
        "id": str(uuid5(UUID(VERSION), "policy-confirmation")),
        "user_id": USER,
        "source_type": "POLICY_CONFIRMATION",
        "source_ref": VERSION,
        "evidence_level": "USER_CONFIRMED_POLICY",
        "status": "VALID",
        "content": confirmation,
        "content_hash": digest_value(confirmation),
        "observed_at": WHEN,
        "valid_from": WHEN,
        "valid_to": None,
    }
    return {
        "user_id": USER,
        "policy_state_events": [
            {
                "policy_id": POLICY,
                "version_id": VERSION,
                "occurred_at": WHEN,
                "from_status": "DRAFT",
                "to_status": "ACTIVE",
            }
        ],
        "tables": {
            "policies": [{"id": POLICY, "user_id": USER, "status": "ACTIVE", "updated_at": WHEN}],
            "policy_versions": [
                {
                    "id": VERSION,
                    "user_id": USER,
                    "policy_id": POLICY,
                    "configuration": config,
                    "content_hash": digest_value(config),
                    "confirmation": confirmation,
                    "confirmed_at": WHEN,
                    "version_number": 1,
                    "valid_from": WHEN,
                    "valid_until": END,
                }
            ],
            "evidence_items": [proof],
        },
    }


@pytest.mark.parametrize("condition", ["active", "expired", "paused", "replaced", "future_proof"])
def test_independent_version_clock_and_formal_original(condition: str) -> None:
    facts = policy_facts()
    at = datetime.fromisoformat(WHEN)
    if condition == "expired":
        at = datetime.fromisoformat(END)
    elif condition == "paused":
        facts["tables"]["policies"][0]["status"] = "PAUSED"
        facts["policy_state_events"].append(
            {
                "policy_id": POLICY,
                "version_id": VERSION,
                "occurred_at": WHEN,
                "from_status": "ACTIVE",
                "to_status": "PAUSED",
            }
        )
    elif condition == "replaced":
        newer = copy.deepcopy(facts["tables"]["policy_versions"][0])
        newer.update(id=str(uuid4()), version_number=2)
        newer["confirmation"]["version_id"] = newer["id"]
        proof = copy.deepcopy(facts["tables"]["evidence_items"][0])
        proof.update(
            id=str(uuid4()),
            source_ref=newer["id"],
            content=newer["confirmation"],
            content_hash=digest_value(newer["confirmation"]),
        )
        facts["tables"]["evidence_items"].append(proof)
        facts["policy_state_events"].append(
            {
                "policy_id": POLICY,
                "version_id": newer["id"],
                "occurred_at": WHEN,
                "from_status": "ACTIVE",
                "to_status": "ACTIVE",
            }
        )
        facts["tables"]["policy_versions"].append(newer)
    elif condition == "future_proof":
        facts["tables"]["evidence_items"][0]["observed_at"] = END
        with pytest.raises(ObservationError, match="not valid"):
            metrics._current_version(facts, VERSION, at)
        return
    active, _ = metrics._current_version(facts, VERSION, at)
    assert active is (condition == "active")


def test_unknown_state_history_is_missing() -> None:
    facts = policy_facts()
    facts["tables"]["policies"][0]["updated_at"] = END
    facts["policy_state_events"] = []
    with pytest.raises(Missing, match="state history at consuming"):
        metrics._current_version(facts, VERSION, datetime.fromisoformat(WHEN))


def test_partial_metric_retains_known_violation_and_missing_reference() -> None:
    result = metrics._metric(
        "S2",
        [
            {"unit_id": "known", "status": "MEASURED", "value": True, "raw_refs": ["actual-1"]},
            {
                "unit_id": "unknown",
                "status": "MISSING",
                "value": None,
                "missing_reason": "Original absent",
                "raw_refs": ["expected-2"],
            },
        ],
        mode="count",
    )
    assert result["status"] == "MISSING" and result["value"] is None
    assert result["numerator"] == 1 and result["denominator"] == 2
    assert result["failures"][0]["raw_refs"] == ["expected-2"]


def test_integer_effect_rejects_bool_amount(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.action["amount_cents"] = True
    result = metrics.observe(fixture.emit())["metrics"]["S2"]
    assert result["status"] == "MISSING" and result["value"] is None


class TimelineFixture(Fixture):
    """Wrap independent helper synthetic originals with actual 13 tool-run bindings."""

    def __init__(self, root: Path, original: dict[str, Any]):
        super().__init__(root, transfer=False)
        self.original_facts = original
        self.manifest["user_id"] = original["user_id"]

    def facts(self, name: str, tables: dict[str, list[Any]]) -> dict[str, Any]:
        facts = copy.deepcopy(self.original_facts)
        replacements = {}
        originals = {}
        for old_digest, artifact in facts["artifact_originals"].items():
            original = {
                "protocol": RAW_PROTOCOL,
                "kind": "FINANCIAL_BASIS",
                "bindings": self.bindings,
                "payload": json.loads(artifact["utf8"]),
            }
            text = json.dumps(original, sort_keys=True, allow_nan=False)
            new_digest = hashlib.sha256(text.encode()).hexdigest()
            replacements[old_digest] = new_digest
            originals[new_digest] = {"utf8": text}
            if not any(raw["sha256"] == new_digest for raw in self.raws):
                path = "basis-" + new_digest + ".json"
                (self.root / path).write_bytes(text.encode())
                self.raws.append({"path": path, "sha256": new_digest, "kind": "FINANCIAL_BASIS"})

        def replace(value: Any) -> None:
            if isinstance(value, dict):
                if set(value) == {"artifact_sha256", "json_pointer", "value_sha256"}:
                    value["artifact_sha256"] = replacements[value["artifact_sha256"]]
                    value["json_pointer"] = "/payload" + value["json_pointer"]
                else:
                    for field in value.values():
                        replace(field)
            elif isinstance(value, list):
                for field in value:
                    replace(field)

        replace(facts)
        facts["artifact_originals"] = originals
        return self.raw(name, "FINANCIAL_FACTS", {"facts": facts, "capture_identity": name})


@pytest.mark.parametrize("cash,shortage", [(100_000, False), (40_000, True)])
def test_s3_counts_independent_raw_shortage_without_inventing_cause(
    tmp_path: Path, cash: int, shortage: bool
) -> None:
    original, _, _ = complete_fixture()
    original.tables["accounts"][0]["balance_cents"] = cash
    cash_opening = original.tables["simulated_bank_postings"][0]
    cash_opening.update(delta_cents=cash, balance_after_cents=cash)
    fixture = TimelineFixture(tmp_path, original.facts())
    cp = checkpoint("DUE")
    fixture.registration["due_checkpoints"] = [
        {"checkpoint_id": cp["checkpoint_id"], "checkpoint": cp}
    ]
    fixture.checkpoints = [{"checkpoint_id": cp["checkpoint_id"], "facts_ref": "AFTER"}]
    result = metrics.observe(fixture.emit())["metrics"]["S3"]
    assert result["status"] == "MEASURED" and result["value"] == int(shortage)
    unit = result["units"][0]
    assert unit["cause_class"] == ("UNKNOWN" if shortage else "NO_SHORTFALL")
    assert unit["cause_status"] == ("MISSING" if shortage else "NOT_APPLICABLE")


def test_e4_measures_integer_idle_series_from_original_timeline(tmp_path: Path) -> None:
    original, policy, product = complete_fixture()
    fixture = TimelineFixture(tmp_path, original.facts())
    cp = checkpoint(
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    fixture.registration["deployment_checkpoints"] = [
        {"checkpoint_id": cp["checkpoint_id"], "checkpoint": cp}
    ]
    fixture.checkpoints = [
        {"checkpoint_id": cp["checkpoint_id"], "facts_ref": "AFTER", "qualifying_action_ids": []}
    ]
    result = metrics.observe(fixture.emit())["metrics"]["E4"]
    assert result["status"] == "MEASURED"
    assert result["value"] == [{"checkpoint_id": cp["checkpoint_id"], "cents": 40_000}]
    assert result["numerator"] is None


def test_missing_registered_timeline_point_retains_unit(tmp_path: Path) -> None:
    original, _, _ = complete_fixture()
    fixture = TimelineFixture(tmp_path, original.facts())
    cp = checkpoint("DEPLOYMENT")
    fixture.registration["deployment_checkpoints"] = [
        {"checkpoint_id": cp["checkpoint_id"], "checkpoint": cp}
    ]
    result = metrics.observe(fixture.emit())["metrics"]["E4"]
    assert result["status"] == "MISSING" and result["denominator"] == 1 and result["value"] is None


@pytest.mark.parametrize("required,expected", [(True, 0), (False, 1)])
def test_e3_exact_frozen_confirmation_cause_and_original_question(
    tmp_path: Path, required: bool, expected: int
) -> None:
    fixture = Fixture(tmp_path, transfer=False)
    basis = {"confirmation_required": required, "cause_code": "EXACT_TOOL_CAUSE"}
    fixture.input_requirements = [basis]
    fixture.registration["ask_requirements"] = [
        {
            "opportunity_id": "ask-op",
            **basis,
            "basis_ref": {
                "registration": "input",
                "json_pointer": "/confirmation_requirements/0",
                "value_sha256": digest_value(basis),
            },
        }
    ]
    fixture.asks = [{"ask_event_id": "ask-1", "opportunity_id": "ask-op", "occurred_at": WHEN}]
    fixture.questions = [{**fixture.asks[0], "question_text": "TOOL_TEST_ONLY scripted question"}]
    result = metrics.observe(fixture.emit())["metrics"]["E3"]
    assert result["status"] == "MEASURED" and result["value"] == expected


def test_e3_unregistered_actual_question_is_missing(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, transfer=False)
    fixture.asks = [{"ask_event_id": "ask-1", "opportunity_id": "ask-op", "occurred_at": WHEN}]
    fixture.questions = [{**fixture.asks[0], "question_text": "TOOL_TEST_ONLY"}]
    result = metrics.observe(fixture.emit())["metrics"]["E3"]
    assert result["status"] == "MISSING" and result["denominator"] == 1 and result["value"] is None


POSITION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PRODUCT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
QUOTE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def noncash_entry(
    key: str, amount: int, *, opening: bool, leg: str, position: str | None = None, before: int = 0
) -> dict[str, Any]:
    row = entry(SOURCE, opening=opening, before=before, delta=amount, leg=leg)
    opening_id = str(uuid5(UUID(position or USER), "opening:" + key))
    row.update(
        id=opening_id if opening else str(uuid5(UUID(ACTION), "posting:" + leg)),
        ledger_key=key,
        account_id=None,
        position_id=position,
        previous_posting_id=None if opening else opening_id,
    )
    return row


class RedeemFixture(Fixture):
    def __init__(self, root: Path, *, consent: bool, loss: int):
        super().__init__(root, consent=False)
        effect = copy.deepcopy(self.effect)
        effect.update(
            action_type="REDEEM_ASSET",
            cash_uses=[],
            position_id=POSITION,
            position_account_id=SOURCE,
            product_id=PRODUCT,
            product_version_number=1,
            terms_digest=digest_value({}),
            original_policy_version_id=VERSION,
            policy_id=POLICY,
            policy_version_id=VERSION,
            policy_version_ids=[VERSION],
            quote_id=QUOTE,
            net_cents=100 - loss,
            loss_cents=loss,
            latest_arrival_at=WHEN,
        )
        digest = digest_value(effect)
        confirmation_id = str(uuid5(UUID(ACTION), "confirmation:" + digest))
        request = {
            "execution": {"effect": effect, "effect_hash": digest},
            "confirmation_evidence_id": confirmation_id if consent else None,
        }
        action = copy.deepcopy(self.action)
        action.update(
            action_type="ASSET_REDEEM",
            request=request,
            request_hash=digest_value(request),
            authorized_at=WHEN if consent else None,
        )
        bank = copy.deepcopy(self.bank)
        bank.update(
            operation_type="REDEEM_ASSET",
            request=request["execution"],
            request_hash=digest_value(request["execution"]),
        )
        position = {
            "id": POSITION,
            "user_id": USER,
            "account_id": SOURCE,
            "product_id": PRODUCT,
            "goal_id": None,
            "policy_version_id": VERSION,
            "principal_cents": 100,
            "accrued_yield_cents": 0,
            "purchased_at": WHEN,
            "maturity_at": END,
            "available_at": WHEN,
            "status": "HELD",
        }
        product = {
            "id": PRODUCT,
            "version_number": 1,
            "asset_class": "CASH_MGMT_T0",
            "risk_level": 0,
            "principal_fluctuation": False,
            "minimum_purchase_cents": 1,
            "lock_days": 0,
            "redemption_delay_days": 0,
            "maturity_rule": {},
            "effective_from": WHEN,
            "effective_until": None,
            "auto_purchase_allowed": True,
            "auto_redeem_allowed": True,
        }
        policy_originals = policy_facts()
        config = {
            "type": "asset_authorization",
            "name": "TOOL_TEST_ONLY",
            "scope": "general_idle_funds",
            "goal_id": None,
            "allowed_asset_classes": ["CASH_MGMT_T0"],
            "max_auto_managed_cents": 1000,
            "single_action_cap_cents": 100,
            "max_redemption_delay_days": 0,
            "max_lock_days": 0,
            "max_principal_risk_level": 0,
            "allow_auto_recovery_without_penalty": True,
            "allow_early_withdrawal_with_penalty": False,
        }
        version = policy_originals["tables"]["policy_versions"][0]
        version.update(configuration=config, content_hash=digest_value(config))
        version["confirmation"]["reviewed_hash"] = digest_value(config)
        policy_proof = policy_originals["tables"]["evidence_items"][0]
        policy_proof["content"] = version["confirmation"]
        policy_proof["content_hash"] = digest_value(policy_proof["content"])
        quote_content = {
            "quote_id": QUOTE,
            "user_id": USER,
            "position_id": POSITION,
            "product_id": PRODUCT,
            "product_version_number": 1,
            "terms_digest": effect["terms_digest"],
            "kind": "REDEEM",
            "principal_cents": 100,
            "fee_cents": 0,
            "loss_cents": loss,
            "net_cents": 100 - loss,
            "request_at": WHEN,
            "principal_available_at": WHEN,
            "expires_at": END,
        }
        quote = {
            "id": QUOTE,
            "user_id": USER,
            "source_type": "SIMULATED_REDEMPTION_QUOTE",
            "source_ref": POSITION,
            "evidence_level": "BANK_OBSERVED",
            "status": "VALID",
            "content": quote_content,
            "content_hash": digest_value(quote_content),
            "observed_at": WHEN,
            "valid_from": WHEN,
            "valid_to": END,
        }
        evidence = [policy_proof, quote]
        if consent:
            proof = copy.deepcopy(self.consent)
            proof.update(id=confirmation_id)
            proof["content"]["effect_hash"] = digest
            proof["content_hash"] = digest_value(proof["content"])
            evidence.append(proof)
        openings = [
            entry(SOURCE, opening=True, before=0, delta=1000, leg=""),
            entry(DESTINATION, opening=True, before=0, delta=0, leg=""),
            noncash_entry("POSITION:" + POSITION, 100, opening=True, leg="", position=POSITION),
            noncash_entry("LOSS:" + USER, 0, opening=True, leg=""),
        ]
        legs = [
            noncash_entry(
                "POSITION:" + POSITION,
                -100,
                opening=False,
                leg="position-out",
                position=POSITION,
                before=100,
            ),
            entry(DESTINATION, opening=False, before=0, delta=100 - loss, leg="cash-in"),
        ]
        if loss:
            legs.append(noncash_entry("LOSS:" + USER, loss, opening=False, leg="loss"))
        receipt = copy.deepcopy(self.receipt)
        receipt.update(
            loss_cents=loss,
            response={"bank_operation_id": ACTION, "posting_ids": [row["id"] for row in legs]},
        )
        self.before.update(
            action_plans=[action],
            evidence_items=evidence,
            policies=policy_originals["tables"]["policies"],
            policy_versions=[version],
            asset_products=[product],
            asset_positions=[position],
            simulated_bank_postings=openings,
        )
        self.policy_events = copy.deepcopy(policy_originals["policy_state_events"])
        for account in self.before["accounts"]:
            account["currency"] = "CNY"
        self.after = copy.deepcopy(self.before)
        self.after.update(
            bank_operations=[bank],
            action_receipts=[receipt],
            simulated_bank_postings=openings + legs,
        )
        self.after["accounts"][1]["balance_cents"] = 100 - loss
        self.after["asset_positions"][0]["status"] = "REDEEMED"
        self.capture["version_bank_operation_ids"] = [ACTION]
        self.registration["version_consumption_ids"] = ["bank-consumer"]
        self.consumptions = [
            {
                "consumption_id": "bank-consumer",
                "consuming_kind": "ACTION",
                "bank_operation_id": ACTION,
                "facts_ref": "AFTER",
                "consuming_at": WHEN,
                "version_ids": [VERSION],
            }
        ]


@pytest.mark.parametrize("consent,loss,errors", [(False, 50, 1), (True, 50, 0), (False, 0, 0)])
def test_s4_actual_loss_requires_original_exact_action_confirmation(
    tmp_path: Path, consent: bool, loss: int, errors: int
) -> None:
    fixture = RedeemFixture(tmp_path, consent=consent, loss=loss)
    metric = metrics.observe(fixture.emit())["metrics"]["S4"]
    assert metric["status"] == "MEASURED" and metric["value"] == errors


def test_positive_cost_without_original_quote_is_missing(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=50)
    fixture.before["evidence_items"] = [
        row for row in fixture.before["evidence_items"] if row["id"] != QUOTE
    ]
    result = metrics.observe(fixture.emit())["metrics"]["S4"]
    assert result["status"] == "MISSING" and result["value"] is None


@pytest.mark.parametrize(
    "consent,manual,automatic", [(False, False, True), (False, True, False), (True, False, False)]
)
def test_e1_zero_loss_recovery_checks_actor_consent_and_actual_projection(
    tmp_path: Path, consent: bool, manual: bool, automatic: bool
) -> None:
    fixture = RedeemFixture(tmp_path, consent=consent, loss=0)
    cp = {
        "checkpoint_id": "recovery-protection",
        "kind": "PROTECTION",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "horizon_end_at": END,
        "available_mode": "ACTUAL_SETTLED",
        "requested_action_type": None,
        "product_id": None,
    }
    expected = {"checkpoint": cp}
    fixture.registration["safe_auto_opportunity_ids"] = ["transfer-only"]
    fixture.registration["safe_auto_requirements"] = {"transfer-only": expected}
    fixture.opportunities = [
        {"opportunity_id": "transfer-only", "action_id": ACTION, "independent_checkpoint": expected}
    ]
    if manual:
        fixture.actors = [
            {
                "event_id": "manual",
                "event_type": "MANUAL_CHOICE",
                "phase": "RUNTIME_INTERVENTION",
                "action_id": ACTION,
                "occurred_at": WHEN,
            }
        ]
    metric = metrics.observe(fixture.emit())["metrics"]["E1"]
    assert metric["status"] == "MEASURED" and metric["value"] == int(automatic)


def test_e1_wrong_application_projection_is_missing(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    fixture.after["accounts"][1]["balance_cents"] = 999
    cp = {
        "checkpoint_id": "recovery-protection",
        "kind": "PROTECTION",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "horizon_end_at": END,
        "available_mode": "ACTUAL_SETTLED",
        "requested_action_type": None,
        "product_id": None,
    }
    expected = {"checkpoint": cp}
    fixture.registration.update(
        safe_auto_opportunity_ids=["transfer-only"],
        safe_auto_requirements={"transfer-only": expected},
    )
    fixture.opportunities = [
        {"opportunity_id": "transfer-only", "action_id": ACTION, "independent_checkpoint": expected}
    ]
    metric = metrics.observe(fixture.emit())["metrics"]["E1"]
    assert metric["status"] == "MISSING" and metric["value"] is None


def recovery_checkpoint() -> dict[str, Any]:
    return {
        "checkpoint_id": "recovery-protection",
        "kind": "PROTECTION",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "horizon_end_at": END,
        "available_mode": "ACTUAL_SETTLED",
        "requested_action_type": None,
        "product_id": None,
    }


def test_s1_native_same_point_recovery_has_independent_margin(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    fixture.registration["protection_checkpoints"] = [
        {"opportunity_id": "transfer-only", "checkpoint": recovery_checkpoint()}
    ]
    result = metrics.observe(fixture.emit())["metrics"]["S1"]
    assert result["status"] == "MEASURED" and result["value"] == 0
    assert (
        result["units"][0]["after_margin_cents"] - result["units"][0]["before_margin_cents"] == 100
    )


def test_s5_actual_consumer_uses_original_version_clock(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    result = metrics.observe(fixture.emit())["metrics"]["S5"]
    assert result["status"] == "MEASURED" and result["value"] == 0 and result["denominator"] == 1


def test_s5_unobserved_actual_consumer_cannot_be_empty_zero(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    fixture.registration["version_consumption_ids"] = []
    fixture.consumptions = []
    result = metrics.observe(fixture.emit())["metrics"]["S5"]
    assert result["status"] == "MISSING" and result["denominator"] == 1 and result["value"] is None


def test_s5_refused_stale_attempt_is_separate_from_consumption(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    fixture.after["bank_operations"][0]["status"] = "REJECTED"
    fixture.after["simulated_bank_postings"] = copy.deepcopy(
        fixture.before["simulated_bank_postings"]
    )
    fixture.after["action_receipts"] = []
    fixture.capture.update(action_ids=[], version_bank_operation_ids=[])
    fixture.transfer = False
    result = metrics.observe(fixture.emit())
    assert result["metrics"]["S5"]["status"] == "NOT_APPLICABLE"
    assert result["metrics"]["S5"]["denominator"] == 0
    assert len(result["refused_version_attempts"]) == 1


def test_missing_registered_basis_original_is_missing(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    run = fixture.emit()
    original = next(raw for raw in fixture.raws if raw["kind"] == "FINANCIAL_BASIS")
    (tmp_path / original["path"]).unlink()
    result = metrics.observe(run)["metrics"]["S2"]
    assert result["status"] == "MISSING" and result["value"] is None


def test_fake_replacement_without_original_confirmation_is_missing() -> None:
    facts = policy_facts()
    newer = copy.deepcopy(facts["tables"]["policy_versions"][0])
    newer.update(id=str(uuid4()), version_number=2)
    facts["tables"]["policy_versions"].append(newer)
    with pytest.raises(ObservationError, match="Candidate replacement"):
        metrics._current_version(facts, VERSION, datetime.fromisoformat(WHEN))


def test_s1_actual_cash_move_into_goal_account_can_violate_emergency_floor(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    originals = policy_facts()
    config = {"type": "emergency_buffer", "name": "TOOL_TEST_ONLY", "amount_cents": 1000}
    version = originals["tables"]["policy_versions"][0]
    version.update(configuration=config, content_hash=digest_value(config))
    version["confirmation"]["reviewed_hash"] = version["content_hash"]
    proof = originals["tables"]["evidence_items"][0]
    proof.update(
        content=version["confirmation"], content_hash=digest_value(version["confirmation"])
    )
    fixture.policy_events = originals["policy_state_events"]
    for tables in (fixture.before, fixture.after):
        tables["policies"] = copy.deepcopy(originals["tables"]["policies"])
        tables["policy_versions"] = copy.deepcopy(originals["tables"]["policy_versions"])
        tables["evidence_items"].append(copy.deepcopy(proof))
        for account in tables["accounts"]:
            account["currency"] = "CNY"
            if account["id"] == DESTINATION:
                account["account_type"] = "GOAL"
    fixture.registration["protection_checkpoints"] = [
        {"opportunity_id": "transfer-only", "checkpoint": recovery_checkpoint()}
    ]
    result = metrics.observe(fixture.emit())["metrics"]["S1"]
    assert result["status"] == "MEASURED" and result["value"] == 1
    assert result["units"][0]["before_margin_cents"] == 0
    assert result["units"][0]["after_margin_cents"] == -100


def test_actor_unknown_runtime_phase_is_missing(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=0)
    expected = {"checkpoint": recovery_checkpoint()}
    fixture.registration.update(
        safe_auto_opportunity_ids=["transfer-only"],
        safe_auto_requirements={"transfer-only": expected},
    )
    fixture.opportunities = [
        {"opportunity_id": "transfer-only", "action_id": ACTION, "independent_checkpoint": expected}
    ]
    fixture.actors = [
        {
            "event_id": "manual",
            "event_type": "MANUAL_CHOICE",
            "phase": "UNKNOWN",
            "occurred_at": WHEN,
            "action_id": ACTION,
        }
    ]
    result = metrics.observe(fixture.emit())["metrics"]["E1"]
    assert result["status"] == "MISSING" and result["value"] is None


def test_wrong_quote_observation_clock_is_missing(tmp_path: Path) -> None:
    fixture = RedeemFixture(tmp_path, consent=False, loss=50)
    next(row for row in fixture.before["evidence_items"] if row["id"] == QUOTE)["observed_at"] = END
    result = metrics.observe(fixture.emit())["metrics"]["S4"]
    assert result["status"] == "MISSING" and result["value"] is None


def test_formal_effective_window_cannot_be_bypassed_by_stored_status() -> None:
    facts = policy_facts()
    facts["tables"]["policy_versions"][0]["valid_until"] = None
    with pytest.raises(ObservationError, match="effective interval differ"):
        metrics._current_version(facts, VERSION, datetime.fromisoformat(END))


class PurchaseScopeFixture(RedeemFixture):
    """Actual synthetic purchase ledger/permission; scope unit is not a financial trial."""

    def __init__(self, root: Path):
        super().__init__(root, consent=False, loss=0)
        effect = copy.deepcopy(self.before["action_plans"][0]["request"]["execution"]["effect"])
        effect.update(
            action_type="PURCHASE_ASSET",
            cash_uses=[{"account_id": SOURCE, "amount_cents": 100}],
            destination_account_id=None,
            original_policy_version_id=None,
            quote_id=None,
            net_cents=None,
            latest_arrival_at=None,
        )
        command = {"effect": effect, "effect_hash": digest_value(effect)}
        action = self.before["action_plans"][0]
        action.update(
            action_type="ASSET_PURCHASE",
            request={"execution": command, "confirmation_evidence_id": None},
        )
        action["request_hash"] = digest_value(action["request"])
        self.before["evidence_items"] = [
            row for row in self.before["evidence_items"] if row["id"] != QUOTE
        ]
        self.before["asset_positions"] = []
        openings = [
            entry(SOURCE, opening=True, before=0, delta=1000, leg=""),
            entry(DESTINATION, opening=True, before=0, delta=0, leg=""),
            noncash_entry("POSITION:" + POSITION, 0, opening=True, leg="", position=POSITION),
        ]
        legs = [
            entry(SOURCE, opening=False, before=1000, delta=-100, leg="cash-out"),
            noncash_entry(
                "POSITION:" + POSITION, 100, opening=False, leg="position-in", position=POSITION
            ),
        ]
        self.before["simulated_bank_postings"] = openings
        self.after = copy.deepcopy(self.before)
        bank = copy.deepcopy(self.bank)
        bank.update(
            operation_type="PURCHASE_ASSET", request=command, request_hash=digest_value(command)
        )
        receipt = copy.deepcopy(self.receipt)
        receipt["response"] = {
            "bank_operation_id": ACTION,
            "posting_ids": [row["id"] for row in legs],
        }
        self.after.update(
            bank_operations=[bank],
            action_receipts=[receipt],
            simulated_bank_postings=openings + legs,
        )
        self.after["accounts"][0]["balance_cents"] = 900
        self.after["asset_positions"] = [
            {
                "id": POSITION,
                "user_id": USER,
                "account_id": SOURCE,
                "product_id": PRODUCT,
                "goal_id": None,
                "policy_version_id": VERSION,
                "principal_cents": 100,
                "accrued_yield_cents": 0,
                "purchased_at": WHEN,
                "maturity_at": END,
                "available_at": WHEN,
                "status": "HELD",
            }
        ]


@pytest.mark.parametrize("other", ["policy", "product"])
def test_e4_other_authorized_opportunity_cannot_reduce_this_idle_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    fixture = PurchaseScopeFixture(tmp_path)
    bundle = metrics.FinanceBundle(fixture.emit())
    actions = {
        row["action_id"]: row
        for row in bundle.rows("FINANCIAL_OBSERVATIONS", "actions", "action_id").values()
    }
    action = actions[ACTION]
    actual = metrics._settled(bundle, action)
    assert actual["effect"]["amount_cents"] == 100
    assert metrics._permission(actual)["authorized"] is True
    cp = {
        "checkpoint_id": "scoped-tool-point",
        "kind": "DEPLOYMENT",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "policy_id": POLICY,
        "product_id": PRODUCT,
    }
    cp[other + "_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    # This unit isolates scope aggregation; the independent timeline calculation
    # is already tested with original complete facts elsewhere. No effect claim.
    monkeypatch.setattr(
        metrics, "_timeline", lambda *_: {**cp, "safe_authorized_deployable_cents": 100}
    )
    result = metrics._deploy(
        bundle,
        {"facts_ref": action["before_facts_ref"], "qualifying_action_ids": []},
        {"checkpoint": cp},
        actions,
    )
    assert result["value"] == 100 and result["actual_deployed_cents"] == 0


def test_e4_exact_policy_product_purchase_reduces_only_matching_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = PurchaseScopeFixture(tmp_path)
    bundle = metrics.FinanceBundle(fixture.emit())
    actions = bundle.rows("FINANCIAL_OBSERVATIONS", "actions", "action_id")
    cp = {
        "checkpoint_id": "matching-tool-point",
        "kind": "DEPLOYMENT",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "policy_id": POLICY,
        "product_id": PRODUCT,
    }
    monkeypatch.setattr(
        metrics, "_timeline", lambda *_: {**cp, "safe_authorized_deployable_cents": 100}
    )
    result = metrics._deploy(
        bundle,
        {"facts_ref": actions[ACTION]["before_facts_ref"], "qualifying_action_ids": [ACTION]},
        {"checkpoint": cp},
        actions,
    )
    assert result["value"] == 0 and result["actual_deployed_cents"] == 100


@pytest.mark.parametrize("other", ["policy", "product"])
def test_e4_declared_other_opportunity_is_rejected_instead_of_false_zero_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    fixture = PurchaseScopeFixture(tmp_path)
    bundle = metrics.FinanceBundle(fixture.emit())
    actions = bundle.rows("FINANCIAL_OBSERVATIONS", "actions", "action_id")
    cp = {
        "checkpoint_id": "misdeclared-tool-point",
        "kind": "DEPLOYMENT",
        "at": WHEN,
        "scope": "GENERAL",
        "goal_id": None,
        "policy_id": POLICY,
        "product_id": PRODUCT,
    }
    cp[other + "_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    monkeypatch.setattr(
        metrics, "_timeline", lambda *_: {**cp, "safe_authorized_deployable_cents": 100}
    )
    with pytest.raises(ObservationError, match="qualifying deployment inventory differs"):
        metrics._deploy(
            bundle,
            {"facts_ref": actions[ACTION]["before_facts_ref"], "qualifying_action_ids": [ACTION]},
            {"checkpoint": cp},
            actions,
        )
