"""Immutable catalogue integrity risks; no database or economic success claims."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from app.api.v1.product_catalog import RegisterCatalogRequest
from app.db.catalog_models import ProductCatalogVersion
from app.db.models import AssetProduct
from app.domain.policy_configuration import configuration_hash
from app.services import product_catalog as service
from app.services.audit_chain import row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_asset_allocation import NOW, product
from pydantic import ValidationError


def original() -> ProductCatalogVersion:
    source = product(41, term=30)
    values = source.model_dump(exclude={"product_id", "terms_digest"})
    actual = AssetProduct(
        id=source.product_id, name="实际模拟产品", early_withdrawal_rule={}, **values
    )
    data = row_copy(actual)
    return ProductCatalogVersion(
        id=uuid4(),
        created_at=NOW,
        product_id=source.product_id,
        protocol_version="product-catalog-v1",
        product_code=source.product_code,
        version_number=source.version_number,
        canonical_product=data,
        product_hash=configuration_hash(data),
        terms_digest=source.terms_digest,
        observed_at=NOW,
        effective_from=source.effective_from,
        effective_until=None,
    )


def test_original_terms_reproduce_aware_utc_and_full_principal_rule() -> None:
    row = original()
    assert service.immutable_terms(row) == product(41, term=30)
    assert row.canonical_product["effective_from"].endswith(".000000Z")
    assert service.canonical_clock(NOW.astimezone()) == service.canonical_clock(NOW)
    with pytest.raises(PolicyLifecycleError):
        service.aware(NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "field",
    [
        "annual_yield_bps",
        "lock_days",
        "risk_level",
        "name",
        "early_withdrawal_rule",
        "maturity_rule",
        "created_at",
        "effective_from",
        "id",
        "version_number",
    ],
)
def test_any_original_terms_or_identity_drift_rejects_the_bound_snapshot(field: str) -> None:
    row = original()
    row.canonical_product = deepcopy(row.canonical_product)
    row.canonical_product[field] = "corrupted actual original"
    with pytest.raises(PolicyLifecycleError) as error:
        service.immutable_terms(row)
    assert error.value.code == "INVALID_CATALOG_ORIGINAL"


def test_catalogue_valid_window_metadata_cannot_be_moved_without_original_match() -> None:
    row = original()
    row.effective_from += timedelta(days=1)
    with pytest.raises(PolicyLifecycleError):
        service.immutable_terms(row)


@pytest.mark.parametrize("field", ["user_id", "products", "bank_authority", "observed_at"])
def test_registration_cannot_supply_a_principal_catalogue_or_clock(field: str) -> None:
    with pytest.raises(ValidationError):
        RegisterCatalogRequest.model_validate({field: "not server source"})
