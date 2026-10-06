import json
import logging
from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint

from app.api.errors import (
    ErrorEnvelope,
    error_response,
    http_error_handler,
    validation_error_handler,
)
from app.api.v1.accounts import router as account_router
from app.api.v1.actions import router as action_router
from app.api.v1.assets import router as asset_router
from app.api.v1.audit import router as audit_router
from app.api.v1.autonomy_envelope import router as autonomy_envelope_router
from app.api.v1.boundary import router as boundary_router
from app.api.v1.boundary_action_events import router as boundary_action_events_router
from app.api.v1.boundary_difference import router as boundary_difference_router
from app.api.v1.calendar_periodic_suggestions import router as calendar_periodic_suggestion_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.decisions import router as decision_router
from app.api.v1.delivery import router as delivery_router
from app.api.v1.demo import router as demo_router
from app.api.v1.dynamic_goal_reserve import router as dynamic_goal_reserve_router
from app.api.v1.evidence import router as evidence_router
from app.api.v1.finite_uncertainty import router as finite_uncertainty_router
from app.api.v1.full_action_set_boundary import router as full_action_set_boundary_router
from app.api.v1.full_action_set_boundary_actual import (
    router as full_action_set_boundary_actual_router,
)
from app.api.v1.full_action_set_boundary_composed import (
    router as full_action_set_boundary_composed_router,
)
from app.api.v1.full_action_set_boundary_full import router as full_action_set_boundary_full_router
from app.api.v1.full_action_set_boundary_recovery_composed import (
    router as full_action_set_boundary_recovery_composed_router,
)
from app.api.v1.full_action_set_boundary_registered import (
    router as full_action_set_boundary_registered_router,
)
from app.api.v1.full_action_set_payment_producers import router as full_periodic_producer_router
from app.api.v1.full_asset_allocation import router as full_asset_allocation_router
from app.api.v1.full_asset_execution import router as full_asset_execution_router
from app.api.v1.full_decision_search import router as full_decision_search_router
from app.api.v1.full_dynamic_goal_execution import router as full_dynamic_goal_execution_router
from app.api.v1.full_evidence_graph import router as full_evidence_graph_router
from app.api.v1.full_goal_adjustments import router as full_goal_adjustment_router
from app.api.v1.full_goal_commands import router as full_goal_command_router
from app.api.v1.full_goal_conflicts import router as full_goal_conflict_router
from app.api.v1.full_goal_reallocation import router as full_goal_reallocation_router
from app.api.v1.full_goal_release_authorization import (
    router as full_goal_release_authorization_router,
)
from app.api.v1.full_goal_release_execution import router as full_goal_release_execution_router
from app.api.v1.full_goals import router as full_goal_router
from app.api.v1.full_intervention import router as full_intervention_router
from app.api.v1.full_joint_goal_execution import router as full_joint_goal_execution_router
from app.api.v1.full_joint_goal_planning import router as full_joint_goal_router
from app.api.v1.full_maturity_execution import router as full_maturity_execution_router
from app.api.v1.full_maturity_replanning import router as full_maturity_replanning_router
from app.api.v1.full_payment_permissions import (
    get_guarded_payment_executor,
)
from app.api.v1.full_payment_permissions import (
    router as full_payment_relation_router,
)
from app.api.v1.full_policies import router as full_policy_router
from app.api.v1.full_policy_change_history import router as full_policy_history_router
from app.api.v1.full_policy_change_multi import router as full_policy_change_multi_router
from app.api.v1.full_policy_compilation import router as full_policy_compilation_router
from app.api.v1.full_policy_dependencies import router as full_policy_dependency_router
from app.api.v1.full_protection_projection import router as full_protection_router
from app.api.v1.full_reconciliation import router as full_reconciliation_router
from app.api.v1.full_recovery_execution import router as full_recovery_execution_router
from app.api.v1.full_recovery_next import router as full_recovery_next_router
from app.api.v1.full_recovery_planning import router as full_recovery_planning_router
from app.api.v1.full_seasonal_adoption import router as full_seasonal_adoption_router
from app.api.v1.future_income_planning import router as future_income_planning_router
from app.api.v1.goals import router as goal_router
from app.api.v1.living_reserve import router as reserve_router
from app.api.v1.local_actor_sessions import router as local_actor_session_router
from app.api.v1.planning import router as planning_router
from app.api.v1.policies import router as policy_router
from app.api.v1.policy_preview import router as policy_preview_router
from app.api.v1.policy_suggestions import router as policy_suggestion_router
from app.api.v1.policy_templates import router as policy_template_router
from app.api.v1.product_catalog import router as product_catalog_router
from app.api.v1.question_workflow import router as question_workflow_router
from app.api.v1.recovery import router as recovery_router
from app.api.v1.scenario_risk_review import router as scenario_risk_review_router
from app.api.v1.scenario_simulation import router as scenario_simulation_router
from app.api.v1.transaction_category import router as transaction_category_router
from app.api.v1.user_policy_declaration import router as user_policy_declaration_router
from app.services.policy_lifecycle import PolicyLifecycleError

