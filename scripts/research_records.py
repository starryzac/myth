"""Local anonymous study records; no recruitment, bank access or research conclusions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

PROTOCOL = "bounded-funds-adult-simulated-study-v1"
CONDITIONS = ("A", "B", "C")
TASKS = tuple(f"R{number:02}" for number in range(1, 7))
SOURCES = {"RESEARCHER_RECORDED", "PARTICIPANT_SELF_REPORTED"}
STATUSES = {"COMPLETED", "PARTIAL", "ABANDONED", "NOT_RUN", "UNSUPPORTED", "INTERRUPTED"}
MAX_BYTES = 1_048_576


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def exact(value: Any, keys: set[str]) -> dict[str, Any]:
    check(type(value) is dict and set(value) == keys, "Record fields must match the closed schema")
    return dict(value)


def identifier(value: Any) -> str:
    check(type(value) is str, "Anonymous identity must be a UUID4")
    parsed = UUID(value)
    check(parsed.version == 4 and str(parsed) == value, "Use a canonical random UUID4")
    return str(parsed)


def timestamp(value: Any) -> datetime:
    check(type(value) is str, "Time must be an explicit aware ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    check(parsed.tzinfo is not None and parsed.utcoffset() is not None, "Naive time is refused")
    return parsed


def integer(value: Any, maximum: int = 10000) -> None:
    check(type(value) is int and 0 <= value <= maximum, "Count/score must be a bounded integer")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        check(key not in result, "Duplicate JSON keys are refused")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ValueError("Nonfinite JSON values cannot become missing observations")


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    check(len(raw) <= MAX_BYTES, "Record exceeds the explicit 1 MiB budget")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    check(type(value) is dict, "Record must be a JSON object")
    return dict(value)


def _registration(directory: Path) -> dict[str, Any]:
    value = read_json(directory / "registration.json")
    exact(
        value,
        {
            "protocol",
            "study_id",
            "created_at",
            "retention_until",
            "consent_source_path",
            "consent_document_sha256",
            "conditions",
            "tasks",
            "human_identity_verified",
        },
    )
    check(
        value["protocol"] == PROTOCOL and value["human_identity_verified"] is False,
        "Study registration has no identity certification",
    )
    identifier(value["study_id"])
    check(
        timestamp(value["retention_until"]) > timestamp(value["created_at"]),
        "Retention end must follow registration",
    )
    check(
        value["conditions"] == list(CONDITIONS) and value["tasks"] == list(TASKS),
        "Retain the original three by six denominator",
    )
    check(
        hashlib.sha256((directory / "consent.original.md").read_bytes()).hexdigest()
        == value["consent_document_sha256"],
        "Original consent material differs",
    )
    return value


def initialize(
    directory: Path, study_id: str, consent_file: Path, retention_until: str
) -> dict[str, Any]:
    identifier(study_id)
    now = datetime.now(UTC)
    check(timestamp(retention_until) > now, "Provide an explicit future retention end")
    original = consent_file.read_bytes()
    check(0 < len(original) <= MAX_BYTES, "Consent material must be present and bounded")
    original.decode("utf-8")
    directory.mkdir(parents=True, exist_ok=False)
    value = {
        "protocol": PROTOCOL,
        "study_id": study_id,
        "created_at": now.isoformat(),
        "retention_until": retention_until,
        "consent_source_path": str(consent_file.resolve()),
        "consent_document_sha256": hashlib.sha256(original).hexdigest(),
        "conditions": list(CONDITIONS),
        "tasks": list(TASKS),
        "human_identity_verified": False,
    }
    (directory / "consent.original.md").write_bytes(original)
    (directory / "registration.json").write_bytes(canonical(value) + b"\n")
    (directory / "records.ndjson").write_bytes(b"")
    return {
        "status": "NOT_STARTED",
        "study_id": study_id,
        "records": 0,
        "participants": 0,
        "research_conclusions": None,
    }


def _records(directory: Path, registration: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous: str | None = None
    for raw in (directory / "records.ndjson").read_bytes().splitlines():
        check(0 < len(raw) <= MAX_BYTES, "Malformed/oversized original record")
        row = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        exact(
            row,
            {
                "event_id",
                "sequence",
                "previous_hash",
                "record_hash",
                "recorded_at",
                "input_sha256",
                "input",
                "generated_private_receipt",
                "human_identity_verified",
            },
        )
        identifier(row["event_id"])
        check(
            type(row["sequence"]) is int
            and row["sequence"] == len(rows) + 1
            and row["previous_hash"] == previous,
            "Original record order differs",
        )
        check(
            row["human_identity_verified"] is False and row["input_sha256"] == digest(row["input"]),
            "Original input differs or claims verified identity",
        )
        claimed = row["record_hash"]
        check(
            claimed == digest({key: value for key, value in row.items() if key != "record_hash"}),
            "Original record hash differs",
        )
        timestamp(row["recorded_at"])
        check(
            not any(old["input"]["event_key"] == row["input"]["event_key"] for old in rows),
            "Original event keys must be unique",
        )
        validate_record(row["input"], registration, rows)
        if row["input"]["kind"] == "CONSENT":
            receipt = exact(row["generated_private_receipt"], {"withdrawal_token"})
            identifier(receipt["withdrawal_token"])
        else:
            check(
                row["generated_private_receipt"] is None,
                "Non-consent records cannot invent private consent receipts",
            )
        previous = claimed
        rows.append(dict(row))
    return rows


def _consent(rows: list[dict[str, Any]], participant_id: str) -> dict[str, Any]:
    found = [
        row
        for row in rows
        if row["input"]["kind"] == "CONSENT" and row["input"]["participant_id"] == participant_id
    ]
    check(len(found) == 1, "A single prior explicit adult consent is required")
    consent = found[0]
    check(
        consent["input"]["adult_self_reported"] is True
        and consent["input"]["explicit_consent"] is True,
        "Adult status and consent cannot be inferred",
    )
    return consent


def _feedback(value: Any) -> None:
    check(
        value is None or type(value) is str and len(value) <= 2000,
        "Feedback must be bounded anonymous text or null",
    )
    if value is not None:
        check(
            not re.search(
                r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)\d{11,19}[Xx]?(?!\d)|(?:姓名|手机号|身份证|银行卡|真实账号|住址|密码|token)\s*[:：=]",
                value,
                re.IGNORECASE,
            ),
            "Possible personal/account/secret data is refused; review locally",
        )


def validate_record(
    value: Any, registration: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    check(type(value) is dict, "Input must be an object")
    kind = value.get("kind")
    common = {"kind", "event_key", "participant_id"}
    keys = {
        "CONSENT": common
        | {
            "adult_self_reported",
            "explicit_consent",
            "quote_consent",
            "consent_document_sha256",
            "consented_at",
            "condition_order",
        },
        "TASK_OBSERVATION": common
        | {
            "condition",
            "task_id",
            "status",
            "measurement_source",
            "started_at",
            "ended_at",
            "duration_seconds",
            "intervention_count",
            "researcher_help_count",
            "supersedes_event_id",
        },
        "QUESTIONNAIRE": common
        | {
            "condition",
            "measurement_source",
            "control_answers",
            "conceptual_answers",
            "confidence_percent",
            "feedback_text",
            "feedback_privacy_reviewed",
            "supersedes_event_id",
        },
        "WITHDRAWAL": common | {"withdrawal_token"},
    }
    check(type(kind) is str and kind in keys, "Unsupported record kind")
    record = exact(value, keys[kind])
    identifier(record["event_key"])
    participant = identifier(record["participant_id"])
    if kind == "CONSENT":
        check(
            record["adult_self_reported"] is True
            and record["explicit_consent"] is True
            and type(record["quote_consent"]) is bool,
            "Consent requires explicit true adult self-report and consent",
        )
        check(
            record["consent_document_sha256"] == registration["consent_document_sha256"],
            "Consent must bind the original material",
        )
        check(
            timestamp(record["consented_at"]) <= datetime.now(UTC),
            "Consent cannot be recorded as a future observation",
        )
        order = record["condition_order"]
        check(
            type(order) is list and len(order) == 3 and set(order) == set(CONDITIONS),
            "Condition order must retain A/B/C exactly once",
        )
        check(
            not any(row["input"]["participant_id"] == participant for row in rows),
            "Participant already has original records",
        )
        return record
    consent = _consent(rows, participant)
    withdrawn = any(
        row["input"]["kind"] == "WITHDRAWAL" and row["input"]["participant_id"] == participant
        for row in rows
    )
    check(not withdrawn, "Withdrawn participant records cannot be extended")
    if kind == "WITHDRAWAL":
        check(
            record["withdrawal_token"] == consent["generated_private_receipt"]["withdrawal_token"],
            "Use the original private withdrawal token",
        )
        return record
    check(
        record["condition"] in CONDITIONS and record["measurement_source"] in SOURCES,
        "Use an original condition and explicit manual measurement source",
    )
    if kind == "TASK_OBSERVATION":
        check(
            record["task_id"] in TASKS and record["status"] in STATUSES,
            "Use one of the original six tasks and an explicit result",
        )
        start, end = record["started_at"], record["ended_at"]
        if start is not None:
            check(
                timestamp(start) >= timestamp(consent["input"]["consented_at"]),
                "Task cannot precede explicit consent",
            )
        if end is not None:
            check(
                start is not None
                and timestamp(end) >= timestamp(start)
                and timestamp(end) <= datetime.now(UTC),
                "Task end needs an actual ordered start",
            )
        duration = record["duration_seconds"]
        check(
            duration is None
            or type(duration) in (int, float)
            and math.isfinite(duration)
            and duration >= 0
            and start is not None
            and end is not None,
            "Manual duration is null or explicit finite seconds with times",
        )
        for field in ("intervention_count", "researcher_help_count"):
            if record[field] is not None:
                integer(record[field])
        previous = [
            row
            for row in rows
            if row["input"]["kind"] == kind
            and row["input"]["participant_id"] == participant
            and row["input"]["condition"] == record["condition"]
            and row["input"]["task_id"] == record["task_id"]
        ]
    else:
        controls = exact(record["control_answers"], {f"C{i}" for i in range(1, 7)})
        for answer in controls.values():
            if answer is not None:
                integer(answer, 7)
                check(answer >= 1, "Control scale starts at one")
        concepts = exact(record["conceptual_answers"], {f"K{i}" for i in range(1, 7)})
        check(
            all(
                answer is None or answer in ("YES", "NO", "UNKNOWN", "SKIPPED")
                for answer in concepts.values()
            ),
            "Concept answers are explicit choices or null",
        )
        if record["confidence_percent"] is not None:
            integer(record["confidence_percent"], 100)
        _feedback(record["feedback_text"])
        check(
            type(record["feedback_privacy_reviewed"]) is bool,
            "Manual feedback privacy declaration must be explicit",
        )
        previous = [
            row
            for row in rows
            if row["input"]["kind"] == kind
            and row["input"]["participant_id"] == participant
            and row["input"]["condition"] == record["condition"]
        ]
    check(
        record["supersedes_event_id"] == (previous[-1]["event_id"] if previous else None),
        "Correction must append and cite the latest exact original record",
    )
    return record


def append_record(directory: Path, value: dict[str, Any]) -> dict[str, Any]:
    lock = directory / ".append.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        registration = _registration(directory)
        rows = _records(directory, registration)
        replay = [row for row in rows if row["input"].get("event_key") == value.get("event_key")]
        if replay:
            check(
                replay[0]["input"] == value, "Same original event key cannot carry different input"
            )
            return {
                "status": "ORIGINAL_REPLAY",
                "record": replay[0],
                "human_identity_verified": False,
            }
        check(
            datetime.now(UTC) <= timestamp(registration["retention_until"]),
            "Retention window expired; stop collection",
        )
        record = validate_record(value, registration, rows)
        row = {
            "event_id": str(uuid4()),
            "sequence": len(rows) + 1,
            "previous_hash": rows[-1]["record_hash"] if rows else None,
            "recorded_at": datetime.now(UTC).isoformat(),
            "input_sha256": digest(record),
            "input": record,
            "generated_private_receipt": {"withdrawal_token": str(uuid4())}
            if record["kind"] == "CONSENT"
            else None,
            "human_identity_verified": False,
        }
        row["record_hash"] = digest(row)
        with (directory / "records.ndjson").open("ab") as stream:
            stream.write(canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {
            "status": "OPERATOR_RECORD_SAVED_NOT_HUMAN_VERIFIED",
            "record": row,
            "human_identity_verified": False,
        }
    finally:
        lock.unlink()


def export_records(
    directory: Path, output: Path, *, include_reviewed_text: bool = False
) -> dict[str, Any]:
    registration = _registration(directory)
    rows = _records(directory, registration)
    withdrawn = {
        row["input"]["participant_id"] for row in rows if row["input"]["kind"] == "WITHDRAWAL"
    }
    consent_rows = [row for row in rows if row["input"]["kind"] == "CONSENT"]
    active = [row for row in consent_rows if row["input"]["participant_id"] not in withdrawn]
    observations: list[dict[str, Any]] = []
    for consent in active:
        participant = consent["input"]["participant_id"]
        _consent(rows, participant)
        for condition in consent["input"]["condition_order"]:
            for task in TASKS:
                found = [
                    row
                    for row in rows
                    if row["input"]["kind"] == "TASK_OBSERVATION"
                    and row["input"]["participant_id"] == participant
                    and row["input"]["condition"] == condition
                    and row["input"]["task_id"] == task
                ]
                original = found[-1] if found else None
                record = original["input"] if original else {}
                observations.append(
                    {
                        "participant_id": participant,
                        "condition": condition,
                        "task_id": task,
                        "status": record.get("status", "MISSING_NOT_RECORDED"),
                        "measurement_source": record.get("measurement_source"),
                        "duration_seconds": record.get("duration_seconds"),
                        "intervention_count": record.get("intervention_count"),
                        "researcher_help_count": record.get("researcher_help_count"),
                        "original_event_id": original["event_id"] if original else None,
                    }
                )
    questionnaires = []
    for consent in active:
        participant = consent["input"]["participant_id"]
        for condition in consent["input"]["condition_order"]:
            found = [
                row
                for row in rows
                if row["input"]["kind"] == "QUESTIONNAIRE"
                and row["input"]["participant_id"] == participant
                and row["input"]["condition"] == condition
            ]
            item = found[-1]["input"] if found else {}
            questionnaires.append(
                {
                    "participant_id": participant,
                    "condition": condition,
                    "measurement_source": item.get("measurement_source"),
                    "control_answers": item.get("control_answers"),
                    "conceptual_answers": item.get("conceptual_answers"),
                    "confidence_percent": item.get("confidence_percent"),
                    "feedback_text": item.get("feedback_text")
                    if include_reviewed_text
                    and item.get("feedback_privacy_reviewed") is True
                    and consent["input"]["quote_consent"] is True
                    else None,
                    "feedback_review_is_operator_declaration": True,
                    "original_event_id": found[-1]["event_id"] if found else None,
                }
            )
    result = {
        "protocol": PROTOCOL,
        "study_id": registration["study_id"],
        "status": "NOT_STARTED" if not rows else "OPERATOR_RECORDS_PRESENT_NOT_HUMAN_VERIFIED",
        "human_identity_verified": False,
        "participants_with_consent_records": len(consent_rows),
        "participants_exported": len(active),
        "participants_withdrawn": len(withdrawn),
        "original_record_count": len(rows),
        "task_opportunity_denominator": len(active) * 18,
        "withdrawal_raw_records_deleted": False,
        "research_conclusions": None,
        "observations": observations,
        "questionnaires": questionnaires,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "anonymous.json").write_bytes(canonical(result) + b"\n")
    columns = [
        "participant_id",
        "condition",
        "task_id",
        "status",
        "measurement_source",
        "duration_seconds",
        "intervention_count",
        "researcher_help_count",
        "original_event_id",
    ]
    with (output / "tasks.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(observations)
    return {
        key: value for key, value in result.items() if key not in {"observations", "questionnaires"}
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create an empty owned anonymous study directory")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--study-id", required=True)
    init.add_argument("--consent-file", type=Path, required=True)
    init.add_argument("--retention-until", required=True)
    append = commands.add_parser("append", help="Append explicit operator-provided anonymous input")
    append.add_argument("--directory", type=Path, required=True)
    append.add_argument("--input", type=Path, required=True)
    export = commands.add_parser(
        "export", help="Export all 3 x 6 opportunities; exclude withdrawn records"
    )
    export.add_argument("--directory", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--include-reviewed-text", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(
                args.directory, args.study_id, args.consent_file, args.retention_until
            )
        elif args.command == "append":
            result = append_record(args.directory, read_json(args.input))
        else:
            result = export_records(
                args.directory, args.output, include_reviewed_text=args.include_reviewed_text
            )
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"Anonymous record operation refused: {error}\n")
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
