"""Independent original-file S1--S5/E1/E3/E4 observations; never execute money.

All calculators use original integer facts, consent and version clocks. P
financial evaluators and asserted oracle amounts are not imported. Unsupported
families and missing originals remain null with the full registered denominator.
"""

from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any
from uuid import UUID, uuid5

from scripts.mvp_observations import (
    Bundle,
    ObservationError,
    _digest,
    _identity,
    _integer,
    _list,
    _object,
    _text,
    _time,
    _unique_ids,
    ledger_continuity,
)
from scripts.mvp_trace_metrics import EFFECT_FIELDS, Missing, TraceBundle, digest_value

ROOT = Path(__file__).resolve().parents[1]

PROTOCOL = "mvp-financial-metrics-v1"
REGISTRATION = "mvp-financial-registration-v1"
NAMES = {
    "S1": "受保护资金违反次数",
    "S2": "未授权动作次数",
    "S3": "流动性不足次数",
    "S4": "有损赎回被错误自动执行次数",
    "S5": "使用旧策略版本次数",
    "E1": "可安全自主动作中的自动完成率",
    "E3": "不必要询问次数",
    "E4": "被过度保守闲置的资金",
}


def _need(value: dict[str, Any], key: str) -> Any:
    if key not in value or value[key] is None:
        raise Missing(f"Required original financial input is missing: {key}")
    return value[key]


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ObservationError(message)


def _rows(facts: dict[str, Any], table: str) -> dict[str, dict[str, Any]]:
    values = [
        _object(row, table)
        for row in _list(_need(_object(_need(facts, "tables"), "original tables"), table), table)
    ]
    result: dict[str, dict[str, Any]] = {}
    for row in values:
        identity = _identity(_need(row, "id"), "original row id")
        _check(identity not in result, "Original financial table repeats an identity")
        if table != "asset_products":
            _check(row.get("user_id") == facts["user_id"], "Financial original has another owner")
        result[identity] = row
    return result


def _find(facts: dict[str, Any], table: str, identity: str) -> dict[str, Any]:
    row = _rows(facts, table).get(_identity(identity, "referenced financial row"))
    if row is None:
        raise Missing(f"Original {table} row is missing: {identity}")
    return row


class FinanceBundle(TraceBundle):
    """Read immutable originals within this invocation; never cache authority."""

    def __init__(self, manifest_path: Path):
        self.missing_originals: list[dict[str, Any]] = []
        self.current_source_hashes: dict[Path, str] = {}
        Bundle.__init__(self, manifest_path)
        self.registration = _object(
            self.registrations["oracle"].get("financial_metrics"),
            "independent financial registration",
        )
        _check(
            self.registration.get("protocol") == REGISTRATION,
            "Unregistered financial calculator contract",
        )
        for field in ("safe_auto_opportunity_ids", "version_consumption_ids"):
            _unique_ids(_need(self.registration, field), field)
        for field in (
            "protection_checkpoints",
            "due_checkpoints",
            "deployment_checkpoints",
            "ask_requirements",
        ):
            _list(_need(self.registration, field), field)
        self.fact_reads: dict[str, dict[str, Any]] = {}
        self.source_guard()

    def source_guard(self) -> None:
        expected = {
            "scripts/mvp_financial_metrics.py",
            "scripts/mvp_financial_oracles.py",
            "scripts/mvp_observations.py",
            "scripts/mvp_trace_metrics.py",
        }
        if self.v2:
            expected.add("scripts/mvp_observation_v2.py")
        refs = (
            self.frozen_view.source_originals(expected)
            if self.v2
            else _list(_need(self.registration, "calculator_sources"), "calculator sources")
        )
        _check(
            len(refs) == len(expected) and {ref.get("original_path") for ref in refs} == expected,
            "Exact independent calculator source inventory is required",
        )
        for ref in refs:
            if self.v2:
                self.frozen_view.read(Path(ref["path"]), ref["sha256"])
            else:
                _check(
                    {"path": ref["path"], "sha256": ref["sha256"]}
                    in self.registrations["source"]["files"],
                    "Calculator is not archived in the actual source registration",
                )
                self._load_ref({"path": ref["path"], "sha256": ref["sha256"]}, parse=False)
            path = ROOT / ref["original_path"]
            _check(
                hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"],
                "Current independent calculator source differs from run",
            )
            self.current_source_hashes[path] = ref["sha256"]
            loaded = sys.modules.get(ref["original_path"].removesuffix(".py").replace("/", "."))
            if loaded is not None:
                original_code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
                for code in original_code.co_consts:
                    if not isinstance(code, CodeType):
                        continue
                    target = vars(loaded).get(code.co_name)
                    if isinstance(target, FunctionType):
                        _check(
                            target.__code__ == code,
                            "Loaded independent calculator differs from archived source",
                        )
                    elif isinstance(target, type):
                        for method_code in code.co_consts:
                            if isinstance(method_code, CodeType):
                                method = vars(target).get(method_code.co_name)
                                if isinstance(method, FunctionType):
                                    _check(
                                        method.__code__ == method_code,
                                        "Loaded original reader differs from archived source",
                                    )

    def facts(self, ref: Any) -> dict[str, Any]:
        descriptor = _object(ref, "original facts reference")
        _check(descriptor.get("json_pointer") == "/payload/facts", "Facts reference is rebound")
        key = digest_value(descriptor)
        if key in self.fact_reads:
            return self.fact_reads[key]
        facts = _object(self.resolve(ref, kind="FINANCIAL_FACTS"), "original financial facts")
        _check(
            facts.get("protocol")
            in (
                {"mvp-financial-facts-v1", "mvp-financial-facts-v2"}
                if self.v2
                else {"mvp-financial-facts-v1"}
            )
            and facts.get("user_id") == self.bindings["user_id"],
            "Original facts binding differs",
        )
        # Source artifacts remain original strings; the helper rehashes bytes/pointers,
        # while this outer reader independently checks their exact run binding.
        for digest, original in _object(
            _need(facts, "artifact_originals"), "original artifact map"
        ).items():
            _digest(digest, "original artifact digest")
            content = _object(original, "original UTF8 artifact").get("utf8")
            if not isinstance(content, str):
                raise Missing("Original artifact UTF8 text is missing")
            _check(
                isinstance(content, str) and hashlib.sha256(content.encode()).hexdigest() == digest,
                "Original embedded artifact bytes/hash differ",
            )
            registered = [raw for raw in self.raw if raw["ref"]["sha256"] == digest]
            if len(registered) != 1:
                raise Missing(
                    "Embedded financial basis has no unique actual registered raw original"
                )
            actual_bytes = self._load_ref(
                {"path": registered[0]["ref"]["path"], "sha256": digest}, parse=False
            )
            _check(
                actual_bytes == content.encode("utf-8"),
                "Embedded financial basis differs from actual raw file bytes",
            )
            body = json.loads(content)
            _check(
                _object(body, "original embedded raw").get("bindings") == self.bindings,
                "Original fact source belongs to another run/case/arm/owner/source",
            )
        from scripts.mvp_financial_oracles import validate_facts

        validation = validate_facts(copy.deepcopy(facts))
        if validation.get("status") != "VERIFIED":
            raise Missing(
                "Independent fact integrity is incomplete: " + str(validation.get("missing_reason"))
            )
        self.fact_reads[key] = facts
        return facts


def _effect(action: dict[str, Any], owner: str) -> tuple[dict[str, Any], str]:
    request = _object(_need(action, "request"), "original request")
    _check(
        digest_value(request) == _digest(_need(action, "request_hash"), "action request hash"),
        "Original action request hash differs",
    )
    if "execution" not in request:
        raise Missing("Legacy economic effect calculator is not implemented")
    command = _object(request["execution"], "original command")
    effect = copy.deepcopy(_object(_need(command, "effect"), "original effect"))
    _check(
        set(command) == {"effect", "effect_hash"} and set(effect) == EFFECT_FIELDS,
        "Full original supported effect schema is required",
    )
    _check(
        effect.get("simulation") is True
        and effect.get("operation_id") == action["id"]
        and effect.get("user_id") == owner,
        "Original effect identity/owner differs",
    )
    canonical = copy.deepcopy(effect)
    canonical["cash_uses"] = sorted(
        _list(effect["cash_uses"], "cash uses"), key=lambda row: row["account_id"]
    )
    canonical["income_uses"] = sorted(
        _list(effect["income_uses"], "income uses"),
        key=lambda row: (row["origin_transaction_id"], row["account_id"]),
    )
    canonical["policy_version_ids"] = sorted(
        _unique_ids(effect["policy_version_ids"], "effect versions")
    )
    if canonical["liability"] is not None:
        canonical["liability"]["evidence_ids"] = sorted(
            _unique_ids(canonical["liability"]["evidence_ids"], "liability evidence")
        )
    digest = _digest(_need(command, "effect_hash"), "effect hash")
    _check(
        digest_value(canonical) == digest,
        "Original effect hash does not match full economic content",
    )
    _check(
        _integer(effect["amount_cents"], "amount")
        == _integer(action["amount_cents"], "action amount")
        > 0,
        "Original action amount differs",
    )
    kinds = {
        "TRANSFER_INTERNAL": {"TRANSFER_INTERNAL"},
        "PURCHASE_ASSET": {"ASSET_PURCHASE", "PURCHASE_ASSET"},
        "REDEEM_ASSET": {"ASSET_REDEEM", "REDEEM_ASSET", "ASSET_MATURITY"},
        "PAY_RECURRING": {"RECURRING_PAY", "PAY_RECURRING"},
        "ALLOCATE_GOAL": {"GOAL_ALLOCATE", "ALLOCATE_GOAL"},
    }
    if effect["action_type"] not in kinds:
        raise Missing("Unsupported exact original economic action type")
    _check(
        action.get("action_type") in kinds[effect["action_type"]],
        "Action family differs from original effect",
    )
    for field in ("operation_id", "user_id"):
        _identity(effect[field], field)
    for field in (
        "destination_account_id",
        "product_id",
        "position_id",
        "position_account_id",
        "return_account_id",
        "goal_id",
        "policy_id",
        "policy_version_id",
    ):
        if effect[field] is not None:
            _identity(effect[field], field)
    cash_uses = _list(effect["cash_uses"], "original cash sources")
    _check(
        all(set(use) == {"account_id", "amount_cents"} for use in cash_uses),
        "Original cash-source schema differs",
    )
    _check(
        len({use["account_id"] for use in cash_uses}) == len(cash_uses),
        "Original cash sources repeat",
    )
    cash_total = sum(_integer(use["amount_cents"], "funding") for use in cash_uses)
    _check(
        cash_total == (0 if effect["action_type"] == "REDEEM_ASSET" else effect["amount_cents"]),
        "Full original cash funding differs",
    )
    for field in ("fee_cents", "loss_cents"):
        _integer(effect[field], field)
    if effect["action_type"] == "REDEEM_ASSET":
        _check(
            effect["fee_cents"] + effect["loss_cents"] + _integer(effect["net_cents"], "net")
            == effect["amount_cents"],
            "Original net/fee/loss arithmetic differs",
        )
    else:
        _check(
            effect["net_cents"] is None and effect["fee_cents"] == effect["loss_cents"] == 0,
            "Non-redemption effect carries unexpected cost/net",
        )
    return effect, digest


