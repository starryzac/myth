"""Immutable restartable orchestration journal; no bank or USER impersonation.

Staged during the financial source freeze. Uses the existing audited extension
EvidenceItem store. Acceptance remains unavailable until native delegated guards
are installed and the corresponding family has actual verification.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_income_goal_scope import AnyGrant
from app.domain.zhiyu_standing_scope import Hash
from pydantic import Field, StrictInt, model_validator

SourceKind = Literal[
    "SETTLED_INCOME",
    "CURRENT_POLICY_VERSION",
    "SERVER_PERIOD",
    "SETTLED_MATURITY_RECEIPT",
    "ORIGINAL_ACTION_SETTLED",
    "CURRENT_QUESTION_ANSWER",
]
RunState = Literal[
    "OBSERVED",
    "WAITING_INFORMATION",
    "WAITING_CONFIRMATION",
    "NO_ACTION",
    "ACCEPTED",
    "UNKNOWN",
    "SETTLED",
    "CANCELLED_BEFORE_ACCEPTANCE",
]


class StandingSource(BoundaryModel):
    protocol: Literal["zhiyu-next-standing-source-v1"] = "zhiyu-next-standing-source-v1"
    environment_id: Annotated[str, Field(min_length=1, max_length=160)]
    user_id: UUID
    epoch_id: UUID
    source_kind: SourceKind
    original_source_id: Annotated[str, Field(min_length=1, max_length=160)]
    original_source_version: Annotated[str, Field(min_length=1, max_length=160)]
    original_source_hash: Hash

    @property
    def event_key(self) -> str:
        # Independent of grant ID, worker PID and polling clock. Re-enabling a
        # grant cannot turn the same original source into a fresh event.
        return configuration_hash(self.model_dump(mode="json"))


class StandingRun(BoundaryModel):
    protocol: Literal["zhiyu-next-standing-run-v1"] = "zhiyu-next-standing-run-v1"
    source: StandingSource
    revision: Annotated[StrictInt, Field(ge=1, le=128)]
    previous_record_hash: Hash | None = None
    state: RunState
    recorded_at: datetime
    grant_id: UUID | None = None
    grant_hash: Hash | None = None
    control_revision_at_acceptance: Annotated[StrictInt, Field(ge=1)] | None = None
    whole_plan_hash: Hash | None = None
    original_action_ids: Annotated[list[UUID], Field(max_length=8)] = Field(default_factory=list)
    original_effect_hashes: Annotated[list[Hash], Field(max_length=8)] = Field(default_factory=list)
    original_bank_keys: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)
    accepted_total_cents: Annotated[StrictInt, Field(ge=0)] = 0
    accounting_day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    original_receipt_ids: Annotated[list[UUID], Field(max_length=8)] = Field(default_factory=list)
    reasons: Annotated[list[str], Field(max_length=64)] = Field(default_factory=list)
    executor_role: Literal["SYSTEM"] = "SYSTEM"
    old_one_shot_user_consent_inherited: Literal[False] = False

    @model_validator(mode="after")
    def bounded_original_inventory(self) -> Self:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("Journal needs the aware server clock")
        if (self.revision == 1) != (self.previous_record_hash is None):
            raise ValueError("Revision must bind the previous original record")
        count = len(self.original_action_ids)
        if (
            count != len(self.original_effect_hashes)
            or count != len(self.original_bank_keys)
            or len(set(self.original_action_ids)) != count
            or len(set(self.original_bank_keys)) != count
            or len(set(self.original_receipt_ids)) != len(self.original_receipt_ids)
            or len(self.original_receipt_ids) > count
        ):
            raise ValueError("Complete unique fixed original inventory required")
        accepted = self.state in {"ACCEPTED", "UNKNOWN", "SETTLED"}
        if accepted and (
            not count
            or not self.accepted_total_cents
            or self.grant_id is None
            or self.grant_hash is None
            or self.whole_plan_hash is None
            or self.accounting_day is None
            or self.control_revision_at_acceptance is None
        ):
            raise ValueError("Accepted run must bind whole scope and original effects")
        if not accepted and (
            count
            or self.accepted_total_cents
            or self.original_receipt_ids
            or self.grant_hash
            or self.whole_plan_hash
            or self.accounting_day
            or self.control_revision_at_acceptance
        ):
            raise ValueError("Unaccepted run cannot claim financial acceptance")
        if self.state == "SETTLED" and len(self.original_receipt_ids) != count:
            raise ValueError("Every original child requires an independently verified receipt")
        return self

    @property
    def record_hash(self) -> str:
        return configuration_hash(self.model_dump(mode="json"))


ACCEPTED_FIELDS = (
    "grant_id",
    "grant_hash",
    "control_revision_at_acceptance",
    "whole_plan_hash",
    "original_action_ids",
    "original_effect_hashes",
    "original_bank_keys",
    "accepted_total_cents",
    "accounting_day",
)
TRANSITIONS: dict[RunState, set[RunState]] = {
    "OBSERVED": {
        "WAITING_INFORMATION",
        "WAITING_CONFIRMATION",
        "NO_ACTION",
        "ACCEPTED",
        "CANCELLED_BEFORE_ACCEPTANCE",
    },
    "WAITING_INFORMATION": {
        "WAITING_INFORMATION",
        "WAITING_CONFIRMATION",
        "NO_ACTION",
        "ACCEPTED",
        "CANCELLED_BEFORE_ACCEPTANCE",
    },
    "WAITING_CONFIRMATION": {
        "WAITING_INFORMATION",
        "WAITING_CONFIRMATION",
        "NO_ACTION",
        "ACCEPTED",
        "CANCELLED_BEFORE_ACCEPTANCE",
    },
    "NO_ACTION": set(),
    "ACCEPTED": {"UNKNOWN", "SETTLED"},
    "UNKNOWN": {"UNKNOWN", "SETTLED"},
    "SETTLED": set(),
    "CANCELLED_BEFORE_ACCEPTANCE": set(),
}


def verify_transition(previous: StandingRun | None, current: StandingRun) -> None:
    if previous is None:
        if current.revision != 1 or current.state != "OBSERVED":
            raise ValueError("First event record is observation only")
        return
    if (
        current.source != previous.source
        or current.revision != previous.revision + 1
        or current.previous_record_hash != previous.record_hash
        or current.recorded_at < previous.recorded_at
        or current.state not in TRANSITIONS[previous.state]
    ):
        raise ValueError("Journal source, revision or transition differs")
    if previous.state in {"ACCEPTED", "UNKNOWN"} and any(
        getattr(current, field) != getattr(previous, field) for field in ACCEPTED_FIELDS
    ):
        raise ValueError("Accepted original keys, effects and scope cannot be replaced")
    if not set(previous.original_receipt_ids).issubset(current.original_receipt_ids):
        raise ValueError("An original receipt cannot disappear during recovery")


def accounted_day_total(runs: list[StandingRun], user_id: UUID, epoch_id: UUID, day: str) -> int:
    """Count all accepted money, including UNKNOWN and unreceipted child budgets.

    The caller supplies verified complete current runs under the user lock. A
    settlement response loss never releases a budget for a second event.
    """
    keys = [row.source.event_key for row in runs]
    if len(keys) != len(set(keys)):
        raise ValueError("Budget inventory contains duplicate event revisions")
    if any((row.source.user_id, row.source.epoch_id) != (user_id, epoch_id) for row in runs):
        raise ValueError("Budget cannot mix owners or epochs")
    return sum(row.accepted_total_cents for row in runs if row.accounting_day == day)


def grant_record_payload(grant: AnyGrant) -> dict[str, object]:
    return {
        "protocol": "zhiyu-next-standing-grant-record-v1",
        "grant": grant.model_dump(mode="json"),
        "grant_hash": configuration_hash(grant.model_dump(mode="json")),
        "native_bank_authority": False,
    }
