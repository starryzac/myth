"""TOOL_TEST_ONLY file fixtures; no real run, PG, financial effect or acceptance."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import pytest
from app.domain.audit_chain import (
    build_checkpoint,
    build_event,
    build_subject,
    checkpoint_canonical_text,
    event_canonical_text,
    posting_set_digest,
    receipt_digest,
    subject_canonical_text,
    subject_hash,
    verify_epoch,
)
from app.domain.audit_chain_types import (
    AuditAnchor,
    AuditEpochTransition,
    AuditFactContext,
    AuditHead,
    AuditIntent,
    AuditPayload,
    AuditReference,
    ReferenceBundle,
)
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import TraceEvidence
from app.tests.test_audit_chain_domain import transfer_originals

from scripts.mvp_observations import (
    BINDINGS,
    RAW_PROTOCOL,
    REG_PROTOCOL,
    RUN_PROTOCOL,
    ObservationError,
)
from scripts.mvp_trace_metrics import (
    CONTRACT,
    INVENTORY_TABLES,
    ROOT,
    TraceBundle,
    digest_value,
    main,
    observe,
)

USER = "00000000-0000-0000-0000-000000000002"
EPOCH = "00000000-0000-0000-0000-000000000003"
DECISION = "00000000-0000-0000-0000-000000000006"
ACTION = "00000000-0000-0000-0000-000000000020"
FACT = "00000000-0000-0000-0000-000000000041"
WHEN = "2026-10-04T00:00:00Z"
NOW = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.fixture
def tmp_path() -> Path:
    """Fresh inherited-ACL workspace fixture; retain originals and all negative cases."""
    base = ROOT / ".runtime" / "W1-trace-tool-fixtures"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir(exist_ok=False)
    return path


def write(path: Path, value: Any) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


class Fixture:
    """Original fixtures are built using data codecs, not financial-oracle decisions."""

    def __init__(self, root: Path):
        self.root = root
        self.source_refs = []
        source_paths = sorted(
            {
                path.relative_to(ROOT).as_posix()
                for path in (ROOT / "apps/api/app/domain").glob("*.py")
            }
            | {
                "scripts/mvp_trace_metrics.py",
                "scripts/mvp_observations.py",
                "pyproject.toml",
                "uv.lock",
            }
        )
        for index, original_path in enumerate(source_paths):
            path = f"source-{index}.bin"
            source_bytes = (ROOT / original_path).read_bytes()
            (root / path).write_bytes(source_bytes)
            self.source_refs.append(
                {
                    "original_path": original_path,
                    "path": path,
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                }
            )
        causal_bytes = b'def fixture_cause():\n    raise ValueError("TOOL_TEST_ONLY")\n'
        (root / "causal.py").write_bytes(causal_bytes)
        self.cause_source = {
            "original_path": "tool-only/causal.py",
            "path": "causal.py",
            "sha256": hashlib.sha256(causal_bytes).hexdigest(),
            "line": 2,
            "line_text": '    raise ValueError("TOOL_TEST_ONLY")',
            "symbol": "fixture_cause",
        }
        fact_content = {
            "account_id": "00000000-0000-0000-0000-000000000021",
            "balance_cents": 1000,
            "fixture_purpose": "TOOL_TEST_ONLY",
        }
        self.fact: dict[str, Any] = {
            "id": FACT,
            "user_id": USER,
            "content": fact_content,
            "content_hash": digest_value(fact_content),
            "source_type": "TOOL_BANK",
            "source_ref": "tool-account-only",
            "evidence_level": "BANK_OBSERVED",
            "status": "VALID",
            "valid_from": WHEN,
            "valid_to": None,
            "observed_at": WHEN,
            "supersedes_id": None,
            "created_at": WHEN,
        }
        self.expected_evidence: dict[str, Any] = {
            "evidence_id": FACT,
            "content_hash": self.fact["content_hash"],
            "source_type": "TOOL_BANK",
            "source_ref": "tool-account-only",
            "evidence_level": "BANK_OBSERVED",
            "subject_fields": {"account_id": fact_content["account_id"]},
        }
        trace = build_trace(
            run_id=UUID(DECISION),
            user_id=UUID(USER),
            phase="EVALUATION",
            as_of=NOW,
            algorithm_versions={"trace": "decision-trace-v1"},
            inputs={},
            sources=[
                TraceEvidence(
                    id=UUID(FACT),
                    user_id=UUID(USER),
                    evidence_level="BANK_OBSERVED",
                    source_type="TOOL_BANK",
                    source_ref="tool-account-only",
                    content=fact_content,
                    content_hash=self.fact["content_hash"],
                    captured_content_hash=self.fact["content_hash"],
                    content_integrity="VERIFIED",
                    status_at_decision="VALID",
                    observed_at=NOW,
                    valid_from=NOW,
                )
            ],
            outcome={"fixture_purpose": "TOOL_TEST_ONLY"},
        )
        snapshot = {"decision_trace": trace.model_dump(mode="json")}
        self.decision = {
            "id": DECISION,
            "user_id": USER,
            "as_of": WHEN,
            "parent_run_id": None,
            "subject_action_plan_id": None,
            "input_snapshot": snapshot,
            "snapshot_hash": digest_value(snapshot),
            "policy_version_ids": [],
            "evidence_ids": [FACT],
            "created_at": WHEN,
        }
        operation, action, postings, receipt, _ = transfer_originals()
        # This borrowed pure codec fixture is a fabricated TOOL_TEST_ONLY transfer, not an oracle.
        self.action = action
        self.action.update(
            decision_run_id=DECISION,
            created_at=NOW,
            authorized_at=NOW,
            expires_at=self.action["request"]["execution"]["effect"]["expires_at"],
        )
        effect_hash = self.action["request"]["execution"]["effect_hash"]
        confirmation_id = str(uuid5(UUID(ACTION), "confirmation:" + effect_hash))
        self.action["request"]["confirmation_evidence_id"] = confirmation_id
        self.action["request_hash"] = digest_value(self.action["request"])
        content = {
            "simulation": True,
            "user_id": USER,
            "action_id": ACTION,
            "effect_hash": effect_hash,
            "accepted": True,
            "confirmed_at": WHEN,
            "valid_until": self.action["expires_at"],
        }
        self.consent: dict[str, Any] = {
            "id": confirmation_id,
            "user_id": USER,
            "created_at": WHEN,
            "evidence_level": "USER_CONFIRMED_ACTION",
            "source_type": "USER_ACTION_CONFIRMATION",
            "source_ref": ACTION,
            "content": content,
            "content_hash": digest_value(content),
            "status": "VALID",
            "observed_at": WHEN,
            "valid_from": WHEN,
            "valid_to": self.action["expires_at"],
            "supersedes_id": None,
        }
        subjects = [
            build_subject(
                user_id=UUID(USER), epoch_id=UUID(EPOCH), kind=kind, id=UUID(row["id"]), data=row
            )
            for kind, row in [
                ("DECISION_RUN", self.decision),
                ("EVIDENCE", self.fact),
                ("EVIDENCE", self.consent),
                ("ACTION_PLAN", self.action),
                ("BANK_OPERATION", operation),
                ("ACTION_RECEIPT", receipt),
            ]
            + [("BANK_POSTING", row) for row in postings]
        ]
        first = build_event(
            AuditIntent(
                user_id=UUID(USER),
                event_type="EPOCH_STARTED",
                aggregate_type="EPOCH",
                aggregate_id=UUID(EPOCH),
                correlation_id=UUID(EPOCH),
                idempotency_key="tool-init",
                occurred_at=NOW,
                payload=AuditPayload(
                    fact_key="tool-init",
                    correlation_kind="EPOCH",
                    epoch_transition=AuditEpochTransition(kind="INIT"),
                ),
            ),
            event_id=UUID("00000000-0000-0000-0000-000000000005"),
            epoch_id=UUID(EPOCH),
            sequence_number=1,
            previous_hash=None,
            observed_at=NOW,
            appended_at=NOW,
        )
        references = [
            AuditReference(kind=s.kind, id=s.id, user_id=UUID(USER), snapshot_hash=subject_hash(s))
            for s in subjects
        ]
        second = build_event(
            AuditIntent(
                user_id=UUID(USER),
                event_type="DECISION_RECORDED",
                aggregate_type="DECISION_RUN",
                aggregate_id=UUID(DECISION),
                correlation_id=UUID(DECISION),
                decision_run_id=UUID(DECISION),
                idempotency_key="tool-decision",
                occurred_at=NOW,
                payload=AuditPayload(
                    fact_key="tool-decision",
                    correlation_kind="DECISION_RUN",
                    references=references[:2],
                    anchors=[
                        AuditAnchor(
                            kind="DECISION_TRACE",
                            reference_id=UUID(DECISION),
                            snapshot_hash=subject_hash(subjects[0]),
                            digest=trace.trace_hash,
                            hash_algorithm="decision-trace-v1",
                        )
                    ],
                    context=AuditFactContext(reason_code="TOOL_CAUSE", cause_ref="failure-1"),
                ),
            ),
            event_id=UUID("00000000-0000-0000-0000-000000000007"),
            epoch_id=UUID(EPOCH),
            sequence_number=2,
            previous_hash=first.event_hash,
            observed_at=NOW,
            appended_at=NOW,
        )
        third = build_event(
            AuditIntent(
                user_id=UUID(USER),
                event_type="ACTION_PROJECTED",
                aggregate_type="ACTION_RECEIPT",
                aggregate_id=subjects[5].id,
                correlation_id=UUID(DECISION),
                decision_run_id=UUID(DECISION),
                action_plan_id=UUID(ACTION),
                action_receipt_id=subjects[5].id,
                causation_id=second.id,
                idempotency_key="tool-projection",
                occurred_at=NOW,
                payload=AuditPayload(
                    fact_key="tool-projection",
                    correlation_kind="DECISION_RUN",
                    references=references,
                    anchors=[
                        AuditAnchor(
                            kind="ACTION_RECEIPT",
                            reference_id=subjects[5].id,
                            snapshot_hash=subject_hash(subjects[5]),
                            digest=receipt_digest(subjects[5].data),
                            hash_algorithm="configuration-sha256-v1",
                        ),
                        AuditAnchor(
                            kind="BANK_POSTING_SET",
                            reference_id=subjects[4].id,
                            snapshot_hash=subject_hash(subjects[4]),
                            digest=posting_set_digest(s.data for s in subjects[6:]),
                            hash_algorithm="configuration-sha256-v1",
                        ),
                    ],
                ),
            ),
            event_id=UUID("00000000-0000-0000-0000-000000000025"),
            epoch_id=UUID(EPOCH),
            sequence_number=3,
            previous_hash=second.event_hash,
            observed_at=NOW,
            appended_at=NOW,
        )
        self.events = [first, second, third]
        self.head = AuditHead(
            user_id=UUID(USER),
            epoch_id=UUID(EPOCH),
            epoch_number=1,
            event_count=3,
            last_sequence=3,
            last_event_id=third.id,
            last_event_hash=third.event_hash,
            genesis_event_id=first.id,
            genesis_event_hash=first.event_hash,
        )
        self.subjects = subjects
        self.tables: dict[str, list[dict[str, Any]]] = {
            "decision_runs": [subjects[0].data],
            "evidence_items": [subjects[1].data, subjects[2].data],
            "action_plans": [subjects[3].data],
            "bank_operations": [subjects[4].data],
            "action_receipts": [subjects[5].data],
            "simulated_bank_postings": [s.data for s in subjects[6:]],
            "policy_versions": [],
            "simulated_bank_redemptions": [],
        }
        self.contract: dict[str, Any] = {
            "protocol": CONTRACT,
            "run_begin_at": WHEN,
            "run_end_at": WHEN,
            "action_requirements": [{"opportunity_id": "action-op", "consent_mode": "ACTION"}],
            "decision_opportunities": [
                {
                    "opportunity_id": "decision-op",
                    "consuming_at": WHEN,
                    "required_evidence": [self.expected_evidence],
                }
            ],
            "audit_checkpoints": [{"checkpoint_id": "audit-op"}],
            "failure_opportunities": [
                {
                    "opportunity_id": "fault-op",
                    "first_failing_step": "step-fault",
                    "error_code": "TOOL_ERROR",
                    "cause_code": "TOOL_CAUSE",
                    "logical_cause_key": "cause-1",
                    "source_ref": self.cause_source,
                }
            ],
            "audit_verifier_sources": self.source_refs,
        }
        self.manifest: dict[str, Any] = {
            "protocol": RUN_PROTOCOL,
            "purpose": "TOOL_TEST_ONLY",
            "execution_mode": "TOOL_TEST_ONLY",
            "experiment_run_id": "11111111-1111-4111-8111-111111111111",
            "case_id": "trace-tool-only",
            "arm_id": "B0",
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": EPOCH,
            "user_id": USER,
            "run_status": "FAILED",
        }
        self.registrations: dict[str, dict[str, Any]] = {
            name: {
                "protocol": REG_PROTOCOL,
                "kind": name.upper(),
                "case_id": "trace-tool-only",
                "purpose": "TOOL_TEST_ONLY",
                "registration_status": "TOOL_TEST_ONLY",
            }
            for name in ("input", "oracle", "design", "rule", "source")
        }
        self.registrations["oracle"]["trace_metrics"] = self.contract
        self.registrations["rule"]["arm_id"] = "B0"
        self.registrations["source"]["files"] = [
            {"path": r["path"], "sha256": r["sha256"]} for r in self.source_refs
        ] + [{"path": "causal.py", "sha256": self.cause_source["sha256"]}]
        self.coverage = {
            "complete": True,
            "user_id": USER,
            "epoch_id": EPOCH,
            "tables": list(INVENTORY_TABLES),
            "execution_http_ids": ["execute"],
        }
        self.exchanges = [
            {
                "exchange_id": "execute",
                "operation": "EXECUTE_ACTION",
                "action_id": ACTION,
                "step_id": "step-execute",
                "sequence": 1,
                "status_code": 200,
                "user_id": USER,
                "started_at": WHEN,
                "finished_at": WHEN,
                "response_body": {"fixture": "TOOL_TEST_ONLY"},
            },
            {
                "exchange_id": "fault",
                "operation": "TOOL_FAULT",
                "step_id": "step-fault",
                "sequence": 2,
                "status_code": 409,
                "user_id": USER,
                "started_at": WHEN,
                "finished_at": WHEN,
                "logical_cause_key": "cause-1",
                "response_body": {"error_code": "TOOL_ERROR"},
            },
        ]
        self.error = {
            "user_id": USER,
            "exchange_id": "fault",
            "failure_id": "failure-1",
            "step_id": "step-fault",
            "error_code": "TOOL_ERROR",
            "cause_code": "TOOL_CAUSE",
            "logical_cause_key": "cause-1",
            "occurred_at": WHEN,
            "message": "TOOL_TEST_ONLY",
            "source_ref": self.cause_source,
        }
        self.causal_trace = {
            "failure_id": "failure-1",
            "step_id": "step-fault",
            "error_code": "TOOL_ERROR",
            "cause_code": "TOOL_CAUSE",
            "logical_cause_key": "cause-1",
            "source_ref": self.cause_source,
            "frames": [self.cause_source],
        }
        self.audit_originals: dict[str, Any] = {
            "head_text": self.head.model_dump_json(),
            "event_texts": [event_canonical_text(e) for e in self.events],
            "subject_texts": [subject_canonical_text(s) for s in subjects],
            "current_subject_texts": [subject_canonical_text(s) for s in subjects],
            "checkpoint_text": checkpoint_canonical_text(
                build_checkpoint(self.head, captured_at=NOW)
            ),
            "reference_manifest": {
                "event_ids": [str(e.id) for e in self.events],
                "event_count": 3,
                "subject_keys": [
                    {"kind": s.kind, "id": str(s.id), "snapshot_hash": subject_hash(s)}
                    for s in subjects
                ],
                "current_keys": [
                    {"kind": s.kind, "id": str(s.id), "snapshot_hash": subject_hash(s)}
                    for s in subjects
                ],
            },
        }
        self.verification = verify_epoch(
            self.events,
            head=self.head,
            expected_user_id=UUID(USER),
            references=ReferenceBundle(subjects=subjects, current_subjects=subjects),
            checkpoint=build_checkpoint(self.head, captured_at=NOW),
            checkpoint_mode="EXACT",
        ).model_dump(mode="json")
        self.trace_records: dict[str, Any] = {"actions": [], "decisions": [], "failures": []}
        self.omit_actions = False
        self.omit_decisions = False
        self.omit_failures = False
        self.omit_audit = False
        self.save()

    def save(self) -> None:
        artifact_refs = {}
        for name, original in self.registrations.items():
            artifact_refs[name] = {
                "path": name + ".json",
                "sha256": write(self.root / (name + ".json"), original),
            }
        self.manifest.update(
            artifact_refs=artifact_refs,
            **{name + "_sha256": ref["sha256"] for name, ref in artifact_refs.items()},
        )
        bindings = {key: self.manifest[key] for key in BINDINGS}
        raw_refs = []

        def raw(name: str, kind: str, payload: dict[str, Any]) -> str:
            digest = write(
                self.root / (name + ".json"),
                {"protocol": RAW_PROTOCOL, "kind": kind, "bindings": bindings, "payload": payload},
            )
            raw_refs.append({"path": name + ".json", "sha256": digest, "kind": kind})
            return digest

        def ref(artifact: str, pointer: str, value: Any) -> dict[str, str]:
            return {
                "artifact_sha256": artifact,
                "json_pointer": "/payload/" + pointer,
                "value_sha256": digest_value(value),
            }

        snapshot_hash = raw(
            "snapshot",
            "TRACE_SNAPSHOT",
            {
                "role": "FINAL",
                "captured_at": WHEN,
                "coverage": self.coverage,
                "tables": self.tables,
            },
        )
        http_hash = raw(
            "http",
            "HTTP_EXCHANGES",
            {
                "exchanges": self.exchanges,
                "verification": self.verification,
                "capture": {
                    "complete": True,
                    "run_ref": bindings,
                    "exchange_ids": [row["exchange_id"] for row in self.exchanges],
                    "error_record_ids": ["failure-1"],
                },
            },
        )
        self.error["run_ref"] = bindings
        self.causal_trace["run_ref"] = bindings
        error_hash = raw(
            "errors", "ERROR_RECORDS", {"errors": [self.error], "traces": [self.causal_trace]}
        )
        self.audit_originals["current_business_refs"] = [
            {
                "table": table,
                "id": row["id"],
                "ref": ref(snapshot_hash, f"tables/{table}/{index}", row),
            }
            for table, rows in self.tables.items()
            for index, row in enumerate(rows)
            if (table, row["id"])
            in {
                (t, str(s.id))
                for s in self.subjects
                for t in self.tables
                if {
                    "decision_runs": "DECISION_RUN",
                    "evidence_items": "EVIDENCE",
                    "action_plans": "ACTION_PLAN",
                    "bank_operations": "BANK_OPERATION",
                    "action_receipts": "ACTION_RECEIPT",
                    "simulated_bank_postings": "BANK_POSTING",
                }.get(t)
                == s.kind
            }
        ]
        raw(
            "audit",
            "AUDIT_ORIGINALS",
            {
                "checkpoints": []
                if self.omit_audit
                else [
                    {
                        "checkpoint_id": "audit-op",
                        "originals": self.audit_originals,
                        "verification_ref": ref(http_hash, "verification", self.verification),
                    }
                ]
            },
        )

        def table_ref(table: str, index: int = 0) -> dict[str, str]:
            return ref(snapshot_hash, f"tables/{table}/{index}", self.tables[table][index])

        decision_record = {
            "opportunity_id": "decision-op",
            "decision_ref": table_ref("decision_runs"),
            "evidence_refs": {FACT: table_ref("evidence_items")},
        }
        action_record = {
            "action_id": ACTION,
            "opportunity_id": "action-op",
            "decision_opportunity_id": "decision-op",
            "action_ref": table_ref("action_plans"),
            "decision_ref": table_ref("decision_runs"),
            "bank_ref": table_ref("bank_operations"),
            "version_refs": {},
            "policy_consent_refs": {},
            "action_consent_ref": table_ref("evidence_items", 1),
            "receipt_refs": [table_ref("action_receipts")],
            "audit_checkpoint_id": "audit-op",
        }
        failure_record = {
            "failure_id": "failure-1",
            "opportunity_id": "fault-op",
            "logical_cause_key": "cause-1",
            "first_failing_step": "step-fault",
            "error_code": "TOOL_ERROR",
            "cause_code": "TOOL_CAUSE",
            "source_ref": self.cause_source,
            "http_ref": ref(http_hash, "exchanges/1", self.exchanges[1]),
            "error_ref": ref(error_hash, "errors/0", self.error),
            "trace_ref": ref(error_hash, "traces/0", self.causal_trace),
            "audit_checkpoint_id": "audit-op",
            "audit_event_id": str(self.events[1].id),
            "run_ref": bindings,
        }
        if not self.trace_records["actions"]:
            self.trace_records["actions"] = [action_record]
        else:
            self.trace_records["actions"][0].update(
                {
                    key: value
                    for key, value in action_record.items()
                    if key.endswith("_ref") or key.endswith("_refs")
                }
            )
        if not self.trace_records["decisions"]:
            self.trace_records["decisions"] = [decision_record]
        else:
            self.trace_records["decisions"][0].update(
                decision_ref=decision_record["decision_ref"],
                evidence_refs=decision_record["evidence_refs"],
            )
        if not self.trace_records["failures"]:
            self.trace_records["failures"] = [failure_record]
        else:
            self.trace_records["failures"][0].update(
                {key: value for key, value in failure_record.items() if key.endswith("_ref")}
            )
        records = {
            key: ([] if getattr(self, "omit_" + key) else value)
            for key, value in self.trace_records.items()
        }
        raw("records", "TRACE_RECORDS", records)
        self.manifest["raw_refs"] = raw_refs
        write(self.root / "run.json", self.manifest)

    def observe(self) -> dict[str, Any]:
        return observe(self.root / "run.json")


def test_complete_typed_originals_are_tool_measurements_only(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    result = fixture.observe()
    assert result["financial_effect_evidence"] is False
    assert result["bindings"]["purpose"] == "TOOL_TEST_ONLY"
    for metric in result["metrics"].values():
        assert metric["status"] == "MEASURED", metric
        assert metric["value"] == 1 and metric["numerator"] == metric["denominator"] == 1


@pytest.mark.parametrize("key", ["case_id", "arm_id", "experiment_run_id", "purpose", "user_id"])
def test_raw_run_case_arm_owner_purpose_binding_cannot_be_relabelled(
    tmp_path: Path, key: str
) -> None:
    fixture = Fixture(tmp_path)
    raw = json.loads((tmp_path / "records.json").read_bytes())
    raw["bindings"][key] = "other"
    digest = write(tmp_path / "records.json", raw)
    fixture.manifest["raw_refs"][-1]["sha256"] = digest
    write(tmp_path / "run.json", fixture.manifest)
    with pytest.raises(ObservationError, match="binding differs"):
        fixture.observe()


def test_missing_actual_trace_cannot_remove_actual_action_from_denominator(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.omit_actions = True
    fixture.save()
    a1 = fixture.observe()["metrics"]["A1"]
    assert a1["status"] == "MISSING" and a1["value"] is None and a1["denominator"] == 1
    assert a1["applicable_units"] == [ACTION]


def test_unattempted_decision_and_missing_raw_file_preserve_frozen_denominator(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    fixture.contract["decision_opportunities"].append(
        {
            "opportunity_id": "never-executed",
            "consuming_at": WHEN,
            "required_evidence": [fixture.expected_evidence],
        }
    )
    fixture.save()
    a2 = fixture.observe()["metrics"]["A2"]
    assert a2["denominator"] == 2 and a2["numerator"] == 1 and a2["status"] == "MISSING"
    # Rename preserves the actual bytes; no negative original is deleted or overwritten.
    (tmp_path / "records.json").rename(tmp_path / "records-absent-original.json")
    result = fixture.observe()
    assert result["missing_originals"]
    assert result["metrics"]["A2"]["denominator"] == 2
    assert result["metrics"]["A2"]["value"] is None


@pytest.mark.parametrize(
    "mutation",
    ["hash", "owner", "subject", "future", "expired", "status", "empty_manifest", "unconsumed"],
)
def test_a2_checks_original_hash_subject_owner_time_and_consumption(
    tmp_path: Path, mutation: str
) -> None:
    fixture = Fixture(tmp_path)
    fact = fixture.tables["evidence_items"][0]
    if mutation == "hash":
        fact["content_hash"] = "f" * 64
    elif mutation == "owner":
        fact["user_id"] = str(uuid4())
    elif mutation == "subject":
        fixture.expected_evidence["subject_fields"]["account_id"] = str(uuid4())
    elif mutation == "future":
        fact["observed_at"] = "2026-10-05T00:00:00Z"
    elif mutation == "expired":
        fact["valid_to"] = WHEN
    elif mutation == "status":
        fact["status"] = "UNKNOWN"
    elif mutation == "empty_manifest":
        fixture.contract["decision_opportunities"][0]["required_evidence"] = []
    else:
        fixture.tables["decision_runs"][0]["evidence_ids"] = []
    fixture.save()
    a2 = fixture.observe()["metrics"]["A2"]
    assert a2["value"] != 1 and a2["denominator"] == 1 and a2["numerator"] == 0


@pytest.mark.parametrize(
    "mutation", ["action_hash", "decision_hash", "bank_key", "consent", "receipt", "legacy"]
)
def test_a1_requires_full_original_links_not_success_labels(tmp_path: Path, mutation: str) -> None:
    fixture = Fixture(tmp_path)
    action = fixture.tables["action_plans"][0]
    if mutation == "action_hash":
        action["request_hash"] = "f" * 64
    elif mutation == "decision_hash":
        fixture.tables["decision_runs"][0]["snapshot_hash"] = "f" * 64
    elif mutation == "bank_key":
        fixture.tables["bank_operations"][0]["idempotency_key"] = "changed"
    elif mutation == "consent":
        fixture.tables["evidence_items"][1]["content"]["accepted"] = False
    elif mutation == "receipt":
        fixture.tables["action_receipts"][0]["response"]["bank_operation_id"] = str(uuid4())
    else:
        action["request"] = {"bank_request": {"kind": "REDEEM"}}
        action["request_hash"] = digest_value(action["request"])
    fixture.save()
    a1 = fixture.observe()["metrics"]["A1"]
    assert a1["value"] != 1 and a1["denominator"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "no_current",
        "no_current_field",
        "missing_current_refs",
        "event_tail",
        "verification_count",
        "unsupported",
        "head_missing",
    ],
)
def test_a3_rechecks_actual_typed_chain_and_complete_originals(
    tmp_path: Path, mutation: str
) -> None:
    fixture = Fixture(tmp_path)
    originals = fixture.audit_originals
    if mutation == "no_current":
        originals["current_subject_texts"] = []
        originals["reference_manifest"]["current_keys"] = []
    elif mutation == "no_current_field":
        originals.pop("current_subject_texts")
    elif mutation == "missing_current_refs":
        fixture.tables["simulated_bank_postings"] = []
    elif mutation == "event_tail":
        originals["event_texts"].pop()
    elif mutation == "verification_count":
        fixture.verification["actual_count"] = 1
    elif mutation == "unsupported":
        originals["event_texts"][1] = originals["event_texts"][1].replace(
            '"payload_version":1', '"payload_version":99'
        )
    else:
        originals.pop("head_text")
    fixture.save()
    a3 = fixture.observe()["metrics"]["A3"]
    assert a3["value"] != 1 and a3["denominator"] == 1 and a3["numerator"] == 0
    assert a3["failures"]


def test_a3_old_current_source_is_missing_not_financial_failure(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    ref = next(
        ref for ref in fixture.source_refs if ref["original_path"].endswith("audit_chain.py")
    )
    old = (tmp_path / ref["path"]).read_bytes() + b"\n# TOOL_TEST_ONLY old version\n"
    (tmp_path / ref["path"]).write_bytes(old)
    ref["sha256"] = hashlib.sha256(old).hexdigest()
    for registered in fixture.registrations["source"]["files"]:
        if registered["path"] == ref["path"]:
            registered["sha256"] = ref["sha256"]
    fixture.save()
    a3 = fixture.observe()["metrics"]["A3"]
    assert a3["status"] == "MISSING" and a3["value"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "message_only",
        "wrong_code",
        "wrong_source",
        "wrong_cause",
        "wrong_step",
        "wrong_stack",
        "no_audit",
    ],
)
def test_a4_nonempty_error_message_cannot_establish_expected_cause(
    tmp_path: Path, mutation: str
) -> None:
    fixture = Fixture(tmp_path)
    if mutation == "message_only":
        fixture.error.pop("cause_code")
    elif mutation == "wrong_code":
        fixture.error["error_code"] = "SOME_OTHER_ERROR"
    elif mutation == "wrong_source":
        fixture.error["source_ref"] = {**fixture.cause_source, "line": 1}
    elif mutation == "wrong_cause":
        fixture.error["cause_code"] = "OTHER_CAUSE"
    elif mutation == "wrong_step":
        fixture.trace_records["failures"][0]["first_failing_step"] = "another-step"
    elif mutation == "wrong_stack":
        fixture.causal_trace["frames"] = []
    else:
        fixture.omit_audit = True
    fixture.save()
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["value"] != 1 and a4["denominator"] == 1 and a4["numerator"] == 0


def test_unexpected_unreported_actual_failure_stays_visible_once(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.exchanges.extend(
        [
            {
                **fixture.exchanges[1],
                "exchange_id": "unexpected-a",
                "sequence": 3,
                "logical_cause_key": "unexpected-cause",
            },
            {
                **fixture.exchanges[1],
                "exchange_id": "unexpected-retry",
                "sequence": 4,
                "logical_cause_key": "unexpected-cause",
            },
        ]
    )
    fixture.save()
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["denominator"] == 2 and a4["numerator"] == 1 and a4["status"] == "MISSING"
    assert "UNEXPECTED:unexpected-cause" in a4["applicable_units"]


def test_missing_predeclared_fault_does_not_leave_denominator(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.omit_failures = True
    fixture.save()
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["denominator"] == 1 and a4["value"] is None


def test_zero_denominators_and_not_run_never_become_100_percent(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    for key in (
        "action_requirements",
        "decision_opportunities",
        "audit_checkpoints",
        "failure_opportunities",
    ):
        fixture.contract[key] = []
    fixture.tables = {key: [] for key in fixture.tables}
    fixture.coverage["execution_http_ids"] = []
    fixture.exchanges = [{"exchange_id": "tool-only-no-execution", "status_code": 200}]
    fixture.omit_actions = fixture.omit_decisions = fixture.omit_failures = fixture.omit_audit = (
        True
    )
    # Rebuild empty raw artifacts directly; no fixture-generated action/error reference needed.
    for name in ("oracle",):
        write(tmp_path / (name + ".json"), fixture.registrations[name])
    fixture.manifest["artifact_refs"]["oracle"]["sha256"] = hashlib.sha256(
        (tmp_path / "oracle.json").read_bytes()
    ).hexdigest()
    fixture.manifest["oracle_sha256"] = fixture.manifest["artifact_refs"]["oracle"]["sha256"]
    bindings = {key: fixture.manifest[key] for key in BINDINGS}
    payloads = {
        "snapshot": {
            "role": "FINAL",
            "captured_at": WHEN,
            "coverage": fixture.coverage,
            "tables": fixture.tables,
        },
        "http": {
            "exchanges": [],
            "capture": {
                "complete": True,
                "run_ref": bindings,
                "exchange_ids": [],
                "error_record_ids": [],
            },
        },
        "errors": {"errors": [], "traces": []},
        "audit": {"checkpoints": []},
        "records": {"actions": [], "decisions": [], "failures": []},
    }
    for registered in fixture.manifest["raw_refs"]:
        name = Path(registered["path"]).stem
        registered["sha256"] = write(
            tmp_path / registered["path"],
            {
                "protocol": RAW_PROTOCOL,
                "kind": registered["kind"],
                "bindings": bindings,
                "payload": payloads[name],
            },
        )
    write(tmp_path / "run.json", fixture.manifest)
    result = fixture.observe()
    assert all(
        row["status"] == "NOT_APPLICABLE" and row["value"] is None and row["denominator"] == 0
        for row in result["metrics"].values()
    )
    fixture.manifest["run_status"] = "NOT_RUN"
    write(tmp_path / "run.json", fixture.manifest)
    assert all(
        row["status"] == "NOT_RUN" and row["value"] is None
        for row in fixture.observe()["metrics"].values()
    )


def test_output_is_exclusive_and_original_bytes_stay_unchanged(tmp_path: Path) -> None:
    Fixture(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.iterdir()}
    output = tmp_path / "trace-observations.json"
    assert main(["--run", str(tmp_path / "run.json"), "--output", str(output)]) == 0
    assert all(path.read_bytes() == content for path, content in originals.items())
    saved = output.read_bytes()
    with pytest.raises(SystemExit):
        main(["--run", str(tmp_path / "run.json"), "--output", str(output)])
    assert output.read_bytes() == saved


def test_duplicate_json_key_and_wrong_original_value_digest_are_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    bundle = TraceBundle(tmp_path / "run.json")
    raw = fixture.trace_records["decisions"][0]["decision_ref"]
    with pytest.raises(ObservationError, match="value digest differs"):
        bundle.resolve({**raw, "value_sha256": "f" * 64})
    content = (
        (tmp_path / "records.json")
        .read_text(encoding="utf-8")
        .replace('{"bindings":', '{"kind":"rebound","bindings":', 1)
        .encode()
    )
    (tmp_path / "records.json").write_bytes(content)
    fixture.manifest["raw_refs"][-1]["sha256"] = hashlib.sha256(content).hexdigest()
    write(tmp_path / "run.json", fixture.manifest)
    with pytest.raises(ObservationError, match="Duplicate JSON key"):
        fixture.observe()


def test_predeclared_missing_failure_and_matching_raw_cause_count_once(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.omit_failures = True
    fixture.save()
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["denominator"] == 1 and a4["value"] is None
    assert a4["applicable_units"] == ["fault-op"]


def test_original_error_only_unexpected_failure_is_not_dropped(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.error["logical_cause_key"] = "unexpected-error-only"
    fixture.save()
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["denominator"] == 2 and a4["value"] is None
    assert "UNEXPECTED:unexpected-error-only" in a4["applicable_units"]


def test_missing_http_error_capture_cannot_establish_complete_failure_rate(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = json.loads((tmp_path / "http.json").read_bytes())
    original["payload"].pop("capture")
    fixture.manifest["raw_refs"][1]["sha256"] = write(tmp_path / "http.json", original)
    write(tmp_path / "run.json", fixture.manifest)
    a4 = fixture.observe()["metrics"]["A4"]
    assert a4["status"] == "MISSING" and a4["value"] is None and a4["denominator"] == 1


def test_failed_units_retain_original_reference_and_reason(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.tables["evidence_items"][0]["content_hash"] = "f" * 64
    fixture.save()
    a2 = fixture.observe()["metrics"]["A2"]
    assert a2["failures"][0]["reason"]
    assert a2["failures"][0]["raw_refs"]


def test_global_catalog_is_explicit_and_never_weakens_tenant_owner_checks(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    product_id = str(uuid4())
    fixture.tables["asset_products"] = [{"id": product_id, "version_number": 1}]
    fixture.save()
    bundle = TraceBundle(tmp_path / "run.json")
    artifact = bundle.present("TRACE_SNAPSHOT")[0]["ref"]["sha256"]
    ref = {
        "artifact_sha256": artifact,
        "json_pointer": "/payload/tables/asset_products/0",
        "value_sha256": digest_value(fixture.tables["asset_products"][0]),
    }
    assert bundle.row(ref, "asset_products", product_id, scope="GLOBAL_CATALOG")["id"] == product_id
    with pytest.raises(ObservationError):
        bundle.row(ref, "asset_products", product_id)
    with pytest.raises(ObservationError):
        bundle.row(ref, "accounts", product_id, scope="GLOBAL_CATALOG")


def test_loaded_old_verifier_code_cannot_claim_current_source_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import audit_chain

    fixture = Fixture(tmp_path)

    def old_verifier(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("TOOL_TEST_ONLY old loaded verifier")

    old_verifier.__module__ = "app.domain.audit_chain"
    monkeypatch.setattr(audit_chain, "verify_epoch", old_verifier)
    a3 = fixture.observe()["metrics"]["A3"]
    assert a3["status"] == "MISSING" and a3["value"] is None
    assert "Loaded verifier" in a3["missing_reason"]