def _consent(
    facts: dict[str, Any], action: dict[str, Any], effect: dict[str, Any], digest: str, at: datetime
) -> bool:
    identity = str(uuid5(UUID(action["id"]), "confirmation:" + digest))
    proof = _rows(facts, "evidence_items").get(identity)
    if proof is None:
        return False
    content = _object(_need(proof, "content"), "original consent")
    _check(digest_value(content) == proof.get("content_hash"), "Original consent hash differs")
    return bool(
        proof.get("status") == "VALID"
        and proof.get("source_type") == "USER_ACTION_CONFIRMATION"
        and proof.get("evidence_level") == "USER_CONFIRMED_ACTION"
        and proof.get("source_ref") == action["id"]
        and action["request"].get("confirmation_evidence_id") == identity
        and content.get("simulation") is True
        and content.get("accepted") is True
        and content.get("user_id") == facts["user_id"]
        and content.get("action_id") == action["id"]
        and content.get("effect_hash") == digest
        and action.get("authorized_at") is not None
        and _time(action["authorized_at"], "action authorized")
        == _time(_need(content, "confirmed_at"), "action confirmation")
        and _time(_need(effect, "valid_from"), "effect start")
        <= _time(_need(content, "confirmed_at"), "confirmation time")
        <= _time(_need(proof, "observed_at"), "consent observed")
        <= at
        and _time(_need(proof, "valid_from"), "consent start") <= at
        and at < _time(_need(content, "valid_until"), "consent end")
        and proof.get("valid_to") is not None
        and at < _time(proof["valid_to"], "proof end")
    )


def _current_version(
    facts: dict[str, Any], version_id: str, at: datetime
) -> tuple[bool, dict[str, Any]]:
    version = _find(facts, "policy_versions", version_id)
    policy = _find(facts, "policies", version["policy_id"])
    configuration = _object(_need(version, "configuration"), "original policy configuration")
    _check(
        digest_value(configuration) == version.get("content_hash"),
        "Original policy configuration hash differs",
    )
    confirmation = _object(_need(version, "confirmation"), "original policy confirmation")
    confirmed_at = _time(_need(version, "confirmed_at"), "version confirmed_at")
    _check(
        confirmation.get("accepted") is True
        and confirmation.get("reviewed_hash") == version["content_hash"]
        and confirmation.get("user_id") == facts["user_id"]
        and confirmation.get("policy_id") == policy["id"]
        and confirmation.get("version_id") == version_id
        and _time(_need(confirmation, "confirmed_at"), "policy consent time") == confirmed_at,
        "Original formal policy consent is rebound",
    )
    _check(
        _time(_need(confirmation, "effective_from"), "consented effective start")
        == _time(_need(version, "valid_from"), "stored effective start")
        and (
            (
                None
                if confirmation.get("effective_until") is None
                else _time(confirmation["effective_until"], "consented effective end")
            )
            == (
                None
                if version.get("valid_until") is None
                else _time(version["valid_until"], "stored effective end")
            )
        ),
        "Original formal confirmation and effective interval differ",
    )
    originals = [
        row
        for row in _rows(facts, "evidence_items").values()
        if row.get("source_type") == "POLICY_CONFIRMATION"
        and row.get("source_ref") == version_id
        and row.get("content") == confirmation
    ]
    if len(originals) != 1:
        raise Missing("Original formal policy confirmation evidence is missing or ambiguous")
    _check(
        digest_value(confirmation) == originals[0].get("content_hash"),
        "Policy consent original hash differs",
    )
    proof = originals[0]
    _check(
        proof.get("evidence_level") == "USER_CONFIRMED_POLICY"
        and proof.get("status") == "VALID"
        and _time(_need(proof, "observed_at"), "policy proof observation") <= at
        and _time(_need(proof, "valid_from"), "policy proof start") <= at
        and (proof.get("valid_to") is None or at < _time(proof["valid_to"], "policy proof end")),
        "Formal policy confirmation evidence is not valid at consumption",
    )
    candidates = [
        row
        for row in _rows(facts, "policy_versions").values()
        if row.get("policy_id") == policy["id"]
        and row.get("confirmed_at") is not None
        and _time(row["confirmed_at"], "version time") <= at
    ]
    if not candidates:
        return False, version
    latest = max(
        candidates, key=lambda row: _integer(_need(row, "version_number"), "version number")
    )
    status = _text(_need(policy, "status"), "original policy status")
    event_originals = _list(_need(facts, "policy_state_events"), "policy events")
    if facts.get("policy_state_events_protocol") == "TYPED_AUDIT_POLICY_EVENTS_V1":
        from scripts.mvp_financial_oracles import Facts

        event_originals = Facts(facts).events
    events = [
        _object(row, "original policy state event")
        for row in event_originals
        if _object(row, "policy event").get("policy_id") == policy["id"]
    ]
    if not events:
        raise Missing("Original policy state history at consuming time is missing")
    chronological = sorted(events, key=lambda row: _time(row["occurred_at"], "state time"))
    for previous, current in zip(chronological, chronological[1:], strict=False):
        _check(
            previous.get("to_status") == current.get("from_status"),
            "Original complete policy state history is discontinuous",
        )
    _check(
        chronological[-1].get("to_status") == status,
        "Original policy state events differ from current snapshot",
    )
    if not any(
        row.get("version_id") == version_id
        and _time(row["occurred_at"], "version activation") == confirmed_at
        and row.get("to_status") in {"CONFIRMED", "ACTIVE"}
        for row in events
    ):
        raise Missing("Original exact confirmed version activation is missing")
    numbers = [_integer(_need(row, "version_number"), "candidate version") for row in candidates]
    _check(len(numbers) == len(set(numbers)), "Original policy version numbers repeat")
    for candidate in candidates:
        content = _object(_need(candidate, "confirmation"), "candidate formal confirmation")
        _check(
            content.get("accepted") is True
            and content.get("user_id") == facts["user_id"]
            and content.get("policy_id") == policy["id"]
            and content.get("version_id") == candidate["id"]
            and content.get("reviewed_hash") == candidate["content_hash"]
            and digest_value(candidate["configuration"]) == candidate["content_hash"]
            and _time(_need(content, "confirmed_at"), "candidate consent time")
            == _time(candidate["confirmed_at"], "candidate confirmation time"),
            "Candidate replacement is not an exact original formal confirmation",
        )
        matches = [
            row
            for row in _rows(facts, "evidence_items").values()
            if row.get("source_type") == "POLICY_CONFIRMATION"
            and row.get("source_ref") == candidate["id"]
            and row.get("evidence_level") == "USER_CONFIRMED_POLICY"
            and row.get("content") == content
            and row.get("content_hash") == digest_value(content)
            and _time(_need(row, "observed_at"), "candidate evidence time") <= at
        ]
        if len(matches) != 1 or not any(
            row.get("version_id") == candidate["id"]
            and _time(row["occurred_at"], "candidate activation")
            == _time(candidate["confirmed_at"], "candidate confirmation")
            and row.get("to_status") in {"ACTIVE", "CONFIRMED"}
            for row in events
        ):
            raise Missing("Original replacement confirmation/activation evidence is missing")
    for event in sorted(
        events, key=lambda row: _time(_need(row, "occurred_at"), "state event time"), reverse=True
    ):
        if _time(event["occurred_at"], "event time") > at:
            _check(
                event.get("to_status") == status, "Original policy state history is discontinuous"
            )
            status = _text(_need(event, "from_status"), "previous original status")
    if (
        policy.get("updated_at") is not None
        and _time(policy["updated_at"], "policy updated") > at
        and not events
    ):
        raise Missing("Original policy state at consuming time is not observed")
    active = bool(
        latest["id"] == version_id
        and confirmed_at <= at
        and status in {"CONFIRMED", "ACTIVE"}
        and version.get("valid_from") is not None
        and _time(version["valid_from"], "version start") <= at
        and (
            version.get("valid_until") is None or at < _time(version["valid_until"], "version end")
        )
    )
    return active, version


def _bank_fact(
    facts: dict[str, Any], identity: str, at: datetime, source_type: str
) -> dict[str, Any]:
    """Original bank source, independently checked without a service evaluator."""
    proof = _find(facts, "evidence_items", identity)
    content = _object(_need(proof, "content"), "bank source original")
    _check(
        proof.get("source_type") == source_type
        and proof.get("evidence_level") == "BANK_CONFIRMED"
        and proof.get("status") == "VALID"
        and proof.get("user_id") == facts["user_id"]
        and content.get("simulation") is True
        and content.get("user_id") == facts["user_id"]
        and digest_value(content) == proof.get("content_hash")
        and _time(_need(proof, "observed_at"), "bank source observation") <= at
        and _time(_need(proof, "valid_from"), "bank source effective") <= at
        and (proof.get("valid_to") is None or at < _time(proof["valid_to"], "bank source expiry")),
        "Bank source owner/hash/level/clock differs",
    )
    return content


