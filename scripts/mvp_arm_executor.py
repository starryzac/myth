"""Five-arm protocol scaffold; absent frozen production hook means NOT_IMPLEMENTED.

Candidate arithmetic has no execution authority. No DB, HTTP, simulated bank or
P evaluator is imported here. Real service use requires the explicit production
provider below, original response files and the original three-phase pipeline.
"""

from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any, Protocol
from uuid import UUID, uuid5

from scripts.mvp_observations import (
    BINDINGS,
    REG_PROTOCOL,
    ObservationError,
    _digest,
    _identity,
    _integer,
    _list,
    _object,
    _text,
    _time,
)
from scripts.mvp_trace_metrics import EFFECT_FIELDS, digest_value

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "mvp-five-arm-service-protocol-v1"
PROVIDER = "app.services.experiment_arms"
PROVIDER_PATH = ROOT / "apps/api/app/services/experiment_arms.py"
ARMS = {"B0", "B1", "B2", "B3", "P"}
Selector = Callable[[dict[str, Any]], dict[str, Any] | None]


class ServiceHookPending(ObservationError):
    """The actual production candidate hook is unavailable or unfrozen."""


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ObservationError(reason)


def _need(value: dict[str, Any], key: str) -> Any:
    if key not in value:
        raise ObservationError("Original protocol field is missing: " + key)
    return value[key]


@dataclass(frozen=True)
class SimulationContext:
    bindings: dict[str, str]
    database_name: str
    now: str
    root_registration_ref: dict[str, str] | None = None
    original_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_digest", self._context_digest())

    def _context_digest(self) -> str:
        values: list[Any] = [self.bindings, self.database_name, self.now]
        if self.root_registration_ref is not None:
            values.extend(["mvp-arm-isolated-context-v2", self.root_registration_ref])
        return digest_value(values)

    def assert_current(self) -> None:
        _check(
            self.original_digest == self._context_digest(),
            "Original context changed; no mutable or cached cross-request authority",
        )

    @classmethod
    def parse(cls, value: dict[str, Any]) -> SimulationContext:
        protocol = value.get("protocol")
        _check(
            protocol in {"mvp-arm-isolated-context-v1", "mvp-arm-isolated-context-v2"},
            "Context protocol differs",
        )
        bindings = _object(_need(value, "bindings"), "context binding")
        _check(set(bindings) == set(BINDINGS), "Exact original thirteen bindings are required")
        for key, item in bindings.items():
            _text(item, key)
        for key in ("experiment_run_id", "isolated_db_epoch", "user_id"):
            _identity(bindings[key], key)
        for key in ("input", "oracle", "design", "rule", "source"):
            _digest(bindings[key + "_sha256"], key)
        _check(bindings["arm_id"] in ARMS, "Unknown arm")
        _check(bindings["seed_version"] == "mvp-301-v6", "Unregistered original seed")
        _check(
            bindings["purpose"]
            in {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN", "TOOL_TEST_ONLY"},
            "Unknown original corpus purpose",
        )
        _check(
            bindings["execution_mode"] in {"SERVICE_INTEGRATION", "TOOL_TEST_ONLY"},
            "MODEL_ONLY proposals cannot be relabelled as service integration",
        )
        _check(
            (bindings["execution_mode"] == "TOOL_TEST_ONLY")
            == (bindings["purpose"] == "TOOL_TEST_ONLY"),
            "Tool provenance differs",
        )
        _check(
            value.get("database_host") == "127.0.0.1"
            and type(value.get("database_port")) is int
            and value["database_port"] == 54329,
            "Only the owned local isolated simulation endpoint is allowed",
        )
        name = _text(_need(value, "database_name"), "isolated database")
        _check(
            re.fullmatch(r"bf_test_[0-9a-f]{32}", name) is not None,
            "Formal/shared/default databases are excluded",
        )
        now = _text(_need(value, "now"), "aware simulation clock")
        _time(now, "simulation clock")
        root_ref = None
        if protocol == "mvp-arm-isolated-context-v2":
            _check(
                bindings["purpose"] in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
                and bindings["execution_mode"] == "SERVICE_INTEGRATION",
                "V2 requires actual frozen purpose",
            )
            root_ref = _object(_need(value, "root_registration_ref"), "external root registration")
            _check(
                set(root_ref) == {"path", "sha256"},
                "Exact external root original reference required",
            )
            path = Path(_text(root_ref["path"], "external root original path"))
            _check(
                path.is_absolute() and path.resolve().is_relative_to(ROOT.resolve()),
                "External root registration outside workspace",
            )
            _digest(root_ref["sha256"], "external root registration SHA")
        else:
            _check(
                "root_registration_ref" not in value, "V1 cannot silently acquire a V2 registration"
            )
        return cls(copy.deepcopy(bindings), name, now, copy.deepcopy(root_ref))


class ProductionHooks(Protocol):
    """Root-owned provider must delegate to original services, not a baseline mock."""

    def verify_simulation_context(self, context: SimulationContext) -> dict[str, Any]: ...

    def prepare_arm_action(
        self,
        context: SimulationContext,
        request: dict[str, Any],
        *,
        candidate_selector: Selector | None,
    ) -> dict[str, Any]: ...

    def confirm_arm_action(
        self,
        context: SimulationContext,
        action_id: str,
        effect_hash: str,
        actor_registration_ref: dict[str, Any],
    ) -> dict[str, Any]: ...

    def execute_arm_action(self, context: SimulationContext, action_id: str) -> dict[str, Any]: ...


def require_production_hooks(
    hooks: ProductionHooks | None, frozen_source_ref: dict[str, Any] | None
) -> ProductionHooks:
    """Hard gate before a callback runs; this provider is currently absent."""
    if hooks is None or frozen_source_ref is None or not PROVIDER_PATH.is_file():
        raise ServiceHookPending("NOT_IMPLEMENTED: explicit production candidate hook is missing")
    _check(
        frozen_source_ref.get("status") == "FROZEN_SERVICE_HOOK"
        and frozen_source_ref.get("original_path") == "apps/api/app/services/experiment_arms.py",
        "Production hook source is not explicitly frozen",
    )
    expected = _digest(frozen_source_ref.get("sha256"), "frozen production source")
    raw = PROVIDER_PATH.read_bytes()
    _check(hashlib.sha256(raw).hexdigest() == expected, "Production source drifted after freeze")
    archive = Path(_text(frozen_source_ref.get("archived_path"), "actual archived provider"))
    _check(
        archive.is_absolute() and archive.is_file() and archive.read_bytes() == raw,
        "Actual provider archive is missing or differs",
    )
    compiled = compile(raw, str(PROVIDER_PATH), "exec", dont_inherit=True)
    definitions = {code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)}
    for name in (
        "verify_simulation_context",
        "prepare_arm_action",
        "confirm_arm_action",
        "execute_arm_action",
    ):
        function = getattr(hooks, name, None)
        _check(
            isinstance(function, FunctionType)
            and function.__module__ == PROVIDER
            and function.__code__ == definitions.get(name),
            "Callbacks must be original frozen production module functions",
        )
    _check(
        "candidate_selector" in inspect.signature(hooks.prepare_arm_action).parameters,
        "Production preparation has no explicit before-effect candidate selector",
    )
    # This is availability/provenance only. A genuine original pipeline record is
    # still needed before a result receives SERVICE_INTEGRATION mode.
    return hooks


