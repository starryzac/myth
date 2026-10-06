"""Register actual simulator catalogue rows; snapshots grant no banking authority."""

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.audit_guard import transaction_gate
from app.db.catalog_models import ProductCatalogVersion
from app.db.models import AssetProduct
from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.evidence_graph import readonly
from app.services.policy_lifecycle import PolicyLifecycleError, _user
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

CATALOG_NAMESPACE = UUID("68dd9f10-4704-4cba-815c-a0fb1414cfb4")
CAPACITY = 1000


class CatalogVersionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    product_id: UUID
    product_code: str
    version_number: int
    original_product: dict[str, Any]
    product_hash: str
    terms_digest: str
    observed_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    immutable_original_verified: Literal[True] = True
    current_source_matched: bool
    bank_authority: Literal[False] = False
    legacy_decisions_bound_to_this_catalogue: Literal[False] = False


class CatalogReadResponse(BaseModel):
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    state: Literal["REGISTERED", "UNKNOWN"]
    versions: list[CatalogVersionView]
    unregistered_product_ids: list[UUID]
    issues: list[str]
    complete_within_registered_capacity: bool


class CatalogRegistrationResponse(BaseModel):
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    dedicated_audit_event_recorded: Literal[False] = False
    registered_ids: list[UUID]
    reused_ids: list[UUID]


class CatalogProductBinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    product_id: UUID
    catalogue_version_id: UUID
    product_record_hash: str
    terms_digest: str


class VerifiedCatalogProducts(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["VERIFIED", "UNKNOWN"]
    products: list[AssetProductTerms]
    bindings: list[CatalogProductBinding]
    issues: list[str]


def aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "产品登记要求服务器时区时间")
    return now.astimezone(UTC)


