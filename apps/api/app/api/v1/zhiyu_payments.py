"""Current-period adapters over verified original recurring-payment commands.

Native command/preparation lookups keep NOT_FOUND_NOT_FINAL. An immutable server
intent pins the month before calling services which may commit before responding.
Neither discovery nor observation grants a bank permission.
"""

import json
from datetime import datetime
from typing import Any, Literal, Self, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.full_payment_permissions import ExecutorDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.full_models import FullPolicy
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Policy,
    PolicyVersion,
    User,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.full_payment_permissions import (
    SOURCE,
    Key,
    PaymentActionConfirmation,
    PaymentConfirmRequest,
    PaymentExecuteRequest,
    PaymentPrepareRequest,
    PaymentRelationScope,
    PaymentScopeRequest,
    PaymentStartRequest,
    clock_utc,
    original_payment_action_key,
    payment_command_identity,
    require_payment_principal,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.action_contracts import PaymentIntent
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.boundary import SETTLEMENT_SOURCE
from app.services.demo_console import _epoch
from app.services.execution import get_action
from app.services.full_payment_permissions import (
    BINDING_SOURCE,
    PREPARE_SOURCE,
    PaymentActionBinding,
    PaymentCommandOriginal,
    confirm_full_payment_action,
    confirm_payment_relation,
    execute_full_payment,
    prepare_full_payment,
    preview_payment_relation,
    read_full_payment_action,
    read_payment_action_consent,
    read_payment_command,
    read_prepared_payment,
    start_payment_relation,
)
from app.services.full_payment_permissions import (
    _original as read_authorization_original,
)
from app.services.full_payment_permissions import (
    _preparation_original as read_preparation_original,
)
from app.services.full_payment_permissions import (
    _verify_effect as verify_original_payment_effect,
)
from app.services.historical_read import historical_ledger_scope, verify_historical_ledger
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import ScenarioRunner
from app.services.zhiyu_orchestration import (
    MARKER,
    operation,
    records,
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
    store,
)
from app.services.zhiyu_policy_catalog import list_catalog_policies
from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PREFIX = "/api/v1/zhiyu-next/payments"
OBSERVE_PATH = PREFIX + "/observe-current-period"
MAX_DISCOVERY_ROWS = 1000
router = APIRouter(prefix=PREFIX, tags=["知余必要付款确认"], dependencies=[Depends(isolated)])


class ObserveRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    original_policy_id: UUIDReference
    client_request_id: UUIDReference


class CurrentPeriodPrepareRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    idempotency_key: Key

    @model_validator(mode="after")
    def nonblank_key(self) -> Self:
        if not self.idempotency_key.strip():
            raise ValueError("An original nonblank preparation key is required")
        return self


class NativeRejectionProof(BoundaryModel):
    protocol: Literal["zhiyu-next-payment-no-native-acceptance-v1"] = (
        "zhiyu-next-payment-no-native-acceptance-v1"
    )
    epoch_id: UUID
    user_id: UUID
    checked_at: datetime
    audit_chain_status: Literal["VALID"] = "VALID"
    requested_native_acceptance_absent: Literal[True] = True


class ServerPaymentIntent(BoundaryModel):
    protocol: Literal["zhiyu-next-payment-intent-v1"] = "zhiyu-next-payment-intent-v1"
    kind: Literal["OBSERVE", "PREPARE"]
    original_request: dict[str, Any]
    server_period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    principal_at_capture: LocalActorPrincipal
    captured_at: datetime
    original_version_id: UUID | None = None
    original_payee_id: str | None = None
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def verified_user_capture(self) -> Self:
        require_payment_principal(
            self.principal_at_capture,
            self.principal_at_capture.user_id,
            self.captured_at,
            {"USER"},
        )
        PaymentPrepareRequest(
            expected_epoch_id=UUID(int=0), period=self.server_period, idempotency_key="validate"
        )
        if set(self.original_request) != {"kind", "path", "body"}:
            raise ValueError("The exact wrapper path and body must be retained")
        if self.kind == "OBSERVE":
            if self.original_version_id is None or not self.original_payee_id:
                raise ValueError("Observation must pin the actual original payee version")
            if (
                self.original_request["kind"] != "PAYMENT_OBSERVATION"
                or self.original_request["path"] != OBSERVE_PATH
            ):
                raise ValueError("Observation must retain its exact native wrapper")
            ObserveRequest.model_validate_json(json.dumps(self.original_request["body"]))
        elif self.original_version_id is not None or self.original_payee_id is not None:
            raise ValueError("Preparation must retain its native authorization instead")
        else:
            path = self.original_request["path"]
            authorization_id = UUID(
                path.removeprefix(PREFIX + "/authorizations/").removesuffix("/prepare")
            )
            if (
                self.original_request["kind"] != "PAYMENT_PREPARE"
                or path != PREFIX + f"/authorizations/{authorization_id}/prepare"
            ):
                raise ValueError("Preparation must retain its exact original authorization path")
            CurrentPeriodPrepareRequest.model_validate_json(
                json.dumps(self.original_request["body"])
            )
        return self


def _error(message: str, code: str = "PAYMENT_SOURCE_UNKNOWN") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _user_actor(actor: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    try:
        require_payment_principal(actor, user_id, clock_utc(now), {"USER"})
    except ValueError:
        raise PolicyLifecycleError(
            "PAYMENT_ROLE_NOT_AUTHORIZED", "需要当前真实USER会话", 403
        ) from None


def _period(session: Session, user_id: UUID, now: datetime) -> str:
    owner = session.get(User, user_id)
    if owner is None or not owner.is_simulated or owner.timezone not in {"UTC", "Asia/Shanghai"}:
        raise _error("当前模拟用户或时区不可核实")
    return clock_utc(now).astimezone(ZoneInfo(owner.timezone)).strftime("%Y-%m")


def _request(kind: str, path: str, body: BoundaryModel) -> dict[str, Any]:
    return {"kind": kind, "path": path, "body": body.model_dump(mode="json")}


def _event_key(kind: str, key: str) -> str:
    return f"payment-intent:{kind}:{key}"


def _wrapper_id(epoch: UUID, path: str, key: str) -> UUID:
    # START and CONFIRM share the original native command-key namespace.
    namespace_path = PREFIX + "/relation" if path.startswith(PREFIX + "/relation") else path
    return uuid5(epoch, f"zhiyu-next-payment-wrapper:{namespace_path}:{key}")


def _native_present(session: Session, user_id: UUID, original: dict[str, Any]) -> bool:
    """Any partial native acceptance blocks a terminal wrapper rejection."""
    kind, body = original["kind"], original["body"]
    epoch, key = UUID(body["expected_epoch_id"]), body["idempotency_key"]
    if kind in {"PAYMENT_START", "PAYMENT_CONFIRM"}:
        command_id = payment_command_identity(user_id, epoch, key)
        return (
            session.get(EvidenceItem, uuid5(command_id, "evidence")) is not None
            or session.get(DecisionRun, command_id) is not None
        )
    if kind != "PAYMENT_PREPARE":
        raise _error("拒绝证明不支持该付款原请求", "REQUEST_SCOPE_MISMATCH")
    path = original["path"]
    authorization_id = UUID(path.removeprefix(PREFIX + "/authorizations/").removesuffix("/prepare"))
    bank_key = original_payment_action_key(authorization_id, key)
    prepare_id = uuid5(user_id, "full-payment-prepare-original:" + bank_key)
    if (
        session.get(EvidenceItem, prepare_id) is not None
        or session.get(DecisionRun, uuid5(prepare_id, "trace")) is not None
    ):
        return True
    return any(
        session.scalar(query) is not None
        for query in (
            select(ActionPlan.id).where(
                ActionPlan.user_id == user_id, ActionPlan.idempotency_key == bank_key
            ),
            select(DecisionRun.id).where(
                DecisionRun.user_id == user_id, DecisionRun.idempotency_key == bank_key
            ),
            select(BankOperation.id).where(
                BankOperation.user_id == user_id, BankOperation.idempotency_key == bank_key
            ),
            select(EvidenceItem.id).where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type.in_([BINDING_SOURCE, PREPARE_SOURCE]),
                EvidenceItem.content["authorization_id"].as_string() == str(authorization_id),
                (
                    EvidenceItem.content["original_prepare_request"]["idempotency_key"].as_string()
                    == key
                )
                | (EvidenceItem.content["request"]["idempotency_key"].as_string() == key),
            ),
        )
    )


def _check_wrapper_rejection(session: Session, user_id: UUID, original: dict[str, Any]) -> None:
    epoch = _epoch(session, user_id, UUID(original["body"]["expected_epoch_id"]))
    request_id = _wrapper_id(epoch, original["path"], original["body"]["idempotency_key"])
    actual = operation(session, user_id, request_id)
    if actual is None:
        return
    row = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:OPERATION:{request_id}"))
    assert row is not None
    if row.content["payload"]["request"] != original:
        raise _error("同一原付款请求不能替换内容", "REQUEST_CONFLICT")
    if actual["status"] != "REJECTED" or _native_present(session, user_id, original):
        raise _error("原拒绝证明与付款原件不匹配")
    error = actual["result"]["error"]
    raise PolicyLifecycleError(error["code"], error["message"], error["status_code"])


def _reject_uncommitted(
    engine: Engine,
    user_id: UUID,
    original: dict[str, Any],
    error: PolicyLifecycleError,
    now: datetime,
) -> None:
    # The caller holds serial_user/audit_command_guard across this proof and write.
    with Session(engine) as reader, reader.begin():
        reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        reader.execute(text("SET TRANSACTION READ ONLY"))
        with historical_ledger_scope(reader), audit_read_scope(reader):
            epoch = _epoch(reader, user_id, UUID(original["body"]["expected_epoch_id"]))
            if _native_present(reader, user_id, original):
                return
            if verify_audit_chain(reader, user_id).status != "VALID":
                return
            verify_historical_ledger(reader, user_id)
    request_id = _wrapper_id(epoch, original["path"], original["body"]["idempotency_key"])
    proof = NativeRejectionProof(epoch_id=epoch, user_id=user_id, checked_at=clock_utc(now))
    with Session(engine) as writer, writer.begin():
        if operation(writer, user_id, request_id) is None:
            store(
                writer,
                user_id,
                "OPERATION",
                str(request_id),
                {
                    "request": original,
                    "operation_status": "REJECTED",
                    "result": {
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "status_code": error.status_code,
                        },
                        "rejection_proof": proof.model_dump(mode="json"),
                        "bank_authority": False,
                    },
                },
                now,
            )


