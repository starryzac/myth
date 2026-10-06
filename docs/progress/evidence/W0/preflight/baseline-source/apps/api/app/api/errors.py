"""Public, redacted error contract shared by all API routes."""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request.state.request_id,
        )
    )
    return JSONResponse(status_code=status, content=body.model_dump())


async def http_error_handler(request: Request, exception: Exception) -> JSONResponse:
    assert isinstance(exception, HTTPException)
    code = "NOT_FOUND" if exception.status_code == 404 else "HTTP_ERROR"
    message = "资源不存在" if exception.status_code == 404 else "请求无法完成"
    return error_response(request, exception.status_code, code, message)


async def validation_error_handler(request: Request, exception: Exception) -> JSONResponse:
    # Pydantic errors can contain raw input. Do not echo or log those values.
    return error_response(request, 422, "VALIDATION_ERROR", "请求字段无效，请检查格式与范围")