def canonical_clock(value: datetime) -> str:
    return aware(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def immutable_terms(row: ProductCatalogVersion) -> AssetProductTerms:
    data = row.canonical_product
    if (
        row.protocol_version != "product-catalog-v1"
        or data.get("id") != str(row.product_id)
        or data.get("product_code") != row.product_code
        or data.get("version_number") != row.version_number
        or configuration_hash(data) != row.product_hash
        or not isinstance(data.get("maturity_rule"), dict)
        or configuration_hash(data["maturity_rule"]) != row.terms_digest
        or data.get("effective_from") != canonical_clock(row.effective_from)
        or data.get("effective_until")
        != (canonical_clock(row.effective_until) if row.effective_until else None)
    ):
        raise PolicyLifecycleError("INVALID_CATALOG_ORIGINAL", "不可变产品原件不匹配", 409)
    fields = set(AssetProductTerms.model_fields) - {"product_id", "terms_digest"}
    try:
        return AssetProductTerms.model_validate_json(
            json.dumps(
                {field: data[field] for field in fields}
                | {"product_id": str(row.product_id), "terms_digest": row.terms_digest}
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_CATALOG_ORIGINAL", "产品条款原件未证明", 409) from error


def version_view(session: Session, row: ProductCatalogVersion, now: datetime) -> CatalogVersionView:
    immutable_terms(row)
    if row.observed_at > now:
        raise PolicyLifecycleError("FUTURE_CATALOG_KNOWLEDGE", "该目录原件尚未观察", 409)
    current = session.get(AssetProduct, row.product_id)
    matched = current is not None and configuration_hash(row_copy(current)) == row.product_hash
    return CatalogVersionView(
        id=row.id,
        product_id=row.product_id,
        product_code=row.product_code,
        version_number=row.version_number,
        original_product=row.canonical_product,
        product_hash=row.product_hash,
        terms_digest=row.terms_digest,
        observed_at=row.observed_at,
        effective_from=row.effective_from,
        effective_until=row.effective_until,
        current_source_matched=matched,
    )


def read_catalog(session: Session, now: datetime) -> CatalogReadResponse:
    now = aware(now)
    readonly(session)
    rows = list(
        session.scalars(
            select(ProductCatalogVersion)
            .order_by(ProductCatalogVersion.product_code, ProductCatalogVersion.version_number)
            .limit(CAPACITY + 1)
        )
    )
    covered = len(rows) <= CAPACITY
    issues: list[str] = []
    versions: list[CatalogVersionView] = []
    for row in rows[:CAPACITY]:
        try:
            view = version_view(session, row, now)
            versions.append(view)
            if not view.current_source_matched:
                issues.append("CATALOG_DRIFT:" + str(row.product_id))
        except PolicyLifecycleError as error:
            issues.append(error.code + ":" + str(row.product_id))
    products = list(
        session.scalars(select(AssetProduct.id).order_by(AssetProduct.id).limit(CAPACITY + 1))
    )
    covered = covered and len(products) <= CAPACITY
    registered = {row.product_id for row in rows[:CAPACITY]}
    unregistered = sorted(set(products[:CAPACITY]) - registered)
    if unregistered:
        issues.append("UNREGISTERED_CATALOG_PRODUCTS")
    if not covered:
        issues.append("CATALOG_CAPACITY_EXCEEDED")
    if not versions:
        issues.append("NO_REGISTERED_CATALOG_ORIGINALS")
    return CatalogReadResponse(
        state="UNKNOWN" if issues else "REGISTERED",
        versions=versions,
        unregistered_product_ids=unregistered,
        issues=issues,
        complete_within_registered_capacity=covered,
    )


def verified_catalog_products(session: Session, now: datetime) -> VerifiedCatalogProducts:
    """One request's actual immutable/current comparison, with no financial grant."""
    now = aware(now)
    catalog = read_catalog(session, now)
    if catalog.state != "REGISTERED":
        return VerifiedCatalogProducts(
            status="UNKNOWN", products=[], bindings=[], issues=catalog.issues
        )
    products: list[AssetProductTerms] = []
    bindings: list[CatalogProductBinding] = []
    for view in catalog.versions:
        if view.effective_from > now or (
            view.effective_until is not None and view.effective_until <= now
        ):
            continue
        row = session.get(ProductCatalogVersion, view.id)
        if row is None:
            return VerifiedCatalogProducts(
                status="UNKNOWN", products=[], bindings=[], issues=["MISSING_CATALOG_ORIGINAL"]
            )
        products.append(immutable_terms(row))
        bindings.append(
            CatalogProductBinding(
                product_id=row.product_id,
                catalogue_version_id=row.id,
                product_record_hash=row.product_hash,
                terms_digest=row.terms_digest,
            )
        )
    return VerifiedCatalogProducts(
        status="VERIFIED", products=products, bindings=bindings, issues=[]
    )


def register_current_catalog(
    session: Session, user_id: UUID, now: datetime
) -> CatalogRegistrationResponse:
    now = aware(now)
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("DIRTY_COMMAND_SESSION", "目录登记不能携带其他待写入行", 409)
    transaction_gate(session, user_id)
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN":
        raise PolicyLifecycleError("AUDIT_EPOCH_REQUIRED", "目录登记要求当前模拟审计期", 409)
    with session.begin_nested():
        products = list(
            session.scalars(
                select(AssetProduct).order_by(AssetProduct.id).limit(CAPACITY + 1).with_for_update()
            )
        )
        if not products or len(products) > CAPACITY:
            raise PolicyLifecycleError("CATALOG_CAPACITY", "目录来源为空或超登记上限", 409)
        registered: list[UUID] = []
        reused: list[UUID] = []
        for product in products:
            data = row_copy(product)
            if product.created_at > now:
                raise PolicyLifecycleError("FUTURE_CATALOG_KNOWLEDGE", "产品来源尚未知", 409)
            digest = configuration_hash(data)
            existing = session.scalar(
                select(ProductCatalogVersion).where(ProductCatalogVersion.product_id == product.id)
            )
            if existing is not None:
                view = version_view(session, existing, now)
                if not view.current_source_matched:
                    raise PolicyLifecycleError("CATALOG_DRIFT", "同版本不能覆盖旧原件", 409)
                reused.append(existing.id)
                continue
            row = ProductCatalogVersion(
                id=uuid5(CATALOG_NAMESPACE, str(product.id)),
                created_at=now,
                product_id=product.id,
                protocol_version="product-catalog-v1",
                product_code=product.product_code,
                version_number=product.version_number,
                canonical_product=data,
                product_hash=digest,
                terms_digest=configuration_hash(product.maturity_rule),
                observed_at=now,
                effective_from=product.effective_from,
                effective_until=product.effective_until,
            )
            immutable_terms(row)
            session.add(row)
            registered.append(row.id)
        session.flush()
    return CatalogRegistrationResponse(registered_ids=registered, reused_ids=reused)