def _wrapper_side(
    session: Session, user_id: UUID, epoch: UUID, path: str, key: str, now: datetime
) -> dict[str, Any] | None:
    request_id = _wrapper_id(epoch, path, key)
    actual = operation(session, user_id, request_id)
    if actual is None:
        return None
    row = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:OPERATION:{request_id}"))
    assert row is not None
    original = row.content["payload"]["request"]
    if (
        actual["status"] != "REJECTED"
        or (
            original.get("path") != path
            and not (
                path == PREFIX + "/relation"
                and original.get("kind") in {"PAYMENT_START", "PAYMENT_CONFIRM"}
            )
        )
        or original.get("body", {}).get("idempotency_key") != key
        or original["body"].get("expected_epoch_id") != str(epoch)
        or _native_present(session, user_id, original)
    ):
        raise _error("原拒绝证明不能替换已有的付款原件", "REQUEST_SCOPE_MISMATCH")
    verify_historical_ledger(session, user_id)
    result = actual["result"]
    raw_proof = result.get("rejection_proof")
    if (
        not isinstance(raw_proof, dict)
        or set(raw_proof) != set(NativeRejectionProof.model_fields)
        or raw_proof.get("requested_native_acceptance_absent") is not True
    ):
        raise _error("原拒绝证明缺少已核实的未提交边界")
    try:
        proof = NativeRejectionProof.model_validate_json(json.dumps(raw_proof))
    except (ValueError, TypeError) as error:
        raise _error("原拒绝证明缺少已核实的未提交边界") from error
    if (
        proof.epoch_id != epoch
        or proof.user_id != user_id
        or proof.checked_at != row.created_at
        or proof.checked_at != row.observed_at
        or proof.checked_at > clock_utc(now)
        or result.get("bank_authority") is not False
    ):
        raise _error("原拒绝证明缺少已核实的未提交边界")
    return {
        "status": "REJECTED",
        "original_request": {"path": original["path"], "body": original["body"]},
        **result,
    }


