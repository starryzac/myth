"""Explicit historical classification, separate from immutable bank economic facts."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.policy_configuration import configuration_hash
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

Category = Literal[
    "food",
    "transport",
    "daily_necessities",
    "rent",
    "utilities",
    "education",
    "healthcare",
    "other",
]
CategoryKey = Annotated[
    StrictStr, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
REVIEW_PROTOCOL: Literal["transaction-category-review-v1"] = "transaction-category-review-v1"
COMMAND_PROTOCOL: Literal["transaction-category-command-v1"] = "transaction-category-command-v1"


class CategoryConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: Category
    accepted: Literal[True]
    reviewed_transaction_hash: Digest
    reason: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    idempotency_key: CategoryKey
    expected_epoch_id: UUID

    @field_validator("accepted", mode="before")
    @classmethod
    def exact_acceptance(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("Explicit acceptance must be the boolean true")
        return value


def category_review_hash(epoch_id: UUID, transaction: dict[str, Any], bank: dict[str, Any]) -> str:
    return configuration_hash(
        {
            "protocol": REVIEW_PROTOCOL,
            "epoch_id": str(epoch_id),
            "transaction": transaction,
            "bank_fact": bank,
        }
    )


def category_command(
    user_id: UUID, transaction_id: UUID, request: CategoryConfirmationRequest
) -> dict[str, Any]:
    return {
        "protocol": COMMAND_PROTOCOL,
        "user_id": str(user_id),
        "transaction_id": str(transaction_id),
        "request": request.model_dump(mode="json"),
    }


def verify_category_transition(
    *,
    user_id: UUID,
    epoch_id: UUID,
    transaction_id: UUID,
    before: dict[str, Any],
    after: dict[str, Any],
    bank: dict[str, Any],
    declaration: dict[str, Any],
) -> None:
    """Verify exact original metadata transition without changing economic columns."""
    from app.domain.history_coverage import bank_fact_snapshot

    if (
        before.get("id") != str(transaction_id)
        or after.get("id") != str(transaction_id)
        or before.get("user_id") != str(user_id)
        or after.get("user_id") != str(user_id)
        or before.get("category_confirmed") is not False
        or after.get("category_confirmed") is not True
        or before.get("direction") != "DEBIT"
        or bank.get("user_id") != str(user_id)
        or declaration.get("user_id") != str(user_id)
        or declaration.get("source_type") != "SIMULATED_USER_CATEGORY_CONFIRMATION"
        or declaration.get("evidence_level") != "USER_DECLARED"
        or declaration.get("status") != "VALID"
    ):
        raise ValueError("Category confirmation lacks exact owned original sources")
    if {k: v for k, v in before.items() if k not in {"category", "category_confirmed"}} != {
        k: v for k, v in after.items() if k not in {"category", "category_confirmed"}
    }:
        raise ValueError("Category confirmation changes immutable bank or transaction facts")
    content = declaration["content"]
    if configuration_hash(content) != declaration.get("content_hash"):
        raise ValueError("Category declaration hash differs")
    body = CategoryConfirmationRequest.model_validate_json(
        json.dumps(content["original_command"]["request"])
    )
    expected_command = category_command(user_id, transaction_id, body)
    if (
        body.expected_epoch_id != epoch_id
        or content["original_command"] != expected_command
        or content.get("command_hash") != configuration_hash(expected_command)
        or content.get("transaction_id") != str(transaction_id)
        or content.get("category") != body.category
        or content.get("confirmed") is not True
        or content.get("simulation") is not True
        or content.get("actor") != "synthetic_user"
        or after.get("category") != body.category
    ):
        raise ValueError("Category declaration does not bind its exact accepted command")

    # This adapter reads the frozen original bank fields only, never inferred classification.
    def original(raw: dict[str, Any]) -> Any:
        return SimpleNamespace(
            **{
                key: UUID(value)
                if key in {"id", "user_id", "account_id", "evidence_id"} and value is not None
                else datetime.fromisoformat(value)
                if key in {"occurred_at", "observed_at", "valid_from", "valid_to"}
                and value is not None
                else value
                for key, value in raw.items()
            }
        )

    frozen_bank = bank_fact_snapshot(original(before), original(bank))
    if frozen_bank[
        "economic_role"
    ] != "CONSUMPTION" or body.reviewed_transaction_hash != category_review_hash(
        epoch_id, before, frozen_bank
    ):
        raise ValueError("Category command does not bind the reviewed bank consumption original")
    receipt = content["original_receipt"]
    if (
        set(receipt)
        != {
            "transaction_id",
            "before_category",
            "category",
            "before_confirmed",
            "confirmed",
            "bank_evidence_id",
            "bank_evidence_hash",
            "confirmed_at",
        }
        or receipt["transaction_id"] != str(transaction_id)
        or receipt["before_category"] != before["category"]
        or receipt["category"] != body.category
        or receipt["before_confirmed"] is not False
        or receipt["confirmed"] is not True
        or receipt["bank_evidence_id"] != bank["id"]
        or receipt["bank_evidence_hash"] != bank["content_hash"]
        or datetime.fromisoformat(receipt["confirmed_at"])
        != datetime.fromisoformat(declaration["observed_at"])
    ):
        raise ValueError("Category receipt differs from its actual recorded original transition")
