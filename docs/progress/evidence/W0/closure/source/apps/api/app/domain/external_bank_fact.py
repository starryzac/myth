"""MVP-401 candidate economics and income attribution; no SQL or financial authority."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from app.domain.audit_chain import canonical_text
from app.domain.audit_chain_types import AuditSubject
from app.domain.bank_posting_codec import bank_posting_snapshot_version
from app.domain.external_bank_fact_types import (
    ExternalCashAttribution,
    ExternalConsumptionPlan,
    ExternalFactRequest,
    ExternalIncomeUse,
    ExternalProjectionResult,
    ExternalSettlementResult,
)
from app.domain.income_ledger import (
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    LedgerPayload,
    location_id,
)
from app.domain.policy_configuration import configuration_hash

EXTERNAL_HASH_ALGORITHM = "bank-external-canonical-sha256-v1"
EXTERNAL_FACT_FIELDS = frozenset(
    "id user_id created_at protocol_version source_id external_ref kind account_id "
    "amount_cents currency counterparty_ref occurred_at observed_at idempotency_key "
    "request request_canonical_text request_hash bank_status accepted_at settled_at "
    "bank_result bank_result_canonical_text bank_result_hash projection_status "
    "projected_at projection_result projection_result_canonical_text "
    "projection_result_hash updated_at".split()
)
EXTERNAL_IMMUTABLE_FIELDS = frozenset(
    "id user_id created_at protocol_version source_id external_ref kind account_id "
    "amount_cents currency counterparty_ref occurred_at observed_at idempotency_key "
    "request request_canonical_text request_hash accepted_at".split()
)


def external_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(external_text(value).encode("utf-8")).hexdigest()


def external_text(value: dict[str, Any]) -> str:
    def arrays(child: Any) -> Any:
        if isinstance(child, tuple | list):
            return [arrays(item) for item in child]
        if isinstance(child, dict):
            return {key: arrays(item) for key, item in child.items()}
        return child

    return canonical_text(arrays(value))


def external_json(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(external_text(value))
    return result


def semantic_request(request: ExternalFactRequest) -> dict[str, Any]:
    return request.model_dump(exclude={"idempotency_key"})


def fact_id(request: ExternalFactRequest) -> UUID:
    request = ExternalFactRequest.model_validate(request.model_dump())
    return uuid5(
        request.user_id,
        "external-bank-fact:" + request.source_id + ":" + request.external_ref,
    )


def semantic_hash(request: ExternalFactRequest) -> str:
    """Compare exact economics; accepting another idempotency key is forbidden."""
    return external_hash(semantic_request(request))


def clearing_key(request: ExternalFactRequest) -> str:
    return "CLEARING:" + request.source_id + ":" + request.counterparty_ref


def economic_legs(request: ExternalFactRequest) -> tuple[tuple[str, str, int], ...]:
    cash = request.amount_cents if request.kind == "INCOME" else -request.amount_cents
    return (
        ("cash:" + str(request.account_id), "CASH:" + str(request.account_id), cash),
        ("external:clearing", clearing_key(request), -cash),
    )


def _bound_json(text: object, payload: object, digest: object) -> dict[str, Any]:
    if type(text) is not str or type(payload) is not dict:
        raise ValueError("External original requires canonical text and complete object")
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ValueError("External original exceeds its byte budget")
    parsed: dict[str, Any] = json.loads(text)
    if canonical_text(parsed) != text or parsed != payload or external_hash(parsed) != digest:
        raise ValueError("External canonical text, JSON and SHA256 differ")
    return parsed


def validate_external_fact_original(row: Mapping[str, Any]) -> ExternalFactRequest:
    """Validate a full live or frozen original; no bank or tenant state is inferred."""
    if set(row) != EXTERNAL_FACT_FIELDS:
        raise ValueError("Unknown or incomplete external bank fact column layout")
    request = _bound_json(row["request_canonical_text"], row["request"], row["request_hash"])
    command = ExternalFactRequest.model_validate_json(
        canonical_text({**request, "idempotency_key": row["idempotency_key"]})
    )
    if "idempotency_key" in request:
        raise ValueError("Original bank semantic request cannot accept retry aliases")
    columns = command.model_dump(exclude={"simulation", "idempotency_key"})
    if any(
        external_json({"value": row[key]}) != external_json({"value": value})
        for key, value in columns.items()
    ):
        raise ValueError("External bank columns differ from the canonical request")
    if str(row["id"]) != str(fact_id(command)) or str(row["user_id"]) != str(command.user_id):
        raise ValueError("External stable fact identity differs from its original request")
    times: dict[str, datetime | None] = {}
    for name in (
        "created_at",
        "occurred_at",
        "observed_at",
        "accepted_at",
        "settled_at",
        "projected_at",
        "updated_at",
    ):
        value = row[name]
        if value is None and name in {"settled_at", "projected_at"}:
            times[name] = None
            continue
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("External times must retain their aware bank observation")
        times[name] = value
    occurred, observed, accepted, updated = (
        times[name] for name in ("occurred_at", "observed_at", "accepted_at", "updated_at")
    )
    assert (
        occurred is not None
        and observed is not None
        and accepted is not None
        and updated is not None
    )
    if not occurred <= observed <= accepted <= updated or times["created_at"] != accepted:
        raise ValueError("External original economic/observation times are inconsistent")
    if row["bank_status"] not in {"ACCEPTED", "SETTLED", "UNKNOWN", "REJECTED"}:
        raise ValueError("Unknown external bank state")
    if row["projection_status"] not in {"PENDING", "PROJECTED", "UNKNOWN"}:
        raise ValueError("Unknown external projection state")
    if row["bank_status"] == "SETTLED":
        result = ExternalSettlementResult.model_validate_json(
            canonical_text(
                _bound_json(
                    row["bank_result_canonical_text"], row["bank_result"], row["bank_result_hash"]
                )
            )
        )
        if (
            result.user_id != command.user_id
            or result.external_fact_id != fact_id(command)
            or result.request_hash != row["request_hash"]
            or result.settled_at != times["settled_at"]
            or result.settled_at < accepted
            or result.settled_at > updated
        ):
            raise ValueError("External settlement original has lost its request or clock")
    elif any(
        row[name] is not None
        for name in ("settled_at", "bank_result", "bank_result_canonical_text", "bank_result_hash")
    ):
        raise ValueError("An unresolved bank observation cannot claim settlement originals")
    if row["projection_status"] == "PROJECTED":
        projected = ExternalProjectionResult.model_validate_json(
            canonical_text(
                _bound_json(
                    row["projection_result_canonical_text"],
                    row["projection_result"],
                    row["projection_result_hash"],
                )
            )
        )
        if (
            row["bank_status"] != "SETTLED"
            or projected.user_id != command.user_id
            or projected.external_fact_id != fact_id(command)
            or projected.request_hash != row["request_hash"]
            or projected.bank_result_hash != row["bank_result_hash"]
            or projected.projected_at != times["projected_at"]
            or projected.projected_at < result.settled_at
            or projected.projected_at > updated
        ):
            raise ValueError("External projection original has lost its exact bank result")
    elif any(
        row[name] is not None
        for name in (
            "projected_at",
            "projection_result",
            "projection_result_canonical_text",
            "projection_result_hash",
        )
    ):
        raise ValueError("An unresolved projection cannot expose partially written originals")
    if row["projection_status"] == "UNKNOWN" and row["bank_status"] != "SETTLED":
        raise ValueError("Projection unknown must preserve a real settled bank fact")
    return command


def verify_external_fact_binding(original: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    validate_external_fact_original(original)
    validate_external_fact_original(current)
    names = set(EXTERNAL_IMMUTABLE_FIELDS)
    if original["bank_status"] == "SETTLED":
        names.update(
            "bank_status settled_at bank_result bank_result_canonical_text bank_result_hash".split()
        )
    if original["projection_status"] == "PROJECTED":
        names.update(
            (
                "projection_status projected_at projection_result "
                "projection_result_canonical_text projection_result_hash"
            ).split()
        )
    if any(
        external_json({"value": original[name]}) != external_json({"value": current[name]})
        for name in names
    ):
        raise ValueError("An external immutable original changed after acceptance or completion")


def posting_digest(postings: Iterable[Mapping[str, Any]]) -> str:
    rows = [dict(row) for row in postings]
    if len(rows) > 10000 or len({str(row["id"]) for row in rows}) != len(rows):
        raise ValueError("External posting set exceeds its budget or repeats an identity")
    for row in rows:
        if bank_posting_snapshot_version(row) != 2:
            raise ValueError("An external economic original requires the explicit v2 layout")
    return external_hash({"postings": sorted(rows, key=lambda row: str(row["id"]))})


def verify_external_settlement(
    fact: Mapping[str, Any], postings: Iterable[Mapping[str, Any]]
) -> ExternalSettlementResult:
    request = validate_external_fact_original(fact)
    if fact["bank_status"] != "SETTLED":
        raise ValueError("External fact has no independently settled bank result")
    result = ExternalSettlementResult.model_validate_json(str(fact["bank_result_canonical_text"]))
    rows = [row for row in postings if str(row.get("external_fact_id")) == str(fact["id"])]
    economic = [row for row in rows if row["ledger_dimension"] == "ECONOMIC"]
    specs = {leg: (key, amount) for leg, key, amount in economic_legs(request)}
    actual = {row["leg_ref"]: (row["ledger_key"], row["delta_cents"]) for row in economic}
    if (
        len(economic) != 2
        or actual != specs
        or {str(row["id"]) for row in economic}
        != {str(value) for value in result.economic_posting_ids}
    ):
        raise ValueError("External settlement must retain exactly both economic legs")
    for row in economic:
        if (
            bank_posting_snapshot_version(row) != 2
            or str(row["user_id"]) != str(request.user_id)
            or str(row["id"]) != str(uuid5(fact_id(request), "external-posting:" + row["leg_ref"]))
            or external_json({"value": row["occurred_at"]})
            != external_json({"value": request.occurred_at})
            or row["operation_id"] is not None
            or row["redemption_id"] is not None
            or row["position_id"] is not None
            or str(row["account_id"])
            != (str(request.account_id) if row["leg_ref"].startswith("cash:") else "None")
            or external_json({"value": row["created_at"]})
            != external_json({"value": fact["accepted_at"]})
        ):
            raise ValueError("External economic posting origin, tenant or clock changed")
    if sum(row["delta_cents"] for row in economic) != 0 or result.posting_digest != posting_digest(
        economic
    ):
        raise ValueError("External economic set does not conserve its canonical originals")
    return result


def verify_external_projection(
    fact: Mapping[str, Any],
    postings: Iterable[Mapping[str, Any]],
    *,
    subjects: Iterable[AuditSubject],
) -> ExternalProjectionResult:
    rows = list(postings)
    settlement = verify_external_settlement(fact, rows)
    request = validate_external_fact_original(fact)
    if fact["projection_status"] != "PROJECTED":
        raise ValueError("An incomplete projection cannot claim application financial facts")
    result = ExternalProjectionResult.model_validate_json(
        str(fact["projection_result_canonical_text"])
    )
    originals: dict[tuple[str, str], dict[str, Any]] = {}
    for subject in subjects:
        key = subject.kind, str(subject.id)
        if key in originals and originals[key] != subject.data:
            raise ValueError("A projection subject has conflicting immutable versions")
        originals[key] = subject.data
    transaction = originals.get(("TRANSACTION", str(result.transaction_id)))
    evidence = originals.get(("EVIDENCE", str(result.transaction_evidence_id)))
    if transaction is None or evidence is None:
        raise ValueError("External projection must retain its original transaction and bank proof")
    expected = {
        "id": uuid5(fact_id(request), "transaction"),
        "user_id": request.user_id,
        "account_id": request.account_id,
        "evidence_id": uuid5(fact_id(request), "transaction-evidence"),
        "amount_cents": request.amount_cents,
        "direction": "CREDIT" if request.kind == "INCOME" else "DEBIT",
        "occurred_at": request.occurred_at,
        "counterparty_ref": request.counterparty_ref,
        "source_ref": "external-bank-fact:" + str(fact_id(request)),
        "observed_at": result.projected_at,
    }
    cash = next(
        row
        for row in rows
        if str(row.get("external_fact_id")) == str(fact["id"])
        and row["leg_ref"] == "cash:" + str(request.account_id)
    )
    expected["balance_after_cents"] = cash["balance_after_cents"]
    if any(
        external_json({"value": transaction.get(key)}) != external_json({"value": value})
        for key, value in expected.items()
    ):
        raise ValueError("External transaction does not represent the settled cash leg")
    body = evidence.get("content")
    if (
        type(body) is not dict
        or evidence.get("content_hash") != configuration_hash(body)
        or evidence.get("source_type") != "SIMULATED_BANK_TRANSACTION"
        or evidence.get("evidence_level") != "BANK_CONFIRMED"
        or str(evidence.get("user_id")) != str(request.user_id)
        or body.get("economic_role") != request.kind
        or body.get("external_fact_id") != str(fact["id"])
        or body.get("posting_id") != str(cash["id"])
        or body.get("request_hash") != fact["request_hash"]
    ):
        raise ValueError("External transaction evidence lost its actual economic bank origin")
    for name in (
        "transaction_id",
        "account_id",
        "direction",
        "amount_cents",
        "balance_after_cents",
        "occurred_at",
        "counterparty_ref",
    ):
        value = transaction["id"] if name == "transaction_id" else transaction[name]
        original = body.get(name)
        if name == "occurred_at":
            original = datetime.fromisoformat(original) if isinstance(original, str) else original
            value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if external_json({"value": original}) != external_json({"value": value}):
            raise ValueError("External bank evidence and transaction differ")
    successor_ids = {str(value) for value in result.proof_successor_ids}
    for identity in successor_ids:
        proof = originals.get(("EVIDENCE", identity))
        if (
            proof is None
            or str(proof.get("user_id")) != str(request.user_id)
            or proof.get("content_hash") != configuration_hash(proof["content"])
        ):
            raise ValueError("External financial successor is missing or changed")
    income = originals[("EVIDENCE", str(result.income_evidence_id))]
    balance = originals[("EVIDENCE", str(result.balance_evidence_id))]
    previous_balance = originals.get(("EVIDENCE", str(balance.get("supersedes_id"))))
    if previous_balance is None or previous_balance.get("content_hash") != configuration_hash(
        previous_balance["content"]
    ):
        raise ValueError("Cash successor requires its actual immutable previous balance proof")
    for proof, amount in (
        (previous_balance, cash["balance_before_cents"]),
        (balance, cash["balance_after_cents"]),
    ):
        if (
            proof.get("source_type") != "SIMULATED_BANK_BALANCE"
            or proof["content"].get("account_id") != str(request.account_id)
            or proof["content"].get("balance_cents") != amount
            or proof["content"].get("currency") != request.currency
            or proof["content"].get("user_id") != str(request.user_id)
        ):
            raise ValueError("Cash proof does not represent the external bank before/after leg")
    previous = originals.get(("EVIDENCE", str(income.get("supersedes_id"))))
    if previous is None or previous.get("content_hash") != configuration_hash(previous["content"]):
        raise ValueError("External income projection must retain its actual before ledger")
    before = frozen_income_ledger(previous["content"], originals)
    after = IncomeLedger.model_validate_json(json.dumps(income["content"]))
    if request.kind == "INCOME":
        if result.income_uses or result.untracked_spent_cents:
            raise ValueError("Salary cannot consume prior income")
        origin = IncomeOrigin(
            origin_transaction_id=result.transaction_id,
            origin_account_id=request.account_id,
            amount_cents=request.amount_cents,
            occurred_at=request.occurred_at,
            observed_at=result.projected_at,
            bank_evidence_id=result.transaction_evidence_id,
            bank_evidence_hash=evidence["content_hash"],
        )
        candidate = add_income_origin(before, origin, result.projected_at)
        fragment_id = location_id(result.transaction_id, request.account_id)
        specs = {
            "external-income:" + str(fragment_id): (
                "LOT_AVAILABLE:" + str(fragment_id),
                request.amount_cents,
            )
        }
    else:
        if (
            sum(use.amount_cents for use in result.income_uses) + result.untracked_spent_cents
            != request.amount_cents
        ):
            raise ValueError("Consumption attribution must cover the complete external debit")
        plan = ExternalConsumptionPlan(
            status="READY",
            reason_code="AVAILABLE_AND_UNTRACKED_CASH",
            uses=result.income_uses,
            untracked_spent_cents=result.untracked_spent_cents,
        )
        remaining = request.amount_cents
        expected_uses = []
        by_origin = {row.origin_transaction_id: row for row in before.origins}
        for fragment in sorted(
            (row for row in before.fragments if row.account_id == request.account_id),
            key=lambda row: (
                by_origin[row.origin_transaction_id].occurred_at,
                row.origin_transaction_id,
                row.fragment_id,
            ),
        ):
            take = min(remaining, fragment.available_cents)
            if take:
                expected_uses.append(
                    ExternalIncomeUse(
                        fragment_id=fragment.fragment_id,
                        origin_transaction_id=fragment.origin_transaction_id,
                        account_id=fragment.account_id,
                        amount_cents=take,
                    )
                )
                remaining -= take
        if tuple(expected_uses) != result.income_uses or remaining != result.untracked_spent_cents:
            raise ValueError(
                "External consumption must use original AVAILABLE FIFO before ordinary cash"
            )
        candidate = apply_consumption(before, plan, result.projected_at)
        specs = {}
        for use in result.income_uses:
            specs["external-consumption:" + str(use.fragment_id) + ":AVAILABLE"] = (
                "LOT_AVAILABLE:" + str(use.fragment_id),
                -use.amount_cents,
            )
            specs["external-consumption:" + str(use.fragment_id) + ":SPENT"] = (
                "LOT_SPENT:" + str(use.fragment_id),
                use.amount_cents,
            )
    if candidate != after or after.user_id != request.user_id:
        raise ValueError(
            "External income locations do not preserve original claims and conservation"
        )
    memo = [
        row
        for row in rows
        if str(row.get("external_fact_id")) == str(fact["id"])
        and row["ledger_dimension"] == "INCOME_LOCATION"
    ]
    if (
        {str(row["id"]) for row in memo} != {str(value) for value in result.memo_posting_ids}
        or len(memo) != len(specs)
        or {row["leg_ref"]: (row["ledger_key"], row["delta_cents"]) for row in memo} != specs
        or result.memo_posting_digest != posting_digest(memo)
    ):
        raise ValueError("External memo set must bind every actual AVAILABLE/SPENT transition")
    if len(rows) != len(memo) + 2 or any(
        str(row["user_id"]) != str(request.user_id)
        or str(row["id"]) != str(uuid5(fact_id(request), "external-posting:" + row["leg_ref"]))
        or str(row["account_id"]) != str(request.account_id)
        or external_json({"value": row["occurred_at"]})
        != external_json({"value": result.projected_at})
        or external_json({"value": row["created_at"]})
        != external_json({"value": result.projected_at})
        for row in memo
    ):
        raise ValueError("External projection contains extra or rebound posting originals")
    if settlement.external_fact_id != result.external_fact_id:
        raise ValueError("External projection does not belong to its original bank settlement")
    return result


def frozen_income_ledger(
    content: dict[str, Any], originals: Mapping[tuple[str, str], dict[str, Any]]
) -> IncomeLedger:
    """Recover an imported v1 ledger only from its retained actual bank origins."""
    if content.get("protocol") != "new-funds-ledger-v1":
        return IncomeLedger.model_validate_json(json.dumps(content))
    raw = LedgerPayload.model_validate_json(json.dumps(content))
    origins: list[IncomeOrigin] = []
    fragments: list[IncomeFragment] = []
    for lot in raw.lots:
        transaction = originals.get(("TRANSACTION", str(lot.transaction_id)))
        proof = originals.get(("EVIDENCE", str(lot.bank_evidence_id)))
        if (
            transaction is None
            or proof is None
            or str(transaction.get("user_id")) != str(raw.user_id)
            or str(transaction.get("account_id")) != str(lot.account_id)
            or str(transaction.get("evidence_id")) != str(lot.bank_evidence_id)
            or transaction.get("direction") != "CREDIT"
            or transaction.get("amount_cents") != lot.original_cents
            or proof.get("content_hash") != lot.bank_evidence_hash
            or proof.get("content_hash") != configuration_hash(proof["content"])
            or proof["content"].get("economic_role") != "INCOME"
        ):
            raise ValueError("A v1 income lot has no complete immutable original bank source")
        occurred = datetime.fromisoformat(transaction["occurred_at"])
        observed = datetime.fromisoformat(transaction["observed_at"])
        if occurred > raw.as_of or observed > raw.as_of:
            raise ValueError("Original income exceeds its complete before ledger epoch")
        origins.append(
            IncomeOrigin(
                origin_transaction_id=lot.transaction_id,
                origin_account_id=lot.account_id,
                amount_cents=lot.original_cents,
                occurred_at=occurred,
                observed_at=observed,
                bank_evidence_id=lot.bank_evidence_id,
                bank_evidence_hash=lot.bank_evidence_hash,
            )
        )
        fragments.append(
            IncomeFragment(
                fragment_id=location_id(lot.transaction_id, lot.account_id),
                origin_transaction_id=lot.transaction_id,
                account_id=lot.account_id,
                available_cents=lot.available_cents,
                spent_cents=lot.spent_cents,
                assigned_cents=lot.assigned_cents,
                reserved_cents=lot.reserved_cents,
                legacy_reserved_cents=lot.reserved_cents,
            )
        )
    return IncomeLedger(
        user_id=raw.user_id,
        as_of=raw.as_of,
        scope_account_ids=tuple(raw.scope_account_ids),
        origins=tuple(origins),
        fragments=tuple(fragments),
    )


def plan_consumption(
    request: ExternalFactRequest,
    ledger: IncomeLedger,
    attribution: ExternalCashAttribution,
) -> ExternalConsumptionPlan:
    """Spend actual AVAILABLE FIFO, then proven untracked cash, without touching claims."""
    request = ExternalFactRequest.model_validate(request.model_dump())
    ledger = IncomeLedger.model_validate(ledger.model_dump())
    attribution = ExternalCashAttribution.model_validate(attribution.model_dump())
    if (
        request.kind != "CONSUMPTION"
        or request.user_id != ledger.user_id
        or attribution.account_id != request.account_id
        or request.account_id not in ledger.scope_account_ids
    ):
        raise ValueError(
            "External consumption attribution requires its exact tenant and CASH scope"
        )
    origins = {row.origin_transaction_id: row for row in ledger.origins}
    local = [row for row in ledger.fragments if row.account_id == request.account_id]
    available = sum(row.available_cents for row in local)
    reserved = sum(row.reserved_cents for row in local)
    untracked = (
        attribution.bank_balance_before_cents
        - attribution.goal_cash_owned_cents
        - available
        - reserved
        - attribution.non_income_claim_cents
    )
    if untracked < 0:
        return ExternalConsumptionPlan(
            status="UNRECONCILED", reason_code="CASH_ATTRIBUTION_INVALID"
        )
    if request.amount_cents > available + untracked:
        return ExternalConsumptionPlan(
            status="UNRECONCILED", reason_code="CONSUMPTION_INVADES_PROTECTED_LOCATION"
        )
    remaining = request.amount_cents
    uses: list[ExternalIncomeUse] = []
    for fragment in sorted(
        local,
        key=lambda row: (
            origins[row.origin_transaction_id].occurred_at,
            row.origin_transaction_id,
            row.fragment_id,
        ),
    ):
        take = min(remaining, fragment.available_cents)
        if take:
            uses.append(
                ExternalIncomeUse(
                    fragment_id=fragment.fragment_id,
                    origin_transaction_id=fragment.origin_transaction_id,
                    account_id=fragment.account_id,
                    amount_cents=take,
                )
            )
            remaining -= take
    if remaining > untracked:
        raise ValueError("The complete plan does not fit its independently proven untracked cash")
    return ExternalConsumptionPlan(
        status="READY",
        reason_code="AVAILABLE_AND_UNTRACKED_CASH",
        uses=tuple(uses),
        untracked_spent_cents=remaining,
    )


def apply_consumption(
    ledger: IncomeLedger, plan: ExternalConsumptionPlan, now: datetime
) -> IncomeLedger:
    if plan.status != "READY":
        raise ValueError("An unreconciled consumption cannot mutate income attribution")
    if now.tzinfo is None or now.utcoffset() is None or now < ledger.as_of:
        raise ValueError("Income projection time cannot be naive or precede its previous snapshot")
    by_id = {row.fragment_id: row for row in ledger.fragments}
    if len({row.fragment_id for row in plan.uses}) != len(plan.uses):
        raise ValueError("External consumption sources must be unique")
    for use in plan.uses:
        fragment = by_id.get(use.fragment_id)
        if fragment is None or (
            fragment.origin_transaction_id != use.origin_transaction_id
            or fragment.account_id != use.account_id
            or fragment.available_cents < use.amount_cents
        ):
            raise ValueError("External consumption no longer fits the original AVAILABLE fragment")
        by_id[fragment.fragment_id] = fragment.model_copy(
            update={
                "available_cents": fragment.available_cents - use.amount_cents,
                "spent_cents": fragment.spent_cents + use.amount_cents,
            }
        )
    return IncomeLedger.model_validate(
        {
            **ledger.model_dump(),
            "as_of": now.astimezone(UTC),
            "fragments": tuple(
                row.model_dump() for row in sorted(by_id.values(), key=lambda row: row.fragment_id)
            ),
        }
    )


def add_income_origin(ledger: IncomeLedger, origin: IncomeOrigin, now: datetime) -> IncomeLedger:
    """The service must first bind this origin to the actual projected bank transaction/proof."""
    ledger = IncomeLedger.model_validate(ledger.model_dump())
    origin = IncomeOrigin.model_validate(origin.model_dump())
    if now.tzinfo is None or now.utcoffset() is None or now < ledger.as_of:
        raise ValueError("New income observation cannot move backwards")
    if origin.origin_account_id not in ledger.scope_account_ids:
        raise ValueError("External income is outside the existing complete CASH account scope")
    previous = next(
        (
            row
            for row in ledger.origins
            if row.origin_transaction_id == origin.origin_transaction_id
        ),
        None,
    )
    if previous is not None:
        if previous != origin:
            raise ValueError(
                "A stable external income origin cannot change its original bank facts"
            )
        return ledger
    fragment = IncomeFragment(
        fragment_id=location_id(origin.origin_transaction_id, origin.origin_account_id),
        origin_transaction_id=origin.origin_transaction_id,
        account_id=origin.origin_account_id,
        available_cents=origin.amount_cents,
    )
    return IncomeLedger.model_validate(
        {
            **ledger.model_dump(),
            "as_of": now.astimezone(UTC),
            "origins": tuple(
                row.model_dump()
                for row in sorted(
                    (*ledger.origins, origin), key=lambda row: row.origin_transaction_id
                )
            ),
            "fragments": tuple(
                row.model_dump()
                for row in sorted((*ledger.fragments, fragment), key=lambda row: row.fragment_id)
            ),
        }
    )