logger = logging.getLogger("bounded_funds.http")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["bounded-funds-api"] = "bounded-funds-api"
    simulation: Literal[True] = True


def create_app() -> FastAPI:
    api = FastAPI(
        title="知余 · 模拟资金 API",
        version="0.1.0",
        responses={422: {"model": ErrorEnvelope}, 500: {"model": ErrorEnvelope}},
    )

    @api.middleware("http")
    async def request_tracking(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid4())
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            response = error_response(request, 500, "INTERNAL_ERROR", "服务暂时不可用")
        response.headers["x-request-id"] = request.state.request_id
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
        )
        return response

    api.add_exception_handler(HTTPException, http_error_handler)
    api.add_exception_handler(RequestValidationError, validation_error_handler)
    api.include_router(account_router)
    api.include_router(policy_router)
    api.include_router(policy_preview_router)
    api.include_router(policy_template_router)
    api.include_router(calendar_periodic_suggestion_router)
    api.include_router(reserve_router)
    api.include_router(boundary_router)
    api.include_router(goal_router)
    api.include_router(full_goal_router)
    api.include_router(full_joint_goal_router)
    api.include_router(full_joint_goal_execution_router)
    api.include_router(full_goal_command_router)
    api.include_router(full_goal_adjustment_router)
    api.include_router(full_goal_conflict_router)
    api.include_router(full_goal_reallocation_router)
    api.include_router(full_goal_release_authorization_router)
    api.include_router(full_goal_release_execution_router)
    api.include_router(dynamic_goal_reserve_router)
    api.include_router(full_dynamic_goal_execution_router)
    api.include_router(full_recovery_execution_router)
    api.include_router(full_recovery_next_router)
    api.include_router(full_maturity_execution_router)
    api.include_router(full_decision_search_router)
    api.include_router(full_seasonal_adoption_router)
    api.include_router(future_income_planning_router)
    api.include_router(full_action_set_boundary_router)
    api.include_router(full_action_set_boundary_full_router)
    api.include_router(full_action_set_boundary_actual_router)
    api.include_router(full_periodic_producer_router)
    api.include_router(full_action_set_boundary_composed_router)
    api.include_router(full_action_set_boundary_recovery_composed_router)
    api.include_router(full_action_set_boundary_registered_router)
    api.include_router(full_policy_router)
    api.include_router(full_policy_history_router)
    api.include_router(full_policy_change_multi_router)
    api.include_router(full_policy_compilation_router)
    api.include_router(full_policy_dependency_router)
    api.include_router(full_asset_allocation_router)
    api.include_router(full_asset_execution_router)
    api.include_router(full_payment_relation_router)
    # This deployment override is installed with the independent bank's mandatory
    # PAY_RECURRING scope hook. HTTP clients cannot supply an executor.
    from app.services.execution import execute_action

    api.dependency_overrides[get_guarded_payment_executor] = lambda: execute_action
    api.include_router(full_recovery_planning_router)
    api.include_router(full_maturity_replanning_router)
    api.include_router(full_reconciliation_router)
    api.include_router(full_protection_router)
    api.include_router(autonomy_envelope_router)
    api.include_router(boundary_difference_router)
    api.include_router(boundary_action_events_router)
    api.include_router(finite_uncertainty_router)
    api.include_router(local_actor_session_router)
    api.include_router(question_workflow_router)
    api.include_router(full_intervention_router)
    api.include_router(scenario_simulation_router)
    api.include_router(scenario_risk_review_router)
    api.include_router(product_catalog_router)
    api.include_router(user_policy_declaration_router)
    api.include_router(policy_suggestion_router)
    api.include_router(transaction_category_router)
    api.include_router(asset_router)
    api.include_router(recovery_router)
    api.include_router(action_router)
    api.include_router(decision_router)
    api.include_router(audit_router)
    api.include_router(evidence_router)
    api.include_router(full_evidence_graph_router)
    api.include_router(planning_router)
    api.include_router(delivery_router)
    api.include_router(dashboard_router)
    api.include_router(demo_router)

    async def lifecycle_error_handler(request: Request, exception: Exception) -> JSONResponse:
        assert isinstance(exception, PolicyLifecycleError)
        return error_response(request, exception.status_code, exception.code, exception.message)

    api.add_exception_handler(PolicyLifecycleError, lifecycle_error_handler)

    @api.get("/api/v1/health", response_model=HealthResponse, operation_id="health")
    def health() -> HealthResponse:
        return HealthResponse()

    return api


app = create_app()