class OriginalReader:
    """Byte/pointer integrity only; a well-formed capture cannot prove a bank effect."""

    def __init__(self, context: SimulationContext, paths: dict[str, Any]):
        context.assert_current()
        self.context = context
        self.paths = copy.deepcopy(_object(paths, "actual original paths"))

    def resolve(self, reference: dict[str, Any]) -> Any:
        ref = _object(reference, "actual original reference")
        _check(
            set(ref) == {"artifact_sha256", "json_pointer", "value_sha256"},
            "Exact source reference is required",
        )
        digest = _digest(ref["artifact_sha256"], "actual original byte SHA")
        path = Path(_text(self.paths.get(digest), "actual original path"))
        _check(path.is_absolute() and path.is_file(), "Actual original file is missing")
        raw = path.read_bytes()
        _check(hashlib.sha256(raw).hexdigest() == digest, "Actual original bytes drifted")

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                _check(key not in result, "Original JSON contains repeated keys")
                result[key] = value
            return result

        def finite(value: str) -> Any:
            raise ObservationError("Original JSON contains nonfinite " + value)

        artifact = _object(
            json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite),
            "actual original",
        )
        _check(
            artifact.get("protocol") == "mvp-raw-observation-v1"
            and artifact.get("bindings") == self.context.bindings,
            "Original run/source/owner is rebound",
        )
        if self.context.root_registration_ref is not None:
            _check(
                artifact.get("root_registration_ref") == self.context.root_registration_ref,
                "Original V2 invocation registration differs",
            )
        pointer = ref["json_pointer"]
        _check(
            isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")),
            "Original JSON pointer is invalid",
        )
        value: Any = artifact
        if pointer:
            for part in pointer[1:].split("/"):
                _check(re.search(r"~(?![01])", part) is None, "Original pointer escape differs")
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    _check(
                        re.fullmatch(r"0|[1-9][0-9]*", part) is not None,
                        "Original array index differs",
                    )
                    value = value[int(part)]
                else:
                    value = _object(value, "original pointer parent")[part]
        _check(
            digest_value(value) == _digest(ref["value_sha256"], "original pointer value"),
            "Original pointer value/hash differs",
        )
        return copy.deepcopy(value)


def _capture(
    context: SimulationContext, capture: dict[str, Any], kind: str
) -> tuple[OriginalReader, dict[str, Any], str]:
    _check(
        capture.get("protocol") == "mvp-arm-original-capture-v1",
        "An actual capture file is required",
    )
    reader = OriginalReader(context, _need(capture, "original_paths"))
    ref = _object(_need(capture, "artifact_ref"), "actual capture reference")
    _check(ref.get("json_pointer") == "", "Capture must reference the full original envelope")
    artifact = _object(reader.resolve(ref), "full original envelope")
    return reader, _original(context, artifact, kind), ref["artifact_sha256"]


