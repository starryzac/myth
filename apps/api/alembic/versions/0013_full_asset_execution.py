"""Immutable whole-portfolio execution identities. Never rewrite existing financial history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_full_asset_execution"
down_revision: str | Sequence[str] | None = "0012_intervention_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES_SQL = """
CREATE TABLE full_asset_execution_portfolios (
	epoch_id UUID NOT NULL, 
	idempotency_key VARCHAR(160) NOT NULL, 
	request JSONB NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	portfolio JSONB NOT NULL, 
	portfolio_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	user_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_full_asset_execution_portfolios PRIMARY KEY (id), 
	CONSTRAINT fk_full_asset_execution_portfolios_epoch_id_audit_epochs FOREIGN KEY(epoch_id,
  user_id) REFERENCES audit_epochs (id, user_id) ON DELETE RESTRICT, 
	CONSTRAINT uq_full_asset_portfolio_owner_epoch UNIQUE (id, user_id, epoch_id), 
	CONSTRAINT uq_full_asset_portfolio_key UNIQUE (user_id, epoch_id, idempotency_key), 
	CONSTRAINT ck_full_asset_execution_portfolios_key CHECK (length(idempotency_key) BETWEEN 1 AND
  160), 
	CONSTRAINT ck_full_asset_execution_portfolios_hashes CHECK (request_hash ~ '^[0-9a-f]{64}$'
  AND portfolio_hash ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_full_asset_execution_portfolios_json CHECK (jsonb_typeof(request) = 'object' AND
  jsonb_typeof(portfolio) = 'object' AND octet_length(request::text) <= 1048576 AND
  octet_length(portfolio::text) <= 1048576), 
	CONSTRAINT ck_full_asset_execution_portfolios_time CHECK (expires_at > created_at), 
	CONSTRAINT uq_full_asset_execution_portfolios_id UNIQUE (id, user_id), 
	CONSTRAINT fk_full_asset_execution_portfolios_user_id_users FOREIGN KEY(user_id) REFERENCES
  users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_asset_execution_portfolios_user_id ON full_asset_execution_portfolios
  (user_id);

CREATE TABLE full_asset_execution_batches (
	portfolio_id UUID NOT NULL, 
	epoch_id UUID NOT NULL, 
	batch_number INTEGER NOT NULL, 
	action_plan_id UUID NOT NULL, 
	bank_idempotency_key VARCHAR(160) NOT NULL, 
	command JSONB NOT NULL, 
	command_hash VARCHAR(64) NOT NULL, 
	catalogue_version_id UUID NOT NULL, 
	product_record_hash VARCHAR(64) NOT NULL, 
	user_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_full_asset_execution_batches PRIMARY KEY (id), 
	CONSTRAINT fk_full_asset_execution_batches_portfolio_id_full_asset_405b FOREIGN
  KEY(portfolio_id, user_id, epoch_id) REFERENCES full_asset_execution_portfolios (id, user_id,
  epoch_id) ON DELETE RESTRICT, 
	CONSTRAINT uq_full_asset_batch_order UNIQUE (portfolio_id, batch_number), 
	CONSTRAINT uq_full_asset_batch_action UNIQUE (action_plan_id), 
	CONSTRAINT uq_full_asset_batch_bank_key UNIQUE (user_id, bank_idempotency_key), 
	CONSTRAINT ck_full_asset_execution_batches_identity CHECK (batch_number BETWEEN 1 AND 4 AND
  length(bank_idempotency_key) BETWEEN 1 AND 160), 
	CONSTRAINT ck_full_asset_execution_batches_hashes CHECK (command_hash ~ '^[0-9a-f]{64}$' AND
  product_record_hash ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_full_asset_execution_batches_json CHECK (jsonb_typeof(command) = 'object' AND
  octet_length(command::text) <= 1048576), 
	CONSTRAINT uq_full_asset_execution_batches_id UNIQUE (id, user_id), 
	CONSTRAINT fk_full_asset_execution_batches_catalogue_version_id_pr_3189 FOREIGN
  KEY(catalogue_version_id) REFERENCES product_catalog_versions (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_full_asset_execution_batches_user_id_users FOREIGN KEY(user_id) REFERENCES users
  (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_asset_execution_batches_user_id ON full_asset_execution_batches (user_id);

CREATE TABLE full_asset_execution_consents (
	portfolio_id UUID NOT NULL, 
	epoch_id UUID NOT NULL, 
	idempotency_key VARCHAR(160) NOT NULL, 
	request JSONB NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	portfolio_hash VARCHAR(64) NOT NULL, 
	evidence_id UUID NOT NULL, 
	evidence_hash VARCHAR(64) NOT NULL, 
	original_evidence JSONB NOT NULL, 
	user_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_full_asset_execution_consents PRIMARY KEY (id), 
	CONSTRAINT fk_full_asset_execution_consents_portfolio_id_full_asse_3ae5 FOREIGN
  KEY(portfolio_id, user_id, epoch_id) REFERENCES full_asset_execution_portfolios (id, user_id,
  epoch_id) ON DELETE RESTRICT, 
	CONSTRAINT uq_full_asset_consent_portfolio UNIQUE (portfolio_id), 
	CONSTRAINT uq_full_asset_consent_key UNIQUE (user_id, epoch_id, idempotency_key), 
	CONSTRAINT ck_full_asset_execution_consents_key CHECK (length(idempotency_key) BETWEEN 1 AND
  160), 
	CONSTRAINT ck_full_asset_execution_consents_hashes CHECK (request_hash ~ '^[0-9a-f]{64}$' AND
  portfolio_hash ~ '^[0-9a-f]{64}$' AND evidence_hash ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_full_asset_execution_consents_json CHECK (jsonb_typeof(request) = 'object' AND
  jsonb_typeof(original_evidence) = 'object' AND octet_length(request::text) <= 1048576 AND
  octet_length(original_evidence::text) <= 1048576), 
	CONSTRAINT uq_full_asset_execution_consents_id UNIQUE (id, user_id), 
	CONSTRAINT fk_full_asset_execution_consents_user_id_users FOREIGN KEY(user_id) REFERENCES
  users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_asset_execution_consents_user_id ON full_asset_execution_consents
  (user_id);
"""

GUARDS = """
CREATE FUNCTION guard_full_asset_execution_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_clock timestamptz; parent_expiry timestamptz;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM audit_epochs WHERE id=NEW.epoch_id AND user_id=NEW.user_id AND
  status='OPEN' AND opened_at <= NEW.created_at)
 THEN RAISE EXCEPTION 'new asset execution metadata requires its actual open owner epoch'; END
  IF;
 IF TG_TABLE_NAME <> 'full_asset_execution_portfolios' THEN
  SELECT created_at, expires_at INTO parent_clock,parent_expiry FROM
  full_asset_execution_portfolios
    WHERE id=NEW.portfolio_id AND user_id=NEW.user_id AND epoch_id=NEW.epoch_id;
  IF parent_clock IS NULL OR NEW.created_at < parent_clock OR NEW.created_at >= parent_expiry
  THEN RAISE EXCEPTION 'asset execution child requires its original unexpired owner portfolio';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION guard_full_asset_execution_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'immutable asset execution original metadata must be retained';
 RETURN NULL;
END $$;
CREATE TRIGGER full_asset_execution_portfolios_insert BEFORE INSERT ON
  full_asset_execution_portfolios FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_insert();
CREATE TRIGGER full_asset_execution_portfolios_immutable BEFORE UPDATE OR DELETE ON
  full_asset_execution_portfolios FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_retention();
CREATE TRIGGER full_asset_execution_portfolios_truncate BEFORE TRUNCATE ON
  full_asset_execution_portfolios FOR EACH STATEMENT EXECUTE FUNCTION
  guard_full_asset_execution_retention();
CREATE TRIGGER full_asset_execution_batches_insert BEFORE INSERT ON
  full_asset_execution_batches FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_insert();
CREATE TRIGGER full_asset_execution_batches_immutable BEFORE UPDATE OR DELETE ON
  full_asset_execution_batches FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_retention();
CREATE TRIGGER full_asset_execution_batches_truncate BEFORE TRUNCATE ON
  full_asset_execution_batches FOR EACH STATEMENT EXECUTE FUNCTION
  guard_full_asset_execution_retention();
CREATE TRIGGER full_asset_execution_consents_insert BEFORE INSERT ON
  full_asset_execution_consents FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_insert();
CREATE TRIGGER full_asset_execution_consents_immutable BEFORE UPDATE OR DELETE ON
  full_asset_execution_consents FOR EACH ROW EXECUTE FUNCTION
  guard_full_asset_execution_retention();
CREATE TRIGGER full_asset_execution_consents_truncate BEFORE TRUNCATE ON
  full_asset_execution_consents FOR EACH STATEMENT EXECUTE FUNCTION
  guard_full_asset_execution_retention();
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM full_asset_execution_portfolios)
 OR EXISTS (SELECT 1 FROM full_asset_execution_batches)
 OR EXISTS (SELECT 1 FROM full_asset_execution_consents)
 THEN RAISE EXCEPTION 'Refusing to discard retained asset execution originals'; END IF;
END $$;
""")
    op.drop_table("full_asset_execution_consents")
    op.drop_table("full_asset_execution_batches")
    op.drop_table("full_asset_execution_portfolios")
    op.execute("DROP FUNCTION guard_full_asset_execution_insert()")
    op.execute("DROP FUNCTION guard_full_asset_execution_retention()")
