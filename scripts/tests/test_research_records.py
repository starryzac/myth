"""TOOL_ONLY anonymous fixtures. No humans, bank calls or research observations."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import research_records as tool


@pytest.fixture
def study(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    material = tmp_path / "synthetic-consent.md"
    material.write_text("SYNTHETIC_TOOL_ONLY_CONSENT_NOT_A_HUMAN_RECORD", encoding="utf-8")
    directory = tmp_path / "tool-only-study"
    result = tool.initialize(
        directory, str(uuid4()), material, (datetime.now(UTC) + timedelta(days=2)).isoformat()
    )
    assert result["status"] == "NOT_STARTED"
    return directory, tool._registration(directory)


def consent(registration: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "CONSENT",
        "event_key": str(uuid4()),
        "participant_id": str(uuid4()),
        "adult_self_reported": True,
        "explicit_consent": True,
        "quote_consent": False,
        "consent_document_sha256": registration["consent_document_sha256"],
        "consented_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        "condition_order": ["B", "C", "A"],
    }


def observation(participant: str) -> dict[str, Any]:
    return {
        "kind": "TASK_OBSERVATION",
        "event_key": str(uuid4()),
        "participant_id": participant,
        "condition": "A",
        "task_id": "R01",
        "status": "NOT_RUN",
        "measurement_source": "RESEARCHER_RECORDED",
        "started_at": None,
        "ended_at": None,
        "duration_seconds": None,
        "intervention_count": None,
        "researcher_help_count": None,
        "supersedes_event_id": None,
    }


def questionnaire(participant: str) -> dict[str, Any]:
    return {
        "kind": "QUESTIONNAIRE",
        "event_key": str(uuid4()),
        "participant_id": participant,
        "condition": "A",
        "measurement_source": "PARTICIPANT_SELF_REPORTED",
        "control_answers": {f"C{i}": None for i in range(1, 7)},
        "conceptual_answers": {f"K{i}": None for i in range(1, 7)},
        "confidence_percent": None,
        "feedback_text": None,
        "feedback_privacy_reviewed": False,
        "supersedes_event_id": None,
    }


def enroll(
    study: tuple[Path, dict[str, Any]], quote: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    directory, registration = study
    body = consent(registration)
    body["quote_consent"] = quote
    return body, tool.append_record(directory, body)["record"]


def test_empty_export_is_not_started_not_a_fake_participant(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    result = tool.export_records(study[0], tmp_path / "empty-export")
    assert result["status"] == "NOT_STARTED"
    assert (
        result["participants_exported"]
        == result["task_opportunity_denominator"]
        == result["original_record_count"]
        == 0
    )
    assert result["research_conclusions"] is None and not result["human_identity_verified"]
    assert (
        len((tmp_path / "empty-export/tasks.csv").read_text(encoding="utf-8-sig").splitlines()) == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("adult_self_reported", False),
        ("adult_self_reported", 1),
        ("explicit_consent", False),
        ("explicit_consent", "true"),
        ("quote_consent", 0),
    ],
)
def test_explicit_adult_consent_strict_not_a_cli_role_flag(
    study: tuple[Path, dict[str, Any]], field: str, value: Any
) -> None:
    record = consent(study[1])
    record[field] = value
    with pytest.raises(ValueError):
        tool.append_record(study[0], record)
    assert (study[0] / "records.ndjson").read_bytes() == b""


@pytest.mark.parametrize("field", ["name", "email", "phone", "bank_account", "role", "success"])
def test_unknown_identity_and_result_fields_rejected(
    study: tuple[Path, dict[str, Any]], field: str
) -> None:
    body = consent(study[1])
    body[field] = "SYNTHETIC_FORBIDDEN"
    with pytest.raises(ValueError):
        tool.append_record(study[0], body)


def test_observation_without_prior_consent_cannot_be_collected(
    study: tuple[Path, dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="prior explicit"):
        tool.append_record(study[0], observation(str(uuid4())))


def test_same_key_replays_original_and_changed_body_refused(
    study: tuple[Path, dict[str, Any]],
) -> None:
    body, original = enroll(study)
    before = (study[0] / "records.ndjson").read_bytes()
    assert tool.append_record(study[0], body)["record"] == original
    assert (study[0] / "records.ndjson").read_bytes() == before
    body["quote_consent"] = not body["quote_consent"]
    with pytest.raises(ValueError, match="Same original"):
        tool.append_record(study[0], body)


def test_all_eighteen_opportunities_retained_missing_is_null(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    body, _ = enroll(study)
    task = observation(body["participant_id"])
    tool.append_record(study[0], task)
    result = tool.export_records(study[0], tmp_path / "denominator-export")
    data = tool.read_json(tmp_path / "denominator-export/anonymous.json")
    assert result["task_opportunity_denominator"] == 18 and len(data["observations"]) == 18
    assert sum(row["status"] == "MISSING_NOT_RECORDED" for row in data["observations"]) == 17
    assert all(
        row["duration_seconds"] is None and row["intervention_count"] is None
        for row in data["observations"]
    )
    assert result["research_conclusions"] is None


def test_manual_measurement_correction_preserves_original_body(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    body, _ = enroll(study)
    original_input = observation(body["participant_id"])
    first = tool.append_record(study[0], original_input)["record"]
    corrected = {
        **original_input,
        "event_key": str(uuid4()),
        "status": "PARTIAL",
        "started_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
        "ended_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "duration_seconds": 60.5,
        "intervention_count": 2,
        "researcher_help_count": 1,
        "supersedes_event_id": first["event_id"],
    }
    tool.append_record(study[0], corrected)
    tool.export_records(study[0], tmp_path / "correction-export")
    rows = tool._records(study[0], study[1])
    assert rows[1]["input"] == original_input
    data = tool.read_json(tmp_path / "correction-export/anonymous.json")
    found = next(
        row for row in data["observations"] if row["condition"] == "A" and row["task_id"] == "R01"
    )
    assert found["status"] == "PARTIAL" and found["duration_seconds"] == 60.5
    assert found["measurement_source"] == "RESEARCHER_RECORDED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("condition", "D"),
        ("task_id", "R07"),
        ("intervention_count", True),
        ("intervention_count", 1.2),
        ("duration_seconds", -1),
        ("measurement_source", "SOFTWARE_MEASURED"),
    ],
)
def test_original_denominator_and_manual_integer_contract(
    study: tuple[Path, dict[str, Any]], field: str, value: Any
) -> None:
    body, _ = enroll(study)
    record = observation(body["participant_id"])
    record[field] = value
    with pytest.raises(ValueError):
        tool.append_record(study[0], record)


def test_questionnaire_default_text_not_exported_and_no_default_scores(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    body, _ = enroll(study, quote=True)
    record = questionnaire(body["participant_id"])
    record["feedback_text"] = "SYNTHETIC_TOOL_ONLY_FEEDBACK_NOT_A_HUMAN_QUOTE"
    record["feedback_privacy_reviewed"] = True
    tool.append_record(study[0], record)
    tool.export_records(study[0], tmp_path / "default-text")
    data = tool.read_json(tmp_path / "default-text/anonymous.json")
    assert all(row["feedback_text"] is None for row in data["questionnaires"])
    assert all(row["confidence_percent"] is None for row in data["questionnaires"])
    tool.export_records(study[0], tmp_path / "reviewed-text", include_reviewed_text=True)
    data = tool.read_json(tmp_path / "reviewed-text/anonymous.json")
    assert (
        next(row for row in data["questionnaires"] if row["condition"] == "A")["feedback_text"]
        == record["feedback_text"]
    )
    assert data["research_conclusions"] is None


@pytest.mark.parametrize(
    "risk", ["bool-score", "missing-concept", "pii-email", "pii-account", "pii-name"]
)
def test_questionnaire_strict_missing_and_pii_risks(
    study: tuple[Path, dict[str, Any]], risk: str
) -> None:
    body, _ = enroll(study)
    record = questionnaire(body["participant_id"])
    if risk == "bool-score":
        record["control_answers"]["C1"] = True
    elif risk == "missing-concept":
        del record["conceptual_answers"]["K1"]
    else:
        record["feedback_text"] = {
            "pii-email": "test@example.invalid",
            "pii-account": "1234567890123456",
            "pii-name": "姓名：合成姓名",
        }[risk]
    with pytest.raises(ValueError):
        tool.append_record(study[0], record)


def test_withdrawal_excludes_every_exported_record_but_not_false_deleted(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    body, receipt = enroll(study)
    tool.append_record(study[0], observation(body["participant_id"]))
    withdraw = {
        "kind": "WITHDRAWAL",
        "event_key": str(uuid4()),
        "participant_id": body["participant_id"],
        "withdrawal_token": receipt["generated_private_receipt"]["withdrawal_token"],
    }
    tool.append_record(study[0], withdraw)
    result = tool.export_records(study[0], tmp_path / "withdrawn")
    assert result["participants_exported"] == result["task_opportunity_denominator"] == 0
    assert result["participants_withdrawn"] == 1 and result["original_record_count"] == 3
    assert not result["withdrawal_raw_records_deleted"]
    assert receipt["generated_private_receipt"]["withdrawal_token"] not in (
        tmp_path / "withdrawn/anonymous.json"
    ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Withdrawn"):
        tool.append_record(study[0], questionnaire(body["participant_id"]))


def test_wrong_withdrawal_and_unbound_correction_refused(
    study: tuple[Path, dict[str, Any]],
) -> None:
    body, _ = enroll(study)
    with pytest.raises(ValueError, match="withdrawal token"):
        tool.append_record(
            study[0],
            {
                "kind": "WITHDRAWAL",
                "event_key": str(uuid4()),
                "participant_id": body["participant_id"],
                "withdrawal_token": str(uuid4()),
            },
        )
    task = observation(body["participant_id"])
    tool.append_record(study[0], task)
    task["event_key"] = str(uuid4())
    with pytest.raises(ValueError, match="latest exact"):
        tool.append_record(study[0], task)


def test_hash_corruption_and_live_lock_cannot_export_or_append(
    study: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    body, _ = enroll(study)
    (study[0] / ".append.lock").write_text("OTHER_OWNED_PROCESS", encoding="utf-8")
    with pytest.raises(FileExistsError):
        tool.append_record(study[0], observation(body["participant_id"]))
    assert (study[0] / ".append.lock").read_text(encoding="utf-8") == "OTHER_OWNED_PROCESS"
    original = tool._records(study[0], study[1])[0]
    original["input"]["adult_self_reported"] = False
    (study[0] / "records.ndjson").write_text(json.dumps(original) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tool.export_records(study[0], tmp_path / "cannot-export")
    assert not (tmp_path / "cannot-export").exists()


def test_duplicate_json_and_nan_not_changed_into_null(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    for raw in ('{"score":1,"score":2}', '{"duration":NaN}'):
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(ValueError):
            tool.read_json(path)
