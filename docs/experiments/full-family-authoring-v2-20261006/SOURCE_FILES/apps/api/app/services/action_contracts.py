"""Small user intents; economic commands and authority are always server-derived."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from app.domain.execution_types import ExecutionEffect, ExecutionValidation
from app.domain.policy_configuration import MoneyCents
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TransferIntent(IntentModel):
    kind: Literal["transfer_internal"]
    source_account_id: UUID
    destination_account_id: UUID
    amount_cents: Annotated[MoneyCents, Field(gt=0)]


class PaymentIntent(IntentModel):
    kind: Literal["pay_recurring"]
    policy_id: UUID
    period: Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")] | None = None
    bill_id: UUID | None = None


class GoalIntent(IntentModel):
    kind: Literal["allocate_goal"]
    goal_id: UUID


class PurchaseIntent(IntentModel):
    kind: Literal["purchase_asset"]
    policy_id: UUID


class RedeemIntent(IntentModel):
    kind: Literal["redeem_asset"]
    position_id: UUID


ActionIntent = Annotated[
    TransferIntent | PaymentIntent | GoalIntent | PurchaseIntent | RedeemIntent,
    Field(discriminator="kind"),
]


class PrepareActionRequest(IntentModel):
    idempotency_key: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    intent: ActionIntent

    @field_validator("idempotency_key")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank idempotency key is required")
        return value


class ConfirmActionRequest(IntentModel):
    effect_hash: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def affirmative(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Only explicit acceptance confirms an action")
        return value


class ActionReceiptResponse(IntentModel):
    simulation: Literal[True] = True
    receipt_id: UUID
    action_id: UUID
    bank_operation_id: UUID
    status: str
    executed_cents: MoneyCents
    fee_cents: MoneyCents
    loss_cents: MoneyCents
    posting_ids: list[UUID]
    occurred_at: datetime
    reconciled_at: datetime | None


class ActionResponse(IntentModel):
    simulation: Literal[True] = True
    user_id: UUID
    action_id: UUID
    decision_run_id: UUID
    status: str
    autonomy_level: str
    effect: ExecutionEffect
    effect_hash: str
    prepared_at: datetime
    as_of: datetime
    prepared_validation: ExecutionValidation
    bank_status: str | None = None
    receipt: ActionReceiptResponse | None = None
