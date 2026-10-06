"""Dedicated, closed-route Zhiyu entry; the historical full app remains available."""

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.dependencies import get_engine
from app.api.v1.zhiyu import router
from app.main import create_app
from app.zhiyu_isolation import require_zhiyu_engine

ALLOWED = {
    "GET": (
        r"/api/v1/health",
        r"/api/v1/zhiyu/(environment|presets|state)",
        r"/api/v1/policies",
        r"/api/v1/policy-proposals",
        r"/api/v1/policy-compilations/[^/]+",
        r"/api/v1/actions/[^/]+",
        r"/api/v1/goals/[^/]+/allocation-preview",
        r"/openapi.json",
        r"/docs",
        r"/docs/oauth2-redirect",
        r"/redoc",
    ),
    "POST": (
        r"/api/v1/policies/compile",
        r"/api/v1/policy-proposals/[^/]+/confirm",
        r"/api/v1/policies/[^/]+/(suspend|revoke)",
        r"/api/v1/actions/[^/]+/confirm",
        r"/api/v1/zhiyu/(goal|income|actions/prepare)",
        r"/api/v1/zhiyu/actions/[^/]+/execute",
    ),
}


def allowed(method: str, path: str) -> bool:
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED.get(method, ()))


def create_zhiyu_app(engine: Engine | None = None) -> FastAPI:
    selected = engine if engine is not None else get_engine()
    require_zhiyu_engine(selected)
    api = create_app()
    api.title = "知余 · 模拟资金 API"
    api.dependency_overrides[get_engine] = lambda: selected
    api.include_router(router)
    original_openapi = api.openapi

    def demo_openapi() -> dict[str, Any]:
        schema = original_openapi()
        schema["paths"] = {
            path: {
                method: operation
                for method, operation in operations.items()
                if allowed(method.upper(), path)
            }
            for path, operations in schema["paths"].items()
            if any(allowed(method.upper(), path) for method in operations)
        }
        return schema

    api.openapi = demo_openapi  # type: ignore[method-assign]

    @api.middleware("http")
    async def demo_routes(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not allowed(request.method, request.url.path):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "DEMO_ROUTE_NOT_ENABLED",
                        "message": "本次模拟演示未开放此能力",
                        "request_id": None,
                    }
                },
            )
        require_zhiyu_engine(selected)
        request.scope["zhiyu_read_only"] = request.method == "GET"
        return await call_next(request)

    return api
