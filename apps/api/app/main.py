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
from app.api.v1.boundary import router as boundary_router
from app.api.v1.decisions import router as decision_router
from app.api.v1.goals import router as goal_router
from app.api.v1.living_reserve import router as reserve_router
from app.api.v1.policies import router as policy_router
from app.api.v1.recovery import router as recovery_router
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
        title="钱途有界 · 模拟资金 API",
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
    api.include_router(reserve_router)
    api.include_router(boundary_router)
    api.include_router(goal_router)
    api.include_router(asset_router)
    api.include_router(recovery_router)
    api.include_router(action_router)
    api.include_router(decision_router)
    api.include_router(audit_router)

    async def lifecycle_error_handler(request: Request, exception: Exception) -> JSONResponse:
        assert isinstance(exception, PolicyLifecycleError)
        return error_response(request, exception.status_code, exception.code, exception.message)

    api.add_exception_handler(PolicyLifecycleError, lifecycle_error_handler)

    @api.get("/api/v1/health", response_model=HealthResponse, operation_id="health")
    def health() -> HealthResponse:
        return HealthResponse()

    return api


app = create_app()
