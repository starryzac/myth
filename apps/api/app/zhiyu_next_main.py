"""Dedicated closed-route factory for the independent development simulation."""

import os
import re
from collections.abc import AsyncIterator
from typing import Any

from app.api.dependencies import ClockDependency, get_engine
from app.api.errors import validation_error_handler
from app.api.v1.zhiyu import router as preserved_router
from app.api.v1.zhiyu_asset_permissions import router as asset_permission_router
from app.api.v1.zhiyu_assets import router as asset_closure_router
from app.api.v1.zhiyu_catalog import router as catalog_router
from app.api.v1.zhiyu_discovery import router as discovery_router
from app.api.v1.zhiyu_goals import router as goals_router
from app.api.v1.zhiyu_next import router
from app.api.v1.zhiyu_payments import router as payments_router
from app.api.v1.zhiyu_policy_review import router as review_router
from app.domain.immutable_joint_validation_scope import immutable_joint_validation_scope
from app.main import create_app
from app.services.audit_receipt_clock import server_receipt_read_clock_scope
from app.services.llm_provider import ModelProviderError
from app.services.zhiyu_local_actor import load_credentials
from app.services.zhiyu_model_settings import ModelConfigurationError
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

ALLOWED = {
    "GET": (
        r"/api/v1/zhiyu-next/assets/state",
        r"/api/v1/zhiyu-next/assets/portfolios/[^/]+",
        r"/api/v1/zhiyu-next/assets/portfolio-commands/[^/]+/[^/]+",
        r"/api/v1/zhiyu-next/assets/maturity/by-key/.+",
        r"/api/v1/zhiyu-next/assets/lossy/actions/[^/]+",
        r"/api/v1/zhiyu-next/assets/lossy/positions/[^/]+/requests/[^/]+",
        r"/api/v1/zhiyu-next/assets/reinvestments/by-maturity-action/[^/]+",
        r"/api/v1/zhiyu-next/assets/mvp-permissions/schema",
        r"/api/v1/zhiyu-next/assets/mvp-permission-commands/[^/]+",
        r"/api/v1/health",
        r"/api/v1/zhiyu-next/(environment|state|model-settings)",
        r"/api/v1/zhiyu-next/operations/[^/]+",
        r"/api/v1/zhiyu-next/(policy-templates|policy-records)",
        r"/api/v1/zhiyu-next/policy-discovery",
        r"/api/v1/zhiyu-next/policy-schema/[^/]+",
        r"/api/v1/zhiyu-next/policy-commands/[^/]+",
        r"/api/v1/zhiyu-next/local-actor/session",
        r"/api/v1/zhiyu-next/(policy-reviews|policy-change-commands)/[^/]+",
        r"/api/v1/zhiyu-next/policy-change-reviews/by-key/[^/]+",
        r"/api/v1/zhiyu-next/payments/(state|observations/[^/]+)",
        r"/api/v1/zhiyu-next/payments/commands/[^/]+/by-key/[^/]+",
        r"/api/v1/zhiyu-next/payments/authorizations/[^/]+/prepared/by-key/[^/]+",
        r"/api/v1/zhiyu-next/payments/actions/[^/]+(/user-consent)?",
        r"/api/v1/zhiyu-next/goals/(planning|conflicts|adjustments)",
        r"/api/v1/zhiyu-next/goals/joint/by-key/[^/]+",
        r"/api/v1/zhiyu-next/goals/joint/[^/]+(/archive)?",
        r"/api/v1/zhiyu-next/goals/models/[^/]+",
        r"/api/v1/zhiyu-next/goals/models/[^/]+/commands/by-key/[^/]+",
        r"/api/v1/zhiyu/presets",
        r"/api/v1/(policies|policy-proposals)",
        r"/api/v1/actions/[^/]+",
        r"/api/v1/goals/[^/]+/allocation-preview",
        r"/openapi.json",
    ),
    "POST": (
        r"/api/v1/zhiyu-next/assets/portfolios/(preview|prepare)",
        r"/api/v1/zhiyu-next/assets/portfolios/[^/]+/(confirm-and-execute|continue-original)",
        r"/api/v1/zhiyu-next/assets/maturity/(preview|prepare)",
        r"/api/v1/zhiyu-next/assets/maturity/actions/[^/]+/(confirm-and-execute|execute-original)",
        r"/api/v1/zhiyu-next/assets/lossy/positions/[^/]+/quote",
        r"/api/v1/zhiyu-next/assets/lossy/prepare",
        r"/api/v1/zhiyu-next/assets/lossy/actions/[^/]+/(confirm-and-execute|execute-original)",
        r"/api/v1/zhiyu-next/assets/reinvestments/(preview|prepare)",
        r"/api/v1/zhiyu-next/assets/mvp-permissions/(candidates|confirm)",
        r"/api/v1/zhiyu-next/(agent/messages|authorizations/confirm|autonomy/pause|autonomy/resume|demo/income|model-settings/test)",
        r"/api/v1/zhiyu-next/(policy-candidates|policy-commands/confirm|policy-commands/lifecycle)",
        r"/api/v1/zhiyu-next/policy-discovery(/confirm)?",
        r"/api/v1/zhiyu-next/local-actor/(login|logout)",
        r"/api/v1/zhiyu-next/policies/(MVP_POLICY|FULL_POLICY)/[^/]+/change-(preview|review|confirm)",
        r"/api/v1/zhiyu-next/payments/observe-current-period",
        r"/api/v1/zhiyu-next/payments/relation/(preview|start)",
        r"/api/v1/zhiyu-next/payments/relation/[^/]+/confirm",
        r"/api/v1/zhiyu-next/payments/authorizations/[^/]+/prepare",
        r"/api/v1/zhiyu-next/payments/actions/[^/]+/(confirm-and-execute|execute-original)",
        r"/api/v1/zhiyu-next/goals/joint/(preview|prepare)",
        r"/api/v1/zhiyu-next/goals/joint/[^/]+/(confirm|execute-child)",
        r"/api/v1/zhiyu-next/goals/(repairs|adjustments)/preview",
        r"/api/v1/zhiyu-next/goals/models/[^/]+/(preview|confirm)",
    ),
    "PUT": (r"/api/v1/zhiyu-next/model-settings",),
}
READ_ONLY_POST = (
    r"/api/v1/zhiyu-next/assets/portfolios/preview",
    r"/api/v1/zhiyu-next/assets/maturity/preview",
    r"/api/v1/zhiyu-next/assets/reinvestments/preview",
    r"/api/v1/zhiyu-next/policies/(MVP_POLICY|FULL_POLICY)/[^/]+/change-preview",
    r"/api/v1/zhiyu-next/payments/relation/preview",
    r"/api/v1/zhiyu-next/goals/joint/preview",
    r"/api/v1/zhiyu-next/goals/(repairs|adjustments)/preview",
    r"/api/v1/zhiyu-next/goals/models/[^/]+/preview",
)