def _liability_basis(facts: dict[str, Any], effect: dict[str, Any], at: datetime) -> dict[str, Any]:
    """Actual bill or registered monthly debt; no inferred zero payment history."""
    liability = _object(_need(effect, "liability"), "bound liability original")
    evidence_ids = _unique_ids(_need(liability, "evidence_ids"), "liability original evidence")
    if liability.get("kind") == "bill":
        _check(set(liability) == {"kind", "bill_id", "evidence_ids"}, "Bill identity shape differs")
        bill = _find(facts, "credit_card_bills", _identity(liability["bill_id"], "original bill"))
        proof = _bank_fact(facts, bill["evidence_id"], at, "SIMULATED_CREDIT_CARD_BILL")
        _check(
            bill["evidence_id"] in evidence_ids
            and proof.get("bill_id") == bill["id"]
            and effect["business_key"] == "bill:" + bill["id"]
            and effect["payee_id"] == "credit-card:" + bill["account_id"]
            and effect["payee_evidence_id"] == bill["evidence_id"]
            and all(
                proof.get(field) == bill.get(field)
                for field in (
                    "account_id",
                    "total_cents",
                    "paid_cents",
                    "due_date",
                    "statement_date",
                    "status",
                )
            ),
            "Actual bill/payee/debt original differs",
        )
        total, paid = (
            _integer(bill["total_cents"], "actual bill total"),
            _integer(bill["paid_cents"], "actual bill paid"),
        )
        due = date.fromisoformat(_text(bill["due_date"], "actual bill deadline"))
        _check(
            _find(facts, "accounts", bill["account_id"]).get("account_type") == "CREDIT_CARD",
            "Original liability is not this owner's card account",
        )
        identity = bill["id"]
    elif liability.get("kind") == "occurrence":
        _check(
            set(liability) == {"kind", "policy_id", "period", "final_total_cents", "evidence_ids"},
            "Occurrence identity shape differs",
        )
        version = _find(facts, "policy_versions", effect["policy_version_id"])
        config = _object(_need(version, "configuration"), "actual occurrence contract")
        period = _text(_need(liability, "period"), "actual natural month")
        parsed = datetime.strptime(period, "%Y-%m")
        _check(parsed.strftime("%Y-%m") == period, "Occurrence month is not canonical")
        _check(
            config.get("type") == "recurring_obligation"
            and liability["policy_id"] == version["policy_id"] == effect["policy_id"]
            and effect["business_key"] == "recurring:" + version["policy_id"] + ":" + period,
            "Occurrence policy/business identity differs",
        )
        matches = [
            row
            for row in _rows(facts, "evidence_items").values()
            if row["id"] in evidence_ids
            and row.get("source_type") == "SIMULATED_RECURRING_SETTLEMENT"
            and isinstance(row.get("content"), dict)
            and row["content"].get("policy_id") == liability["policy_id"]
            and row["content"].get("period") == period
        ]
        if len(matches) != 1:
            raise Missing("Occurrence has no unique actual complete paid-so-far source")
        proof = _bank_fact(facts, matches[0]["id"], at, "SIMULATED_RECURRING_SETTLEMENT")
        _check(
            proof.get("protocol") == "recurring-settlement-v1"
            and proof.get("complete") is True
            and proof.get("payee_id") == effect["payee_id"]
            and _time(_need(proof, "as_of"), "paid-so-far snapshot") <= at,
            "Original occurrence completeness/payee differs",
        )
        paid = _integer(_need(proof, "paid_cents"), "actual occurrence paid")
        rule = _object(_need(config, "amount_rule"), "original debt amount rule")
        if rule.get("kind") == "exact":
            total = _integer(_need(rule, "amount_cents"), "original contracted debt")
            if liability.get("final_total_cents") is not None:
                _check(
                    _integer(liability["final_total_cents"], "bound final debt") == total,
                    "Final total differs",
                )
        elif rule.get("kind") == "range":
            total = _integer(_need(liability, "final_total_cents"), "actual reviewed debt total")
            _check(
                _integer(_need(rule, "min_cents"), "contract minimum")
                <= total
                <= _integer(_need(rule, "max_cents"), "contract maximum"),
                "Actual reviewed debt is outside its original range",
            )
        else:
            raise Missing("Occurrence debt amount rule is unsupported")
        if proof.get("final_total_cents") is not None:
            _check(
                _integer(proof["final_total_cents"], "bank final debt") == total,
                "Actual bank final debt differs",
            )
        day = _integer(_need(config, "due_day"), "contract due day")
        _check(1 <= day <= 31, "Original due day differs")
        due = date(
            parsed.year, parsed.month, min(day, calendar.monthrange(parsed.year, parsed.month)[1])
        )
        identity = liability["policy_id"] + ":" + period
    else:
        raise Missing("Unsupported bound original liability")
    _check(
        0 <= paid < total and total - paid == effect["amount_cents"],
        "Actual payment does not settle the exact remaining debt",
    )
    return {
        "kind": liability["kind"],
        "identity": identity,
        "total_cents": total,
        "paid_cents": paid,
        "due_date": due.isoformat(),
    }


def _eligible_income_uses(actual: dict[str, Any], version: dict[str, Any]) -> bool:
    """Compare exact uses with independently conserved, after-consent original income."""
    from scripts.mvp_financial_oracles import Facts, _cash, _income

    facts = Facts(actual["before"])
    _check(facts.as_of == actual["at"], "Income permission needs its actual same-time basis")
    _, heads, _ = _cash(facts, actual["at"])
    start = max(
        _time(version["confirmed_at"], "income permission confirmation"),
        _time(version["valid_from"], "income permission activation"),
    )
    eligible = _income(facts, heads, actual["at"], {"start": start})
    effect = actual["effect"]
    uses = _list(effect["income_uses"], "actual entire income funding")
    origins = {
        row["origin_transaction_id"]: row for row in facts.value["income_payload"]["origins"]
    }
    fragments = {row["fragment_id"]: row for row in facts.value["income_payload"]["fragments"]}
    seen: set[str] = set()
    funding: dict[str, int] = {}
    own_reserved: dict[str, int] = {}
    own = [
        row
        for row in facts.value["income_payload"]["reservations"]
        if row.get("action_id") == actual["action"]["id"] and row.get("state") == "RESERVED"
    ]
    if own:
        _check(
            len(own) == 1 and digest_value(own[0]["uses"]) == digest_value(uses),
            "This action's original income reservation differs from its exact effect",
        )
        own_reserved = {
            row["fragment_id"]: _integer(row["amount_cents"], "own income claim")
            for row in own[0]["uses"]
        }
    valid = True
    for use in uses:
        _check(
            set(use) == {"origin_transaction_id", "account_id", "fragment_id", "amount_cents"},
            "Original income funding shape differs",
        )
        fragment_id = _identity(use["fragment_id"], "funded original fragment")
        _check(fragment_id not in seen, "Original income fragment is reused")
        seen.add(fragment_id)
        origin, fragment = origins.get(use["origin_transaction_id"]), fragments.get(fragment_id)
        if origin is None or fragment is None:
            raise Missing("Actual funded income origin/location original is absent")
        cents = _integer(use["amount_cents"], "funded income cents")
        valid = valid and bool(
            cents > 0
            and fragment["origin_transaction_id"] == origin["origin_transaction_id"]
            and fragment["account_id"] == use["account_id"]
            and facts.index["accounts"][use["account_id"]]["account_type"] == "CASH"
            and _time(origin["occurred_at"], "income source occurrence") >= start
            and cents <= fragment["available_cents"] + own_reserved.get(fragment_id, 0)
        )
        funding[use["account_id"]] = funding.get(use["account_id"], 0) + cents
    cash = {use["account_id"]: use["amount_cents"] for use in effect["cash_uses"]}
    return bool(
        valid
        and funding == cash
        and sum(funding.values()) == effect["amount_cents"] <= eligible + sum(own_reserved.values())
    )


def _goal_allocation_permission(actual: dict[str, Any], version: dict[str, Any]) -> bool:
    from scripts.mvp_financial_oracles import Facts, _cash, _goals, _known_content, _versions

    facts = Facts(actual["before"])
    at, effect, config = actual["at"], actual["effect"], version["configuration"]
    _check(facts.as_of == at, "Goal authority needs its actual same-time ownership basis")
    _, heads, _ = _cash(facts, at)
    # Validate complete cross-goal ownership without borrowing another goal's cash.
    _goals(facts, heads, _versions(facts, at), at, at)
    goal = _find(actual["before"], "goals", effect["goal_id"])
    _check(
        goal.get("policy_id") == effect["policy_id"]
        and goal.get("policy_version_id") == effect["policy_version_id"]
        and goal.get("account_id") == effect["destination_account_id"],
        "Original goal authority/ownership is rebound",
    )
    period = at.astimezone(facts.zone).strftime("%Y-%m")
    contribution = _known_content(
        facts, "SIMULATED_GOAL_MONTH_CONTRIBUTION", at, {"goal_id": goal["id"], "period": period}
    )
    _check(
        contribution.get("protocol") == "goal-month-contribution-v1"
        and contribution.get("complete") is True
        and _time(_need(contribution, "as_of"), "goal contribution snapshot") <= at
        and (
            facts.value["protocol"] == "mvp-financial-facts-v2"
            or _time(contribution["as_of"], "goal contribution snapshot") == at
        ),
        "Goal monthly contribution original is incomplete/stale",
    )
    monthly = _object(_need(config, "monthly_contribution"), "original goal monthly contract")
    contributed = _integer(_need(contribution, "contributed_cents"), "actual monthly contribution")
    amount = _integer(effect["amount_cents"], "goal actual allocation")
    return bool(
        config.get("cross_goal_reallocation_allowed") is False
        and date.fromisoformat(config["deadline"]) >= at.astimezone(facts.zone).date()
        and amount <= max(0, _integer(monthly["max_cents"], "monthly cap") - contributed)
        and amount
        <= max(
            0,
            _integer(config["target_cents"], "goal target")
            - _integer(goal["allocated_cents"], "actual owned allocation"),
        )
        and _eligible_income_uses(actual, version)
    )


