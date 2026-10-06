"""Actual PostgreSQL immutable catalogue and additive migration; original money stays intact."""

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import AssetProduct
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.services import product_catalog as service
from app.services.demo_seed import seed_demo
from app.tests.test_full_policy_schema import ROOT, physical_originals
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_product_catalogue_additive_migration_keeps_all29_original_tables_exactly() -> None:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "0009_full_policy_lifecycle")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            before = physical_originals(engine)
            assert len(before) == 29 and "product_catalog_versions" not in before
            command.upgrade(config, "0010_product_catalog")
            after = physical_originals(engine)
            assert after.pop("product_catalog_versions") == []
            assert after == before
            assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {
                "alembic_version"
            }
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0010_product_catalog"
                )
        finally:
            engine.dispose()


def test_register_replay_immutable_read_drift_and_new_version_use_actual_products_only(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    url = "/api/v1/catalog/products"
    before = physical_originals(engine)
    read = client.get(url)
    assert read.status_code == 200 and read.json()["state"] == "UNKNOWN"
    assert read.json()["unregistered_product_ids"]
    assert physical_originals(engine) == before
    bodies: tuple[dict[str, Any], ...] = (
        {"products": []},
        {"user_id": str(uuid4())},
        {"observed_at": NOW.isoformat()},
    )
    for body in bodies:
        assert client.post(url + "/register-current", json=body).status_code == 422
    assert client.get(url + "?clock=backdate").status_code == 422
    assert physical_originals(engine) == before

    response = client.post(url + "/register-current", json={})
    assert response.status_code == 200
    result = response.json()
    assert not result["grants_authority"] and not result["dedicated_audit_event_recorded"]
    after = physical_originals(engine)
    assert {k: v for k, v in after.items() if k != "product_catalog_versions"} == {
        k: v for k, v in before.items() if k != "product_catalog_versions"
    }
    assert len(result["registered_ids"]) == len(before["asset_products"])
    reused = client.post(url + "/register-current", json={})
    assert reused.status_code == 200 and reused.json()["registered_ids"] == []
    assert set(reused.json()["reused_ids"]) == set(result["registered_ids"])
    current = client.get(url).json()
    assert current["state"] == "REGISTERED"
    assert all(
        v["current_source_matched"] and v["immutable_original_verified"]
        for v in current["versions"]
    )
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        verified = service.verified_catalog_products(session, NOW)
        assert verified.status == "VERIFIED" and verified.products and verified.bindings
        assert {p.product_id for p in verified.products} == {
            b.product_id for b in verified.bindings
        }
    assert physical_originals(engine) == after

    view = current["versions"][0]
    version_url = url + "/versions/" + view["id"]
    for sql in (
        "UPDATE product_catalog_versions SET product_hash = repeat('b',64)",
        "DELETE FROM product_catalog_versions",
        "TRUNCATE product_catalog_versions",
    ):
        with Session(engine) as session, pytest.raises(DBAPIError):
            session.execute(text(sql))
            session.commit()
    assert physical_originals(engine) == after
    with Session(engine) as session, session.begin():
        session.execute(
            update(AssetProduct)
            .where(AssetProduct.id == UUID(view["product_id"]))
            .values(name="旧V1允许的实际来源变化，用于不可变目录差量风险")
        )
    drifted = physical_originals(engine)
    assert drifted["product_catalog_versions"] == after["product_catalog_versions"]
    historical = client.get(version_url)
    assert historical.status_code == 200
    assert historical.json()["original_product"] == view["original_product"]
    assert historical.json()["current_source_matched"] is False
    assert client.get(url).json()["state"] == "UNKNOWN"
    assert client.post(url + "/register-current", json={}).status_code == 409
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        failed = service.verified_catalog_products(session, NOW)
        assert failed.status == "UNKNOWN" and failed.products == [] and failed.bindings == []
    assert physical_originals(engine) == drifted


def test_new_actual_product_version_retains_old_original_reproduction(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    url = "/api/v1/catalog/products"
    assert client.post(url + "/register-current", json={}).status_code == 200
    prior = physical_originals(engine)["product_catalog_versions"]
    with Session(engine) as session, session.begin():
        old = session.scalar(select(AssetProduct).where(AssetProduct.version_number == 2))
        assert old is not None
        values = {
            column.key: deepcopy(getattr(old, column.key))
            for column in AssetProduct.__table__.columns
        }
        values.update(id=uuid4(), version_number=3, name="独立新增实际V3条款", created_at=NOW)
        new = AssetProduct(**values)
        session.add(new)
        session.flush()
        new_id = new.id
    before_register = physical_originals(engine)
    response = client.post(url + "/register-current", json={})
    assert response.status_code == 200 and len(response.json()["registered_ids"]) == 1
    after = physical_originals(engine)
    assert {k: v for k, v in after.items() if k != "product_catalog_versions"} == {
        k: v for k, v in before_register.items() if k != "product_catalog_versions"
    }
    assert all(row in after["product_catalog_versions"] for row in prior)
    current = client.get(url).json()
    assert current["state"] == "REGISTERED"
    assert any(
        v["product_id"] == str(new_id) and v["version_number"] == 3 for v in current["versions"]
    )
    for old_original in prior:
        original = client.get(url + "/versions/" + str(old_original["id"]))
        assert original.status_code == 200
        assert original.json()["original_product"] == old_original["canonical_product"]
    assert physical_originals(engine) == after
