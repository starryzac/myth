"""Run declared synthetic inputs through existing services; never install expected outcomes."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.base import Base
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    User,
)
from app.db.testing import require_test_database
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ConfirmActionRequest,
    PaymentIntent,
    PrepareActionRequest,
)
from app.services.audit_chain import verify_audit_chain
from app.services.demo_console_types import DemoCommandView, DemoEventRequest, TemplateKind
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from app.services.scenario_references import resolve_inputs
from app.services.scenario_types import (
    InitialState,
    PropertyName,
    PropertyResult,
    Scenario,
    ScenarioResult,
    ScenarioRPC,
    ScenarioStep,
)
from app.services.simulated_bank import validate_bank_projection
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_FAULT_CONTEXT: ContextVar[object | None] = ContextVar("scenario_fault_invocation", default=None)
_FAULT_LOCK = RLock()


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("INVALID_SCENARIO_INPUT", message, 409)


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("A trusted aware simulator clock is required")
    return now.astimezone(UTC)


def _identifier(value: object) -> UUID:
    return TypeAdapter(UUID).validate_python(value)


def _periods(now: datetime, timezone: str) -> list[str]:
    local = now.astimezone(ZoneInfo(timezone))
    return sorted({(local + timedelta(days=day)).strftime("%Y-%m") for day in range(91)})


@contextmanager
def _fault(
    kind: str,
    step_kind: str,
    engine: Engine,
    user_id: UUID,
    inputs: dict[str, Any] | None = None,
) -> Iterator[None]:
    if kind == "NONE":
        yield
        return
    with _FAULT_LOCK:
        marker = object()
        token = _FAULT_CONTEXT.set(marker)
        try:
            with _inject_fault(kind, step_kind, engine, user_id, marker, inputs):
                yield
        finally:
            _FAULT_CONTEXT.reset(token)


@contextmanager
def _inject_fault(
    kind: str,
    step_kind: str,
    engine: Engine,
    user_id: UUID,
    marker: object,
    inputs: dict[str, Any] | None = None,
) -> Iterator[None]:
    from app.services import execution, external_bank_facts

    if kind == "DROP_BANK_RESPONSE" and step_kind == "EXECUTE_ACTION":
        original: Callable[..., Any] = execution.__dict__["process_operation"]

        def lost(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)  # Real independent bank commit happens first.
            if (
                _FAULT_CONTEXT.get() is not marker
                or len(args) < 2
                or args[0] is not engine
                or args[1] != user_id
            ):
                return result
            # Transport loss is uncertainty after the independent commit, never bank refusal.
            raise TimeoutError("SIMULATED_BANK_RESPONSE_LOST")

        with patch.object(execution, "process_operation", lost):
            yield
    elif kind == "FAIL_APPLICATION_PROJECTION" and step_kind == "RUN_RECOVERY":
        from app.services import recovery
        from app.services.scenario_service_steps import RecoveryKey

        recovery_key = RecoveryKey.model_validate(inputs).idempotency_key
        projection = recovery.__dict__["project_request"]

        def failed_recovery(*args: Any, **kwargs: Any) -> Any:
            if (
                _FAULT_CONTEXT.get() is not marker
                or len(args) < 2
                or not isinstance(args[0], Session)
                or args[0].get_bind() is not engine
                or not isinstance(args[1], SimulatedBankRedemption)
                or args[1].user_id != user_id
                or args[1].status != "SETTLED"
                or args[1].settled_at is None
            ):
                return projection(*args, **kwargs)
            session, request = args[0], args[1]
            action = session.get(ActionPlan, request.action_plan_id)
            run = session.get(DecisionRun, action.decision_run_id) if action is not None else None
            if (
                action is None
                or action.user_id != user_id
                or run is None
                or run.user_id != user_id
                or run.trigger_type != "SAFETY_RECOVERY"
                or run.input_snapshot.get("idempotency_key") != recovery_key
                or run.idempotency_key != "recovery:" + configuration_hash({"key": recovery_key})
                or run.snapshot_hash != configuration_hash(run.input_snapshot)
                or action.request_hash != configuration_hash(action.request)
                or request.request_hash != configuration_hash(request.request)
                or action.request.get("bank_request") != request.request
            ):
                return projection(*args, **kwargs)
            # The original phase-2 SETTLED bank commit already exists. Only this
            # original run/key's phase-3 savepoint is interrupted; no bank bytes change.
            raise _error("SIMULATED_APPLICATION_PROJECTION_INTERRUPTION")

        with patch.object(recovery, "project_request", failed_recovery):
            yield
    elif kind == "FAIL_APPLICATION_PROJECTION" and step_kind in {"EXECUTE_ACTION", "EXTERNAL_FACT"}:
        owner: Any = execution if step_kind == "EXECUTE_ACTION" else external_bank_facts
        name = "project_execution" if step_kind == "EXECUTE_ACTION" else "_project"
        projection = getattr(owner, name)

        def failed(*args: Any, **kwargs: Any) -> Any:
            if (
                _FAULT_CONTEXT.get() is not marker
                or len(args) < 2
                or not isinstance(args[0], Session)
                or args[0].get_bind() is not engine
                or getattr(args[1], "user_id", None) != user_id
            ):
                return projection(*args, **kwargs)
            raise _error("SIMULATED_APPLICATION_PROJECTION_INTERRUPTION")

        with patch.object(owner, name, failed):
            yield
    else:
        raise _error("This fault is not implemented for this service step")


class ScenarioRunner:
    """A per-invocation coordinator, with no retained permission, source, or ledger cache."""

    def __init__(self, engine: Engine, user_id: UUID):
        self.engine = engine
        self.user_id = user_id

    def _isolated(self) -> None:
        require_test_database(self.engine.url.database)
        if self.engine.url.host != "127.0.0.1" or self.engine.url.port != 54329:
            raise _error(
                "Scenario fixtures and fault injection require the local isolated simulator"
            )

    def _epoch(self, session: Session, expected: UUID) -> None:
        from app.services.demo_console import _epoch

        _epoch(session, self.user_id, expected)

    def demo_event(self, body: DemoEventRequest, now: datetime) -> DemoCommandView:
        """The public route supplies only its frozen event and the server's trusted clock."""
        now = _clock(now)
        scenario = Scenario(
            scenario_id=f"demo:{body.expected_epoch_id}:{body.event_kind}",
            purpose="DEVELOPMENT",
            dataset_id="native-demo-v1",
            family_id="demo-console",
            initial_state=InitialState(expected_epoch_id=body.expected_epoch_id),
            steps=[
                ScenarioStep(
                    step_id="event", kind="DEMO_EVENT", at=now, inputs=body.model_dump(mode="json")
                )
            ],
        )
        with audit_command_guard(self.engine, self.user_id):
            result = self._execute(scenario)
        step = result.steps[0]
        if "error" in step:
            problem = step["error"]
            raise PolicyLifecycleError(problem["code"], problem["message"], problem["status_code"])
        # Nested bank fact contracts are strict: reconstruct serialized JSON as JSON,
        # rather than treating UUID/timestamp strings as native Python objects.
        return DemoCommandView.model_validate_json(json.dumps(step["result"]))

    def run(self, scenario: Scenario) -> ScenarioResult:
        self._isolated()
        if scenario.purpose != "DEVELOPMENT":
            raise _error(
                "Frozen case manifest verification is not implemented; no frozen execution"
            )
        with audit_command_guard(self.engine, self.user_id):
            return self._execute(scenario)

    def _execute(self, scenario: Scenario) -> ScenarioResult:
        scenario = Scenario.model_validate_json(scenario.model_dump_json())
        if scenario.initial_state.mode == "SEED_NEW":
            self._isolated()
            from app.services.demo_seed import seed_demo

            with Session(self.engine) as session:
                if session.scalar(select(User.id).limit(1)) is not None:
                    raise _error("SEED_NEW cannot reset an existing simulation history")
            seed_demo(self.engine)
        if scenario.initial_state.expected_epoch_id is not None:
            with Session(self.engine) as session:
                self._epoch(session, scenario.initial_state.expected_epoch_id)
        rows: list[dict[str, Any]] = []
        previous: dict[str, dict[str, Any]] = {}
        failed = False
        for step in scenario.steps:
            if step.fault != "NONE" or step.kind in {"OBSERVE_RECURRING_PAYMENT", "EXTERNAL_FACT"}:
                self._isolated()
            row: dict[str, Any] = {
                "step_id": step.step_id,
                "kind": step.kind,
                "at": step.at.isoformat(),
            }
            try:
                try:
                    resolved, references = resolve_inputs(step.inputs, previous)
                except ValueError as error:
                    raise _error(str(error)) from error
                if references:
                    row["input_references"] = references
                    row["resolved_inputs"] = resolved
                resolved_step = step.model_copy(update={"inputs": resolved})
                with _fault(step.fault, step.kind, self.engine, self.user_id, resolved):
                    row["result"] = self._dispatch(resolved_step)
            except PolicyLifecycleError as error:
                row["error"] = {
                    "code": error.code,
                    "message": error.message,
                    "status_code": error.status_code,
                }
            except ValidationError as error:
                row["error"] = {
                    "code": "INVALID_SCENARIO_STEP",
                    "message": str(error),
                    "status_code": 422,
                }
            except TimeoutError as error:
                if step.fault != "DROP_BANK_RESPONSE":
                    raise
                row["error"] = {
                    "code": "SIMULATED_BANK_RESPONSE_LOST",
                    "message": str(error),
                    "status_code": 409,
                }
            problem = row.get("error")
            if step.expected_error is not None:
                matched = (
                    problem is not None
                    and problem["code"] == step.expected_error.code
                    and problem["status_code"] == step.expected_error.status_code
                )
                row["expected_error"] = step.expected_error.model_dump(mode="json")
                row["expected_error_matched"] = matched
                if problem is None:
                    # The real successful service result is retained. An unmet test
                    # expectation never invents a refusal or rolls back its effects.
                    row["expectation_failure"] = "EXPECTED_ERROR_NOT_OBSERVED"
                failed = not matched
            else:
                failed = problem is not None
            rows.append(row)
            previous[step.step_id] = row
            if failed:
                break
        executed_clock = next(
            (
                step.at
                for step in reversed(scenario.steps)
                if any(row["step_id"] == step.step_id for row in rows)
            ),
            datetime.now(UTC),
        )
        properties = self.check_properties(
            scenario.expected_properties,
            scenario.initial_state.expected_epoch_id,
            executed_clock,
        )
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            purpose=scenario.purpose,
            dataset_id=scenario.dataset_id,
            family_id=scenario.family_id,
            input_sha256=configuration_hash(scenario.model_dump(mode="json")),
            status="FAILED"
            if failed
            else ("PROPERTY_FAILED" if any(not p.passed for p in properties) else "EXECUTED"),
            steps=rows,
            properties=properties,
        )

    def _dispatch(self, step: ScenarioStep) -> dict[str, Any]:
        from app.domain.external_bank_fact_types import ExternalFactRequest
        from app.services import demo_console, execution, external_bank_facts

        now = _clock(step.at)
        data = step.inputs
        from app.services.scenario_service_steps import dispatch_service_step

        observed = dispatch_service_step(self.engine, self.user_id, step.kind, data, now)
        if observed is not None:
            return observed
        if step.kind == "DEMO_EVENT":
            return demo_console.run_demo_event(
                self.engine, self.user_id, DemoEventRequest.model_validate(data), now
            ).model_dump(mode="json")
        if step.kind == "PREPARE_TEMPLATE":
            if set(data) != {"kind", "expected_epoch_id"}:
                raise _error("Template inputs require only the original kind and epoch")
            kind: TemplateKind = TypeAdapter(TemplateKind).validate_python(data["kind"])
            return demo_console.prepare_demo_template(
                self.engine, self.user_id, kind, _identifier(data["expected_epoch_id"]), now
            ).model_dump(mode="json")
        if step.kind == "CONFIRM_TEMPLATE":
            if (
                set(data) != {"proposal_id", "reviewed_hash", "accepted"}
                or data["accepted"] is not True
            ):
                raise _error("Explicit synthetic actor review of the exact declaration is required")
            with Session(self.engine) as session, session.begin():
                return confirm_proposal(
                    session,
                    self.user_id,
                    _identifier(data["proposal_id"]),
                    data["reviewed_hash"],
                    True,
                    now,
                ).model_dump(mode="json")
        if step.kind == "EXTERNAL_FACT":
            request = ExternalFactRequest.model_validate_json(json.dumps(data))
            if request.user_id != self.user_id:
                raise _error("A synthetic bank fact cannot change the scenario principal")
            return external_bank_facts.ingest_external_fact(
                self.engine, self.user_id, request, now
            ).model_dump(mode="json")
        if step.kind == "PREPARE_ACTION":
            return execution.prepare_action(
                self.engine, self.user_id, PrepareActionRequest.model_validate(data), now
            ).model_dump(mode="json")
        if step.kind == "CONFIRM_ACTION":
            if set(data) != {"action_id", "effect_hash", "accepted"}:
                raise _error("An exact original action and affirmative consent are required")
            body = ConfirmActionRequest.model_validate(
                {key: data[key] for key in ("effect_hash", "accepted")}
            )
            return execution.confirm_action(
                self.engine, self.user_id, _identifier(data["action_id"]), body, now
            ).model_dump(mode="json")
        if step.kind == "EXECUTE_ACTION":
            if set(data) != {"action_id"}:
                raise _error("Execution accepts the original action identity only")
            return execution.execute_action(
                self.engine, self.user_id, _identifier(data["action_id"]), now
            ).model_dump(mode="json")
        if step.kind == "OBSERVE_RECURRING_PAYMENT":
            if set(data) != {"policy_id", "period"}:
                raise _error("The trusted cumulative observer accepts a policy and period only")
            return self.observe_recurring_payment(
                _identifier(data["policy_id"]),
                TypeAdapter(str).validate_python(data["period"]),
                now,
            )
        raise _error("Unsupported scenario step")

    def observe_recurring_payment(
        self, policy_id: UUID, period: str, now: datetime
    ) -> dict[str, Any]:
        """Observe a newly declared simulator obligation's actual empty payment history.

        This is a BANK fact, never a user permission or an invented executed payment.
        Nonempty or conflicting history is refused rather than rewritten.
        """
        import re

        from app.services.boundary import SETTLEMENT_SOURCE
        from app.services.policy_lifecycle import is_version_authorized

        self._isolated()
        now = _clock(now)
        if re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period) is None:
            raise _error("An exact natural month is required")
        with (
            audit_command_guard(self.engine, self.user_id),
            Session(self.engine) as session,
            session.begin(),
        ):
            transaction_gate(session, self.user_id)
            user = session.scalar(select(User).where(User.id == self.user_id).with_for_update())
            version = session.scalar(
                select(PolicyVersion)
                .where(
                    PolicyVersion.user_id == self.user_id,
                    PolicyVersion.policy_id == policy_id,
                )
                .order_by(PolicyVersion.version_number.desc())
            )
            if (
                user is None
                or not user.is_simulated
                or version is None
                or version.configuration["type"] != "recurring_obligation"
                or not is_version_authorized(session, self.user_id, version.id, now)
            ):
                raise _error("The cumulative fact needs the original confirmed recurring policy")
            rows = list(
                session.scalars(select(BankOperation).where(BankOperation.user_id == self.user_id))
            )
            if any(
                row.request.get("effect", {}).get("policy_id") == str(policy_id)
                and row.request.get("effect", {}).get("liability", {}).get("period") == period
                for row in rows
            ):
                raise _error(
                    "An existing payment cannot be replaced by the initial empty-history observer"
                )
            identity = uuid5(policy_id, f"scenario-empty-bank-payment-v1:{period}")
            existing = session.get(EvidenceItem, identity)
            original = {
                "simulation": True,
                "protocol": "recurring-settlement-v1",
                "user_id": str(self.user_id),
                "policy_id": str(policy_id),
                "period": period,
                "paid_cents": 0,
                "payee_id": version.configuration["payee_id"],
                "complete": True,
                "as_of": now.isoformat(),
                "observation_protocol": "scenario-empty-bank-payment-v1",
            }
            if existing is not None:
                if (
                    existing.user_id != self.user_id
                    or existing.status != "VALID"
                    or existing.source_type != SETTLEMENT_SOURCE
                    or existing.content_hash != configuration_hash(existing.content)
                    or any(
                        existing.content.get(k) != v for k, v in original.items() if k != "as_of"
                    )
                ):
                    raise _error(
                        "The original cumulative observation conflicts; no replacement allowed"
                    )
                return {
                    "evidence_id": str(existing.id),
                    "content_hash": existing.content_hash,
                    "observation": existing.content,
                }
            conflict = session.scalar(
                select(EvidenceItem.id).where(
                    EvidenceItem.user_id == self.user_id,
                    EvidenceItem.source_type == SETTLEMENT_SOURCE,
                    EvidenceItem.content["policy_id"].as_string() == str(policy_id),
                    EvidenceItem.content["period"].as_string() == period,
                    EvidenceItem.status != "SUPERSEDED",
                )
            )
            if conflict is not None:
                raise _error(
                    "A cumulative statement already exists; it cannot be superseded by the runner"
                )
            proof = EvidenceItem(
                id=identity,
                user_id=self.user_id,
                created_at=now,
                evidence_level="BANK_CONFIRMED",
                source_type=SETTLEMENT_SOURCE,
                source_ref=f"scenario-empty-bank-payment-v1:{policy_id}:{period}",
                content=original,
                content_hash=configuration_hash(original),
                status="VALID",
                valid_from=now,
                observed_at=now,
            )
            session.add(proof)
            session.flush()
            return {
                "evidence_id": str(proof.id),
                "content_hash": proof.content_hash,
                "observation": original,
            }

    def check_properties(
        self, names: list[PropertyName], expected_epoch_id: UUID | None, now: datetime
    ) -> list[PropertyResult]:
        if not names:
            return []
        from app.services.demo_console import get_demo_state

        results: list[PropertyResult] = []
        with Session(self.engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            if expected_epoch_id is not None:
                self._epoch(session, expected_epoch_id)
            for name in names:
                detail: dict[str, Any] = {}
                passed = False
                try:
                    if name == "AUDIT_VALID":
                        actual = verify_audit_chain(session, self.user_id)
                        detail = actual.model_dump(mode="json")
                        passed = actual.status == "VALID"
                    elif name == "BANK_LEDGER_VALID":
                        validate_bank_projection(session, self.user_id, _clock(now))
                        grouped: dict[str, list[SimulatedBankPosting]] = defaultdict(list)
                        postings = list(
                            session.scalars(
                                select(SimulatedBankPosting)
                                .where(SimulatedBankPosting.user_id == self.user_id)
                                .order_by(SimulatedBankPosting.sequence_number)
                            )
                        )
                        for row in postings:
                            grouped[row.ledger_key].append(row)
                        for rows in grouped.values():
                            for index, row in enumerate(rows, 1):
                                if (
                                    row.sequence_number != index
                                    or row.balance_before_cents + row.delta_cents
                                    != row.balance_after_cents
                                    or (
                                        index > 1
                                        and row.balance_before_cents
                                        != rows[index - 2].balance_after_cents
                                    )
                                ):
                                    raise _error("Independent integer ledger continuity failed")
                        detail = {
                            "postings": len(postings),
                            "ledgers": len(grouped),
                            "independent_integer_continuity": True,
                        }
                        passed = bool(postings)
                    elif name == "NO_LOSS_AUTOMATIC":
                        from app.services.execution import get_action
                        from app.services.execution_sources import read_execution_confirmation

                        actions = list(
                            session.scalars(
                                select(ActionPlan).where(ActionPlan.user_id == self.user_id)
                            )
                        )
                        priced = []
                        legacy: list[dict[str, Any]] = []
                        for original in actions:
                            modern = "execution" in original.request
                            recovery = "bank_request" in original.request
                            if modern == recovery:
                                raise _error("The original action protocol is absent or ambiguous")
                            if modern:
                                priced.append(
                                    get_action(session, self.user_id, original.id, _clock(now))
                                )
                            else:
                                receipt = session.scalar(
                                    select(ActionReceipt).where(
                                        ActionReceipt.user_id == self.user_id,
                                        ActionReceipt.action_plan_id == original.id,
                                    )
                                )
                                if receipt is not None:
                                    legacy.append(self._legacy_recovery(session, original.id, now))
                                elif original.status in {"SUCCEEDED", "RECONCILED"}:
                                    raise _error(
                                        "A completed legacy action is missing its original receipt"
                                    )
                                else:
                                    legacy.append(
                                        {
                                            "action_id": str(original.id),
                                            "status": original.status,
                                            "receipt": None,
                                            "verified": False,
                                        }
                                    )
                        losing = [
                            row
                            for row in priced
                            if row.effect.loss_cents > 0 or row.effect.fee_cents > 0
                        ]
                        passed = all(
                            row.autonomy_level == "ASK_ONCE"
                            and (
                                row.receipt is None
                                or (
                                    row.status in {"SUCCEEDED", "RECONCILED"}
                                    and read_execution_confirmation(
                                        session, row.effect, row.receipt.occurred_at
                                    )
                                    is not None
                                )
                            )
                            for row in losing
                        ) and all(
                            item["receipt"] is None
                            or (
                                item["verified"]
                                and item["receipt"]["fee_cents"] == 0
                                and item["receipt"]["loss_cents"] == 0
                            )
                            for item in legacy
                        )
                        detail = {
                            "loss_or_fee_action_ids": [str(row.action_id) for row in losing],
                            "actual_levels": [row.autonomy_level for row in losing],
                            "modern_actions": len(priced),
                            "legacy_actions": legacy,
                            "scope": (
                                "Every recorded executed action; pending legacy rows "
                                "have no verified executed receipt"
                            ),
                        }
                    elif name == "DEMO_ROUND_COMPLETED":
                        state = get_demo_state(session, self.user_id, _clock(now))
                        kinds = {row.event_kind for row in state.commands}
                        required = {
                            "CREATE_CAR_GOAL",
                            "SALARY_RECEIVED",
                            "LARGE_CONSUMPTION",
                            "AUTO_REDEEM",
                            "FIXED_EARLY_WITHDRAWAL",
                            "CHANGE_RENT",
                        }
                        passed = (
                            kinds == required
                            and len(state.commands) == 6
                            and all(row.status == "COMPLETED" for row in state.commands)
                        )
                        detail = {
                            "commands": [
                                {
                                    "id": str(row.command_id),
                                    "event": row.event_kind,
                                    "status": row.status,
                                    "receipts": [
                                        str(action.receipt.receipt_id)
                                        for action in row.actions
                                        if action.receipt
                                    ],
                                }
                                for row in state.commands
                            ]
                        }
                except PolicyLifecycleError as error:
                    detail = {"error_code": error.code, "message": error.message}
                results.append(
                    PropertyResult(
                        name=name,
                        passed=passed,
                        scope="Actual current epoch; unseen and FULL-family cases untested",
                        detail=detail,
                    )
                )
        return results

    def _legacy_recovery(self, session: Session, action_id: UUID, now: datetime) -> dict[str, Any]:
        from app.services.recovery_receipt_integrity import verify_recovery_receipt

        original = session.get(ActionPlan, action_id)
        request = session.scalar(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.user_id == self.user_id,
                SimulatedBankRedemption.action_plan_id == action_id,
            )
        )
        receipts = list(
            session.scalars(
                select(ActionReceipt).where(
                    ActionReceipt.user_id == self.user_id,
                    ActionReceipt.action_plan_id == action_id,
                )
            )
        )
        if (
            original is None
            or original.user_id != self.user_id
            or "bank_request" not in original.request
            or "execution" in original.request
            or request is None
            or len(receipts) != 1
        ):
            raise _error("The owned original legacy bank request and receipt are required")
        receipt = receipts[0]
        verify_recovery_receipt(session, request, receipt, _clock(now))
        raw = {column.name: getattr(receipt, column.name) for column in receipt.__table__.columns}
        return {
            "action_id": str(action_id),
            "bank_request_id": str(request.id),
            "bank_status": request.status,
            "receipt_id": str(receipt.id),
            "verified": True,
            "receipt": json.loads(json.dumps(raw, default=str)),
            "scope": (
                "Read-only original legacy recovery protocol; no permission "
                "or current ActionResponse synthesized"
            ),
        }

    def rpc(self, body: ScenarioRPC, now: datetime) -> dict[str, Any]:
        self._isolated()
        now = _clock(now)
        with audit_command_guard(self.engine, self.user_id):
            return self._rpc(body, now)

    def _rpc(self, body: ScenarioRPC, now: datetime) -> dict[str, Any]:
        with Session(self.engine) as session:
            self._epoch(session, body.expected_epoch_id)
        if body.operation == "ingest_goal_income":
            assert body.goal_id is not None
            from app.domain.external_bank_fact_types import ExternalFactRequest
            from app.services.external_bank_facts import ingest_external_fact
            from app.services.policy_lifecycle import is_version_authorized

            key = f"scenario-goal-income-v1:{body.expected_epoch_id}:{body.goal_id}"
            with Session(self.engine) as session:
                goal = session.get(Goal, body.goal_id)
                if (
                    goal is None
                    or goal.user_id != self.user_id
                    or not is_version_authorized(session, self.user_id, goal.policy_version_id, now)
                ):
                    raise _error("The original owned confirmed goal is required")
                cash = session.scalar(
                    select(Account)
                    .where(Account.user_id == self.user_id, Account.account_type == "CASH")
                    .order_by(Account.id)
                    .limit(1)
                )
                if cash is None:
                    raise _error("The original simulator cash account is required")
                previous = session.scalar(
                    select(ExternalBankFact).where(
                        ExternalBankFact.user_id == self.user_id,
                        ExternalBankFact.idempotency_key == key,
                    )
                )
                request = ExternalFactRequest(
                    user_id=self.user_id,
                    idempotency_key=key,
                    external_ref=key,
                    kind="INCOME",
                    account_id=cash.id,
                    amount_cents=200_000,
                    counterparty_ref="payroll",
                    occurred_at=previous.occurred_at if previous else now,
                )
            income_result = ingest_external_fact(self.engine, self.user_id, request, now)
            return {
                "goal_id": str(body.goal_id),
                "request": request.model_dump(mode="json"),
                "result": income_result.model_dump(mode="json"),
                "scope": (
                    "Fixed synthetic income through original independent bank; "
                    "no allocation, ownership or permission injected"
                ),
            }
        if body.operation == "verify_legacy_recovery":
            assert body.action_id is not None
            with Session(self.engine) as session, session.begin():
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                session.execute(text("SET TRANSACTION READ ONLY"))
                self._epoch(session, body.expected_epoch_id)
                return self._legacy_recovery(session, body.action_id, now)
        if body.operation == "verify_round":
            properties = self.check_properties(
                ["BANK_LEDGER_VALID", "AUDIT_VALID", "NO_LOSS_AUTOMATIC", "DEMO_ROUND_COMPLETED"],
                body.expected_epoch_id,
                now,
            )
            return {
                "properties": [row.model_dump(mode="json") for row in properties],
                "all_passed": all(row.passed for row in properties),
            }
        if body.operation == "snapshot":
            with Session(self.engine) as session, session.begin():
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                session.execute(text("SET TRANSACTION READ ONLY"))
                actual = {
                    table.name: [
                        dict(row)
                        for row in session.execute(select(table).order_by(table.c.id)).mappings()
                    ]
                    for table in Base.metadata.sorted_tables
                }
            encoded = json.loads(json.dumps(actual, default=str))
            return {
                "table_count": len(actual),
                "rows_by_table": {name: len(rows) for name, rows in actual.items()},
                "snapshot_sha256": configuration_hash(encoded),
                "scope": "23 business tables; coordinator records physical migration metadata",
            }
        if body.policy_id is None:
            raise _error("The original rent policy is required")
        from app.services.demo_console import get_demo_state
        from app.services.execution import prepare_action

        with Session(self.engine) as session:
            state = get_demo_state(session, self.user_id, now)
            user = session.get(User, self.user_id)
            if user is None:
                raise _error("The original simulator user is required")
            timezone = user.timezone
            rent = next(row for row in state.templates if row.kind == "RENT")
            if rent.status != "CONFIRMED" or rent.confirmed_policy_id != body.policy_id:
                raise _error("The fixture must bind the actual confirmed rent template")
        months = _periods(now, timezone)
        observations = [
            self.observe_recurring_payment(body.policy_id, period, now) for period in months
        ]
        prepared = prepare_action(
            self.engine,
            self.user_id,
            PrepareActionRequest(
                idempotency_key=f"scenario-rent-old:{body.expected_epoch_id}",
                intent=PaymentIntent(
                    kind="pay_recurring",
                    policy_id=body.policy_id,
                    period=now.astimezone(ZoneInfo(timezone)).strftime("%Y-%m"),
                ),
            ),
            now,
        )
        return {
            "action_id": str(prepared.action_id),
            "effect_hash": prepared.effect_hash,
            "status": prepared.status,
            "policy_version_id": str(prepared.effect.policy_version_id),
            "action": prepared.model_dump(mode="json"),
            "synthetic_bank_observations": observations,
            "scope": "Actual empty payment history observed; no payment or consent injected",
        }