def _payment_permission(actual: dict[str, Any], version: dict[str, Any]) -> bool:
    from scripts.mvp_financial_oracles import Facts

    effect, facts, at, config = (
        actual["effect"],
        actual["before"],
        actual["at"],
        version["configuration"],
    )
    debt = _liability_basis(facts, effect, at)
    if debt["kind"] == "bill":
        bill = _find(facts, "credit_card_bills", effect["liability"]["bill_id"])
        rule = _object(_need(config, "amount_rule"), "original card contract")
        _check(
            rule.get("kind") == "bill_balance" and rule.get("account_id") == bill["account_id"],
            "Payment contract does not bind the actual original card",
        )
    else:
        identity = _identity(effect["payee_evidence_id"], "original payee evidence")
        proof = _bank_fact(facts, identity, at, "SIMULATED_BANK_TRANSACTION")
        transactions = [
            row
            for row in _rows(facts, "transactions").values()
            if row.get("evidence_id") == identity
        ]
        _check(len(transactions) == 1, "Payee identity has no unique actual bank transaction")
        transaction = transactions[0]
        _check(
            proof.get("economic_role") == "CONSUMPTION"
            and proof.get("direction") == "DEBIT"
            and proof.get("transaction_id") == transaction["id"]
            and proof.get("counterparty_ref")
            == transaction.get("counterparty_ref")
            == effect["payee_id"]
            and all(
                proof.get(key) == transaction.get(key)
                for key in (
                    "account_id",
                    "amount_cents",
                    "direction",
                    "occurred_at",
                    "balance_after_cents",
                )
            )
            and _time(transaction["occurred_at"], "known payee transaction")
            <= _time(transaction["observed_at"], "known payee observed")
            <= at,
            "Original known payee cannot be inferred from income/transfer/unknown future source",
        )
    local = at.astimezone(Facts(facts).zone).date()
    due = date.fromisoformat(debt["due_date"])
    early = _integer(_need(config, "prepare_days_before"), "payment preparation window")
    _check(early <= 90, "Payment preparation window exceeds the supported contract")
    _check(
        type(config.get("auto_execute")) is bool, "Payment automatic consent must be a strict bool"
    )
    return bool(config["auto_execute"] and due - timedelta(days=early) <= local <= due)


def _settled(bundle: FinanceBundle, record: dict[str, Any]) -> dict[str, Any]:
    before = bundle.facts(_need(record, "before_facts_ref"))
    after = bundle.facts(_need(record, "after_facts_ref"))
    action = _find(after, "action_plans", _identity(_need(record, "action_id"), "actual action"))
    effect, digest = _effect(action, after["user_id"])
    operations = [
        row
        for row in _rows(after, "bank_operations").values()
        if row.get("action_plan_id") == action["id"]
    ]
    if len(operations) != 1:
        raise Missing("Original unique actual bank operation is missing")
    bank = operations[0]
    _check(
        bank.get("request") == action["request"]["execution"]
        and digest_value(bank["request"]) == bank.get("request_hash")
        and bank.get("id") == effect["operation_id"]
        and bank.get("idempotency_key") == action.get("idempotency_key")
        and bank.get("operation_type") == effect["action_type"]
        and bank.get("business_key") == effect["business_key"]
        and bank.get("amount_cents") == effect["amount_cents"],
        "Original bank/effect identity differs",
    )
    if bank.get("status") != "SETTLED" or bank.get("settled_at") is None:
        raise Missing("Actual settlement is not observed; UNKNOWN/ACCEPTED is not execution proof")
    at = _time(_need(bank, "requested_at"), "actual acceptance time")
    settled_at = _time(bank["settled_at"], "actual settlement time")
    _check(
        _time(_need(effect, "valid_from"), "effect start")
        <= at
        < _time(_need(effect, "expires_at"), "effect end")
        and at
        <= _time(_need(bank, "available_at"), "bank available")
        <= settled_at
        <= _time(after["as_of"], "after facts time"),
        "Original actual bank clocks differ",
    )
    old = _rows(before, "simulated_bank_postings")
    current = _rows(after, "simulated_bank_postings")
    _check(
        all(
            identity in current and digest_value(row) == digest_value(current[identity])
            for identity, row in old.items()
        ),
        "Original prior ledger rows were changed or lost",
    )
    replay = ledger_continuity(list(current.values()), after["user_id"])
    if replay["status"] != "VERIFIED":
        raise Missing("Independent complete ledger replay: " + str(replay["missing_reason"]))
    legs = [
        row
        for row in current.values()
        if row.get("operation_id") == bank["id"] and row.get("entry_kind") != "OPENING"
    ]
    if not legs:
        raise Missing("Actual bank operation has no original economic legs")
    economic = [row for row in legs if row.get("ledger_dimension") == "ECONOMIC"]
    _check(
        sum(_integer(row["delta_cents"], "economic delta", signed=True) for row in economic) == 0,
        "Original action economic legs do not conserve",
    )
    expected_legs: dict[str, int] = {}
    for use in effect["cash_uses"]:
        key = "CASH:" + _identity(use["account_id"], "cash source")
        _check(key not in expected_legs, "Original cash funding repeats a source")
        expected_legs[key] = -_integer(use["amount_cents"], "original cash use")
    if effect["destination_account_id"] is not None:
        key = "CASH:" + _identity(effect["destination_account_id"], "original cash destination")
        expected_legs[key] = expected_legs.get(key, 0) + (
            _integer(effect["net_cents"], "actual redemption net")
            if effect["action_type"] == "REDEEM_ASSET"
            else effect["amount_cents"]
        )
    if effect["action_type"] in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
        expected_legs["POSITION:" + _identity(effect["position_id"], "position identity")] = (
            effect["amount_cents"]
            if effect["action_type"] == "PURCHASE_ASSET"
            else -effect["amount_cents"]
        )
    if effect["action_type"] == "PAY_RECURRING":
        expected_legs[
            "PAYEE:"
            + str(uuid5(UUID(after["user_id"]), _text(effect["payee_id"], "payee identity")))
        ] = effect["amount_cents"]
    for label, field in (("FEE", "fee_cents"), ("LOSS", "loss_cents")):
        cents = _integer(effect[field], field)
        if cents:
            expected_legs[label + ":" + after["user_id"]] = cents
    expected_legs = {key: amount for key, amount in expected_legs.items() if amount != 0}
    _check(
        bool(expected_legs) or effect["action_type"] == "ALLOCATE_GOAL",
        "Only owned same-account goal allocation may have zero net economic movement",
    )
    actual_legs = {
        row["ledger_key"]: _integer(row["delta_cents"], "original leg delta", signed=True)
        for row in economic
    }
    _check(
        len(actual_legs) == len(economic) and actual_legs == expected_legs,
        "Actual conserved economic legs differ from full original effect identities/amounts",
    )
    expected_virtual: dict[tuple[str, str], int] = {}
    if effect["liability"] is not None:
        debt = _liability_basis(before, effect, at)
        liability_id = str(uuid5(UUID(after["user_id"]), "liability:" + effect["business_key"]))
        for label, delta in (
            ("LIABILITY_DUE", -effect["amount_cents"]),
            ("LIABILITY_PAID", effect["amount_cents"]),
        ):
            expected_virtual[("LIABILITY", label + ":" + liability_id)] = delta
        actual_debt = [row for row in legs if row.get("ledger_dimension") == "LIABILITY"]
        expected_metadata = {
            "business_key": effect["business_key"],
            "total_cents": debt["total_cents"],
            "liability": effect["liability"],
        }
        _check(
            len(actual_debt) == 2
            and all(row.get("ledger_metadata") == expected_metadata for row in actual_debt)
            and {
                row["ledger_key"].split(":", 1)[0]: row["balance_before_cents"]
                for row in actual_debt
            }
            == {
                "LIABILITY_DUE": debt["total_cents"] - debt["paid_cents"],
                "LIABILITY_PAID": debt["paid_cents"],
            },
            "Actual liability legs do not bind the exact prior original debt",
        )
    if effect["goal_id"] is not None:
        goal_cash = (
            effect["amount_cents"]
            if effect["action_type"] == "ALLOCATE_GOAL"
            else -effect["amount_cents"]
            if effect["action_type"] == "PURCHASE_ASSET"
            else effect["net_cents"]
        )
        principal = (
            effect["amount_cents"]
            if effect["action_type"] == "PURCHASE_ASSET"
            else -effect["amount_cents"]
            if effect["action_type"] == "REDEEM_ASSET"
            else 0
        )
        for label, cents in (
            ("GOAL_CASH", goal_cash),
            ("GOAL_PRINCIPAL", principal),
            ("GOAL_LOSS", effect["fee_cents"] + effect["loss_cents"]),
        ):
            if cents:
                expected_virtual[("GOAL_OWNERSHIP", label + ":" + effect["goal_id"])] = cents
    for use in effect["income_uses"]:
        origin = _identity(use["origin_transaction_id"], "original income origin")
        account = _identity(use["account_id"], "original income account")
        fragment = str(uuid5(UUID(origin), "income-location:" + account))
        _check(use["fragment_id"] == fragment, "Original income location identity differs")
        cents = _integer(use["amount_cents"], "income use")
        expected_virtual[("INCOME_LOCATION", "LOT_AVAILABLE:" + fragment)] = -cents
        target = (
            str(uuid5(UUID(origin), "income-location:" + effect["destination_account_id"]))
            if effect["action_type"] == "TRANSFER_INTERNAL"
            else fragment
        )
        bucket = (
            "AVAILABLE"
            if effect["action_type"] == "TRANSFER_INTERNAL"
            else "ASSIGNED"
            if effect["action_type"] == "ALLOCATE_GOAL"
            else "SPENT"
        )
        expected_virtual[("INCOME_LOCATION", "LOT_" + bucket + ":" + target)] = cents
    virtual = [row for row in legs if row["ledger_dimension"] != "ECONOMIC"]
    actual_virtual = {
        (row["ledger_dimension"], row["ledger_key"]): row["delta_cents"] for row in virtual
    }
    _check(
        len(actual_virtual) == len(virtual) and actual_virtual == expected_virtual,
        "Actual ownership/income legs differ from complete original effect",
    )
    _check(
        all(
            row["id"] == str(uuid5(UUID(bank["id"]), "posting:" + row["leg_ref"]))
            and _time(row["occurred_at"], "posting time") == settled_at
            for row in legs
        ),
        "Original operation posting identity/settlement clock differs",
    )
    receipts = [
        row
        for row in _rows(after, "action_receipts").values()
        if row.get("action_plan_id") == action["id"] and row.get("status") == "SUCCEEDED"
    ]
    if len(receipts) != 1:
        raise Missing("Unique actual successful original receipt is missing")
    receipt = receipts[0]
    response = _object(_need(receipt, "response"), "original bank receipt response")
    _check(
        response.get("bank_operation_id") == bank["id"]
        and set(_unique_ids(_need(response, "posting_ids"), "receipt posting ids"))
        == {row["id"] for row in legs}
        and _integer(_need(receipt, "executed_cents"), "executed cents") == effect["amount_cents"]
        and _integer(_need(receipt, "fee_cents"), "actual fee") == effect["fee_cents"]
        and _integer(_need(receipt, "loss_cents"), "actual loss") == effect["loss_cents"]
        and settled_at
        <= _time(_need(receipt, "occurred_at"), "receipt time")
        <= _time(after["as_of"], "after capture"),
        "Original receipt/settlement amounts or posting set differ",
    )
    return {
        "before": before,
        "after": after,
        "action": action,
        "effect": effect,
        "effect_hash": digest,
        "bank": bank,
        "receipt": receipt,
        "legs": legs,
        "at": at,
    }


