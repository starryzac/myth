"""Immutable FULL catalogue originals without changing legacy product rows or tests."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdentityMixin, UTCDateTime


class ProductCatalogVersion(IdentityMixin, Base):
    __tablename__ = "product_catalog_versions"
    product_id: Mapped[UUID] = mapped_column(ForeignKey("asset_products.id", ondelete="RESTRICT"))
    protocol_version: Mapped[str] = mapped_column(String(40), server_default="product-catalog-v1")
    product_code: Mapped[str] = mapped_column(String(64))
    version_number: Mapped[int] = mapped_column(Integer)
    canonical_product: Mapped[dict[str, Any]] = mapped_column(JSONB)
    product_hash: Mapped[str] = mapped_column(String(64))
    terms_digest: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime())
    effective_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        UniqueConstraint("product_id", name="uq_catalog_product_identity"),
        UniqueConstraint("product_code", "version_number", name="uq_catalog_product_version"),
        CheckConstraint(
            "protocol_version = 'product-catalog-v1' AND version_number > 0", name="version"
        ),
        CheckConstraint(
            "product_hash ~ '^[0-9a-f]{64}$' AND terms_digest ~ '^[0-9a-f]{64}$'", name="hashes"
        ),
        CheckConstraint(
            "jsonb_typeof(canonical_product) = 'object' "
            "AND octet_length(canonical_product::text) <= 1048576",
            name="original",
        ),
        CheckConstraint(
            "observed_at >= created_at AND "
            "(effective_until IS NULL OR effective_until >= effective_from)",
            name="time",
        ),
    )
