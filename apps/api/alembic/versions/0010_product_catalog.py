"""Add immutable product originals, preserving every legacy product and decision."""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_product_catalog"

down_revision: str | Sequence[str] | None = "0009_full_policy_lifecycle"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.execute(
        "\nCREATE TABLE product_catalog_versions (\n\tproduct_id UUID NOT NULL"
        ", \n\tprotocol_version VARCHAR(40) DEFAULT 'product-catalog-v1' NOT "
        "NULL, \n\tproduct_code VARCHAR(64) NOT NULL, \n\tversion_number INTEGE"
        "R NOT NULL, \n\tcanonical_product JSONB NOT NULL, \n\tproduct_hash VAR"
        "CHAR(64) NOT NULL, \n\tterms_digest VARCHAR(64) NOT NULL, \n\tobserved"
        "_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\teffective_from TIMESTAMP "
        "WITH TIME ZONE NOT NULL, \n\teffective_until TIMESTAMP WITH TIME ZON"
        "E, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAU"
        "LT now() NOT NULL, \n\tCONSTRAINT pk_product_catalog_versions PRIMAR"
        "Y KEY (id), \n\tCONSTRAINT uq_catalog_product_identity UNIQUE (produ"
        "ct_id), \n\tCONSTRAINT uq_catalog_product_version UNIQUE (product_co"
        "de, version_number), \n\tCONSTRAINT ck_product_catalog_versions_vers"
        "ion CHECK (protocol_version = 'product-catalog-v1' AND version_num"
        "ber > 0), \n\tCONSTRAINT ck_product_catalog_versions_hashes CHECK (p"
        "roduct_hash ~ '^[0-9a-f]{64}$' AND terms_digest ~ '^[0-9a-f]{64}$'"
        "), \n\tCONSTRAINT ck_product_catalog_versions_original CHECK (jsonb_"
        "typeof(canonical_product) = 'object' AND octet_length(canonical_pr"
        "oduct::text) <= 1048576), \n\tCONSTRAINT ck_product_catalog_versions"
        "_time CHECK (observed_at >= created_at AND (effective_until IS NUL"
        "L OR effective_until >= effective_from)), \n\tCONSTRAINT fk_product_"
        "catalog_versions_product_id_asset_products FOREIGN KEY(product_id)"
        " REFERENCES asset_products (id) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute(
        "CREATE FUNCTION guard_product_catalog_original() RETURNS trigger L"
        "ANGUAGE plpgsql AS $$\nBEGIN\n RAISE EXCEPTION 'Product catalogue or"
        "iginals are immutable';\n RETURN NULL;\nEND $$"
    )

    op.execute(
        "CREATE TRIGGER product_catalog_original_immutable\nBEFORE UPDATE OR"
        " DELETE ON product_catalog_versions\nFOR EACH ROW EXECUTE FUNCTION "
        "guard_product_catalog_original()"
    )
    op.execute(
        "CREATE TRIGGER product_catalog_truncate_immutable BEFORE TRUNCATE "
        "ON product_catalog_versions FOR EACH STATEMENT "
        "EXECUTE FUNCTION guard_product_catalog_original()"
    )


def downgrade() -> None:

    op.execute(
        "DO $$ BEGIN\n IF EXISTS (SELECT 1 FROM product_catalog_versions) TH"
        "EN\n RAISE EXCEPTION 'Refusing to discard product catalogue origina"
        "ls';\n END IF;\nEND $$"
    )

    op.drop_table("product_catalog_versions")

    op.execute("DROP FUNCTION guard_product_catalog_original()")
