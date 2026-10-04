"""Subject encoders retain pre-optimization originals through strict public seams."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.domain import audit_chain
from app.domain.audit_chain_types import AuditSubject
from app.tests.test_audit_chain_domain import recorded_chain

ORIGINALS = cast(
    dict[str, Any],
    json.loads(
        (Path(__file__).parent / "fixtures/audit_subject_encoding_originals.json").read_text(
            encoding="utf-8"
        )
    ),
)


def original_fields(case: dict[str, Any]) -> dict[str, Any]:
    fields = deepcopy(cast(dict[str, Any], case["fields"]))
    for field in ("user_id", "epoch_id", "id"):
        fields[field] = UUID(fields[field])
    return fields


@pytest.mark.parametrize("case", ORIGINALS["valid"], ids=lambda case: case["name"])
def test_capture_encoding_preserves_frozen_original_bytes_and_hash(case: dict[str, Any]) -> None:
    subject, text, digest = audit_chain.encode_subject_original(**original_fields(case))
    assert subject.model_dump(mode="json") == case["fields"]
    assert text.encode("utf-8") == case["canonical_text"].encode("utf-8")
    assert digest == case["snapshot_hash"]


@pytest.mark.parametrize("case", ORIGINALS["valid"], ids=lambda case: case["name"])
def test_public_subject_encoders_match_pre_optimization_originals(case: dict[str, Any]) -> None:
    subject = audit_chain.build_subject(**original_fields(case))
    assert audit_chain.subject_canonical_text(subject) == case["canonical_text"]
    assert audit_chain.subject_hash(subject) == case["snapshot_hash"]
    assert (
        audit_chain.parse_subject(case["canonical_text"]).model_dump(mode="json") == case["fields"]
    )


@pytest.mark.parametrize("case", ORIGINALS["invalid"], ids=lambda case: case["name"])
def test_invalid_subjects_keep_the_original_error_type_and_message(case: dict[str, Any]) -> None:
    fields = original_fields(case)
    with pytest.raises(ValueError) as captured:
        audit_chain.encode_subject_original(**fields)
    assert type(captured.value).__name__ == case["exception_type"]
    assert str(captured.value) == case["message"]
    subject = AuditSubject.model_validate(fields)
    for encoder in (audit_chain.subject_hash, audit_chain.subject_canonical_text):
        with pytest.raises(ValueError) as repeated:
            encoder(subject)
        assert type(repeated.value).__name__ == case["exception_type"]
        assert str(repeated.value) == case["message"]


def raw_original() -> dict[str, Any]:
    return next(
        case
        for case in cast(list[dict[str, Any]], ORIGINALS["valid"])
        if case["name"] == "raw_invalid_claim_preserved"
    )


def test_encoder_snapshots_input_but_public_encoders_recheck_mutable_dto_content() -> None:
    case = raw_original()
    fields = original_fields(case)
    subject, text, digest = audit_chain.encode_subject_original(**fields)
    fields["data"]["content"]["quantile"] = 0.5
    assert text == audit_chain.subject_canonical_text(subject) == case["canonical_text"]
    assert digest == audit_chain.subject_hash(subject) == case["snapshot_hash"]
    subject.data["content"]["quantile"] = 0.5
    assert audit_chain.subject_canonical_text(subject) == case["canonical_text"].replace(
        '"quantile":0.8', '"quantile":0.5'
    )
    assert audit_chain.subject_hash(subject) != case["snapshot_hash"]
    subject.data["amount_cents"] = True
    for encoder in (audit_chain.subject_hash, audit_chain.subject_canonical_text):
        with pytest.raises(
            ValueError, match="Original row money must be strict signed integer cents"
        ):
            encoder(subject)


def test_public_hash_retains_declared_field_check_after_model_copy() -> None:
    subject = audit_chain.parse_subject(raw_original()["canonical_text"])
    copied = subject.model_copy(update={"unregistered_authority": True})
    with pytest.raises(ValueError, match="Audit model has undeclared copied fields"):
        audit_chain.subject_hash(copied)


@pytest.mark.parametrize(
    ("mutation", "exception_type", "message"),
    [
        (
            "spacing",
            "AuditContractError",
            "Audit subject text is not its original canonical encoding",
        ),
        ("duplicate", "AuditContractError", "Audit original text is not strict JSON"),
        ("nonfinite", "AuditContractError", "Audit original text is not strict JSON"),
        (
            "version",
            "AuditUnsupportedVersion",
            "Unsupported original audit schema or canonical version",
        ),
    ],
)
def test_parser_keeps_canonical_protocol_and_strict_json_checks(
    mutation: str, exception_type: str, message: str
) -> None:
    text = raw_original()["canonical_text"]
    if mutation == "spacing":
        text = " " + text
    elif mutation == "duplicate":
        text = '{"simulation":true,' + text[1:]
    elif mutation == "nonfinite":
        text = text.replace('"original_float":1.0', '"original_float":NaN')
    else:
        text = text.replace("audit-subject-v1", "audit-subject-v99")
    with pytest.raises(ValueError) as captured:
        audit_chain.parse_subject(text)
    assert type(captured.value).__name__ == exception_type
    assert str(captured.value) == message


@pytest.mark.parametrize("budget", ["depth", "bytes"])
def test_public_encoders_still_enforce_raw_subject_resource_bounds(budget: str) -> None:
    subject = audit_chain.parse_subject(raw_original()["canonical_text"])
    nested: Any = "deep"
    if budget == "depth":
        for _ in range(69):
            nested = {"next": nested}
    else:
        nested = "x" * (audit_chain.MAX_SUBJECT_BYTES + 1)
    subject.data["content"]["bounded"] = nested
    message = (
        "Audit value cannot be canonically encoded"
        if budget == "depth"
        else "Audit JSON exceeds byte limit"
    )
    for encoder in (audit_chain.subject_hash, audit_chain.subject_canonical_text):
        with pytest.raises(ValueError, match=message):
            encoder(subject)
    with pytest.raises(ValueError, match=message):
        audit_chain.encode_subject_original(**subject.model_dump(mode="python"))


@pytest.mark.parametrize("scope", ["user_id", "epoch_id"])
def test_same_identity_in_another_tenant_or_epoch_cannot_satisfy_original_chain(scope: str) -> None:
    events, head, references = recorded_chain()
    fields = references.subjects[0].model_dump(mode="python")
    fields[scope] = UUID(int=99)
    if scope == "user_id":
        fields["data"]["user_id"] = str(fields[scope])
    subject, _, _ = audit_chain.encode_subject_original(**fields)
    result = audit_chain.verify_epoch(
        events,
        head=head,
        expected_user_id=head.user_id,
        references=references.model_copy(update={"subjects": [subject]}),
    )
    assert result.status == "INTEGRITY_ERROR"
    assert result.reference_status == "INTEGRITY_ERROR"
