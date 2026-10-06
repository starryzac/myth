"""TOOL_TEST_ONLY protocol/candidate tests, with no application, DB or financial calls."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import pytest

from scripts import mvp_arm_executor as arms
from scripts.mvp_observations import BINDINGS, REG_PROTOCOL, ObservationError
from scripts.mvp_trace_metrics import EFFECT_FIELDS, digest_value

ROOT = Path(__file__).resolve().parents[2]
OWNER = "22222222-2222-4222-8222-222222222222"
RUN = "11111111-1111-4111-8111-111111111111"
EPOCH = "77777777-7777-4777-8777-777777777777"
CASH = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
ACTION = "55555555-5555-4555-8555-555555555555"
POLICY = "88888888-8888-4888-8888-888888888888"
VERSION = "99999999-9999-4999-8999-999999999999"
NOW = "2026-10-05T00:00:00+00:00"
END = "2026-10-31T00:00:00+00:00"


@pytest.fixture
def tmp_path() -> Path:
    base = ROOT / ".runtime/W1-arm-executor-tool-fixtures"
    base.mkdir(parents=True, exist_ok=True)
    target = base / uuid4().hex
    target.mkdir(exist_ok=False)
    return target


class Fixture:
    def __init__(self, root: Path, arm: str = "B1"):
        self.root = root
        self.paths: dict[str, str] = {}
        bindings = {key: "a" * 64 for key in BINDINGS}
        bindings.update(
            experiment_run_id=RUN,
            user_id=OWNER,
            isolated_db_epoch=EPOCH,
            case_id="arm-protocol-tool-only",
            arm_id=arm,
            seed_version="mvp-301-v6",
            execution_mode="TOOL_TEST_ONLY",
            purpose="TOOL_TEST_ONLY",
        )
        self.context_value: dict[str, Any] = {
            "protocol": "mvp-arm-isolated-context-v1",
            "bindings": bindings,
            "database_host": "127.0.0.1",
            "database_port": 54329,
            "database_name": "bf_test_" + "b" * 32,
            "now": NOW,
        }
        self.context = arms.SimulationContext.parse(self.context_value)
        self.rule: dict[str, Any] = {
            "protocol": "mvp-arm-rule-v1",
            "arm_id": arm,
            "case_id": bindings["case_id"],
            "purpose": "TOOL_TEST_ONLY",
            "seed_version": "mvp-301-v6",
            "cash_account_ids": [CASH],
            "threshold_cents": 300,
            "timezone": "Asia/Shanghai",
            "horizon_end_at": END,
            "fixed_policy_version_ids": [VERSION],
        }
        self.intent = {"kind": "purchase_asset", "policy_id": POLICY}
        self.rule["manual_actions"] = [
            {"opportunity_id": "tool-op", "amount_cents": 200, "intent": self.intent}
        ]
        configuration = {
            "type": "recurring_obligation",
            "due_day": 10,
            "amount_rule": {"kind": "exact", "amount_cents": 250},
        }
        self.view: dict[str, Any] = {
            "protocol": "mvp-arm-planning-view-v1",
            "bindings": self.context.bindings,
            "user_id": OWNER,
            "as_of": NOW,
            "trigger": "REGISTERED_MANUAL",
            "intent": self.intent,
            "accounts": [
                {
                    "id": CASH,
                    "user_id": OWNER,
                    "account_type": "CASH",
                    "currency": "CNY",
                    "balance_cents": 1000,
                },
                {
                    "id": OTHER,
                    "user_id": OWNER,
                    "account_type": "GOAL",
                    "currency": "CNY",
                    "balance_cents": 10000,
                },
            ],
            "policy_versions": [
                {
                    "id": VERSION,
                    "user_id": OWNER,
                    "configuration": configuration,
                    "content_hash": digest_value(configuration),
                }
            ],
        }
        effect = dict.fromkeys(EFFECT_FIELDS)
        effect.update(
            simulation=True,
            operation_id=ACTION,
            user_id=OWNER,
            action_type="TRANSFER_INTERNAL",
            business_key="TOOL_ONLY",
            amount_cents=100,
            cash_uses=[{"account_id": CASH, "amount_cents": 100}],
            income_uses=[],
            policy_version_ids=[],
            destination_account_id=OTHER,
            fee_cents=0,
            loss_cents=0,
            settlement_delay_days=0,
            valid_from=NOW,
            expires_at=END,
        )
        self.response: dict[str, Any] = {
            "simulation": True,
            "user_id": OWNER,
            "action_id": ACTION,
            "decision_run_id": RUN,
            "status": "AUTHORIZED",
            "autonomy_level": "AUTO_EXECUTE",
            "effect": effect,
            "effect_hash": digest_value(effect),
            "prepared_at": NOW,
            "as_of": NOW,
            "prepared_validation": {"status": "READY"},
        }

    def raw(self, kind: str, payload: dict[str, Any], pointer: str = "") -> dict[str, Any]:
        artifact = {
            "protocol": "mvp-raw-observation-v1",
            "kind": kind,
            "bindings": copy.deepcopy(self.context.bindings),
            "payload": copy.deepcopy(payload),
        }
        value: Any = artifact
        if pointer:
            for part in pointer[1:].split("/"):
                value = value[part]
        raw = json.dumps(artifact, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / (uuid4().hex + ".json")
        path.write_bytes(raw)
        self.paths[digest] = str(path)
        return {
            "artifact_sha256": digest,
            "json_pointer": pointer,
            "value_sha256": digest_value(value),
        }

    def capture(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        ref = self.raw(kind, payload)
        return {
            "protocol": "mvp-arm-original-capture-v1",
            "artifact_ref": ref,
            "original_paths": copy.deepcopy(self.paths),
        }

    def pipeline(self, names: set[str], action: str | None = ACTION) -> dict[str, Any]:
        return {
            name: {
                "original_ref": self.raw(
                    "PIPELINE_STAGE",
                    {
                        "stage": {
                            "stage": name,
                            "user_id": OWNER,
                            "action_id": action,
                            "occurred_at": NOW,
                            "capture_origin": "PRODUCTION_SERVICE_CALL",
                            "test_scope": "TOOL_TEST_ONLY",
                        }
                    },
                    "/payload/stage",
                )
            }
            for name in names
        }

    def preparation(self, response: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "capture_origin": "PRODUCTION_SERVICE_CALL",
            "outcome": "PREPARED",
            "response": copy.deepcopy(response or self.response),
            "pipeline": self.pipeline(
                {
                    "user_lock",
                    "prepare_transaction",
                    "candidate_before_new_effect",
                    "economic_hash",
                    "source_context",
                    "execution_revalidation",
                    "decision_trace",
                }
            ),
        }
        return self.capture("ARM_PREPARE", payload)

    def confirmation(self, *, accepted: bool = True) -> dict[str, Any]:
        effect_hash = self.response["effect_hash"]
        content = {
            "simulation": True,
            "user_id": OWNER,
            "action_id": ACTION,
            "effect_hash": effect_hash,
            "accepted": accepted,
            "confirmed_at": NOW,
            "valid_until": END,
        }
        proof = {
            "id": str(uuid5(UUID(ACTION), "confirmation:" + effect_hash)),
            "user_id": OWNER,
            "source_type": "USER_ACTION_CONFIRMATION",
            "source_ref": ACTION,
            "evidence_level": "USER_CONFIRMED_ACTION",
            "content": content,
            "content_hash": digest_value(content),
            "valid_from": NOW,
            "valid_to": END,
            "observed_at": NOW,
            "status": "VALID",
        }
        event = {
            "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
            "event_type": "AFFIRMATIVE_CONFIRMATION",
            "phase": "RUNTIME_INTERVENTION",
            "action_id": ACTION,
            "effect_hash": effect_hash,
            "occurred_at": NOW,
        }
        return self.capture(
            "ARM_CONFIRMATION",
            {
                "capture_origin": "PRODUCTION_SERVICE_CALL",
                "response": self.response,
                "confirmation_evidence_ref": self.raw(
                    "EVIDENCE", {"proof": proof}, "/payload/proof"
                ),
                "actor_event_ref": self.raw("ACTOR_LOG", {"event": event}, "/payload/event"),
            },
        )

    def execution(self, status: str = "UNKNOWN") -> dict[str, Any]:
        command = {"effect": self.response["effect"], "effect_hash": self.response["effect_hash"]}
        payload: dict[str, Any] = {
            "capture_origin": "PRODUCTION_SERVICE_CALL",
            "action_id": ACTION,
            "effect_hash": self.response["effect_hash"],
            "action_status": status,
            "pipeline": self.pipeline(
                {
                    "user_lock",
                    "reservation_transaction",
                    "bank_request_commit",
                    "business_projection_commit",
                }
            ),
            "bank_operation_ref": self.raw(
                "BANK",
                {
                    "operation": {
                        "id": ACTION,
                        "user_id": OWNER,
                        "action_plan_id": ACTION,
                        "request": command,
                        "request_hash": digest_value(command),
                    }
                },
                "/payload/operation",
            ),
        }
        if status == "SUCCEEDED":
            payload.update(
                receipt_ref=self.raw(
                    "RECEIPT",
                    {
                        "receipt": {
                            "status": "SUCCEEDED",
                            "user_id": OWNER,
                            "action_plan_id": ACTION,
                            "executed_cents": 100,
                            "fee_cents": 0,
                            "loss_cents": 0,
                            "response": {"bank_operation_id": ACTION},
                        }
                    },
                    "/payload/receipt",
                ),
                posting_inventory_ref=self.raw("POSTINGS", {"rows": []}, "/payload/rows"),
            )
        return self.capture("ARM_EXECUTION", payload)


@pytest.mark.parametrize("arm,amount", [("B0", 200), ("B1", 700), ("B2", 750)])
def test_three_independent_raw_candidate_rules(tmp_path: Path, arm: str, amount: int) -> None:
    fixture = Fixture(tmp_path, arm)
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    result = selector(fixture.view)
    assert result is not None and result["amount_cents"] == amount
    assert result["cash_uses"] == [{"account_id": CASH, "amount_cents": amount}]
    assert result["execution_mode"] is None and result["execution_status"] == "NOT_EXECUTED"
    assert result["unsafe_candidate_status"] == "NOT_MEASURED"


@pytest.mark.parametrize("arm", ["B3", "P"])
def test_actual_p_candidate_is_retained_without_fake_evaluator(tmp_path: Path, arm: str) -> None:
    fixture = Fixture(tmp_path, arm)
    assert arms.candidate_selector(fixture.context, fixture.rule, "tool-op") is None


def test_b0_no_agent_recovery_or_unregistered_manual_choice(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "B0")
    fixture.view["trigger"] = "RECOVERY_AGENT"
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    with pytest.raises(ObservationError, match="does not discover"):
        selector(fixture.view)
    fixture.view["trigger"] = "REGISTERED_MANUAL"
    wrong = arms.candidate_selector(fixture.context, fixture.rule, "unregistered")
    assert wrong is not None
    with pytest.raises(ObservationError, match="manual choice"):
        wrong(fixture.view)


@pytest.mark.parametrize("change", ["owner", "bool", "repeat", "scope", "currency"])
def test_bad_accounts_are_rejected_even_for_zero_candidate(tmp_path: Path, change: str) -> None:
    fixture = Fixture(tmp_path)
    fixture.rule["threshold_cents"] = 2000
    if change == "owner":
        fixture.view["accounts"][0]["user_id"] = RUN
    elif change == "bool":
        fixture.view["accounts"][0]["balance_cents"] = True
    elif change == "repeat":
        fixture.view["accounts"].append(copy.deepcopy(fixture.view["accounts"][0]))
    elif change == "currency":
        fixture.view["accounts"][0]["currency"] = "USD"
    else:
        fixture.rule["cash_account_ids"] = [OTHER]
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    with pytest.raises(ObservationError):
        selector(fixture.view)


def test_zero_candidate_is_absence_not_execution_success(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.rule["threshold_cents"] = 1000
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None and selector(fixture.view) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_host", "localhost"),
        ("database_port", 5432),
        ("database_port", True),
        ("database_name", "bounded_funds"),
        ("now", "2026-10-05"),
    ],
)
def test_formal_endpoint_and_naive_clock_are_excluded(
    tmp_path: Path, field: str, value: Any
) -> None:
    fixture = Fixture(tmp_path)
    fixture.context_value[field] = value
    with pytest.raises(ObservationError):
        arms.SimulationContext.parse(fixture.context_value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_mode", "MODEL_ONLY"),
        ("seed_version", "old"),
        ("purpose", "SUCCESS"),
        ("arm_id", "other"),
    ],
)
def test_no_label_rewrite_or_unregistered_binding(tmp_path: Path, field: str, value: str) -> None:
    fixture = Fixture(tmp_path)
    fixture.context_value["bindings"][field] = value
    with pytest.raises(ObservationError):
        arms.SimulationContext.parse(fixture.context_value)


def test_context_and_rule_changes_rejected_within_invocation(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    fixture.rule["threshold_cents"] = 0
    with pytest.raises(ObservationError, match="rule changed"):
        selector(fixture.view)
    fixture.context.bindings["arm_id"] = "P"
    with pytest.raises(ObservationError, match="context changed"):
        fixture.context.assert_current()


@pytest.mark.parametrize("change", ["range", "repeat", "hash", "missing"])
def test_b2_requires_actual_exact_fixed_originals(tmp_path: Path, change: str) -> None:
    fixture = Fixture(tmp_path, "B2")
    row = fixture.view["policy_versions"][0]
    if change == "range":
        row["configuration"]["amount_rule"]["kind"] = "range"
        row["content_hash"] = digest_value(row["configuration"])
    elif change == "repeat":
        fixture.view["policy_versions"].append(copy.deepcopy(row))
    elif change == "hash":
        row["configuration"]["amount_rule"]["amount_cents"] = 0
    else:
        fixture.view["policy_versions"] = []
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    with pytest.raises(ObservationError):
        selector(fixture.view)


def test_missing_production_hook_rejects_before_any_callback(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(arms.ServiceHookPending, match="NOT_IMPLEMENTED"):
        arms.prepare(
            fixture.context,
            fixture.rule,
            "tool-op",
            {"idempotency_key": "original", "intent": fixture.intent},
        )
    with pytest.raises(arms.ServiceHookPending, match="NOT_IMPLEMENTED"):
        arms.execute(fixture.context, {}, None)


def test_prep_record_retains_file_provenance_and_pending_status(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    capture = fixture.preparation()
    result = arms.record_prepare(fixture.context, capture)
    assert result["original_artifact_sha256"] == capture["artifact_ref"]["artifact_sha256"]
    assert result["response"] == fixture.response and result["execution_mode"] is None
    assert result["capability_status"] == "PENDING_PRODUCTION_VERIFICATION"
    assert (
        result["financial_effect_evidence"] is False
        and result["actual_violation_status"] == "NOT_MEASURED"
    )


@pytest.mark.parametrize("change", ["missing", "bytes", "pointer", "value"])
def test_actual_original_integrity_not_status_string(tmp_path: Path, change: str) -> None:
    fixture = Fixture(tmp_path)
    capture = fixture.preparation()
    ref = capture["artifact_ref"]
    path = Path(capture["original_paths"][ref["artifact_sha256"]])
    if change == "missing":
        capture["original_paths"] = {}
    elif change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "pointer":
        ref["json_pointer"] = "/payload"
    else:
        ref["value_sha256"] = "b" * 64
    with pytest.raises(ObservationError):
        arms.record_prepare(fixture.context, capture)


def test_original_effect_changes_are_rejected_without_rehash(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.response["effect"]["amount_cents"] = 200
    with pytest.raises(ObservationError, match="cannot rehash history"):
        arms.record_prepare(fixture.context, fixture.preparation())


@pytest.mark.parametrize(
    "arm,autonomy,required",
    [
        ("B0", "AUTO_EXECUTE", True),
        ("B3", "AUTO_EXECUTE", True),
        ("P", "AUTO_EXECUTE", False),
        ("P", "ASK_ONCE", True),
    ],
)
def test_actual_actor_confirmation_requirement(
    tmp_path: Path, arm: str, autonomy: str, required: bool
) -> None:
    fixture = Fixture(tmp_path, arm)
    fixture.response["autonomy_level"] = autonomy
    assert arms.actor_requirement(fixture.context, fixture.response) is required


@pytest.mark.parametrize("autonomy", ["ADVISE_ONLY", "BLOCKED"])
def test_blocked_original_never_requests_actor_or_execution(tmp_path: Path, autonomy: str) -> None:
    fixture = Fixture(tmp_path, "B3")
    fixture.response["autonomy_level"] = autonomy
    with pytest.raises(ObservationError, match="never become executable"):
        arms.actor_requirement(fixture.context, fixture.response)


def test_exact_actor_confirmation_is_original_pending_record(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "B3")
    result = arms.record_confirmation(
        fixture.context, fixture.confirmation(), ACTION, fixture.response["effect_hash"]
    )
    assert result["execution_mode"] is None and result["financial_effect_evidence"] is False
    with pytest.raises(ObservationError, match="affirmative"):
        arms.record_confirmation(
            fixture.context,
            fixture.confirmation(accepted=False),
            ACTION,
            fixture.response["effect_hash"],
        )


@pytest.mark.parametrize(
    "status,censored", [("UNKNOWN", True), ("SUBMITTED", True), ("SUCCEEDED", False)]
)
def test_execution_records_preserve_unknown_and_never_claim_financial_proof(
    tmp_path: Path, status: str, censored: bool
) -> None:
    fixture = Fixture(tmp_path)
    result = arms.record_execution(
        fixture.context, fixture.execution(status), ACTION, fixture.response["effect_hash"]
    )
    assert result["action_status"] == status and result["censored"] is censored
    assert result["execution_mode"] is None and result["financial_effect_evidence"] is False
    assert result["actual_violation_status"] == "NOT_MEASURED"


def test_only_small_intents_no_permission_or_economic_effect_injection() -> None:
    request: dict[str, Any] = {
        "idempotency_key": "existing-original",
        "intent": {"kind": "purchase_asset", "policy_id": POLICY},
    }
    arms._request(request)
    request["intent"]["amount_cents"] = 999999
    with pytest.raises(ObservationError, match="grants/effects"):
        arms._request(request)


def test_cli_describes_pending_capability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert arms.main(["--describe"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "NOT_IMPLEMENTED" and result["execution_mode"] is None
    assert result["financial_effect_evidence"] is False


def changed_capture(fixture: Fixture, capture: dict[str, Any], edit: Any) -> dict[str, Any]:
    """Make a NEW synthetic original; never overwrite the captured counterexample."""
    digest = capture["artifact_ref"]["artifact_sha256"]
    original = json.loads(Path(capture["original_paths"][digest]).read_bytes())
    edit(original)
    raw = json.dumps(original, sort_keys=True, allow_nan=False).encode()
    new_digest = hashlib.sha256(raw).hexdigest()
    path = fixture.root / (uuid4().hex + ".json")
    path.write_bytes(raw)
    fixture.paths[new_digest] = str(path)
    return {
        "protocol": "mvp-arm-original-capture-v1",
        "original_paths": copy.deepcopy(fixture.paths),
        "artifact_ref": {
            "artifact_sha256": new_digest,
            "json_pointer": "",
            "value_sha256": digest_value(original),
        },
    }


@pytest.mark.parametrize("clock_delta", [False, True])
def test_actual_typed_confirmation_utc_representation_keeps_original_time_checks(
    tmp_path: Path,
    clock_delta: bool,
) -> None:
    fixture = Fixture(tmp_path, "B3")
    capture = fixture.confirmation()
    original = json.loads(
        Path(capture["original_paths"][capture["artifact_ref"]["artifact_sha256"]]).read_bytes()
    )
    reference = original["payload"]["confirmation_evidence_ref"]
    source = json.loads(Path(capture["original_paths"][reference["artifact_sha256"]]).read_bytes())
    evidence = source["payload"]["proof"]
    assert evidence["valid_to"].endswith("+00:00")
    evidence["valid_to"] = evidence["valid_to"].replace("+00:00", "Z")
    evidence["observed_at"] = (
        "2026-01-01T00:00:00Z" if clock_delta else evidence["observed_at"].replace("+00:00", "Z")
    )
    raw = json.dumps(source, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = fixture.root / (uuid4().hex + ".json")
    path.write_bytes(raw)
    fixture.paths[digest] = str(path)
    revised_reference = {
        "artifact_sha256": digest,
        "json_pointer": reference["json_pointer"],
        "value_sha256": digest_value(evidence),
    }
    revised = changed_capture(
        fixture,
        capture,
        lambda value: value["payload"].update(confirmation_evidence_ref=revised_reference),
    )
    if clock_delta:
        with pytest.raises(ObservationError, match="time/validity"):
            arms.record_confirmation(
                fixture.context, revised, ACTION, fixture.response["effect_hash"]
            )
    else:
        assert (
            arms.record_confirmation(
                fixture.context, revised, ACTION, fixture.response["effect_hash"]
            )["execution_mode"]
            is None
        )


def test_no_p_preview_amount_is_used_by_fixed_baseline(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "B2")
    fixture.view["p_preview"] = {"suggested_amount_cents": 0, "status": "BLOCKED"}
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None and selector(fixture.view)["amount_cents"] == 750  # type: ignore[index]


def test_fixed_calendar_clips_month_end_in_registered_local_timezone(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "B2")
    config = fixture.view["policy_versions"][0]["configuration"]
    config["due_day"] = 31
    fixture.view["policy_versions"][0]["content_hash"] = digest_value(config)
    fixture.rule["horizon_end_at"] = "2026-11-30T23:00:00+08:00"
    selector = arms.candidate_selector(fixture.context, fixture.rule, "tool-op")
    assert selector is not None
    result = selector(fixture.view)
    assert result is not None and result["amount_cents"] == 500


def test_nonempty_pipeline_reference_is_not_verified_original(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    capture = changed_capture(
        fixture,
        fixture.preparation(),
        lambda original: original["payload"]["pipeline"]["economic_hash"].update(
            original_ref="VALID"
        ),
    )
    with pytest.raises(ObservationError, match="object"):
        arms.record_prepare(fixture.context, capture)


def test_other_run_source_cannot_bind_an_original_record(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    capture = changed_capture(
        fixture,
        fixture.preparation(),
        lambda original: original["bindings"].update(experiment_run_id=EPOCH),
    )
    with pytest.raises(ObservationError, match="rebound"):
        arms.record_prepare(fixture.context, capture)


def test_partial_effect_and_bool_money_are_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    del fixture.response["effect"]["income_uses"]
    with pytest.raises(ObservationError, match="Full original effect"):
        arms.record_prepare(fixture.context, fixture.preparation())
    fixture = Fixture(tmp_path)
    fixture.response["effect"]["amount_cents"] = True
    fixture.response["effect_hash"] = digest_value(fixture.response["effect"])
    with pytest.raises(ObservationError, match="bounded integer"):
        arms.record_prepare(fixture.context, fixture.preparation())


def test_success_without_original_receipt_remains_unrecordable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    capture = changed_capture(
        fixture,
        fixture.execution("SUCCEEDED"),
        lambda original: original["payload"].pop("receipt_ref"),
    )
    with pytest.raises(ObservationError, match="receipt_ref"):
        arms.record_execution(fixture.context, capture, ACTION, fixture.response["effect_hash"])


def test_changed_confirmation_owner_and_original_hash_are_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "B3")
    with pytest.raises(ObservationError, match="logical operation/effect"):
        arms.record_confirmation(fixture.context, fixture.confirmation(), ACTION, "b" * 64)


def test_mock_callback_cannot_be_promoted_by_source_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = tmp_path / "provider.py"
    provider.write_text(
        "def prepare_arm_action(context, request, *, candidate_selector=None):\n"
        "    raise RuntimeError('TOOL_ONLY')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(arms, "PROVIDER_PATH", provider)
    source_ref = {
        "status": "FROZEN_SERVICE_HOOK",
        "original_path": "apps/api/app/services/experiment_arms.py",
        "sha256": hashlib.sha256(provider.read_bytes()).hexdigest(),
        "archived_path": str(provider),
    }
    with pytest.raises(ObservationError, match="original frozen production module"):
        arms.require_production_hooks(object(), source_ref)  # type: ignore[arg-type]


def test_registered_rule_requires_actual_bytes_not_amount_metadata(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(ObservationError, match="actual original arm rule|Actual original arm rule"):
        arms._registered_rule(fixture.context, fixture.rule, None)
    path = tmp_path / "registered-rule.json"
    path.write_text(json.dumps(fixture.rule, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    fixture.context_value["bindings"]["rule_sha256"] = digest
    context = arms.SimulationContext.parse(fixture.context_value)
    arms._registered_rule(context, fixture.rule, {"path": str(path), "sha256": digest})
    path.write_text(json.dumps({**fixture.rule, "threshold_cents": 0}), encoding="utf-8")
    with pytest.raises(ObservationError, match="hash differs"):
        arms._registered_rule(context, fixture.rule, {"path": str(path), "sha256": digest})


def test_original_json_duplicate_and_nonfinite_values_are_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    for raw in (b'{"protocol":"x","protocol":"x"}', b'{"status":NaN}'):
        path = tmp_path / (uuid4().hex + ".json")
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        reader = arms.OriginalReader(fixture.context, {digest: str(path)})
        with pytest.raises(ObservationError):
            reader.resolve(
                {"artifact_sha256": digest, "json_pointer": "", "value_sha256": "a" * 64}
            )


def test_original_payment_intent_keeps_exact_optional_identifiers() -> None:
    request: dict[str, Any] = {
        "idempotency_key": "same-original-operation",
        "intent": {
            "kind": "pay_recurring",
            "policy_id": POLICY,
            "period": "2026-10",
            "bill_id": None,
        },
    }
    arms._request(request)
    request["intent"]["grant"] = True
    with pytest.raises(ObservationError, match="extra/missing"):
        arms._request(request)


@pytest.mark.parametrize("arm", ["B0", "B1", "B2", "B3", "P"])
def test_native_original_rule_wrapper_keeps_observation_and_corpus_binding(
    tmp_path: Path, arm: str
) -> None:
    fixture = Fixture(tmp_path, arm)
    wrapper = {
        "protocol": REG_PROTOCOL,
        "kind": "RULE",
        "arm_id": arm,
        "case_id": fixture.context.bindings["case_id"],
        "purpose": "TOOL_TEST_ONLY",
        "mechanism_id": "tool-" + arm,
        "execution_mode": "TOOL_TEST_ONLY",
        "arm_algorithm": fixture.rule,
    }
    selector = arms.candidate_selector(fixture.context, wrapper, "tool-op")
    if arm in {"B3", "P"}:
        assert selector is None
    else:
        assert selector is not None
        result = selector(fixture.view)
        assert result is not None and result["rule_value_sha256"] == digest_value(wrapper)


@pytest.mark.parametrize("attempt", [None, ACTION])
def test_actual_common_gateway_rejection_does_not_fabricate_executed_effect(
    tmp_path: Path, attempt: str | None
) -> None:
    fixture = Fixture(tmp_path)
    error = {
        "http_status": 400,
        "code": "COMMON_SOURCE_CONTEXT_REJECTED",
        "message": "TOOL_ONLY actual captured refusal",
    }
    capture = fixture.capture(
        "ARM_PREPARE",
        {
            "capture_origin": "PRODUCTION_SERVICE_CALL",
            "outcome": "COMMON_GATEWAY_REJECTED",
            "attempt_action_id": attempt,
            "response": None,
            "error": {
                **error,
                "original_ref": fixture.raw("HTTP_ERROR", {"error": error}, "/payload/error"),
            },
            "pipeline": fixture.pipeline(
                {
                    "user_lock",
                    "prepare_transaction",
                    "candidate_before_new_effect",
                    "economic_hash",
                    "source_context",
                    "execution_revalidation",
                    "decision_trace",
                },
                attempt,
            ),
        },
    )
    result = arms.record_prepare(fixture.context, capture)
    assert result["outcome"] == "COMMON_GATEWAY_REJECTED" and result["response"] is None
    assert result["execution_mode"] is None and result["actual_violation_status"] == "NOT_MEASURED"
    forged = changed_capture(
        fixture, capture, lambda original: original["payload"].update(response=fixture.response)
    )
    with pytest.raises(ObservationError, match="fabricated prepared effect"):
        arms.record_prepare(fixture.context, forged)


def test_original_preparation_time_survives_later_call_without_rewriting_capture(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    capture = fixture.preparation()
    old_bytes = Path(
        capture["original_paths"][capture["artifact_ref"]["artifact_sha256"]]
    ).read_bytes()
    later_context = copy.deepcopy(fixture.context_value)
    later_context["now"] = "2026-10-05T00:01:00+00:00"
    context = arms.SimulationContext.parse(later_context)
    result = arms.record_prepare(context, capture)
    assert result["response"]["prepared_at"] == NOW and result["response"]["as_of"] == NOW
    assert (
        Path(capture["original_paths"][capture["artifact_ref"]["artifact_sha256"]]).read_bytes()
        == old_bytes
    )


@pytest.mark.parametrize(
    "field,stamp",
    [
        ("as_of", "2026-10-05T00:01:00+00:00"),
        ("as_of", "2026-10-04T23:59:00+00:00"),
        ("prepared_at", "2026-10-05T00:01:00+00:00"),
    ],
)
def test_future_or_reversed_original_response_clocks_are_rejected(
    tmp_path: Path, field: str, stamp: str
) -> None:
    fixture = Fixture(tmp_path)
    fixture.response[field] = stamp
    with pytest.raises(ObservationError, match="clock is reversed or in the future"):
        arms.record_prepare(fixture.context, fixture.preparation())