def _cash_accounts(view: dict[str, Any], rule: dict[str, Any], owner: str) -> list[dict[str, Any]]:
    rows = [_object(row, "raw account") for row in _list(_need(view, "accounts"), "cash originals")]
    ids = [_identity(_need(row, "id"), "raw account") for row in rows]
    _check(len(ids) == len(set(ids)), "Repeated raw account")
    for row in rows:
        _check(
            row.get("user_id") == owner and row.get("currency") == "CNY",
            "Raw account owner/currency differs",
        )
        _integer(_need(row, "balance_cents"), "raw account balance")
    scope = [
        _identity(value, "registered cash scope")
        for value in _list(_need(rule, "cash_account_ids"), "registered cash scope")
    ]
    _check(
        bool(scope) and len(scope) == len(set(scope)) and set(scope) <= set(ids),
        "Registered cash scope is incomplete/repeated",
    )
    selected = [row for row in rows if row["id"] in scope]
    _check(
        all(row.get("account_type") == "CASH" for row in selected),
        "Baseline cannot use goal/position-owned money",
    )
    return selected


def _fund(amount: int, accounts: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
    remaining = amount
    result = []
    seen = set()
    for row in sorted(accounts, key=lambda row: row["id"]):
        identity = _identity(_need(row, "id"), "original cash account")
        _check(identity not in seen and row.get("user_id") == owner, "Foreign/repeated cash source")
        seen.add(identity)
        if row.get("account_type") != "CASH":
            continue
        take = min(remaining, _integer(_need(row, "balance_cents"), "raw cash balance"))
        if take:
            result.append({"account_id": identity, "amount_cents": take})
            remaining -= take
    _check(remaining == 0, "Candidate exceeds original cash; no fabricated source or permission")
    return result


def _static_obligations(view: dict[str, Any], rule: dict[str, Any], at: datetime) -> int:
    end = _time(_need(rule, "horizon_end_at"), "registered static horizon")
    zone = rule.get("timezone")
    _check(zone in {"Asia/Shanghai", "UTC"}, "Static calendar timezone is missing/unsupported")
    local_zone = timezone(timedelta(hours=8)) if zone == "Asia/Shanghai" else UTC
    at, end = at.astimezone(local_zone), end.astimezone(local_zone)
    _check(at <= end and (end.date() - at.date()).days <= 90, "Unsupported fixed horizon")
    expected = [
        _identity(value, "fixed original")
        for value in _list(_need(rule, "fixed_policy_version_ids"), "registered fixed originals")
    ]
    _check(len(expected) == len(set(expected)), "Fixed original identities repeat")
    originals = [
        _object(row, "raw policy version")
        for row in _list(_need(view, "policy_versions"), "raw policy versions")
    ]
    rows = {_identity(_need(row, "id"), "fixed policy version"): row for row in originals}
    _check(len(rows) == len(originals), "Repeated raw policy versions")
    total = 0
    for identity in expected:
        if identity not in rows:
            raise ObservationError("Original registered fixed obligation is missing")
        row = rows[identity]
        _check(row.get("user_id") == view["user_id"], "Fixed obligation owner differs")
        config = _object(_need(row, "configuration"), "raw fixed policy")
        _check(
            digest_value(config) == row.get("content_hash")
            and config.get("type") == "recurring_obligation",
            "Raw fixed policy differs",
        )
        amount_rule = _object(_need(config, "amount_rule"), "raw fixed amount rule")
        _check(amount_rule.get("kind") == "exact", "Range/bill is not a static fixed amount")
        cents = _integer(_need(amount_rule, "amount_cents"), "original fixed amount")
        day = _integer(_need(config, "due_day"), "original due day")
        _check(1 <= day <= 31, "Invalid due day")
        # The producer supplies actual local aware clock; no P boundary value is read.
        year, month = at.year, at.month
        while (year, month) <= (end.year, end.month):
            due = at.replace(
                year=year,
                month=month,
                day=min(day, calendar.monthrange(year, month)[1]),
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            if at.date() <= due.date() <= end.date():
                total += cents
            month += 1
            if month == 13:
                year, month = year + 1, 1
    return _integer(total, "fixed horizon total")


def candidate_selector(
    context: SimulationContext, registration: dict[str, Any], opportunity_id: str
) -> Selector | None:
    """B3/P return None to retain the actual P planner; no invented P evaluator."""
    arm = context.bindings["arm_id"]
    context.assert_current()
    original_registration = registration
    if registration.get("protocol") == REG_PROTOCOL:
        _check(
            registration.get("kind") == "RULE"
            and registration.get("arm_id") == arm
            and registration.get("purpose") == context.bindings["purpose"]
            and registration.get("case_id") == context.bindings["case_id"],
            "Original observation RULE wrapper is rebound",
        )
        registration = _object(
            _need(registration, "arm_algorithm"), "actual registered arm algorithm"
        )
    _check(
        registration.get("protocol") == "mvp-arm-rule-v1"
        and registration.get("arm_id") == arm
        and registration.get("purpose") == context.bindings["purpose"]
        and registration.get("case_id") == context.bindings["case_id"]
        and registration.get("seed_version") == context.bindings["seed_version"],
        "Original arm rule is rebound",
    )
    frozen_rule = copy.deepcopy(registration)
    frozen_digest = digest_value(original_registration)
    if arm in {"B3", "P"}:
        return None

    def select(view: dict[str, Any]) -> dict[str, Any] | None:
        context.assert_current()
        _check(
            digest_value(original_registration) == frozen_digest,
            "Registered arm rule changed after selection",
        )
        _check(
            view.get("protocol") == "mvp-arm-planning-view-v1"
            and view.get("user_id") == context.bindings["user_id"]
            and view.get("as_of") == context.now
            and view.get("bindings") == context.bindings,
            "Before-effect planning view is not bound to this original transaction/run",
        )
        intent = _object(_need(view, "intent"), "original small intent")
        _check(
            intent.get("kind") in {"purchase_asset", "allocate_goal", "transfer_internal"},
            "This candidate arithmetic family is unsupported; no Agent recovery is inferred",
        )
        accounts = _cash_accounts(view, frozen_rule, context.bindings["user_id"])
        cash = sum(
            _integer(row["balance_cents"], "raw balance")
            for row in accounts
            if row.get("account_type") == "CASH"
        )
        manual = False
        if arm == "B0":
            _check(
                view.get("trigger") == "REGISTERED_MANUAL",
                "B0 does not discover or perform Agent recovery",
            )
            choices = [
                _object(row, "registered manual choice")
                for row in _list(_need(registration, "manual_actions"), "manual choices")
                if row.get("opportunity_id") == opportunity_id
            ]
            _check(len(choices) == 1, "One original manual choice is required")
            amount = _integer(_need(choices[0], "amount_cents"), "registered manual amount")
            _check(choices[0].get("intent") == view.get("intent"), "Manual intent was changed")
            manual = True
        elif arm == "B1":
            amount = max(0, cash - _integer(_need(registration, "threshold_cents"), "threshold"))
        else:
            amount = max(
                0,
                cash
                - _static_obligations(view, registration, _time(context.now, "static rule clock")),
            )
        if not amount:
            return None
        if intent["kind"] == "transfer_internal":
            _check(
                arm == "B0" and intent.get("amount_cents") == amount,
                "Explicit manual transfer amount cannot be replaced by a savings rule",
            )
        return {
            "protocol": "mvp-arm-candidate-v1",
            "bindings": copy.deepcopy(context.bindings),
            "opportunity_id": opportunity_id,
            "arm_id": arm,
            "amount_cents": amount,
            "cash_uses": _fund(amount, accounts, context.bindings["user_id"]),
            "intent": copy.deepcopy(_object(_need(view, "intent"), "original small intent")),
            "manual_choice": manual,
            "execution_status": "NOT_EXECUTED",
            "unsafe_candidate_status": "NOT_MEASURED",
            "source_view_sha256": digest_value(view),
            "rule_value_sha256": frozen_digest,
            "execution_mode": None,
            "capability_status": "CANDIDATE_ARITHMETIC_ONLY",
        }

    return select


def candidate_selector_v2(
    context: SimulationContext, registration: dict[str, Any], opportunity_id: str
) -> Selector | None:
    """Original RULE stays original; the provider supplies separately bound dynamic resolution."""
    context.assert_current()
    _check(
        context.root_registration_ref is not None, "V2 candidate needs external root registration"
    )
    arm = context.bindings["arm_id"]
    original_registration = registration
    frozen_digest = digest_value(original_registration)
    algorithm = registration.get("arm_algorithm", registration)
    _check(
        algorithm.get("protocol") == "mvp-arm-rule-v1"
        and algorithm.get("arm_id") == arm
        and algorithm.get("case_id") == context.bindings["case_id"]
        and algorithm.get("purpose") == context.bindings["purpose"]
        and algorithm.get("seed_version") == context.bindings["seed_version"],
        "Original V2 arm algorithm is rebound",
    )
    if arm in {"P", "B3"}:
        return None

    def select_v2(view: dict[str, Any]) -> dict[str, Any] | None:
        context.assert_current()
        _check(digest_value(original_registration) == frozen_digest, "Original V2 RULE changed")
        _check(
            view.get("protocol") == "mvp-arm-planning-view-v1"
            and view.get("bindings") == context.bindings
            and view.get("user_id") == context.bindings["user_id"]
            and view.get("as_of") == context.now,
            "Actual V2 raw view differs",
        )
        resolution = _object(_need(view, "rule_resolution"), "actual rule resolution")
        _check(
            resolution.get("protocol") == "mvp-arm-rule-resolution-v2"
            and resolution.get("bindings") == context.bindings
            and resolution.get("root_registration_ref") == context.root_registration_ref
            and resolution.get("opportunity_id") == opportunity_id
            and resolution.get("original_rule_value_sha256") == frozen_digest,
            "Original V2 RULE resolution binding differs",
        )
        resolved = _object(
            _need(resolution, "resolved_arm_algorithm"), "resolved original arm algorithm"
        )
        _check(
            digest_value(resolved) == resolution.get("resolved_arm_algorithm_value_sha256"),
            "Resolved V2 rule bytes differ",
        )
        _check(
            resolved.get("protocol") == "mvp-arm-rule-v1"
            and resolved.get("arm_id") == arm
            and resolved.get("case_id") == context.bindings["case_id"]
            and resolved.get("purpose") == context.bindings["purpose"]
            and resolved.get("seed_version") == context.bindings["seed_version"],
            "Resolved V2 rule identity differs",
        )
        _list(_need(resolution, "resolution_refs"), "original rule resolution refs")
        intent = _object(_need(view, "intent"), "actual small intent")
        _check(intent.get("kind") == "purchase_asset", "V2 baseline remains GENERAL purchase only")
        accounts = _cash_accounts(view, resolved, context.bindings["user_id"])
        cash = sum(_integer(row["balance_cents"], "raw original balance") for row in accounts)
        manual = arm == "B0"
        if manual:
            _check(view.get("trigger") == "REGISTERED_MANUAL", "B0 cannot infer Agent recovery")
            choices = [
                row
                for row in _list(_need(resolved, "manual_actions"), "resolved manual actions")
                if row.get("opportunity_id") == opportunity_id
            ]
            _check(
                len(choices) == 1 and choices[0].get("intent") == intent,
                "Actual original manual intent differs",
            )
            amount = _integer(
                _need(choices[0], "amount_cents"), "original registered manual amount"
            )
        elif arm == "B1":
            amount = max(
                0, cash - _integer(_need(resolved, "threshold_cents"), "original shared threshold")
            )
        else:
            amount = max(
                0,
                cash
                - _static_obligations(view, resolved, _time(context.now, "original rule clock")),
            )
        if not amount:
            return None
        return {
            "protocol": "mvp-arm-candidate-v1",
            "bindings": copy.deepcopy(context.bindings),
            "opportunity_id": opportunity_id,
            "arm_id": arm,
            "amount_cents": amount,
            "cash_uses": _fund(amount, accounts, context.bindings["user_id"]),
            "intent": copy.deepcopy(intent),
            "manual_choice": manual,
            "execution_status": "NOT_EXECUTED",
            "unsafe_candidate_status": "NOT_MEASURED",
            "source_view_sha256": digest_value(view),
            "rule_value_sha256": frozen_digest,
            "rule_resolution_binding_sha256": digest_value(resolution),
            "execution_mode": None,
            "capability_status": "CANDIDATE_ARITHMETIC_ONLY",
        }

    return select_v2


def _original(context: SimulationContext, artifact: dict[str, Any], kind: str) -> dict[str, Any]:
    if context.root_registration_ref is not None:
        _check(
            artifact.get("root_registration_ref") == context.root_registration_ref,
            "Original V2 invocation registration differs",
        )
    _check(
        artifact.get("protocol") == "mvp-raw-observation-v1"
        and artifact.get("kind") == kind
        and artifact.get("bindings") == context.bindings,
        "Actual service original is rebound",
    )
    return _object(_need(artifact, "payload"), "actual service payload")


def _effect(
    context: SimulationContext, effect: dict[str, Any], action_id: str, effect_hash: str
) -> None:
    _check(
        set(effect) == EFFECT_FIELDS
        and effect.get("simulation") is True
        and effect.get("user_id") == context.bindings["user_id"]
        and effect.get("operation_id") == action_id,
        "Full original effect identity/schema is required",
    )
    canonical = copy.deepcopy(effect)
    canonical["cash_uses"] = sorted(
        _list(effect["cash_uses"], "original cash sources"), key=lambda row: row["account_id"]
    )
    canonical["income_uses"] = sorted(
        _list(effect["income_uses"], "original income sources"),
        key=lambda row: (row["origin_transaction_id"], row["account_id"]),
    )
    canonical["policy_version_ids"] = sorted(
        _list(effect["policy_version_ids"], "original policy versions")
    )
    if canonical["liability"] is not None:
        canonical["liability"]["evidence_ids"] = sorted(
            _list(canonical["liability"]["evidence_ids"], "liability originals")
        )
    _check(
        digest_value(canonical) == _digest(effect_hash, "original effect hash"),
        "Original effect hash changed; an adapter cannot rehash history",
    )
    for key in ("amount_cents", "fee_cents", "loss_cents", "settlement_delay_days"):
        _integer(_need(effect, key), "original " + key)
    _check(effect["amount_cents"] > 0, "Original economic effect amount must be positive")
    if effect["net_cents"] is not None:
        _integer(effect["net_cents"], "original net amount")


def _response(context: SimulationContext, response: dict[str, Any]) -> dict[str, Any]:
    for key in ("user_id", "action_id", "decision_run_id"):
        _identity(_need(response, key), key)
    _check(
        response["user_id"] == context.bindings["user_id"] and response.get("simulation") is True,
        "Actual simulated owner differs",
    )
    _effect(
        context,
        _object(_need(response, "effect"), "original full economic effect"),
        response["action_id"],
        _need(response, "effect_hash"),
    )
    _check(
        response.get("autonomy_level") in {"AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"},
        "Original autonomy result is missing",
    )
    prepared_at = _time(_need(response, "prepared_at"), "original preparation time")
    _check(
        prepared_at
        <= _time(_need(response, "as_of"), "original response clock")
        <= _time(context.now, "context clock"),
        "Original response clock is reversed or in the future",
    )
    _object(_need(response, "prepared_validation"), "actual common gateway validation")
    return copy.deepcopy(response)


def _pipeline(
    context: SimulationContext,
    reader: OriginalReader,
    payload: dict[str, Any],
    required: set[str],
    action_id: str | None,
) -> None:
    pipeline = _object(_need(payload, "pipeline"), "original preparation pipeline")
    _check(
        set(pipeline) == required,
        "Every common pipeline stage needs its original evidence reference",
    )
    for name, stage in pipeline.items():
        value = _object(
            reader.resolve(
                _object(
                    _need(_object(stage, "stage reference"), "original_ref"),
                    "original stage reference",
                )
            ),
            "original stage capture",
        )
        _check(
            value.get("stage") == name
            and value.get("user_id") == context.bindings["user_id"]
            and value.get("action_id") == action_id
            and value.get("capture_origin") == "PRODUCTION_SERVICE_CALL",
            "Pipeline stage identity/owner/original differs",
        )
        _time(_need(value, "occurred_at"), "original stage clock")


def record_prepare(context: SimulationContext, capture: dict[str, Any]) -> dict[str, Any]:
    reader, payload, original_sha = _capture(context, capture, "ARM_PREPARE")
    _check(
        payload.get("capture_origin") == "PRODUCTION_SERVICE_CALL",
        "Model/mock result cannot claim actual preparation",
    )
    required = {
        "user_lock",
        "prepare_transaction",
        "candidate_before_new_effect",
        "economic_hash",
        "source_context",
        "execution_revalidation",
        "decision_trace",
    }
    outcome = _text(_need(payload, "outcome"), "actual preparation outcome")
    _check(
        outcome in {"PREPARED", "GATEWAY_REJECTED", "COMMON_GATEWAY_REJECTED", "NO_CANDIDATE"},
        "Unknown preparation outcome",
    )
    response = payload.get("response")
    if outcome == "PREPARED":
        response = _response(context, _object(response, "original ActionResponse"))
    elif outcome in {"GATEWAY_REJECTED", "COMMON_GATEWAY_REJECTED"}:
        _check(
            response is None,
            "A genuine common rejection cannot contain a fabricated prepared effect/response",
        )
        error = _object(_need(payload, "error"), "actual gateway refusal original")
        _integer(_need(error, "http_status"), "actual HTTP refusal status")
        _text(_need(error, "code"), "actual gateway refusal code")
        reader.resolve(_object(_need(error, "original_ref"), "actual refusal reference"))
    attempt_action_id = payload.get("attempt_action_id")
    if attempt_action_id is not None:
        _identity(attempt_action_id, "original pre-effect attempt identity")
    if isinstance(response, dict):
        _check(
            attempt_action_id in {None, response["action_id"]},
            "Original attempt changed its logical action",
        )
    _pipeline(
        context,
        reader,
        payload,
        required,
        response["action_id"] if isinstance(response, dict) else attempt_action_id,
    )
    return {
        "protocol": PROTOCOL,
        "bindings": context.bindings,
        "stage": "PREPARE",
        "outcome": outcome,
        "response": response,
        "original_artifact_sha256": original_sha,
        "execution_mode": None,
        "capability_status": "PENDING_PRODUCTION_VERIFICATION",
        "unsafe_candidate_status": "NOT_MEASURED",
        "actual_violation_status": "NOT_MEASURED",
        "financial_effect_evidence": False,
        "original_capture": copy.deepcopy(capture),
    }


def _attest(context: SimulationContext, provider: ProductionHooks) -> None:
    context.assert_current()
    reader, attestation, _ = _capture(
        context, provider.verify_simulation_context(context), "ARM_CONTEXT"
    )
    _check(
        attestation.get("database_host") == "127.0.0.1"
        and type(attestation.get("database_port")) is int
        and attestation.get("database_port") == 54329
        and attestation.get("database_name") == context.database_name
        and attestation.get("user_id") == context.bindings["user_id"]
        and attestation.get("is_simulated") is True
        and bool(attestation.get("original_user_ref"))
        and attestation.get("isolated_db_epoch") == context.bindings["isolated_db_epoch"],
        "Actual isolated simulated user/epoch attestation is missing",
    )
    user = _object(
        reader.resolve(
            _object(attestation["original_user_ref"], "original simulated user reference")
        ),
        "actual original user",
    )
    _check(
        user.get("id") == context.bindings["user_id"] and user.get("is_simulated") is True,
        "Original actual user is not this simulated owner",
    )


def _request(request: dict[str, Any]) -> None:
    _check(
        set(request) == {"idempotency_key", "intent"},
        "Only the original small user intent is accepted",
    )
    key = _text(request["idempotency_key"], "original idempotency key")
    _check(bool(key.strip()) and len(key) <= 160, "Invalid original idempotency key")
    intent = _object(request["intent"], "original small user intent")
    schemas = {
        "purchase_asset": {"kind", "policy_id"},
        "allocate_goal": {"kind", "goal_id"},
        "redeem_asset": {"kind", "position_id"},
        "transfer_internal": {
            "kind",
            "source_account_id",
            "destination_account_id",
            "amount_cents",
        },
    }
    kind = intent.get("kind")
    if kind == "pay_recurring":
        _check(
            {"kind", "policy_id"} <= set(intent) <= {"kind", "policy_id", "period", "bill_id"},
            "Original payment intent has extra/missing fields",
        )
        _identity(intent["policy_id"], "original payment policy")
        if intent.get("period") is not None:
            _check(
                isinstance(intent["period"], str)
                and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", intent["period"]) is not None,
                "Original payment period differs",
            )
        if intent.get("bill_id") is not None:
            _identity(intent["bill_id"], "original payment bill")
        return
    _check(
        kind in schemas and set(intent) == schemas[kind],
        "Unimplemented/malformed small intent; grants/effects cannot be supplied",
    )
    for name, value in intent.items():
        if name.endswith("_id"):
            _identity(value, "original intent " + name)
    if kind == "transfer_internal":
        _check(
            _integer(intent["amount_cents"], "explicit original amount") > 0,
            "Original amount must be positive",
        )


def _registered_rule(
    context: SimulationContext, registration: dict[str, Any], source_ref: dict[str, Any] | None
) -> None:
    if source_ref is None:
        raise ObservationError(
            "Actual original arm rule file is required; declared amounts do not freeze a rule"
        )
    path = Path(_text(source_ref.get("path"), "actual registered rule path"))
    _check(path.is_absolute() and path.is_file(), "Actual registered rule file is missing")
    raw = path.read_bytes()
    _check(
        hashlib.sha256(raw).hexdigest()
        == context.bindings["rule_sha256"]
        == _digest(source_ref.get("sha256"), "registered rule byte SHA"),
        "Original registered rule/source hash differs",
    )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _check(key not in result, "Registered rule JSON contains repeated keys")
            result[key] = value
        return result

    _check(
        json.loads(raw, object_pairs_hook=unique) == registration,
        "Actual registered rule content differs",
    )


def prepare(
    context: SimulationContext,
    registration: dict[str, Any],
    opportunity_id: str,
    request: dict[str, Any],
    *,
    hooks: ProductionHooks | None = None,
    frozen_source_ref: dict[str, Any] | None = None,
    registration_source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context.assert_current()
    provider = require_production_hooks(hooks, frozen_source_ref)
    _check(
        context.bindings["execution_mode"] == "SERVICE_INTEGRATION",
        "Tool fixtures cannot invoke a production service",
    )
    _request(request)
    _registered_rule(context, registration, registration_source_ref)
    _attest(context, provider)
    selector = candidate_selector(context, registration, opportunity_id)
    return record_prepare(
        context,
        provider.prepare_arm_action(context, copy.deepcopy(request), candidate_selector=selector),
    )


def record_confirmation(
    context: SimulationContext, capture: dict[str, Any], action_id: str, effect_hash: str
) -> dict[str, Any]:
    reader, payload, original_sha = _capture(context, capture, "ARM_CONFIRMATION")
    _check(
        payload.get("capture_origin") == "PRODUCTION_SERVICE_CALL",
        "Confirmation is not an actual call",
    )
    response = _response(
        context, _object(_need(payload, "response"), "actual confirmation response")
    )
    _check(
        response["action_id"] == action_id and response["effect_hash"] == effect_hash,
        "Actor confirmation changed the original logical operation/effect",
    )
    evidence = _object(
        reader.resolve(
            _object(_need(payload, "confirmation_evidence_ref"), "actual confirmation proof")
        ),
        "original action consent",
    )
    _check(
        evidence.get("user_id") == context.bindings["user_id"]
        and evidence.get("id") == str(uuid5(UUID(action_id), "confirmation:" + effect_hash))
        and evidence.get("source_type") == "USER_ACTION_CONFIRMATION"
        and evidence.get("source_ref") == action_id
        and evidence.get("evidence_level") == "USER_CONFIRMED_ACTION",
        "A policy/grant/mock cannot replace actual per-action actor confirmation",
    )
    content = _object(_need(evidence, "content"), "actual original confirmation content")
    _check(
        content.get("user_id") == context.bindings["user_id"]
        and content.get("action_id") == action_id
        and content.get("effect_hash") == effect_hash
        and content.get("accepted") is True
        and content.get("simulation") is True
        and digest_value(content)
        == _digest(_need(evidence, "content_hash"), "original confirmation content SHA"),
        "Exact affirmative actor confirmation is missing/rebound",
    )
    actor = _object(
        reader.resolve(_object(_need(payload, "actor_event_ref"), "actual actor event")),
        "original actor event",
    )
    _check(
        actor.get("actor_kind") == "SYNTHETIC_SCRIPTED_ACTOR"
        and actor.get("event_type") == "AFFIRMATIVE_CONFIRMATION"
        and actor.get("phase") == "RUNTIME_INTERVENTION"
        and actor.get("action_id") == action_id
        and actor.get("effect_hash") == effect_hash,
        "A real synthetic actor event must accompany each original confirmation",
    )
    _time(_need(actor, "occurred_at"), "actual actor clock")
    confirmed_at = _time(_need(content, "confirmed_at"), "actual confirmation clock")
    _check(
        actor["occurred_at"] == content["confirmed_at"]
        and _time(_need(evidence, "valid_from"), "confirmation start") == confirmed_at
        and confirmed_at
        <= _time(context.now, "original invocation clock")
        < _time(_need(content, "valid_until"), "confirmation validity")
        and _time(_need(evidence, "valid_to"), "original evidence validity")
        == _time(content["valid_until"], "original content validity")
        and _time(_need(evidence, "observed_at"), "original observation clock") == confirmed_at
        and evidence.get("status") == "VALID",
        "Exact original action confirmation time/validity differs",
    )
    _check(
        _time(content["valid_until"], "original action consent end")
        == _time(response["effect"]["expires_at"], "original effect expiry")
        and _time(response["effect"]["valid_from"], "original effect start") <= confirmed_at,
        "Original consent is outside this exact original effect validity",
    )
    return {
        "original_artifact_sha256": original_sha,
        "response": response,
        "execution_mode": None,
        "capability_status": "PENDING_PRODUCTION_VERIFICATION",
        "financial_effect_evidence": False,
    }


def record_execution(
    context: SimulationContext, capture: dict[str, Any], action_id: str, effect_hash: str
) -> dict[str, Any]:
    reader, payload, original_sha = _capture(context, capture, "ARM_EXECUTION")
    _check(
        payload.get("action_id") == action_id and payload.get("effect_hash") == effect_hash,
        "Actual execution changed the original logical operation/effect",
    )
    _check(
        payload.get("capture_origin") == "PRODUCTION_SERVICE_CALL",
        "Execution original is not an actual call",
    )
    _pipeline(
        context,
        reader,
        payload,
        {
            "user_lock",
            "reservation_transaction",
            "bank_request_commit",
            "business_projection_commit",
        },
        action_id,
    )
    status = _text(_need(payload, "action_status"), "original action status")
    _check(
        status in {"SUCCEEDED", "UNKNOWN", "FAILED", "REJECTED", "SUBMITTED"},
        "Original terminal/pending status differs",
    )
    bank = _object(
        reader.resolve(
            _object(_need(payload, "bank_operation_ref"), "actual original bank operation")
        ),
        "original bank operation",
    )
    _check(
        bank.get("user_id") == context.bindings["user_id"]
        and bank.get("action_plan_id") == action_id,
        "Original bank operation is another owner/logical action",
    )
    bank_id = _identity(_need(bank, "id"), "original bank operation ID")
    command = _object(_need(bank, "request"), "original bank command")
    _check(
        set(command) == {"effect", "effect_hash"} and command["effect_hash"] == effect_hash,
        "Actual bank command changed the original effect",
    )
    _effect(context, _object(command["effect"], "original bank effect"), action_id, effect_hash)
    _check(
        digest_value(command) == _digest(_need(bank, "request_hash"), "original bank request hash"),
        "Actual bank request hash differs",
    )
    if status == "SUCCEEDED":
        receipt = _object(
            reader.resolve(_object(_need(payload, "receipt_ref"), "actual receipt original")),
            "original receipt",
        )
        _check(
            receipt.get("user_id") == context.bindings["user_id"]
            and receipt.get("action_plan_id") == action_id
            and _object(_need(receipt, "response"), "original receipt bank binding").get(
                "bank_operation_id"
            )
            == bank_id,
            "Original receipt belongs to another owner/action/bank",
        )
        for key in ("executed_cents", "fee_cents", "loss_cents"):
            _integer(_need(receipt, key), "original receipt " + key)
        _list(
            reader.resolve(
                _object(_need(payload, "posting_inventory_ref"), "actual full posting inventory")
            ),
            "actual posting inventory",
        )
    return {
        "protocol": PROTOCOL,
        "bindings": copy.deepcopy(context.bindings),
        "stage": "EXECUTION",
        "original_artifact_sha256": original_sha,
        "action_status": status,
        "execution_mode": None,
        "capability_status": "PENDING_PRODUCTION_VERIFICATION",
        "actual_violation_status": "NOT_MEASURED",
        "financial_effect_evidence": False,
        "economic_execution_verified": False,
        "censored": status in {"UNKNOWN", "SUBMITTED"},
    }


def actor_requirement(context: SimulationContext, response: dict[str, Any]) -> bool:
    """Per-action intervention contract; this never evaluates financial safety."""
    actual = _response(context, response)
    _check(
        actual["autonomy_level"] in {"AUTO_EXECUTE", "ASK_ONCE"},
        "ADVISE_ONLY/BLOCKED never become executable through the arm adapter",
    )
    return context.bindings["arm_id"] in {"B0", "B3"} or actual["autonomy_level"] == "ASK_ONCE"


def execute(
    context: SimulationContext,
    prepared: dict[str, Any],
    actor_ref: dict[str, Any] | None,
    *,
    hooks: ProductionHooks | None = None,
    frozen_source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = require_production_hooks(hooks, frozen_source_ref)
    _check(
        context.bindings["execution_mode"] == "SERVICE_INTEGRATION",
        "Tool fixtures cannot invoke a production service",
    )
    _check(
        prepared.get("bindings") == context.bindings and prepared.get("outcome") == "PREPARED",
        "Only this original prepared action may execute",
    )
    original_prepared = record_prepare(
        context, _object(_need(prepared, "original_capture"), "actual original preparation capture")
    )
    _check(
        original_prepared["response"] == prepared.get("response"),
        "Prepared response drifted after capture",
    )
    response = _object(_need(original_prepared, "response"), "actual prepared response")
    action_id = _identity(response["action_id"], "actual action")
    _check(
        response["autonomy_level"] in {"AUTO_EXECUTE", "ASK_ONCE"},
        "ADVISE_ONLY/BLOCKED never become executable through the arm adapter",
    )
    _attest(context, provider)
    confirmation = None
    if actor_requirement(context, response):
        _check(
            actor_ref is not None,
            "This arm requires an actual registered synthetic actor confirmation",
        )
        confirmation = record_confirmation(
            context,
            provider.confirm_arm_action(
                context,
                action_id,
                response["effect_hash"],
                _object(actor_ref, "original actor registration"),
            ),
            action_id,
            response["effect_hash"],
        )
    actual = provider.execute_arm_action(context, action_id)
    record = record_execution(context, actual, action_id, response["effect_hash"])
    record["confirmation_original"] = confirmation
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true", required=True)
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "protocol": PROTOCOL,
                "status": "NOT_IMPLEMENTED",
                "production_provider": PROVIDER,
                "financial_effect_evidence": False,
                "execution_mode": None,
                "reason": "Production narrow hook/freeze/original pipeline evidence is pending",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