def allowed(method: str, path: str) -> bool:
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED.get(method, ()))


def read_only_request(method: str, path: str) -> bool:
    return method == "GET" or (
        method == "POST" and any(re.fullmatch(pattern, path) for pattern in READ_ONLY_POST)
    )


async def _server_receipt_read_clock(now: ClockDependency) -> AsyncIterator[None]:
    # Async yield keeps the context in the request task; sync consumers inherit it.
    with server_receipt_read_clock_scope(now):
        yield


def create_zhiyu_next_app(engine: Engine | None = None) -> FastAPI:
    selected = engine if engine is not None else get_engine()
    require_zhiyu_next_engine(selected)
    load_credentials(selected, create=True)
    api = create_app()
    api.title = "知余扩展版 · 开发验证 API"
    api.dependency_overrides[get_engine] = lambda: selected
    receipt_reads = [Depends(_server_receipt_read_clock)]
    api.include_router(router, dependencies=receipt_reads)
    api.include_router(catalog_router, dependencies=receipt_reads)
    api.include_router(discovery_router, dependencies=receipt_reads)
    api.include_router(review_router, dependencies=receipt_reads)
    api.include_router(payments_router, dependencies=receipt_reads)
    api.include_router(goals_router, dependencies=receipt_reads)
    api.include_router(asset_closure_router, dependencies=receipt_reads)
    api.include_router(asset_permission_router, dependencies=receipt_reads)
    api.include_router(preserved_router, dependencies=receipt_reads)
    original_openapi = api.openapi

    def filtered_openapi() -> dict[str, Any]:
        schema = original_openapi()
        schema["paths"] = {
            path: {
                method: value
                for method, value in operations.items()
                if allowed(method.upper(), path)
            }
            for path, operations in schema["paths"].items()
            if any(allowed(method.upper(), path) for method in operations)
        }
        return schema

    api.openapi = filtered_openapi  # type: ignore[method-assign]

    async def model_error(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ModelConfigurationError | ModelProviderError)
        return JSONResponse(
            status_code=503, content={"error": {"code": error.code, "message": error.message}}
        )

    api.add_exception_handler(ModelConfigurationError, model_error)
    api.add_exception_handler(ModelProviderError, model_error)

    async def safe_validation(request: Request, error: Exception) -> Response:
        if request.url.path in {
            "/api/v1/zhiyu-next/model-settings",
            "/api/v1/zhiyu-next/local-actor/login",
        }:
            local_login = request.url.path.endswith("/local-actor/login")
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "LOCAL_CREDENTIAL_INVALID"
                        if local_login
                        else "LLM_CONFIGURATION_INVALID",
                        "message": "本地确认凭证格式无效。"
                        if local_login
                        else "模型配置格式无效，请检查设置。",
                    }
                },
            )
        assert isinstance(error, RequestValidationError)
        return await validation_error_handler(request, error)

    api.add_exception_handler(RequestValidationError, safe_validation)

    @api.middleware("http")
    async def routes(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not allowed(request.method, request.url.path):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {"code": "NEXT_ROUTE_NOT_ENABLED", "message": "扩展版尚未开放此能力"}
                },
            )
        if request.method in {"POST", "PUT"}:
            origin = request.headers.get("origin")
            # Same-origin browser requests or local non-browser development tools.
            # The model settings route cannot be driven from an arbitrary website.
            if origin and origin not in {
                f"http://127.0.0.1:{os.environ.get('ZHIYU_NEXT_WEB_PORT', '19273')}",
                f"http://localhost:{os.environ.get('ZHIYU_NEXT_WEB_PORT', '19273')}",
            }:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "LOCAL_ORIGIN_REQUIRED",
                            "message": "请从当前扩展版入口操作",
                        }
                    },
                )
        require_zhiyu_next_engine(selected)
        request.scope["zhiyu_read_only"] = read_only_request(request.method, request.url.path)
        from app.domain.immutable_joint_archive_scope import immutable_joint_archive_scope

        with immutable_joint_validation_scope(), immutable_joint_archive_scope():
            return await call_next(request)

    return api
