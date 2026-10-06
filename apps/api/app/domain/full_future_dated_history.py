"""Narrow retained-history proof: every old dated window is strictly in the future.

This proves a calendar condition, never an unpaid amount, settlement, or grant.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_configuration import DatedExpensePolicy, validate_full_configuration
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt

if TYPE_CHECKING:
    from app.domain.full_protection_projection import FullProtectionPolicySource

PROTOCOL: Literal["full-future-dated-history-v1"] = "full-future-dated-history-v1"
REFERENCE_KIND = "VERIFIED_FUTURE_DATED_HISTORY"
MAX_VERSIONS = 128
MAX_COMMANDS = 256
MAX_BYTES = 524288
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[StrictInt, Field(ge=0)]


class FutureDatedHistoryProof(BoundaryModel):
    protocol: Literal["full-future-dated-history-v1"] = PROTOCOL
    status: Literal["VERIFIED_FUTURE_DATED_HISTORY", "UNKNOWN"]
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    current_version_id: UUID
    current_content_hash: Hash
    as_of: datetime
    timezone: str
    today: date
    actual_version_count: Count | None
    captured_version_count: Count
    actual_command_count: Count | None
    captured_command_count: Count
    expected_evidence_count: Count | None
    captured_evidence_count: Count
    policy_original: dict[str, Any] | None
    user_original: dict[str, Any] | None
    epoch_original: dict[str, Any] | None
    versions: list[dict[str, Any]]
    commands: list[dict[str, Any]]
    evidence_originals: list[dict[str, Any]]
    source_digest: Hash
    reasons: list[str]
    unpaid_amount_proven: Literal[False] = False
    settlement_proven: Literal[False] = False
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False


def history_digest(proof: FutureDatedHistoryProof) -> str:
    raw = proof.model_dump(mode="json", exclude={"source_digest", "status", "reasons"})
    return configuration_hash(raw)


def _clock(value: Any) -> datetime:
    if type(value) is not str:
        raise ValueError("Original clock must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Original clock must be aware")
    return result


def _uuid(value: Any) -> str:
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Original identity must be canonical UUID text")
    return value


def _require(value: Any) -> None:
    if not value:
        raise ValueError("Complete current future-dated history not proven")


def _validate(proof: FutureDatedHistoryProof) -> None:
    _require(proof.timezone in {"UTC", "Asia/Shanghai"})
    zone = ZoneInfo(proof.timezone)
    _require(proof.today == proof.as_of.astimezone(zone).date())
    _require(
        proof.actual_version_count == proof.captured_version_count == len(proof.versions)
        and 1 < len(proof.versions) <= MAX_VERSIONS
        and proof.actual_command_count == proof.captured_command_count == len(proof.commands)
        and 0 < len(proof.commands) <= MAX_COMMANDS
        and proof.expected_evidence_count
        == proof.captured_evidence_count
        == len(proof.evidence_originals)
        and history_digest(proof) == proof.source_digest
    )
    _require(len(json.dumps(proof.model_dump(mode="json")).encode("utf-8")) <= MAX_BYTES)
    user, policy, epoch = proof.user_original, proof.policy_original, proof.epoch_original
    _require(user is not None and policy is not None and epoch is not None)
    assert user is not None and policy is not None and epoch is not None
    _require(
        user.get("id") == str(proof.user_id)
        and user.get("is_simulated") is True
        and user.get("timezone") == proof.timezone
        and policy.get("id") == str(proof.policy_id)
        and policy.get("user_id") == str(proof.user_id)
        and policy.get("epoch_id") == str(proof.epoch_id)
        and policy.get("template_name") == "DatedExpensePolicy"
        and policy.get("dsl_version") == "FULL_V1"
        and policy.get("status") in {"ACTIVE", "CONFIRMED"}
        and epoch.get("id") == str(proof.epoch_id)
        and epoch.get("user_id") == str(proof.user_id)
        and epoch.get("status") == "OPEN"
    )
    evidence: dict[str, dict[str, Any]] = {}
    for row in proof.evidence_originals:
        identifier = _uuid(row["id"])
        _require(identifier not in evidence and row["user_id"] == str(proof.user_id))
        _require(configuration_hash(row["content"]) == row["content_hash"])
        _require(_clock(row["observed_at"]) <= proof.as_of)
        evidence[identifier] = row
    versions: dict[str, dict[str, Any]] = {}
    required_evidence: set[str] = set()
    previous_hash = None
    previous_clock: datetime | None = None
    for number, row in enumerate(proof.versions, 1):
        identifier = _uuid(row["id"])
        _require(
            identifier not in versions
            and type(row["version_number"]) is int
            and row["version_number"] == number
            and row["user_id"] == str(proof.user_id)
            and row["policy_id"] == str(proof.policy_id)
            and row["previous_hash"] == previous_hash
        )
        canonical = validate_full_configuration("DatedExpensePolicy", row["configuration"])
        _require(canonical == row["configuration"])
        _require(configuration_hash(canonical) == row["content_hash"])
        _require(
            type(row["impact_analysis"]) is dict
            and type(row["impact_analysis"].get("reference_snapshots")) is list
            and all(type(ref) is dict for ref in row["impact_analysis"]["reference_snapshots"])
        )
        dated = DatedExpensePolicy.model_validate(canonical)
        if number < len(proof.versions):
            _require(dated.window.start > proof.today)
        confirmed = _clock(row["confirmed_at"])
        _require(
            confirmed == _clock(row["created_at"])
            and confirmed <= proof.as_of
            and (previous_clock is None or confirmed >= previous_clock)
        )
        start = (
            datetime.combine(dated.valid_from, time.min, zone).astimezone(UTC)
            if dated.valid_from is not None
            else confirmed
        )
        end = (
            datetime.combine(dated.valid_until + timedelta(days=1), time.min, zone).astimezone(UTC)
            if dated.valid_until is not None
            else None
        )
        _require(
            _clock(row["valid_from"]) == start
            and (row["valid_until"] is None if end is None else _clock(row["valid_until"]) == end)
            and (end is None or end > start)
        )
        confirmation = row["confirmation"]
        expected = {
            "protocol": "full-policy-confirmation-v1",
            "user_id": str(proof.user_id),
            "epoch_id": str(proof.epoch_id),
            "policy_id": str(proof.policy_id),
            "version_id": identifier,
            "template_name": "DatedExpensePolicy",
            "reviewed_hash": row["content_hash"],
            "confirmed_at": confirmed.isoformat(),
            "accepted": True,
            "bank_authority": False,
        }
        _require(
            type(confirmation) is dict
            and set(confirmation)
            == set(expected) | {"confirmation_evidence_id", "request_key", "request_hash"}
            and all(confirmation.get(k) == v for k, v in expected.items())
            and confirmation["accepted"] is True
            and confirmation["bank_authority"] is False
            and type(row["evidence_ids"]) is list
        )
        ids = [_uuid(value) for value in row["evidence_ids"]]
        _require(len(ids) == len(set(ids)))
        required_evidence.update(ids)
        original = evidence[_uuid(confirmation["confirmation_evidence_id"])]
        _require(
            original["id"] in ids
            and original["source_type"] == "FULL_POLICY_CONFIRMATION"
            and original["evidence_level"] == "USER_CONFIRMED_POLICY"
            and original["source_ref"] == identifier
            and original["status"] == "VALID"
            and original["content"] == confirmation
            and _clock(original["observed_at"]) == confirmed
            and _clock(original["valid_from"]) == confirmed
        )
        versions[identifier] = row
        previous_hash, previous_clock = row["content_hash"], confirmed
    _require(set(evidence) == required_evidence)
    last = proof.versions[-1]
    _require(last["id"] == str(proof.current_version_id))
    _require(last["content_hash"] == proof.current_content_hash)
    seen_commands: set[str] = set()
    confirmed_versions: list[str] = []
    previous_result_hash = None
    previous_status = None
    previous_clock = None
    for number, row in enumerate(proof.commands, 1):
        identifier = _uuid(row["id"])
        _require(
            identifier not in seen_commands
            and row["user_id"] == str(proof.user_id)
            and row["policy_id"] == str(proof.policy_id)
            and row["epoch_id"] == str(proof.epoch_id)
            and type(row["command_number"]) is int
            and row["command_number"] == number
            and row["previous_hash"] == previous_result_hash
            and row["previous_status"] == previous_status
            and row["version_id"] in versions
        )
        current = versions[row["version_id"]]
        request, result = row["request"], row["result"]
        created = _clock(row["created_at"])
        _require(created <= proof.as_of and (previous_clock is None or created >= previous_clock))
        _require(
            configuration_hash(request) == row["request_hash"]
            and configuration_hash(result) == row["result_hash"]
            and request["protocol"] == "full-policy-command-v1"
            and request["kind"] == row["kind"]
            and request["user_id"] == str(proof.user_id)
            and request["policy_id"] == (None if number == 1 else str(proof.policy_id))
            and request["body"]["idempotency_key"] == row["idempotency_key"]
            and result["command_id"] == identifier
            and type(result["command_number"]) is int
            and result["command_number"] == number
            and result["previous_command_hash"] == previous_result_hash
            and result["policy_id"] == str(proof.policy_id)
            and result["epoch_id"] == str(proof.epoch_id)
            and result["version_id"] == row["version_id"]
            and result["status"] == row["resulting_status"]
            and result["configuration_hash"] == current["content_hash"]
            and result["bank_authority"] is False
            and result["receipt_is_current_authority"] is False
            and result["dedicated_audit_event"] is False
            and result["simulation"] is True
            and result["action_dependencies_supported"] is False
        )
        _require(row["kind"] in {"CREATE", "CHANGE", "RESUME", "SUSPEND", "REVOKE", "REFRESH_TIME"})
        if row["kind"] in {"CREATE", "CHANGE", "RESUME"}:
            confirmation = current["confirmation"]
            _require(
                confirmation["request_key"] == row["idempotency_key"]
                and confirmation["request_hash"] == row["request_hash"]
                and _clock(current["confirmed_at"]) == created
                and request["body"]["accepted"] is True
                and request["body"]["reviewed_hash"] == current["content_hash"]
            )
            if row["kind"] != "RESUME":
                _require(
                    validate_full_configuration(
                        "DatedExpensePolicy", request["body"]["configuration"]
                    )
                    == current["configuration"]
                )
            confirmed_versions.append(row["version_id"])
        seen_commands.add(identifier)
        previous_result_hash, previous_status, previous_clock = (
            row["result_hash"],
            row["resulting_status"],
            created,
        )
    _require(
        proof.commands[0]["kind"] == "CREATE"
        and len(confirmed_versions) == len(versions)
        and set(confirmed_versions) == set(versions)
        and proof.commands[-1]["version_id"] == last["id"]
        and proof.commands[-1]["resulting_status"] == policy["status"]
        and _clock(policy["updated_at"]) == _clock(proof.commands[-1]["created_at"])
    )


def validate_future_dated_history(proof: FutureDatedHistoryProof) -> bool:
    """Recompute from complete raw originals; a status string alone is insufficient."""
    try:
        _validate(proof)
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def verify_future_dated_history(
    source: FullProtectionPolicySource,
    proof: FutureDatedHistoryProof,
    as_of: datetime,
    timezone: str,
) -> bool:
    """Exact current source/epoch/clock binding for the explicit new projection branch."""
    if (
        proof.status != "VERIFIED_FUTURE_DATED_HISTORY"
        or proof.reasons
        or not validate_future_dated_history(proof)
        or proof.as_of != as_of
        or proof.timezone != timezone
        or source.template_name != "DatedExpensePolicy"
        or source.policy_id != proof.policy_id
        or source.version_id != proof.current_version_id
        or source.content_hash != proof.current_content_hash
        or source.version_number != len(proof.versions)
        or not source.planning_confirmation_valid
        or not source.references_current
        or source.effective_status not in {"ACTIVE", "CONFIRMED"}
    ):
        return False
    current = proof.versions[-1]
    refs = current["impact_analysis"]["reference_snapshots"]
    allowed_refs = refs + [{"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}]
    return (
        source.configuration == current["configuration"]
        and source.confirmation == current["confirmation"]
        and source.confirmation.get("user_id") == str(proof.user_id)
        and source.confirmation.get("epoch_id") == str(proof.epoch_id)
        and source.confirmed_at == _clock(current["confirmed_at"])
        and source.valid_from == _clock(current["valid_from"])
        and source.valid_until
        == (None if current["valid_until"] is None else _clock(current["valid_until"]))
        and {str(value) for value in source.evidence_ids} == set(current["evidence_ids"])
        and source.reference_snapshots in (refs, allowed_refs)
    )