def _read_intent(
    session: Session, user_id: UUID, kind: str, key: str, original: dict[str, Any]
) -> ServerPaymentIntent | None:
    rows = records(session, user_id, "EVENT")
    if len(rows) > 10000:
        raise _error("服务器请求历史超过读取容量", "INPUT_LIMIT_EXCEEDED")
    matching = [row for row in rows if row.content["key"] == _event_key(kind, key)]
    if not matching:
        return None
    try:
        payload = matching[0].content["payload"]
        if (
            not isinstance(payload, dict)
            or set(payload) != set(ServerPaymentIntent.model_fields)
            or payload.get("bank_authority") is not False
        ):
            raise _error("服务器原付款请求字段不完整")
        actual = ServerPaymentIntent.model_validate_json(json.dumps(payload))
        if (
            actual.kind != kind
            or actual.principal_at_capture.user_id != user_id
            or actual.original_request != original
        ):
            raise _error("同一原付款请求不能替换路径、内容或所有者", "REQUEST_CONFLICT")
        return actual
    except PolicyLifecycleError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise _error("服务器原付款请求尚无法核实") from error


def _capture_intent(
    engine: Engine,
    user_id: UUID,
    kind: Literal["OBSERVE", "PREPARE"],
    key: str,
    original: dict[str, Any],
    actor: LocalActorPrincipal,
    now: datetime,
) -> ServerPaymentIntent:
    _user_actor(actor, user_id, now)
    with Session(engine) as session, session.begin():
        _epoch(session, user_id, UUID(original["body"]["expected_epoch_id"]))
        if verify_audit_chain(session, user_id).status != "VALID":
            raise _error("服务器原付款请求审计无法核实")
        prior = _read_intent(session, user_id, kind, key, original)
        if prior is not None:
            return prior
        version_id, payee_id = None, None
        if kind == "OBSERVE":
            policy_id = UUID(original["body"]["original_policy_id"])
            version = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == policy_id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            if version is None or version.configuration.get("type") != "recurring_obligation":
                raise _error("需要原周期义务版本")
            payee_id = version.configuration["payee_id"]
            version_id = version.id
        actual = ServerPaymentIntent(
            kind=kind,
            original_request=original,
            server_period=_period(session, user_id, now),
            principal_at_capture=actor,
            captured_at=now,
            original_version_id=version_id,
            original_payee_id=payee_id,
        )
        store(
            session,
            user_id,
            "EVENT",
            _event_key(kind, key),
            actual.model_dump(mode="json"),
            now,
        )
        return actual


