"""Frozen TRANSFER_GOAL identities and projection; never current authorization."""

import json
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.domain.full_goal_transfer import (
    GoalTransferAuthorization,
    GoalTransferBankCommand,
    GoalTransferCurrentScope,
    GoalTransferEffect,
    goal_transfer_leg_specs,
)
from app.domain.full_goal_transfer_current_proof import (
    GoalTransferCurrentProof,
    validate_current_goal_transfer_effect,
)
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.domain.policy_configuration import configuration_hash

_FROZEN_KEY_BYTES = 1024 * 1024
_FROZEN_MEMO_BYTES = 8 * 1024 * 1024
_FROZEN_MEMO_ENTRIES = 8


def _strict_frozen_action_key(action: dict[str, Any]) -> bytes | None:
    """Complete exact JSON content; unsupported inputs retain the original parser path."""
    if type(action) is not dict:
        return None
    pending: list[tuple[Any, int]] = [(action, 0)]
    nodes = 0
    minimum_bytes = 0
    while pending:
        child, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 250000:
            return None
        kind = type(child)
        if kind is str:
            minimum_bytes += len(child)
        elif kind is int:
            minimum_bytes += max(1, child.bit_length() * 3 // 10)
        if minimum_bytes > _FROZEN_KEY_BYTES:
            return None
        if child is None or kind is str or kind is int or kind is bool:
            continue
        if kind is float:
            if not math.isfinite(child):
                return None
        elif kind is dict:
            if len(child) + len(pending) > 250000 - nodes:
                return None
            if any(type(key) is not str for key in child):
                return None
            minimum_bytes += sum(len(key) for key in child)
            if minimum_bytes > _FROZEN_KEY_BYTES:
                return None
            pending.extend((value, depth + 1) for value in child.values())
        elif kind is list:
            if len(child) + len(pending) > 250000 - nodes:
                return None
            pending.extend((value, depth + 1) for value in child)
        else:
            return None
    try:
        raw = json.dumps(
            action, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None
    return raw if len(raw) <= _FROZEN_KEY_BYTES else None


@dataclass
class _FrozenActionMemo:
    current_snapshot: Callable[[], bool]
    values: dict[bytes, tuple[GoalTransferBankCommand, list[GoalCashSourceProof]]] = field(
        default_factory=dict
    )
    bytes_used: int = 0

    def clear(self) -> None:
        self.values.clear()
        self.bytes_used = 0

    def usable(self) -> bool:
        try:
            if self.current_snapshot():
                return True
        except Exception:
            pass
        self.clear()
        return False


_frozen_action_memo: ContextVar[_FrozenActionMemo | None] = ContextVar(
    "single_audit_frozen_goal_transfer_math", default=None
)


@contextmanager
def frozen_goal_transfer_read_scope(current_snapshot: Callable[[], bool] | None) -> Iterator[None]:
    """One true audit invocation, including an explicit disabled scope for writable callers."""
    value = _FrozenActionMemo(current_snapshot) if current_snapshot is not None else None
    token = _frozen_action_memo.set(value)
    try:
        yield
    finally:
        if value is not None:
            value.clear()
        _frozen_action_memo.reset(token)


def _clock(value: Any) -> datetime:
    if type(value) is not str:
        raise ValueError("Frozen transfer requires an original aware ISO clock")
    clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Frozen transfer clock is not aware")
    return clock


def read_frozen_goal_transfer_action(
    action: dict[str, Any],
) -> tuple[GoalTransferBankCommand, list[GoalCashSourceProof]]:
    memo = _frozen_action_memo.get()
    try:
        key = _strict_frozen_action_key(action) if memo is not None and memo.usable() else None
    except Exception:
        key = None
    if memo is not None and key is not None and key in memo.values:
        try:
            return deepcopy(memo.values[key])
        except Exception:
            memo.clear()
    result = _read_frozen_goal_transfer_action(action)
    if (
        memo is not None
        and key is not None
        and memo.usable()
        and len(memo.values) < _FROZEN_MEMO_ENTRIES
    ):
        try:
            # Retain an owned result, including on the initial successful return.
            owned = deepcopy(result)
            size = len(key) + len(owned[0].model_dump_json().encode("utf-8")) + sum(
                len(base.model_dump_json().encode("utf-8")) for base in owned[1]
            )
            if size <= _FROZEN_MEMO_BYTES - memo.bytes_used and (
                _strict_frozen_action_key(action) == key
            ):
                memo.values[key] = owned
                memo.bytes_used += size
        except Exception:
            # Failure of an optional optimization must not replace the native result/error.
            memo.clear()
    return result


def _read_frozen_goal_transfer_action(
    action: dict[str, Any],
) -> tuple[GoalTransferBankCommand, list[GoalCashSourceProof]]:
    request = action["request"]
    if (
        request.get("protocol") not in {"goal-transfer-action-v1", "goal-transfer-action-v2"}
        or "execution" in request
        or "goal_release_execution" in request
        or configuration_hash(request) != action["request_hash"]
    ):
        raise ValueError("Original explicit paired transfer request differs")
    command = GoalTransferBankCommand.model_validate_json(
        json.dumps(request["goal_transfer_execution"])
    )
    authorization = GoalTransferAuthorization.model_validate_json(
        json.dumps(request["goal_transfer_authorization"])
    )
    bases = [
        GoalCashSourceProof.model_validate_json(json.dumps(row))
        for row in request.get("goal_transfer_source_bases", [])
    ]
    effect, scope = command.effect, command.effect.scope
    if (
        UUID(action["id"]) != effect.operation_id
        or UUID(action["user_id"]) != effect.user_id
        or action["action_type"] != "TRANSFER_GOAL"
        or action["autonomy_level"] != "ASK_ONCE"
        or type(action["amount_cents"]) is not int
        or action["amount_cents"] != effect.amount_cents
        or action["idempotency_key"] != effect.bank_idempotency_key
        or action["source_account_id"] != str(effect.source_account_id)
        or action["destination_account_id"] != str(effect.destination_account_id)
        or action["goal_id"] != str(effect.source_goal_id)
        or action["policy_version_id"] != str(scope.source.policy_version_id)
        or action.get("position_id") is not None
        or action.get("product_id") is not None
        or _clock(action["expires_at"]) != effect.expires_at
        or authorization.authorization_id != effect.authorization_id
        or authorization.scope != scope
        or authorization.scope_hash != effect.scope_hash
        or authorization.request_hash != effect.authorization_request_hash
        or effect.authorization_evidence_id != uuid5(effect.operation_id, "authorization-evidence")
        or effect.authorization_evidence_hash
        != configuration_hash(authorization.model_dump(mode="json"))
    ):
        raise ValueError("Frozen transfer action or dual USER authorization identity differs")
    if request["protocol"] == "goal-transfer-action-v2":
        if (
            not isinstance(scope, GoalTransferCurrentScope)
            or "goal_transfer_source_bases" in request
        ):
            raise ValueError("Current proof requires the explicit new scope and action protocol")
        proof = GoalTransferCurrentProof.model_validate_json(
            json.dumps(request["goal_transfer_current_proof"])
        )
        validate_current_goal_transfer_effect(proof, effect)
        prepare = dict(request["original_prepare_request"])
        UUID(prepare.pop("preview_id"))
        if prepare != authorization.original_request.model_dump(mode="json"):
            raise ValueError("Current paired action changed its original confirmed preview request")
        preview = request["original_preview_request"]
        expected = {
            "expected_epoch_id": str(scope.epoch_id),
            "policy_id": str(scope.policy.policy_id),
            "expected_policy_version_id": str(scope.policy.version_id),
            "source_goal_id": str(scope.source.goal_id),
            "expected_source_policy_version_id": str(scope.source.policy_version_id),
            "target_goal_id": str(scope.target.goal_id),
            "expected_target_policy_version_id": str(scope.target.policy_version_id),
            "amount_cents": scope.amount_cents,
        }
        if any(preview.get(key) != value for key, value in expected.items()):
            raise ValueError("Current source proof cannot replace the reviewed original pair")
        return command, []
    if (
        isinstance(scope, GoalTransferCurrentScope)
        or len(bases) != 2
        or "goal_transfer_current_proof" in request
    ):
        raise ValueError("Old action serializer cannot inherit the current proof extension")
    for base, goal in zip(bases, (scope.source, scope.target), strict=True):
        if (
            base.state != "VERIFIED_CASH_ONLY"
            or base.reasons
            or (base.user_id, base.epoch_id, base.goal_id, base.goal_account_id)
            != (scope.user_id, scope.epoch_id, goal.goal_id, goal.account_id)
            or base.as_of > effect.valid_from
        ):
            raise ValueError("Both retained original goal-cash bases must bind the paired scope")
    if bases[0].source_binding_hash != effect.source_provenance_hash:
        raise ValueError("Source transfer hash does not bind its original allocation basis")
    slices = {
        (row.allocation_action_id, row.fragment_id): row
        for row in bases[0].original_allocation_slices
    }
    fields = (
        "original_policy_version_id",
        "origin_transaction_id",
        "income_location_account_id",
        "allocation_effect_hash",
        "allocation_bank_request_hash",
        "allocation_action_request_hash",
    )
    for use in effect.source_uses:
        part = slices.get((use.allocation_action_id, use.fragment_id))
        if (
            part is None
            or any(getattr(part, key) != getattr(use, key) for key in fields)
            or use.amount_cents > part.original_allocated_cents
        ):
            raise ValueError("Transferred cash no longer binds its exact original allocation slice")
    return command, bases


def frozen_goal_transfer_identity(
    operation: dict[str, Any],
    action: dict[str, Any],
    observed_at: datetime,
) -> tuple[GoalTransferEffect, dict[str, tuple[str, str, int]]]:
    command, _ = read_frozen_goal_transfer_action(action)
    effect = command.effect
    requested, available = _clock(operation["requested_at"]), _clock(operation["available_at"])
    if (
        operation["request"] != command.model_dump(mode="json")
        or operation["request_hash"] != configuration_hash(operation["request"])
        or operation["id"] != action["id"]
        or operation["action_plan_id"] != action["id"]
        or operation["user_id"] != action["user_id"]
        or operation["operation_type"] != "TRANSFER_GOAL"
        or operation["business_key"] != effect.business_key
        or operation["idempotency_key"] != effect.bank_idempotency_key
        or operation.get("legacy_redemption_id") is not None
        or operation.get("closing_position_id") is not None
        or not effect.valid_from <= requested < effect.expires_at
        or not requested <= available <= observed_at
    ):
        raise ValueError("Original transfer Bank request differs from its immutable paired action")
    return effect, {
        row.leg_ref: (row.ledger_key, row.ledger_dimension, row.delta_cents)
        for row in goal_transfer_leg_specs(command)
    }


def verify_frozen_goal_transfer_posting(effect: GoalTransferEffect, row: dict[str, Any]) -> None:
    from app.domain.full_goal_transfer import goal_transfer_effect_hash

    command = GoalTransferBankCommand(effect=effect, effect_hash=goal_transfer_effect_hash(effect))
    spec = next(
        (leg for leg in goal_transfer_leg_specs(command) if leg.leg_ref == row.get("leg_ref")), None
    )
    if (
        spec is None
        or row.get("id") != str(uuid5(effect.operation_id, "posting:" + str(row.get("leg_ref"))))
        or row.get("operation_id") != str(effect.operation_id)
        or row.get("user_id") != str(effect.user_id)
        or row.get("account_id") != str(spec.account_id)
        or row.get("ledger_key") != spec.ledger_key
        or row.get("ledger_dimension") != spec.ledger_dimension
        or row.get("ledger_metadata") != spec.required_metadata
        or type(row.get("delta_cents")) is not int
        or row["delta_cents"] != spec.delta_cents
        or row.get("position_id") is not None
        or row.get("redemption_id") is not None
        or row.get("external_fact_id") is not None
    ):
        raise ValueError("Transfer posting changed its exact goal, account, amount or origin")


def verify_frozen_goal_transfer_projection(
    effect: GoalTransferEffect,
    operation: dict[str, Any],
    receipt: dict[str, Any],
    rows: list[dict[str, Any]],
    originals: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Common verifier still checks full bank ledger, receipt/hash/clock and anchors."""
    body = receipt["response"]
    cash = [row for row in rows if row.get("ledger_dimension") == "ECONOMIC"]
    if (
        len(rows) != 4
        or len(cash) != 2
        or type(body) is not dict
        or set(body)
        != {"bank_operation_id", "posting_ids", "transaction_ids", "goal_ownership_evidence_ids"}
        or body["bank_operation_id"] != str(effect.operation_id)
        or body["posting_ids"] != sorted(row["id"] for row in rows)
        or body["transaction_ids"]
        != sorted(str(uuid5(effect.operation_id, "transaction:" + row["leg_ref"])) for row in cash)
        or type(body["goal_ownership_evidence_ids"]) is not list
        or len(body["goal_ownership_evidence_ids"]) != 2
        or len(set(body["goal_ownership_evidence_ids"])) != 2
    ):
        raise ValueError("Transfer receipt must bind both cash legs and both goal ownership proofs")
    settled, reconciled = _clock(operation["settled_at"]), _clock(receipt["reconciled_at"])
    counterparty = f"goal-transfer:{effect.source_goal_id}:{effect.target_goal_id}"
    for row in cash:
        identity = uuid5(effect.operation_id, "transaction:" + row["leg_ref"])
        proof_id = uuid5(identity, "evidence")
        transaction, proof = (
            originals.get(("TRANSACTION", str(identity))),
            originals.get(("EVIDENCE", str(proof_id))),
        )
        payload = {
            "simulation": True,
            "user_id": str(effect.user_id),
            "transaction_id": str(identity),
            "account_id": row["account_id"],
            "direction": "DEBIT" if row["delta_cents"] < 0 else "CREDIT",
            "amount_cents": abs(row["delta_cents"]),
            "balance_after_cents": row["balance_after_cents"],
            "occurred_at": settled.isoformat(),
            "counterparty_ref": counterparty,
            "economic_role": "INTERNAL_TRANSFER",
            "bank_operation_id": str(effect.operation_id),
            "bank_posting_id": row["id"],
        }
        reference = f"bank-operation:{effect.operation_id}:{row['leg_ref']}"
        if (
            transaction is None
            or proof is None
            or (
                transaction.get("user_id") != str(effect.user_id)
                or transaction.get("account_id") != row["account_id"]
                or transaction.get("evidence_id") != str(proof_id)
                or transaction.get("source_ref") != reference
                or transaction.get("direction") != payload["direction"]
                or transaction.get("amount_cents") != payload["amount_cents"]
                or transaction.get("balance_after_cents") != payload["balance_after_cents"]
                or transaction.get("category") != "internal_transfer"
                or transaction.get("counterparty_ref") != counterparty
                or _clock(transaction["occurred_at"]) != settled
                or _clock(transaction["created_at"]) != reconciled
                or _clock(transaction["observed_at"]) != reconciled
                or proof.get("user_id") != str(effect.user_id)
                or proof.get("content") != payload
                or proof.get("content_hash") != configuration_hash(payload)
                or proof.get("source_type") != "SIMULATED_BANK_TRANSACTION"
                or proof.get("evidence_level") != "BANK_CONFIRMED"
                or proof.get("source_ref") != reference
                or _clock(proof["valid_from"]) != settled
                or _clock(proof["created_at"]) != reconciled
                or _clock(proof["observed_at"]) != reconciled
            )
        ):
            raise ValueError("Transfer cash transaction cannot be relabeled as income or rebound")
    ownership = [
        originals.get(("EVIDENCE", identity)) for identity in body["goal_ownership_evidence_ids"]
    ]
    for goal in (effect.scope.source, effect.scope.target):
        leg = next(row for row in rows if row.get("leg_ref") == f"goal_cash:{goal.goal_id}")
        matches = [
            proof
            for proof in ownership
            if proof and proof.get("content", {}).get("goal_id") == str(goal.goal_id)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Each received/released goal needs its exact original ownership evidence"
            )
        proof = matches[0]
        assert proof is not None
        content = proof["content"]
        principal = content.get("principal_owned_cents")
        if (
            proof.get("user_id") != str(effect.user_id)
            or proof.get("source_type") != "SIMULATED_GOAL_OWNERSHIP"
            or proof.get("evidence_level") != "BANK_CONFIRMED"
            or proof.get("content_hash") != configuration_hash(content)
            or content.get("protocol") != "goal-ownership-v1"
            or content.get("policy_id") != str(goal.policy_id)
            or content.get("account_id") != str(goal.account_id)
            or content.get("cash_owned_cents") != leg["balance_after_cents"]
            or type(principal) is not int
            or principal < 0
            or content.get("allocated_cents") != leg["balance_after_cents"] + principal
            or content.get("transfer_operation_id") != str(effect.operation_id)
            or _clock(content["as_of"]) != reconciled
            or _clock(proof["created_at"]) != reconciled
            or _clock(proof["observed_at"]) != reconciled
        ):
            raise ValueError(
                "Transferred goal ownership is not explained by its exact original Bank leg"
            )
