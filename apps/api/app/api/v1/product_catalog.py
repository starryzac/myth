"""Actual simulator product originals, registered explicitly without money authority."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.db.catalog_models import ProductCatalogVersion
from app.services import product_catalog as service
from app.services.evidence_graph import readonly
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict


class RegisterCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def require_no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/catalog/products",
    tags=["不可变模拟产品目录"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
    dependencies=[Depends(require_no_query)],
)


@router.get("", response_model=service.CatalogReadResponse, operation_id="read_product_catalog")
def read_catalog(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> service.CatalogReadResponse:
    return service.read_catalog(session, now)


@router.get(
    "/versions/{version_id}",
    response_model=service.CatalogVersionView,
    operation_id="read_product_catalog_original",
)
def read_original(
    version_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> service.CatalogVersionView:
    readonly(session)
    row = session.get(ProductCatalogVersion, version_id)
    if row is None:
        raise PolicyLifecycleError("NOT_FOUND", "产品目录原件不存在", 404)
    return service.version_view(session, row, service.aware(now))


@router.post(
    "/register-current",
    response_model=service.CatalogRegistrationResponse,
    operation_id="register_actual_current_product_catalog",
)
def register_actual(
    body: RegisterCatalogRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> service.CatalogRegistrationResponse:
    return service.register_current_catalog(session, user.id, now)