def _original_observation(
    session: Session,
    user_id: UUID,
    body: ObserveRequest,
    intent: ServerPaymentIntent,
    now: datetime,
) -> dict[str, Any] | None:
    """Verify the committed initial observation, never infer current paid=0 from it."""
    proof_id = uuid5(
        body.original_policy_id, f"scenario-empty-bank-payment-v1:{intent.server_period}"
    )
    proof = session.get(EvidenceItem, proof_id)
    if proof is None:
        return None
    if not isinstance(proof.observed_at, datetime):
        raise _error("原初始银行观察时间不可核实")
    version = session.get(PolicyVersion, intent.original_version_id)
    if (
        version is None
        or version.user_id != user_id
        or version.policy_id != body.original_policy_id
        or version.content_hash != configuration_hash(version.configuration)
        or version.configuration.get("payee_id") != intent.original_payee_id
    ):
        raise _error("原观察绑定策略版本不可核实")
    expected = {
        "simulation": True,
        "protocol": "recurring-settlement-v1",
        "user_id": str(user_id),
        "policy_id": str(body.original_policy_id),
        "period": intent.server_period,
        "paid_cents": 0,
        "payee_id": intent.original_payee_id,
        "complete": True,
        "as_of": proof.observed_at.isoformat(),
        "observation_protocol": "scenario-empty-bank-payment-v1",
    }
    if (
        proof.user_id != user_id
        or proof.source_type != SETTLEMENT_SOURCE
        or proof.source_ref
        != f"scenario-empty-bank-payment-v1:{body.original_policy_id}:{intent.server_period}"
        or proof.status not in {"VALID", "SUPERSEDED"}
        or proof.evidence_level != "BANK_CONFIRMED"
        or proof.content_hash != configuration_hash(proof.content)
        or proof.created_at != proof.observed_at
        or proof.valid_from != proof.observed_at
        or proof.valid_to is not None
        or not proof.observed_at <= clock_utc(now)
        or proof.content != expected
        or type(proof.content.get("paid_cents")) is not int
        or proof.content.get("simulation") is not True
        or proof.content.get("complete") is not True
    ):
        raise _error("原初始银行观察证据不可核实")
    return {
        "evidence_id": str(proof.id),
        "content_hash": proof.content_hash,
        "observation": proof.content,
    }


