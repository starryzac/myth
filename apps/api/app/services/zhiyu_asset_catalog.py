"""Next-only append of two server-owned fixed-principal development products.

No HTTP caller supplies a product, rate, amount, date, term, or receipt. All
original six Demo products are retained byte-for-byte, and this service is never
called against the preserved Demo engine. Registration is original native work.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.catalog_models import ProductCatalogVersion
from app.db.models import AssetProduct, AuditEpoch, EvidenceItem
from app.domain.immutable_joint_archive_scope import immutable_joint_archive_scope
from app.domain.immutable_joint_validation_scope import immutable_joint_validation_scope
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, row_copy, verify_audit_chain
from app.services.demo_console import _epoch, _user
from app.services.demo_seed import DEMO_USER_ID, _product_values
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.product_catalog import (
    CATALOG_NAMESPACE,
    CatalogReadResponse,
    VerifiedCatalogProducts,
    read_catalog,
    register_current_catalog,
    verified_catalog_products,
)
from app.services.zhiyu_orchestration import MARKER, _valid, serial_user, store
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROTOCOL = "zhiyu-next-asset-catalog-v1"
KEY = "asset-catalog:" + PROTOCOL
NAMESPACE = UUID("301da4d5-0e20-563a-b563-8317d0bf8dc0")
LEGACY_DENOMINATOR = {
    (code, version)
    for code in ("BF_DEMO_T0", "BF_DEMO_T1", "BF_DEMO_FIXED_30D")
    for version in (1, 2)
}
RECORD_FIELDS = {
    "protocol",
    "purpose",
    "environment_id",
    "epoch_id",
    "user_id",
    "registered_at",
    "old_product_originals",
    "old_product_hash",
    "new_product_originals",
    "native_registration",
    "append_only",
    "bank_authority",
    "actor",
    "financial_permission_created",
}


def validate_registration_receipt(receipt: Any, products: list[dict[str, Any]]) -> set[UUID]:
    """The native registration receipt is provenance, never permission."""
    fields = {
        "simulation",
        "grants_authority",
        "dedicated_audit_event_recorded",
        "registered_ids",
        "reused_ids",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != fields
        or receipt["simulation"] is not True
        or receipt["grants_authority"] is not False
        or receipt["dedicated_audit_event_recorded"] is not False
        or not isinstance(receipt["registered_ids"], list)
        or not isinstance(receipt["reused_ids"], list)
    ):
        raise ValueError("Native registration receipt is incomplete")
    registered = [UUID(value) for value in receipt["registered_ids"]]
    reused = [UUID(value) for value in receipt["reused_ids"]]
    expected = {uuid5(CATALOG_NAMESPACE, product["id"]) for product in products}
    new_ids = {uuid5(CATALOG_NAMESPACE, str(preset_id(days))) for days in (7, 90)}
    if (
        len(registered) + len(reused) != 8
        or len(set(registered + reused)) != 8
        or set(registered + reused) != expected
        or not new_ids <= set(registered)
    ):
        raise ValueError("Native registration receipt does not bind all eight originals")
    return expected


def validate_saved_record(
    result: dict[str, Any],
    old: list[dict[str, Any]],
    new: list[dict[str, Any]],
    environment: str,
    epoch: UUID,
    user_id: UUID,
    now: datetime,
    created_at: datetime,
) -> set[UUID]:
    """Reject altered originals and omitted fields without repairing defaults."""
    if set(result) != RECORD_FIELDS:
        raise ValueError("Catalogue binding fields differ from the original protocol")
    registered_at = _now(datetime.fromisoformat(result["registered_at"]))
    expected_new = sorted(
        [row_copy(AssetProduct(**preset_values(days, registered_at))) for days in (7, 90)],
        key=lambda row: row["id"],
    )
    if (
        result["protocol"] != PROTOCOL
        or result["purpose"] != "DEVELOPMENT"
        or result["environment_id"] != environment
        or result["epoch_id"] != str(epoch)
        or result["user_id"] != str(user_id)
        or result["bank_authority"] is not False
        or result["financial_permission_created"] is not False
        or result["append_only"] is not True
        or result["actor"] != "SYSTEM"
        or not _now(created_at) == registered_at <= _now(now)
        or result["old_product_originals"] != old
        or result["old_product_hash"] != configuration_hash({"products": old})
        or len(old) != 6
        or len(new) != 2
        or new != expected_new
        or result["new_product_originals"] != new
    ):
        raise ValueError("Catalogue originals changed or are not server-owned presets")
    return validate_registration_receipt(result["native_registration"], old + new)


def require_complete_native_catalog(
    catalogue: VerifiedCatalogProducts,
    products: list[dict[str, Any]],
    native_ids: set[UUID],
) -> None:
    if (
        catalogue.status != "VERIFIED"
        or catalogue.issues
        or len(catalogue.products) != 8
        or len(catalogue.bindings) != 8
        or {binding.product_id for binding in catalogue.bindings}
        != {UUID(product["id"]) for product in products}
        or {binding.catalogue_version_id for binding in catalogue.bindings} != native_ids
    ):
        raise PolicyLifecycleError("ASSET_CATALOG_SOURCE_UNKNOWN", "完整原目录未匹配", 409)


def preset_id(days: int) -> UUID:
    if days not in {7, 90}:
        raise ValueError("Only the two fixed server-owned development products exist")
    return uuid5(NAMESPACE, f"{PROTOCOL}:FIXED_{days}D:version:1")


def preset_values(days: int, registered_at: datetime) -> dict[str, Any]:
    product_id = preset_id(days)
    registered_at = _now(registered_at)
    yield_bps = 180 if days == 7 else 260
    return {
        "id": product_id,
        "created_at": registered_at,
        "product_code": f"ZY_NEXT_FIXED_{days}D",
        "version_number": 1,
        "name": f"知余模拟{days}天定存",
        "asset_class": "FIXED_DEPOSIT",
        "risk_level": 0,
        "principal_fluctuation": False,
        "minimum_purchase_cents": 10000,
        "lock_days": days,
        "redemption_delay_days": 0,
        "annual_yield_bps": yield_bps,
        "early_withdrawal_loss_bps": 20,
        "maturity_rule": {
            "protocol": "fixed-principal-return-v1",
            "kind": "RETURN_TO_CASH",
            "day_basis": "CALENDAR",
            "guaranteed": True,
            "term_days": days,
            "settlement_delay_days": 0,
            "principal_return_bps": 10000,
            "rollover": False,
            "auto_rollover": False,
            "yield_rule": {
                "protocol": "simple-annual-yield-v1",
                "basis": "ACT_365",
                "annual_yield_bps": yield_bps,
                "simulation": True,
                "fee_cents": 0,
                "purchase_fee_bps": 0,
                "redemption_fee_bps": 0,
                "accrual": "UNTIL_MATURITY",
            },
        },
        "early_withdrawal_rule": {
            "allowed": True,
            "requires_confirmation_if_loss": True,
            "loss_basis": "principal_cents",
            "simulation": True,
        },
        "auto_purchase_allowed": True,
        "auto_redeem_allowed": False,
        "effective_from": registered_at,
        "effective_until": None,
    }


def inventory(session: Session) -> tuple[list[AssetProduct], list[AssetProduct]]:
    rows = list(session.scalars(select(AssetProduct).order_by(AssetProduct.id).limit(9)))
    new_ids = {preset_id(days) for days in (7, 90)}
    old = [row for row in rows if row.id not in new_ids]
    new = [row for row in rows if row.id in new_ids]
    if (
        len(rows) > 8
        or len(old) != 6
        or {(row.product_code, row.version_number) for row in old} != LEGACY_DENOMINATOR
        or [row_copy(row) for row in old]
        != sorted(
            [row_copy(AssetProduct(**value)) for value in _product_values()],
            key=lambda product: product["id"],
        )
    ):
        raise PolicyLifecycleError(
            "ASSET_CATALOG_DENOMINATOR_UNKNOWN", "原六产品完整分母不匹配", 409
        )
    return old, new


@dataclass(frozen=True)
class _CatalogueRead:
    """Detached one-invocation source, not an authority or cross-request cache."""

    source: dict[str, Any]
    source_hash: str
    result: dict[str, Any] | None


def _catalogue_source(
    session: Session, user_id: UUID, epoch: UUID, now: datetime, environment: str
) -> dict[str, Any]:
    if _epoch(session, user_id, epoch) != epoch:
        raise PolicyLifecycleError("ASSET_CATALOG_EPOCH_UNKNOWN", "当前审计期改变", 409)
    head = session.get(AuditEpoch, epoch)
    if (head is None or head.user_id != user_id or head.status != "OPEN"
            or _now(head.opened_at) > now):
        raise PolicyLifecycleError(
            "ASSET_CATALOG_EPOCH_UNKNOWN", "当前原审计期或服务器时间未证明", 409
        )
    old, new = inventory(session)
    versions = list(session.scalars(
        select(ProductCatalogVersion).order_by(ProductCatalogVersion.id).limit(9)
    ))
    if len(versions) > 8:
        raise PolicyLifecycleError("ASSET_CATALOG_SOURCE_UNKNOWN", "原目录分母超出固定八项", 409)
    binding = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:BINDING:{KEY}"))
    return {
        "environment_id": environment, "user_id": str(user_id), "epoch_id": str(epoch),
        "audit_head": row_copy(head),
        "old_product_originals": [row_copy(row) for row in old],
        "new_product_originals": [row_copy(row) for row in new],
        "catalogue_originals": [row_copy(row) for row in versions],
        "binding_original": row_copy(binding) if binding is not None else None,
    }


def _same_catalogue_source(original: _CatalogueRead, current: dict[str, Any]) -> None:
    if configuration_hash(original.source) != original.source_hash or current != original.source:
        raise PolicyLifecycleError(
            "ASSET_CATALOG_SOURCE_CHANGED", "只读原件、审计序列或目录引用已改变，保留原件", 409
        )


def _unregistered_seed_source(
    source: dict[str, Any], catalogue: VerifiedCatalogProducts, initial: CatalogReadResponse
) -> None:
    """Accept provisioning input only, retaining its genuine UNKNOWN projection."""
    if (source["catalogue_originals"] or source["new_product_originals"] or initial.versions
            or catalogue.status != "UNKNOWN" or catalogue.products or catalogue.bindings
            or initial.state != "UNKNOWN" or not initial.complete_within_registered_capacity
            or len(source["old_product_originals"]) != 6
            or set(initial.unregistered_product_ids)
            != {UUID(row["id"]) for row in source["old_product_originals"]}
            or set(initial.issues) != {
                "UNREGISTERED_CATALOG_PRODUCTS", "NO_REGISTERED_CATALOG_ORIGINALS"
            } or catalogue.issues != initial.issues):
        raise PolicyLifecycleError(
            "ASSET_CATALOG_SOURCE_UNKNOWN", "未登记初态不是完整原六产品，保留原件", 409
        )


def _read_catalogue_original(
    engine: Engine, user_id: UUID, epoch: UUID, now: datetime, environment: str
) -> _CatalogueRead:
    # Every native catalogue verifier runs on its own real clean RR/RO Session.
    # A new seed honestly remains UNKNOWN until the native catalogue is registered.
    with Session(engine) as session, session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        with (audit_read_scope(session), immutable_joint_validation_scope(),
              immutable_joint_archive_scope()):
            source = _catalogue_source(session, user_id, epoch, now, environment)
            audit = verify_audit_chain(session, user_id, epoch)
            if audit.status != "VALID":
                raise PolicyLifecycleError("ASSET_CATALOG_AUDIT_INVALID", "原当前审计未证明", 409)
            catalogue = verified_catalog_products(session, now)
            old, new = source["old_product_originals"], source["new_product_originals"]
            record = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:BINDING:{KEY}"))
            result: dict[str, Any] | None = None
            if record is not None:
                result = _valid(session, record, user_id, epoch)["payload"]
                try:
                    native_ids = validate_saved_record(
                        result, old, new, environment, epoch, user_id, now, record.created_at
                    )
                except (ValueError, KeyError, TypeError, PolicyLifecycleError):
                    raise PolicyLifecycleError(
                        "ASSET_CATALOG_ORIGINAL_INVALID", "原产品或登记原件改变，不修补", 409
                    ) from None
                require_complete_native_catalog(catalogue, old + new, native_ids)
            elif new:
                raise PolicyLifecycleError(
                    "ASSET_CATALOG_PARTIAL_NOT_FINAL", "新产品已有但原注册绑定缺失，保留原件", 409
                )
            elif catalogue.status == "VERIFIED":
                expected = {product["id"]: product for product in old}
                if (catalogue.issues or len(catalogue.products) != 6
                        or len(catalogue.bindings) != 6
                        or {str(binding.product_id) for binding in catalogue.bindings}
                        != set(expected)
                        or any(binding.catalogue_version_id != uuid5(
                            CATALOG_NAMESPACE, str(binding.product_id)
                        ) or binding.product_record_hash != configuration_hash(
                            expected[str(binding.product_id)]
                        ) for binding in catalogue.bindings)):
                    raise PolicyLifecycleError(
                        "ASSET_CATALOG_SOURCE_UNKNOWN", "完整六项原目录未匹配", 409
                    )
            else:
                initial = read_catalog(session, now)
                _unregistered_seed_source(source, catalogue, initial)
            # Native reads must not mutate any source, even within the same RR snapshot.
            if source != _catalogue_source(session, user_id, epoch, now, environment):
                raise PolicyLifecycleError("ASSET_CATALOG_SOURCE_CHANGED", "原只读来源改变", 409)
            detached = json.loads(json.dumps(source))
            return _CatalogueRead(detached, configuration_hash(detached),
                                  json.loads(json.dumps(result)) if result is not None else None)


def _append_catalogue_from_read(
    session: Session, original: _CatalogueRead, user_id: UUID,
    epoch: UUID, now: datetime, environment: str,
) -> dict[str, Any]:
    # Locks exclude audit/reset/source races; this writer never calls a RO verifier.
    _user(session, user_id, lock=True)
    session.scalar(select(AuditEpoch).where(
        AuditEpoch.id == epoch, AuditEpoch.user_id == user_id
    ).with_for_update())
    session.execute(text(
        "LOCK TABLE asset_products, product_catalog_versions IN SHARE ROW EXCLUSIVE MODE"
    ))
    _same_catalogue_source(original, _catalogue_source(session, user_id, epoch, now, environment))
    if original.result is not None or original.source["new_product_originals"]:
        raise PolicyLifecycleError("ASSET_CATALOG_REQUEST_CONFLICT", "已有原登记不能重建", 409)
    old_originals = original.source["old_product_originals"]
    for days in (7, 90):
        session.add(AssetProduct(**preset_values(days, now)))
    session.flush()
    registered = register_current_catalog(session, user_id, now)
    old_after, new_after = inventory(session)
    if [row_copy(row) for row in old_after] != old_originals or len(new_after) != 2:
        raise PolicyLifecycleError("ASSET_CATALOG_CHANGED", "追加不能改变原六产品", 409)
    result = {
        "protocol": PROTOCOL, "purpose": "DEVELOPMENT", "environment_id": environment,
        "epoch_id": str(epoch), "user_id": str(user_id), "registered_at": now.isoformat(),
        "old_product_originals": old_originals,
        "old_product_hash": configuration_hash({"products": old_originals}),
        "new_product_originals": [row_copy(row) for row in new_after],
        "native_registration": registered.model_dump(mode="json"),
        "append_only": True, "bank_authority": False, "actor": "SYSTEM",
        "financial_permission_created": False,
    }
    validate_saved_record(result, old_originals, [row_copy(row) for row in new_after],
                          environment, epoch, user_id, now, now)
    # Products, native original registration and original binding commit together.
    # No transient writer-derived object is called a verified financial catalogue.
    store(session, user_id, "BINDING", KEY, json.loads(json.dumps(result)), now)
    session.flush()
    return _catalogue_source(session, user_id, epoch, now, environment)


def ensure_zhiyu_asset_catalog(
    engine: Engine, user_id: UUID, expected_epoch_id: UUID, now: datetime,
    *, purpose: Literal["DEVELOPMENT"],
) -> dict[str, Any]:
    """Native atomic append; return only an independently verified RR/RO original."""
    if purpose != "DEVELOPMENT" or user_id != DEMO_USER_ID:
        raise ValueError("Only the exact development simulation user is supported")
    environment = require_zhiyu_next_engine(engine, check_connection=True)
    now = _now(now)
    with serial_user(engine, user_id):
        original = _read_catalogue_original(engine, user_id, expected_epoch_id, now, environment)
        if original.result is not None:
            return original.result
        with Session(engine) as session, session.begin():
            committed_source = _append_catalogue_from_read(
                session, original, user_id, expected_epoch_id, now, environment
            )
        # Commit has finished before the new independent RR/RO snapshot is opened.
        # If this read fails, keep the committed provenance; original-key retry
        # validates/reuses it. Never fabricate success or compensate by deleting it.
        verified = _read_catalogue_original(engine, user_id, expected_epoch_id, now, environment)
        _same_catalogue_source(verified, committed_source)
        if verified.result is None:
            raise PolicyLifecycleError("ASSET_CATALOG_ORIGINAL_UNKNOWN", "原登记尚未独立核实", 409)
        return verified.result