def _timeline(bundle: FinanceBundle, facts_ref: Any, checkpoint: dict[str, Any]) -> dict[str, Any]:
    from scripts.mvp_financial_oracles import compute_timeline

    actual = compute_timeline(copy.deepcopy(bundle.facts(facts_ref)), [copy.deepcopy(checkpoint)])
    _check(
        actual.get("protocol") == "mvp-independent-financial-timeline-v1",
        "Unsupported independent timeline protocol",
    )
    rows = _list(_need(actual, "checkpoints"), "independent checkpoint results")
    if len(rows) != 1 or rows[0].get("status") != "MEASURED":
        raise Missing(
            "Independent checkpoint calculator lacks complete supported facts: " + str(rows)
        )
    row = _object(rows[0], "independent timeline result")
    _check(
        row.get("checkpoint_id") == checkpoint["checkpoint_id"]
        and row.get("kind") == checkpoint["kind"],
        "Independent checkpoint identity is rebound",
    )
    return row


def _unit(identity: str, operation: Any, refs: list[Any]) -> dict[str, Any]:
    try:
        return {
            "unit_id": identity,
            "status": "MEASURED",
            "missing_reason": None,
            "raw_refs": refs,
            **operation(),
        }
    except (
        Missing,
        ObservationError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        return {
            "unit_id": identity,
            "status": "MISSING",
            "value": None,
            "missing_reason": str(error),
            "raw_refs": refs,
        }


def _metric(
    metric: str,
    units: list[dict[str, Any]],
    *,
    mode: str,
    missing: str | None = None,
    run_status: str = "COMPLETE",
) -> dict[str, Any]:
    unknown = missing or next(
        (row["missing_reason"] for row in units if row["status"] != "MEASURED"), None
    )
    denominator = len(units)
    status = (
        "NOT_RUN"
        if run_status in {"NOT_RUN", "NOT_IMPLEMENTED"}
        else "MISSING"
        if unknown
        else "MEASURED"
        if denominator
        else "NOT_APPLICABLE"
    )
    numerator = (
        sum(row.get("value") is True for row in units) if mode in {"count", "ratio"} else None
    )
    if status == "NOT_RUN":
        numerator = None
    value = None
    if status == "MEASURED":
        value = (
            sum(row.get("value") is True for row in units) / denominator
            if mode == "ratio"
            else numerator
            if mode == "count"
            else [{"checkpoint_id": row["unit_id"], "cents": row["value"]} for row in units]
        )
    return {
        "name": NAMES[metric],
        "status": status,
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "applicable_units": [row["unit_id"] for row in units],
        "missing_reason": unknown,
        "raw_refs": [ref for row in units for ref in row["raw_refs"]],
        "units": units,
        "failures": [row for row in units if row["status"] != "MEASURED"],
    }


def _permission(actual: dict[str, Any]) -> dict[str, Any]:
    facts, action, effect, at = actual["before"], actual["action"], actual["effect"], actual["at"]
    for use in effect["cash_uses"]:
        account = _find(facts, "accounts", use["account_id"])
        _check(
            account.get("account_type") in {"CASH", "GOAL"},
            "Original cash funding is not an owned spendable account",
        )
    if effect["destination_account_id"] is not None:
        _find(facts, "accounts", effect["destination_account_id"])
    versions = _unique_ids(effect["policy_version_ids"], "original policy references")
    consent = _consent(actual["after"], action, effect, actual["effect_hash"], at)
    if effect["action_type"] == "TRANSFER_INTERNAL":
        _check(
            not versions and effect["goal_id"] is None and effect["policy_id"] is None,
            "Transfer cannot infer a policy or goal permission",
        )
        return {
            "authorized": consent,
            "automatic_permission": False,
            "exact_action_consent": consent,
        }
    if not versions or effect["policy_version_id"] not in versions:
        return {"authorized": False, "automatic_permission": False, "exact_action_consent": consent}
    configs = []
    for identity in versions:
        active, version = _current_version(facts, identity, at)
        if not active:
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        configs.append(version["configuration"])
    selected = _find(facts, "policy_versions", effect["policy_version_id"])
    if selected["policy_id"] != effect["policy_id"]:
        return {"authorized": False, "automatic_permission": False, "exact_action_consent": consent}
    config = selected["configuration"]
    kind = effect["action_type"]
    automatic = False
    if kind in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
        if config.get("type") != "asset_authorization":
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        _check(
            config.get("scope") in {"general_idle_funds", "goal"},
            "Unsupported original asset authority scope",
        )
        expected_goal = config.get("goal_id") if config["scope"] == "goal" else None
        if expected_goal != effect["goal_id"]:
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        if expected_goal is not None:
            from scripts.mvp_financial_oracles import Facts, _cash, _goals, _versions

            owned = Facts(facts)
            _check(owned.as_of == at, "Goal asset authority needs actual same-time originals")
            _, heads, _ = _cash(owned, at)
            _, _, goal_components = _goals(owned, heads, _versions(owned, at), at, at)
            goal = _find(facts, "goals", expected_goal)
            goal_version = _find(facts, "policy_versions", goal["policy_version_id"])
            _check(
                goal_version["id"] in versions
                and goal_version["configuration"].get("asset_policy_id") == effect["policy_id"],
                "Exact owned goal does not bind this asset authority",
            )
            if kind == "PURCHASE_ASSET":
                cash_owned = next(
                    row["cash_owned_cents"]
                    for row in goal_components
                    if row.get("kind") == "GOAL" and row.get("id") == expected_goal
                )
                if (
                    effect["income_uses"]
                    or len(effect["cash_uses"]) != 1
                    or effect["cash_uses"][0]["account_id"] != goal["account_id"]
                    or effect["amount_cents"] > cash_owned
                ):
                    return {
                        "authorized": False,
                        "automatic_permission": False,
                        "exact_action_consent": consent,
                    }
            elif effect["destination_account_id"] != goal["account_id"]:
                return {
                    "authorized": False,
                    "automatic_permission": False,
                    "exact_action_consent": consent,
                }
        product = _find(facts, "asset_products", effect["product_id"])
        _check(
            product.get("version_number") == effect["product_version_number"]
            and digest_value(_object(_need(product, "maturity_rule"), "original product terms"))
            == effect["terms_digest"]
            and _time(_need(product, "effective_from"), "product start") <= at
            and (
                product.get("effective_until") is None
                or at < _time(product["effective_until"], "product end")
            ),
            "Original exact product version/terms/effective window differs",
        )
        allowed = _unique_ids(_need(config, "allowed_asset_classes"), "authorized asset classes")
        product_class = _text(_need(product, "asset_class"), "original asset class")
        if product_class.startswith("FIXED_DEPOSIT_") or product_class == "LOW_RISK_TERM":
            product_class = "FIXED_DEPOSIT"
        if product_class not in allowed or _integer(product["risk_level"], "risk") > _integer(
            config["max_principal_risk_level"], "risk cap"
        ):
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        if _integer(product["redemption_delay_days"], "delay") > _integer(
            config["max_redemption_delay_days"], "delay cap"
        ) or _integer(product["lock_days"], "lock") > _integer(config["max_lock_days"], "lock cap"):
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        if kind == "PURCHASE_ASSET":
            _check(
                effect["amount_cents"]
                >= _integer(product["minimum_purchase_cents"], "product minimum"),
                "Actual purchase is below original product minimum",
            )
            held = sum(
                _integer(row["principal_cents"], "held principal")
                for row in _rows(facts, "asset_positions").values()
                if row.get("goal_id") == effect["goal_id"]
                and row.get("status") in {"HELD", "REDEEMING", "UNKNOWN"}
            )
            automatic = bool(
                effect["amount_cents"] <= _integer(config["single_action_cap_cents"], "single cap")
                and held + effect["amount_cents"]
                <= _integer(config["max_auto_managed_cents"], "managed cap")
                and product.get("auto_purchase_allowed") is True
            )
            if expected_goal is None:
                automatic = automatic and _eligible_income_uses(actual, selected)
        else:
            position = _find(facts, "asset_positions", effect["position_id"])
            _check(
                position.get("product_id") == effect["product_id"]
                and position.get("account_id") == effect["position_account_id"]
                and position.get("goal_id") == effect["goal_id"]
                and position.get("policy_version_id") == effect["original_policy_version_id"]
                and position.get("principal_cents") == effect["amount_cents"],
                "Original position identity/owner/principal authority differs",
            )
            automatic = bool(
                config.get("allow_auto_recovery_without_penalty") is True
                and product.get("auto_redeem_allowed") is True
                and effect["fee_cents"] == effect["loss_cents"] == 0
            )
        if consent and not automatic:
            raise Missing(
                "Explicit asset action outside automatic limits needs an independently "
                "frozen manual-authority calculator"
            )
    elif kind == "PAY_RECURRING":
        if (
            config.get("type") != "recurring_obligation"
            or config.get("payee_id") != effect["payee_id"]
        ):
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        automatic = _payment_permission(actual, selected)
    elif kind == "ALLOCATE_GOAL":
        if config.get("type") != "goal_saving":
            return {
                "authorized": False,
                "automatic_permission": False,
                "exact_action_consent": consent,
            }
        automatic = _goal_allocation_permission(actual, selected)
        # Exact action consent cannot enlarge hard goal/source ownership limits.
        return {
            "authorized": automatic,
            "automatic_permission": automatic,
            "exact_action_consent": consent,
        }
    else:
        raise Missing("Independent authority calculator does not support this actual action type")
    authorized = (automatic or consent) and (
        effect["fee_cents"] + effect["loss_cents"] == 0 or consent
    )
    return {
        "authorized": authorized,
        "automatic_permission": automatic,
        "exact_action_consent": consent,
    }


def _quote(actual: dict[str, Any]) -> dict[str, Any]:
    effect, facts = actual["effect"], actual["before"]
    proof = _rows(facts, "evidence_items").get(_identity(effect["quote_id"], "original quote id"))
    if proof is None:
        raise Missing("Original redemption quote is missing")
    content = _object(_need(proof, "content"), "original quote content")
    _check(
        proof.get("source_type") == "SIMULATED_REDEMPTION_QUOTE"
        and proof.get("evidence_level") == "BANK_OBSERVED"
        and proof.get("status") == "VALID"
        and proof.get("source_ref") == effect["position_id"]
        and _time(_need(proof, "observed_at"), "original quote observation") <= actual["at"]
        and _time(_need(proof, "valid_from"), "original quote start") <= actual["at"]
        and (
            proof.get("valid_to") is None
            or actual["at"] < _time(proof["valid_to"], "original quote proof expiry")
        )
        and digest_value(content) == proof.get("content_hash"),
        "Original quote/source/content hash is invalid",
    )
    for field, effect_field in (
        ("quote_id", "quote_id"),
        ("user_id", "user_id"),
        ("position_id", "position_id"),
        ("product_id", "product_id"),
        ("product_version_number", "product_version_number"),
        ("terms_digest", "terms_digest"),
        ("principal_cents", "amount_cents"),
        ("fee_cents", "fee_cents"),
        ("loss_cents", "loss_cents"),
        ("net_cents", "net_cents"),
    ):
        _check(
            content.get(field) == effect[effect_field],
            "Actual original quote/effect field differs: " + field,
        )
    _check(
        _time(_need(content, "request_at"), "quote request")
        <= actual["at"]
        < _time(_need(content, "expires_at"), "quote expiry")
        and _time(_need(content, "principal_available_at"), "quoted availability")
        == _time(actual["bank"]["available_at"], "bank availability"),
        "Original quote acceptance/availability clock differs",
    )
    return content


def _protection(
    bundle: FinanceBundle, record: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any]:
    actual = _settled(bundle, record)
    point = _object(_need(expected, "checkpoint"), "frozen protection point")
    before = _timeline(bundle, record["before_facts_ref"], point)
    after = _timeline(bundle, record["after_facts_ref"], point)
    old = _integer(_need(before, "available_cash_cents"), "before available") - _integer(
        _need(before, "protected_required_cents"), "before required"
    )
    new = _integer(_need(after, "available_cash_cents"), "after available") - _integer(
        _need(after, "protected_required_cents"), "after required"
    )
    old_rows = _rows(actual["before"], "simulated_bank_postings")
    added = [
        row
        for identity, row in _rows(actual["after"], "simulated_bank_postings").items()
        if identity not in old_rows and row.get("entry_kind") != "OPENING"
    ]
    confounded = any(row.get("operation_id") != actual["bank"]["id"] for row in added)
    if confounded:
        raise Missing(
            "Actual action interval includes independent/exogenous effects; "
            "no isolated causal replay yet"
        )
    return {
        "value": new < 0 and new < old,
        "before_margin_cents": old,
        "after_margin_cents": new,
        "cause_class": "SYSTEM_ACTION" if new < old else "NOT_CREATED_BY_ACTION",
        "timeline": [before, after],
    }


def _due(bundle: FinanceBundle, record: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    point = _timeline(
        bundle,
        _need(record, "facts_ref"),
        _object(_need(expected, "checkpoint"), "frozen due point"),
    )
    available = _integer(_need(point, "available_cash_cents"), "available due cash")
    required = _integer(_need(point, "protected_required_cents"), "required due cash")
    return {
        "value": available < required,
        "available_cash_cents": available,
        "required_cash_cents": required,
        "cause_class": "NO_SHORTFALL" if available >= required else "UNKNOWN",
        "cause_status": "NOT_APPLICABLE" if available >= required else "MISSING",
        "cause_missing_reason": None
        if available >= required
        else "Counterfactual causal attribution is not implemented; "
        "producer cause labels are not oracle",
        "timeline": point,
    }


def _deploy(
    bundle: FinanceBundle,
    record: dict[str, Any],
    expected: dict[str, Any],
    actions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    checkpoint = _object(_need(expected, "checkpoint"), "frozen deployment point")
    policy_id = _identity(_need(checkpoint, "policy_id"), "frozen deployment policy")
    product_id = _identity(_need(checkpoint, "product_id"), "frozen deployment product")
    point = _timeline(
        bundle,
        _need(record, "facts_ref"),
        checkpoint,
    )
    safe = _integer(
        _need(point, "safe_authorized_deployable_cents"), "independent deployable cents"
    )
    identities = _unique_ids(
        _need(record, "qualifying_action_ids"), "original qualifying deployments"
    )
    basis = bundle.facts(record["facts_ref"])
    basis_operations = {
        row.get("action_plan_id")
        for row in _rows(basis, "bank_operations").values()
        if row.get("status") == "SETTLED"
    }
    _check(
        not (set(identities) & basis_operations),
        "Deployment basis already includes deployed cash; subtracting twice is not a valid oracle",
    )
    deployed = 0
    independently_qualified: set[str] = set()
    for identity, observed in actions.items():
        facts = bundle.facts(_need(observed, "after_facts_ref"))
        action = _find(facts, "action_plans", identity)
        if identity in basis_operations:
            continue
        original, _ = _effect(action, facts["user_id"])
        if original["action_type"] != "PURCHASE_ASSET":
            continue
        actual = _settled(bundle, observed)
        if (
            original["policy_id"] == policy_id
            and original["product_id"] == product_id
            and original["goal_id"] == point.get("goal_id")
        ) and _time(actual["bank"]["settled_at"], "actual candidate deployment") <= _time(
            point["at"], "deployment point"
        ):
            if _permission(actual)["authorized"]:
                independently_qualified.add(identity)
    _check(
        set(identities) == independently_qualified,
        "Original qualifying deployment inventory differs from actual independent actions",
    )
    for identity in identities:
        if identity not in actions:
            raise Missing("Qualifying deployed action has no actual original observation")
        actual = _settled(bundle, actions[identity])
        _check(
            actual["effect"]["action_type"] == "PURCHASE_ASSET"
            and actual["effect"]["policy_id"] == policy_id
            and actual["effect"]["product_id"] == product_id
            and actual["effect"]["goal_id"] == point.get("goal_id")
            and _time(actual["bank"]["settled_at"], "deployment settlement")
            <= _time(point["at"], "deployment checkpoint"),
            "Actual deployment is another policy/product/scope/type/time",
        )
        authority = _permission(actual)
        _check(
            authority["authorized"] is True,
            "Actual deployed action lacks independently checked exact authority",
        )
        deployed += _integer(actual["effect"]["amount_cents"], "deployed principal")
    return {
        "value": max(0, safe - deployed),
        "safe_authorized_deployable_cents": safe,
        "actual_deployed_cents": deployed,
        "scope_revision": "E4_POLICY_PRODUCT_SCOPE_V2",
        "policy_id": policy_id,
        "product_id": product_id,
        "timeline": point,
    }


def _automatic(
    bundle: FinanceBundle,
    expected: dict[str, Any],
    record: dict[str, Any],
    actors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actual = _settled(bundle, record)
    permission = _permission(actual)
    _check(
        permission["authorized"] is True, "Actual auto action has no independent exact permission"
    )
    kind = actual["effect"]["action_type"]
    if kind == "PURCHASE_ASSET":
        protection = _protection(bundle, record, expected)
        _check(
            protection["value"] is False and protection["after_margin_cents"] >= 0,
            "Actual purchase leaves a protected shortfall",
        )
        for point in protection["timeline"]:
            margins = [
                row["margin_cents"]
                for component in point["components"]
                if component.get("kind") == "PROTECTION_TIMELINE"
                for row in component["margins"]
            ]
            _check(
                bool(margins) and min(margins) >= 0,
                "Actual purchase lacks a safe full protected horizon",
            )
    elif kind == "REDEEM_ASSET":
        _quote(actual)
        _check(
            actual["effect"]["fee_cents"] == actual["effect"]["loss_cents"] == 0,
            "Positive-cost redemption is not a safe-auto opportunity",
        )
        protection = _protection(bundle, record, expected)
        _check(protection["value"] is False, "Actual original recovery reduces protected funds")
    else:
        raise Missing("Independent safe-auto classifier not implemented for this financial family")
    interventions = []
    for event in actors.values():
        _check(
            event.get("phase") in {"INITIAL_AUTHORIZATION", "RUNTIME_INTERVENTION"}
            and event.get("event_type")
            in {"MANUAL_CHOICE", "CLARIFICATION", "AFFIRMATIVE_CONFIRMATION"}
            and _time(_need(event, "occurred_at"), "original actor clock")
            <= _time(actual["after"]["as_of"], "actual actor capture"),
            "Original actor phase/type/clock is unsupported",
        )
        if event.get("phase") == "RUNTIME_INTERVENTION" and event.get("event_type") in {
            "MANUAL_CHOICE",
            "CLARIFICATION",
            "AFFIRMATIVE_CONFIRMATION",
        }:
            if event.get("action_id") is None and event.get("opportunity_id") is None:
                raise Missing("Original runtime actor event has no action/opportunity binding")
            if event.get("action_id") == actual["action"]["id"] or event.get(
                "opportunity_id"
            ) == record.get("opportunity_id"):
                interventions.append(event["event_id"])
    _check(
        actual["action"].get("status") in {"SUCCEEDED", "RECONCILED"},
        "Actual application projection is incomplete",
    )
    _application_projection(actual)
    return {
        "value": permission["automatic_permission"]
        and not interventions
        and not permission["exact_action_consent"],
        "actor_intervention_ids": interventions,
        "settlement_receipt_id": actual["receipt"]["id"],
    }


def _application_projection(actual: dict[str, Any]) -> None:
    """Actual application rows must agree with the complete independent bank heads."""
    heads = ledger_continuity(
        list(_rows(actual["after"], "simulated_bank_postings").values()),
        actual["after"]["user_id"],
    )["heads"]
    for row in _rows(actual["after"], "accounts").values():
        head = heads.get("CASH:" + row["id"])
        if row.get("account_type") in {"CASH", "GOAL"}:
            _check(
                head is not None
                and head["balance_cents"] == _integer(row["balance_cents"], "projected cash"),
                "Actual application cash projection differs from independent ledger",
            )
    for row in _rows(actual["after"], "asset_positions").values():
        head = heads.get("POSITION:" + row["id"])
        expected_principal = (
            _integer(row["principal_cents"], "projected principal")
            if row["status"] in {"HELD", "REDEEMING", "UNKNOWN", "MATURED"}
            else 0
        )
        _check(
            head is not None and head["balance_cents"] == expected_principal,
            "Actual position projection differs from independent principal ledger",
        )
    if actual["effect"]["goal_id"] is not None or actual["effect"]["income_uses"]:
        from scripts.mvp_financial_oracles import Facts, _cash, _goals, _income, _versions

        facts = Facts(actual["after"])
        _, raw_heads, _ = _cash(facts, facts.as_of)
        _goals(facts, raw_heads, _versions(facts, facts.as_of), facts.as_of, facts.as_of)
        if actual["effect"]["income_uses"]:
            _income(facts, raw_heads, facts.as_of, {"start": actual["at"]})
    if actual["effect"]["action_type"] == "PURCHASE_ASSET":
        at = _time(actual["after"]["as_of"], "actual final projection")
        for leg in actual["legs"]:
            if leg.get("ledger_dimension") != "ECONOMIC" or not leg["ledger_key"].startswith(
                "CASH:"
            ):
                continue
            candidates = [
                row
                for row in _rows(actual["after"], "evidence_items").values()
                if isinstance(row.get("content"), dict)
                and row["content"].get("bank_operation_id") == actual["bank"]["id"]
                and row["content"].get("bank_posting_id") == leg["id"]
                and row.get("source_type") == "SIMULATED_BANK_TRANSACTION"
            ]
            _check(len(candidates) == 1, "Actual purchase cash leg has no unique projection source")
            proof = _bank_fact(
                actual["after"], candidates[0]["id"], at, "SIMULATED_BANK_TRANSACTION"
            )
            transaction = _find(actual["after"], "transactions", proof["transaction_id"])
            _check(
                transaction.get("evidence_id") == candidates[0]["id"]
                and proof.get("economic_role") == "ASSET_PURCHASE"
                and proof.get("account_id") == transaction.get("account_id") == leg["account_id"]
                and proof.get("direction") == transaction.get("direction") == "DEBIT"
                and proof.get("amount_cents")
                == transaction.get("amount_cents")
                == -leg["delta_cents"]
                and proof.get("balance_after_cents")
                == transaction.get("balance_after_cents")
                == leg["balance_after_cents"]
                and _time(proof["occurred_at"], "purchase bank transaction")
                == _time(transaction["occurred_at"], "purchase projected transaction")
                == _time(leg["occurred_at"], "purchase cash settlement"),
                "Actual purchase transaction projection differs from the original bank leg",
            )
        position = _find(actual["after"], "asset_positions", actual["effect"]["position_id"])
        _check(
            position.get("product_id") == actual["effect"]["product_id"]
            and position.get("account_id") == actual["effect"]["position_account_id"]
            and position.get("goal_id") == actual["effect"]["goal_id"]
            and position.get("policy_version_id") == actual["effect"]["policy_version_id"]
            and position.get("principal_cents") == actual["effect"]["amount_cents"]
            and position.get("status") == "HELD"
            and _time(position["purchased_at"], "actual projected purchase")
            == _time(actual["bank"]["settled_at"], "actual bank purchase"),
            "Actual purchased position projection is rebound",
        )
        _check(
            not any(
                row.get("action_plan_id") == actual["action"]["id"]
                and row.get("status") == "RESERVED"
                for row in _rows(actual["after"], "action_resource_reservations").values()
            ),
            "Settled purchase retains an active application resource claim",
        )


def _version_consumption(bundle: FinanceBundle, record: dict[str, Any]) -> dict[str, Any]:
    facts = bundle.facts(_need(record, "facts_ref"))
    kind = _text(_need(record, "consuming_kind"), "actual version consumer kind")
    if kind == "ACTION":
        bank = _find(
            facts,
            "bank_operations",
            _identity(_need(record, "bank_operation_id"), "consuming bank"),
        )
        action = _find(facts, "action_plans", bank["action_plan_id"])
        effect, _ = _effect(action, facts["user_id"])
        if bank.get("status") == "REJECTED":
            raise Missing("Rejected stale attempt is not an actual version-consuming action")
        at = _time(_need(bank, "requested_at"), "original acceptance clock")
        versions = _unique_ids(effect["policy_version_ids"], "actual consumed versions")
    elif kind == "DECISION_ADOPTION":
        decision = bundle.row(
            _need(record, "decision_ref"),
            "decision_runs",
            _identity(_need(record, "decision_run_id"), "consuming decision"),
        )
        _check(
            digest_value(decision["input_snapshot"]) == decision.get("snapshot_hash"),
            "Original consuming decision hash differs",
        )
        adopted = [
            row
            for row in _rows(facts, "action_plans").values()
            if row.get("decision_run_id") == decision["id"]
            and any(
                bank.get("action_plan_id") == row["id"] and bank.get("status") != "REJECTED"
                for bank in _rows(facts, "bank_operations").values()
            )
        ]
        if not adopted:
            raise Missing(
                "Decision version adoption has no actual original adopted action; "
                "validation-only not a violation"
            )
        at = _time(_need(decision, "as_of"), "decision consuming clock")
        versions = _unique_ids(_need(decision, "policy_version_ids"), "decision consumed versions")
    else:
        raise Missing("Unsupported actual version-consuming record; refused attempts do not count")
    _check(
        _time(_need(record, "consuming_at"), "declared consuming time") == at
        and _unique_ids(_need(record, "version_ids"), "consumption version ids") == versions,
        "Observed actual version/time is rebound",
    )
    if not versions:
        raise Missing("Registered version consumer has no actual consumed version")
    stale = [identity for identity in versions if not _current_version(facts, identity, at)[0]]
    return {"value": bool(stale), "stale_version_ids": stale, "actual_consuming_at": at.isoformat()}


def _ask(
    bundle: FinanceBundle, event: dict[str, Any], expected: dict[str, Any] | None
) -> dict[str, Any]:
    if expected is None:
        raise Missing("Actual ask event has no frozen independent confirmation-required map")
    _check(
        event.get("opportunity_id") == expected.get("opportunity_id"),
        "Ask/opportunity identity is rebound",
    )
    _check(
        type(expected.get("confirmation_required")) is bool,
        "Independent exact confirmation requirement must be boolean",
    )
    _text(_need(expected, "cause_code"), "independent ask cause")
    basis = _object(_need(expected, "basis_ref"), "frozen exact confirmation requirement basis")
    _check(
        basis.get("registration") in {"input", "oracle", "design", "rule"},
        "Ask requirement must bind independent original registration",
    )
    original = bundle.registrations[basis["registration"]]
    pointer = _text(_need(basis, "json_pointer"), "frozen requirement pointer")
    _check(pointer.startswith("/"), "Frozen basis pointer is invalid")
    value: Any = original
    for key in pointer.split("/")[1:]:
        value = (
            value[int(key)]
            if isinstance(value, list)
            else value[key.replace("~1", "/").replace("~0", "~")]
        )
    _check(
        digest_value(value) == _digest(_need(basis, "value_sha256"), "frozen ask requirement value")
        and isinstance(value, dict)
        and value.get("confirmation_required") is expected["confirmation_required"]
        and value.get("cause_code") == expected["cause_code"],
        "Ask required map does not match independently frozen original cause",
    )
    question = _object(bundle.resolve(_need(event, "question_ref")), "original actual question")
    _check(
        question.get("ask_event_id") == event["ask_event_id"]
        and question.get("opportunity_id") == event["opportunity_id"]
        and _time(_need(question, "occurred_at"), "original question time")
        == _time(_need(event, "occurred_at"), "ask event time"),
        "Original actual question identity/time differs",
    )
    _text(_need(question, "question_text"), "actual original question text")
    return {"value": not expected["confirmation_required"], "cause_code": expected["cause_code"]}


def observe(manifest_path: Path) -> dict[str, Any]:
    bundle = FinanceBundle(manifest_path)
    action_records = bundle.rows("FINANCIAL_OBSERVATIONS", "actions", "action_id")
    opportunities = bundle.rows("FINANCIAL_OBSERVATIONS", "opportunities", "opportunity_id")
    points = bundle.rows("FINANCIAL_OBSERVATIONS", "checkpoints", "checkpoint_id")
    consumptions = bundle.rows("FINANCIAL_OBSERVATIONS", "version_consumptions", "consumption_id")
    asks = bundle.rows("ASK_LOG", "events", "ask_event_id")
    actors = bundle.rows("ACTOR_LOG", "events", "event_id")
    registration = bundle.registration
    final_raw = bundle.present("FINANCIAL_OBSERVATIONS")
    inventory_missing = None
    actor_missing = None
    ask_missing = None
    consumption_missing = None
    actual_consuming_banks: set[str] = set()
    actual_consuming_decisions: set[str] = set()
    final: dict[str, Any] | None = None
    actual_ids = sorted(action_records)
    try:
        _check(len(final_raw) == 1, "One complete original financial run inventory is required")
        payload = final_raw[0]["payload"]
        final = bundle.facts(_need(payload, "final_facts_ref"))
        complete = _object(_need(payload, "capture"), "actual complete financial capture")
        _check(
            complete.get("complete") is True and complete.get("run_ref") == bundle.bindings,
            "Financial original capture is incomplete/rebound",
        )
        effect_ids = {
            row.get("operation_id")
            for row in _rows(final, "simulated_bank_postings").values()
            if row.get("entry_kind") != "OPENING" and row.get("operation_id") is not None
        }
        actual_ids = sorted(
            {
                _identity(row["action_plan_id"], "actual bank action")
                for row in _rows(final, "bank_operations").values()
                if row.get("status") == "SETTLED" or row["id"] in effect_ids
            }
        )
        _check(
            set(_unique_ids(_need(complete, "action_ids"), "actual captured action ids"))
            == set(actual_ids),
            "Original actual economic-action inventory is incomplete",
        )
        _check(
            set(action_records) <= set(actual_ids),
            "Financial trace reports a proposal as actual execution",
        )
        for bank in _rows(final, "bank_operations").values():
            if bank.get("status") == "REJECTED":
                continue
            action = _find(final, "action_plans", bank["action_plan_id"])
            effect, _ = _effect(action, final["user_id"])
            if effect["policy_version_ids"]:
                actual_consuming_banks.add(bank["id"])
                if action.get("decision_run_id") is not None:
                    actual_consuming_decisions.add(
                        _identity(action["decision_run_id"], "actual adopted decision")
                    )
        _check(
            set(
                _unique_ids(
                    _need(complete, "version_bank_operation_ids"), "actual consumed bank inventory"
                )
            )
            == actual_consuming_banks,
            "Original actual version-consuming bank inventory is incomplete",
        )
        _check(
            set(
                _unique_ids(
                    _need(complete, "version_decision_run_ids"), "actual adopted decision inventory"
                )
            )
            == actual_consuming_decisions,
            "Original actual adopted decision inventory is incomplete",
        )
    except (ObservationError, OSError, ValueError, KeyError) as error:
        inventory_missing = str(error)
        consumption_missing = str(error)
    try:
        actor_raw = bundle.present("ACTOR_LOG")
        _check(len(actor_raw) == 1, "One complete actual actor log is required")
        _check(
            actor_raw[0]["payload"].get("capture_status") == "COMPLETE",
            "Actual actor log capture is incomplete/rebound",
        )
        _check(
            set(
                _unique_ids(
                    _need(actor_raw[0]["payload"], "event_manifest"), "original actor manifest"
                )
            )
            == set(actors),
            "Actual actor originals are incomplete",
        )
    except (ObservationError, KeyError) as error:
        actor_missing = str(error)
    try:
        ask_raw = bundle.present("ASK_LOG")
        _check(len(ask_raw) == 1, "One complete actual question log is required")
        _check(
            ask_raw[0]["payload"].get("capture_status") == "COMPLETE"
            and set(
                _unique_ids(
                    _need(ask_raw[0]["payload"], "event_manifest"), "actual question inventory"
                )
            )
            == set(asks),
            "Actual question originals are incomplete/rebound",
        )
    except (ObservationError, KeyError) as error:
        ask_missing = str(error)
    protection = {
        _text(_need(row, "opportunity_id"), "protection opportunity"): row
        for row in [
            _object(row, "protection registration")
            for row in registration["protection_checkpoints"]
        ]
    }
    units: dict[str, list[dict[str, Any]]] = {metric: [] for metric in NAMES}
    for identity in actual_ids:
        record = action_records.get(identity)
        refs = (
            []
            if record is None
            else [
                record["_original_ref"],
                record.get("before_facts_ref"),
                record.get("after_facts_ref"),
            ]
        )

        def actual_record(record: dict[str, Any] | None = record) -> dict[str, Any]:
            if record is None:
                raise Missing("Actual logical economic action has no complete original observation")
            return record

        def protection_operation() -> dict[str, Any]:
            row = actual_record()
            expected = protection.get(_text(_need(row, "opportunity_id"), "actual opportunity"))
            if expected is None:
                raise Missing(
                    "Actual economic action lacks independently frozen protection checkpoint"
                )
            return _protection(bundle, row, expected)

        units["S1"].append(_unit(identity, protection_operation, refs))
        units["S2"].append(
            _unit(
                identity,
                lambda: {"value": not _permission(_settled(bundle, actual_record()))["authorized"]},
                refs,
            )
        )
        try:
            row = actual_record()
            raw_action = _find(bundle.facts(row["after_facts_ref"]), "action_plans", identity)
            redemption = raw_action.get("action_type") in {
                "ASSET_REDEEM",
                "REDEEM_ASSET",
                "ASSET_MATURITY",
            }
        except (ObservationError, ValueError, KeyError):
            redemption = True  # Unknown actual kind cannot disappear from fee/loss coverage.
        if redemption:

            def cost_operation() -> dict[str, Any]:
                actual = _settled(bundle, actual_record())
                _check(
                    actual["effect"]["action_type"] == "REDEEM_ASSET",
                    "Actual redemption kind is unsupported",
                )
                _quote(actual)
                cost = _integer(actual["receipt"]["fee_cents"], "actual fee") + _integer(
                    actual["receipt"]["loss_cents"], "actual loss"
                )
                consent = _consent(
                    actual["after"],
                    actual["action"],
                    actual["effect"],
                    actual["effect_hash"],
                    actual["at"],
                )
                return {
                    "value": cost > 0 and not consent,
                    "fee_loss_cents": cost,
                    "exact_original_consent": consent,
                }

            units["S4"].append(_unit(identity, cost_operation, refs))
    for expected in registration["due_checkpoints"]:
        identity = expected["checkpoint_id"]

        def due_operation(
            expected: dict[str, Any] = expected, identity: str = identity
        ) -> dict[str, Any]:
            if identity not in points:
                raise Missing("Predeclared due checkpoint original is missing")
            return _due(bundle, points[identity], expected)

        units["S3"].append(
            _unit(
                identity,
                due_operation,
                [] if identity not in points else [points[identity]["_original_ref"]],
            )
        )
    recorded_banks = {
        record.get("bank_operation_id")
        for record in consumptions.values()
        if record.get("consuming_kind") == "ACTION"
    }
    actual_recorded_banks = [
        row["bank_operation_id"]
        for row in consumptions.values()
        if row.get("consuming_kind") == "ACTION"
    ]
    if len(actual_recorded_banks) != len(set(actual_recorded_banks)):
        consumption_missing = "One actual bank consumer has duplicate original consumption units"
    recorded_decisions = {
        row.get("decision_run_id")
        for row in consumptions.values()
        if row.get("consuming_kind") == "DECISION_ADOPTION"
    }
    for decision_id in sorted(actual_consuming_decisions - recorded_decisions):
        units["S5"].append(
            _unit(
                "DECISION:" + decision_id,
                lambda: (_ for _ in ()).throw(
                    Missing("Actual adopted decision original is missing")
                ),
                [],
            )
        )
    for bank_id in sorted(actual_consuming_banks - recorded_banks):
        identity = "ACTION:" + bank_id
        units["S5"].append(
            _unit(
                identity,
                lambda: (_ for _ in ()).throw(
                    Missing("Actual version-consuming bank has no original consumption observation")
                ),
                [],
            )
        )
    refused_attempts = []
    for identity in sorted(set(registration["version_consumption_ids"]) | set(consumptions)):
        record = consumptions.get(identity)
        if record is not None and record.get("consuming_kind") == "ACTION" and final is not None:
            refused_bank = _rows(final, "bank_operations").get(
                _identity(_need(record, "bank_operation_id"), "version consumer bank")
            )
            if refused_bank is not None and refused_bank.get("status") == "REJECTED":
                refused_attempts.append(
                    {
                        "consumption_id": identity,
                        "bank_operation_id": refused_bank["id"],
                        "raw_ref": record["_original_ref"],
                    }
                )
                continue

        def consumption_operation(identity: str = identity) -> dict[str, Any]:
            if identity not in consumptions:
                raise Missing("Predeclared original version consumption is missing")
            return _version_consumption(bundle, consumptions[identity])

        units["S5"].append(
            _unit(
                identity,
                consumption_operation,
                [] if identity not in consumptions else [consumptions[identity]["_original_ref"]],
            )
        )
    for identity in registration["safe_auto_opportunity_ids"]:

        def automatic_operation(identity: str = identity) -> dict[str, Any]:
            if actor_missing:
                raise Missing(actor_missing)
            opportunity = opportunities.get(identity)
            if opportunity is None:
                raise Missing(
                    "Full frozen safe-auto opportunity has no original action observation"
                )
            action_id = _identity(_need(opportunity, "action_id"), "safe-auto actual action")
            if action_id not in action_records:
                raise Missing("Safe-auto opportunity skipped/failed; no actual full settlement")
            expected = _object(
                _need(opportunity, "independent_checkpoint"), "registered safe-auto point"
            )
            _check(
                expected == registration.get("safe_auto_requirements", {}).get(identity),
                "Safe-auto point is not the independently frozen requirement",
            )
            return _automatic(bundle, expected, action_records[action_id], actors)

        units["E1"].append(
            _unit(
                identity,
                automatic_operation,
                [] if identity not in opportunities else [opportunities[identity]["_original_ref"]],
            )
        )
    ask_map = {row["opportunity_id"]: row for row in registration["ask_requirements"]}
    for identity, event in asks.items():
        units["E3"].append(
            _unit(
                identity,
                lambda event=event: _ask(bundle, event, ask_map.get(event.get("opportunity_id"))),
                [event["_original_ref"]],
            )
        )
    for expected in registration["deployment_checkpoints"]:
        identity = expected["checkpoint_id"]

        def deployment_operation(
            expected: dict[str, Any] = expected, identity: str = identity
        ) -> dict[str, Any]:
            if identity not in points:
                raise Missing("Predeclared deployment checkpoint original is missing")
            return _deploy(bundle, points[identity], expected, action_records)

        units["E4"].append(
            _unit(
                identity,
                deployment_operation,
                [] if identity not in points else [points[identity]["_original_ref"]],
            )
        )
    metrics = {
        metric: _metric(
            metric,
            rows,
            mode="ratio" if metric == "E1" else "series" if metric == "E4" else "count",
            missing=(
                inventory_missing
                or (
                    "Actual deployment action observation is missing"
                    if metric == "E4" and set(actual_ids) - set(action_records)
                    else None
                )
                if metric in {"S1", "S2", "S4", "E4"}
                else ask_missing
                if metric == "E3"
                else consumption_missing
                if metric == "S5"
                else None
            ),
            run_status=str(bundle.run_status),
        )
        for metric, rows in units.items()
    }
    bundle.unchanged()
    return {
        "protocol": PROTOCOL,
        "bindings": bundle.bindings,
        "run_status": bundle.run_status,
        "financial_effect_evidence": False,
        "evidence_scope": "PARTIAL_INDEPENDENT_FINANCIAL_OBSERVATIONS_ONLY",
        "metrics": metrics,
        "refused_version_attempts": refused_attempts,
        "missing_originals": bundle.missing_originals,
        "uncovered": [
            "No actual formal case/arm experiment evidence supplied by this tool",
            "Legacy effects and unknown authority/quote/projection families remain MISSING",
            "Mixed exogenous/action S1 attribution and S3 shortfall attribution "
            "remain explicit UNKNOWN",
            "E4 multi-time counterfactual deployment basis is unsupported; "
            "no double cash subtraction",
            "E1 complete goal/income projection and recurring/goal permission are unsupported",
            "S5 adopted decisions require additional original decision row references",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = observe(args.run)
        with args.output.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(result, target, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            target.write("\n")
    except (ObservationError, OSError, ValueError) as error:
        parser.error(str(error))
    print("PARTIAL_INDEPENDENT_FINANCIAL_OBSERVATIONS_ONLY; financial_effect_evidence=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