def _observation_request(session: Session, user_id: UUID, request_id: UUID) -> dict[str, Any]:
    row = session.get(
        EvidenceItem, uuid5(_epoch(session, user_id), f"{MARKER}:OPERATION:{request_id}")
    )
    if row is None:
        raise _error("原观察请求不存在")
    original: dict[str, Any] = row.content["payload"]["request"]
    if original.get("kind") != "PAYMENT_OBSERVATION" or original.get("path") != OBSERVE_PATH:
        raise _error("原请求不是本期付款观察", "REQUEST_SCOPE_MISMATCH")
    return {"path": original["path"], "body": original["body"]}


@router.post("/observe-current-period")
def observe(
    body: ObserveRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    original = _request("PAYMENT_OBSERVATION", OBSERVE_PATH, body)
    intent = None
    with serial_user(engine, user.id):
        _user_actor(actor, user.id, now)
        with Session(engine) as reader:
            _epoch(reader, user.id, body.expected_epoch_id)
            prior = replay_operation(reader, user.id, body.client_request_id, original)
            if prior is not None:
                return {**identity(session, user.id, engine), **prior}
        try:
            intent = _capture_intent(
                engine, user.id, "OBSERVE", str(body.client_request_id), original, actor, now
            )
            with Session(engine) as reader:
                actual = _original_observation(reader, user.id, body, intent, now)
            if actual is None:
                ScenarioRunner(engine, user.id).observe_recurring_payment(
                    body.original_policy_id, intent.server_period, now
                )
                with Session(engine) as reader:
                    actual = _original_observation(reader, user.id, body, intent, now)
            if actual is None:
                raise _error("已请求原观察但结果尚无法核实，请保留原请求")
            result = {
                "server_period": intent.server_period,
                "original_observation": actual,
                "bank_authority": False,
                "transfers_funds": False,
            }
            with Session(engine) as writer, writer.begin():
                remember_operation(writer, user.id, body.client_request_id, original, result, now)
            return {**identity(session, user.id, engine), **result}
        except PolicyLifecycleError as error:
            # A committed fact or a failed read never becomes a fictitious rejection.
            if intent is not None:
                proof_id = uuid5(
                    body.original_policy_id,
                    f"scenario-empty-bank-payment-v1:{intent.server_period}",
                )
                with Session(engine) as reader:
                    absent = reader.get(EvidenceItem, proof_id) is None
                if absent:
                    remember_rejection(
                        engine, user.id, body.client_request_id, original, error, now
                    )
            raise


@router.get("/observations/{client_request_id}")
def observation_original(
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = operation(session, user.id, client_request_id)
        if actual is None:
            return {
                **identity(session, user.id, engine),
                "client_request_id": str(client_request_id),
                "status": "NOT_FOUND_NOT_FINAL",
                "replacement_allowed": False,
            }
        original = _observation_request(session, user.id, client_request_id)
        if actual["status"] == "COMPLETED":
            body = ObserveRequest.model_validate_json(json.dumps(original["body"]))
            full_request = {"kind": "PAYMENT_OBSERVATION", **original}
            intent = _read_intent(session, user.id, "OBSERVE", str(client_request_id), full_request)
            if intent is None:
                raise _error("原观察缺少服务器月份请求")
            proof = _original_observation(session, user.id, body, intent, now)
            if (
                proof is None
                or actual["result"].get("server_period") != intent.server_period
                or actual["result"].get("original_observation") != proof
            ):
                raise _error("原观察结果与真实初始银行原件不匹配")
        return {
            **identity(session, user.id, engine),
            **actual,
            "original_request": original,
            "replacement_allowed": False,
        }


def _label(
    session: Session,
    model: type[Account] | type[Policy] | type[FullPolicy],
    row_id: UUID,
    user_id: UUID,
) -> str:
    row = cast(Account | Policy | FullPolicy | None, session.get(model, row_id))
    if row is None or row.user_id != user_id:
        raise _error("付款原件标签的所有者不可核实")
    return row.name


def _source_labels(session: Session, scope: PaymentRelationScope, user_id: UUID) -> dict[str, str]:
    # A historical receipt keeps its own payee identity even if newer sources changed.
    return {
        "original_name": _label(session, Policy, scope.original_policy_id, user_id),
        "payee_name": "已核实固定收款对象",
        "source_account_name": _label(session, Account, scope.source_account_id, user_id),
    }


@router.get("/state")
def state(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        epoch = _epoch(session, user.id)
        if verify_audit_chain(session, user.id).status != "VALID":
            raise _error("付款发现来源审计无法核实")
        items = list_catalog_policies(session, user.id, now).items
        fulls = [
            item
            for item in items
            if item.template_name == "PeriodicTransferPolicy"
            and item.source_kind == "FULL_POLICY"
            and item.effective_status == "ACTIVE"
        ]
        originals = [
            item
            for item in items
            if item.template_name == "RecurringObligationPolicy"
            and item.source_kind == "MVP_POLICY"
            and item.effective_status == "ACTIVE"
            and item.planning_confirmation_valid
        ]
        if len(fulls) * len(originals) > MAX_DISCOVERY_ROWS:
            raise _error("付款配对超过读取容量", "INPUT_LIMIT_EXCEEDED")
        pairs: list[dict[str, Any]] = []
        for full in fulls:
            for native in originals:
                try:
                    preview = preview_payment_relation(
                        session,
                        user.id,
                        PaymentScopeRequest(
                            expected_epoch_id=epoch,
                            full_policy_id=full.policy_id,
                            expected_full_version_id=full.current_version_id,
                            original_policy_id=native.policy_id,
                            expected_original_version_id=native.current_version_id,
                        ),
                        now,
                    )
                except PolicyLifecycleError as error:
                    if error.code in {
                        "PAYMENT_POLICY_MAPPING_MISMATCH",
                        "STALE_FULL_PAYMENT_POLICY",
                        "STALE_ORIGINAL_PAYMENT_POLICY",
                        "PAYMENT_SCOPE_UNBOUNDED",
                        "PAYMENT_SCOPE_OUTSIDE_VALIDITY",
                    }:
                        continue
                    raise
                scope = preview.scope
                pairs.append(
                    {
                        "full_policy_id": str(scope.full_policy_id),
                        "full_version_id": str(scope.full_version_id),
                        "full_configuration_hash": scope.full_configuration_hash,
                        "full_name": _label(session, FullPolicy, scope.full_policy_id, user.id),
                        "original_policy_id": str(scope.original_policy_id),
                        "original_version_id": str(scope.original_version_id),
                        "original_configuration_hash": scope.original_configuration_hash,
                        **_source_labels(session, scope, user.id),
                    }
                )
        proofs = list(
            session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.user_id == user.id,
                    EvidenceItem.source_type.in_([SOURCE, BINDING_SOURCE]),
                )
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
                .limit(MAX_DISCOVERY_ROWS + 1)
            )
        )
        if len(proofs) > MAX_DISCOVERY_ROWS:
            raise _error("付款原件超过读取容量", "INPUT_LIMIT_EXCEEDED")
        relations: list[dict[str, Any]] = []
        prepared: list[dict[str, Any]] = []
        period = _period(session, user.id, now)
        for proof in proofs:
            if proof.source_type == SOURCE:
                locator = PaymentCommandOriginal.model_validate_json(json.dumps(proof.content))
                if locator.epoch_id != epoch:
                    continue
                actual = read_payment_command(session, user.id, epoch, locator.idempotency_key, now)
                if actual.original is None:
                    raise _error("付款原命令来源丢失")
                receipt = actual.original
                if receipt.evidence_id != proof.id:
                    raise _error("付款目录不能混入替换的原命令来源")
                relations.append(
                    {
                        "receipt": receipt.model_dump(mode="json"),
                        "full_name": _label(
                            session, FullPolicy, receipt.original.scope.full_policy_id, user.id
                        ),
                        **_source_labels(session, receipt.original.scope, user.id),
                    }
                )
            else:
                binding = PaymentActionBinding.model_validate_json(json.dumps(proof.content))
                if binding.epoch_id != epoch:
                    continue
                actual_prepared = read_prepared_payment(
                    session,
                    user.id,
                    binding.authorization_id,
                    binding.original_prepare_request.idempotency_key,
                    now,
                )
                if (
                    actual_prepared.original_binding != binding
                    or actual_prepared.original_action is None
                ):
                    raise _error("付款原准备来源丢失或改变")
                if binding.period == period or actual_prepared.original_action.status in {
                    "PLANNED",
                    "AUTHORIZED",
                    "SUBMITTED",
                    "UNKNOWN",
                }:
                    prepared.append(actual_prepared.model_dump(mode="json"))
        return {
            **identity(session, user.id, engine),
            "server_period": period,
            "pairs": pairs,
            "relations": relations,
            "prepared": prepared,
            "observation_available": True,
            "bank_authority": False,
        }


@router.post("/relation/preview")
def preview(
    body: PaymentScopeRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_payment_relation(session, user.id, body, now)
        return {**identity(session, user.id, engine), "preview": actual.model_dump(mode="json")}


@router.post("/relation/start")
def start(
    body: PaymentStartRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    original = _request("PAYMENT_START", PREFIX + "/relation/start", body)
    with serial_user(engine, user.id):
        _user_actor(actor, user.id, now)
        with Session(engine) as reader:
            _check_wrapper_rejection(reader, user.id, original)
        try:
            actual = start_payment_relation(engine, user.id, body, actor, now)
        except PolicyLifecycleError as error:
            _reject_uncommitted(engine, user.id, original, error, now)
            raise
        return {**identity(session, user.id, engine), "command": actual.model_dump(mode="json")}


@router.post("/relation/{start_command_id}/confirm")
def confirm_relation(
    start_command_id: UUID,
    body: PaymentConfirmRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    original = _request("PAYMENT_CONFIRM", PREFIX + f"/relation/{start_command_id}/confirm", body)
    with serial_user(engine, user.id):
        _user_actor(actor, user.id, now)
        with Session(engine) as reader:
            _check_wrapper_rejection(reader, user.id, original)
        try:
            actual = confirm_payment_relation(engine, user.id, start_command_id, body, actor, now)
        except PolicyLifecycleError as error:
            _reject_uncommitted(engine, user.id, original, error, now)
            raise
        return {**identity(session, user.id, engine), "command": actual.model_dump(mode="json")}


@router.get("/commands/{epoch_id}/by-key/{key}")
def relation_original(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        _epoch(session, user.id, epoch_id)
        actual = read_payment_command(session, user.id, epoch_id, key, now)
        return {
            **identity(session, user.id, engine),
            **actual.model_dump(mode="json"),
            "wrapper_operation": _wrapper_side(
                session, user.id, epoch_id, PREFIX + "/relation", key, now
            ),
        }


@router.post("/authorizations/{authorization_id}/prepare")
def prepare(
    authorization_id: UUID,
    body: CurrentPeriodPrepareRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    path = PREFIX + f"/authorizations/{authorization_id}/prepare"
    original = _request("PAYMENT_PREPARE", path, body)
    with serial_user(engine, user.id):
        _user_actor(actor, user.id, now)
        with Session(engine) as reader:
            _check_wrapper_rejection(reader, user.id, original)
        intent = _capture_intent(
            engine,
            user.id,
            "PREPARE",
            f"{authorization_id}:{body.idempotency_key}",
            original,
            actor,
            now,
        )
        native = PaymentPrepareRequest(
            expected_epoch_id=body.expected_epoch_id,
            period=intent.server_period,
            idempotency_key=body.idempotency_key,
        )
        try:
            actual = prepare_full_payment(engine, user.id, authorization_id, native, actor, now)
        except PolicyLifecycleError as error:
            _reject_uncommitted(engine, user.id, original, error, now)
            raise
        return {
            **identity(session, user.id, engine),
            "server_period": intent.server_period,
            "action": actual.model_dump(mode="json"),
        }


@router.get("/authorizations/{authorization_id}/prepared/by-key/{key}")
def prepared_original(
    authorization_id: UUID,
    key: Key,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        try:
            actual = read_prepared_payment(session, user.id, authorization_id, key, now)
        except PolicyLifecycleError as error:
            if error.code != "NOT_FOUND" or error.status_code != 404:
                raise
            partial = partial_preparation_original(
                session, user.id, authorization_id, key, now
            )
            if partial is None:
                raise
            return {**identity(session, user.id, engine), **partial}
        return {
            **identity(session, user.id, engine),
            **actual.model_dump(mode="json"),
            "wrapper_operation": _wrapper_side(
                session,
                user.id,
                _epoch(session, user.id),
                PREFIX + f"/authorizations/{authorization_id}/prepare",
                key,
                now,
            ),
        }


def partial_preparation_original(
    session: Session, user_id: UUID, authorization_id: UUID, key: Key, now: datetime
) -> dict[str, Any] | None:
    """Read the actual prepare-commit seam; this never creates a binding or bank grant."""
    row = session.scalar(
        select(ActionPlan).where(
            ActionPlan.user_id == user_id,
            ActionPlan.idempotency_key == original_payment_action_key(authorization_id, key),
        )
    )
    if row is None:
        return None
    if session.get(EvidenceItem, uuid5(row.id, "full-payment-binding")) is not None:
        # Present-but-invalid native evidence must keep its original integrity error.
        return None
    epoch_id = _epoch(session, user_id)
    path = PREFIX + f"/authorizations/{authorization_id}/prepare"
    wrapper = CurrentPeriodPrepareRequest(expected_epoch_id=epoch_id, idempotency_key=key)
    original = _request("PAYMENT_PREPARE", path, wrapper)
    intent = _read_intent(
        session, user_id, "PREPARE", f"{authorization_id}:{key}", original
    )
    if intent is None:
        raise _error("原部分准备缺少已审计的服务器月份请求")
    native = PaymentPrepareRequest(
        expected_epoch_id=epoch_id, period=intent.server_period, idempotency_key=key
    )
    preparation = read_preparation_original(session, user_id, authorization_id, native, now)
    authorization = read_authorization_original(
        session, user_id, authorization_id, now, replay=True
    )
    if (
        preparation is None
        or authorization is None
        or authorization.original.kind != "CONFIRM"
        or preparation.epoch_id != epoch_id
        or authorization.original.scope.epoch_id != epoch_id
        or preparation.authorization_evidence_hash != authorization.evidence_hash
        or row.user_id != user_id
        or row.action_type != "PAY_RECURRING"
        or row.idempotency_key != preparation.full_payment_action_key
        or row.request.get("intent")
        != PaymentIntent(
            kind="pay_recurring",
            policy_id=authorization.original.scope.original_policy_id,
            period=intent.server_period,
        ).model_dump(mode="json")
        or row.request_hash != configuration_hash(row.request)
    ):
        raise _error("原部分动作与已验真准备/确认范围不一致")
    actual = get_action(session, user_id, row.id, now)
    verify_original_payment_effect(
        session, authorization.original.scope, actual.effect, now, current=False
    )
    no_bank = (
        actual.bank_status is None
        and actual.receipt is None
        and session.scalar(
            select(BankOperation.id).where(BankOperation.action_plan_id == row.id)
        )
        is None
        and session.scalar(
            select(ActionReceipt.id).where(ActionReceipt.action_plan_id == row.id)
        )
        is None
    )
    continuation = {"path": path, "body": wrapper.model_dump(mode="json")}
    return {
        "simulation": True,
        "user_id": str(user_id),
        "authorization_id": str(authorization_id),
        "idempotency_key": key,
        "status": "PARTIAL_BINDING_NOT_FINAL",
        "partial_protocol": "zhiyu-next-payment-partial-preparation-v1",
        "original_binding": None,
        "original_action": actual.model_dump(mode="json"),
        "original_preparation": preparation.model_dump(mode="json"),
        "original_authorization": authorization.model_dump(mode="json"),
        "original_request": continuation,
        "server_period": intent.server_period,
        "replacement_allowed": False,
        "bank_execute_allowed": False,
        "bank_authority": False,
        "continuation_available": no_bank and actual.status in {"PLANNED", "AUTHORIZED"},
        "continue_original_preparation": continuation,
        "wrapper_operation": None,
    }


@router.get("/actions/{action_id}")
def action_original(
    action_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_full_payment_action(session, user.id, action_id, now)
        return {**identity(session, user.id, engine), "action": actual.model_dump(mode="json")}


@router.get("/actions/{action_id}/user-consent")
def consent_original(
    action_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_payment_action_consent(session, user.id, action_id, now)
        return {**identity(session, user.id, engine), **actual.model_dump(mode="json")}


@router.post("/actions/{action_id}/confirm-and-execute")
def confirm_and_execute(
    action_id: UUID,
    body: PaymentActionConfirmation,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
    executor: ExecutorDependency,
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        # Consent may commit before execute fails: independent native GET recovers it.
        confirm_full_payment_action(engine, user.id, action_id, body, actor, now)
        actual = execute_full_payment(
            engine,
            user.id,
            action_id,
            PaymentExecuteRequest(expected_epoch_id=body.expected_epoch_id),
            actor,
            now,
            guarded_executor=executor,
        )
        return {**identity(session, user.id, engine), "action": actual.model_dump(mode="json")}


@router.post("/actions/{action_id}/execute-original")
def execute_original(
    action_id: UUID,
    body: PaymentExecuteRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
    executor: ExecutorDependency,
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        actual = execute_full_payment(
            engine,
            user.id,
            action_id,
            body,
            actor,
            now,
            guarded_executor=executor,
        )
        return {**identity(session, user.id, engine), "action": actual.model_dump(mode="json")}
