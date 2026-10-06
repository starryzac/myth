"""General bank operations with preserved legacy facts and resource reservations."""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0004_execution_bank"
down_revision: str | Sequence[str] | None = "0003_simulated_bank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE bank_operations ( action_plan_id UUID NOT NULL, "
        "legacy_redemption_id UUID, closing_position_id UUID, operation_type "
        "VARCHAR(48) NOT NULL, business_key VARCHAR(160) NOT NULL, idempotency_key "
        "VARCHAR(160) NOT NULL, request JSONB NOT NULL, request_hash VARCHAR(64) NOT "
        "NULL, requested_at TIMESTAMP WITH TIME ZONE NOT NULL, available_at TIMESTAMP "
        "WITH TIME ZONE NOT NULL, settled_at TIMESTAMP WITH TIME ZONE, status "
        "VARCHAR(24) DEFAULT 'ACCEPTED' NOT NULL, user_id UUID NOT NULL, id UUID NOT "
        "NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, CONSTRAINT "
        "pk_bank_operations PRIMARY KEY (id), CONSTRAINT "
        "fk_bank_operations_action_plan_id_action_plans FOREIGN KEY(action_plan_id, "
        "user_id) REFERENCES action_plans (id, user_id) ON DELETE RESTRICT, CONSTRAINT "
        "fk_bank_operations_legacy_redemption_id_simulated_bank__6b40 FOREIGN "
        "KEY(legacy_redemption_id, user_id) REFERENCES simulated_bank_redemptions (id, "
        "user_id) ON DELETE RESTRICT, CONSTRAINT uq_bank_operations_action UNIQUE "
        "(action_plan_id), CONSTRAINT uq_bank_operations_legacy UNIQUE "
        "(legacy_redemption_id), CONSTRAINT uq_bank_operations_key UNIQUE (user_id, "
        "idempotency_key), CONSTRAINT ck_bank_operations_request_hash CHECK "
        "(request_hash ~ '^[0-9a-f]{64}$'), CONSTRAINT "
        "ck_bank_operations_request_object CHECK (jsonb_typeof(request) = 'object'), "
        "CONSTRAINT ck_bank_operations_availability CHECK (available_at >= "
        "requested_at), CONSTRAINT ck_bank_operations_status CHECK (status IN "
        "('ACCEPTED', 'SETTLED', 'UNKNOWN', 'REJECTED')), CONSTRAINT "
        "ck_bank_operations_settlement CHECK ((status = 'SETTLED' AND settled_at IS NOT"
        " NULL AND settled_at >= available_at) OR (status <> 'SETTLED' AND settled_at "
        "IS NULL)), CONSTRAINT uq_bank_operations_id UNIQUE (id, user_id), CONSTRAINT "
        "fk_bank_operations_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON"
        " DELETE RESTRICT )"
    )
    op.execute("CREATE INDEX ix_bank_operations_user_id ON bank_operations (user_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_bank_operations_business ON bank_operations (user_id, "
        "business_key) WHERE status <> 'REJECTED'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_bank_operations_closing_position ON bank_operations "
        "(closing_position_id) WHERE status <> 'REJECTED'"
    )
    op.execute(
        "CREATE TABLE action_resource_reservations ( action_plan_id UUID NOT NULL, "
        "resource_kind VARCHAR(32) NOT NULL, resource_key VARCHAR(160) NOT NULL, "
        "amount_cents BIGINT NOT NULL, status VARCHAR(24) DEFAULT 'RESERVED' NOT NULL, "
        "resolved_at TIMESTAMP WITH TIME ZONE, user_id UUID NOT NULL, id UUID NOT NULL,"
        " created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, CONSTRAINT "
        "pk_action_resource_reservations PRIMARY KEY (id), CONSTRAINT "
        "fk_action_resource_reservations_action_plan_id_action_plans FOREIGN "
        "KEY(action_plan_id, user_id) REFERENCES action_plans (id, user_id) ON DELETE "
        "RESTRICT, CONSTRAINT uq_action_resource UNIQUE (action_plan_id, resource_kind,"
        " resource_key), CONSTRAINT ck_action_resource_reservations_positive_amount "
        "CHECK (amount_cents > 0), CONSTRAINT "
        "ck_action_resource_reservations_resource_kind CHECK (resource_kind IN ('CASH',"
        " 'INCOME', 'GOAL_CASH', 'MANAGED', 'POSITION', 'OBLIGATION', 'BUSINESS')), "
        "CONSTRAINT ck_action_resource_reservations_status CHECK (status IN "
        "('RESERVED', 'CONSUMED', 'RELEASED')), CONSTRAINT "
        "ck_action_resource_reservations_resolution CHECK ((status = 'RESERVED' AND "
        "resolved_at IS NULL) OR (status <> 'RESERVED' AND resolved_at IS NOT NULL)), "
        "CONSTRAINT uq_action_resource_reservations_id UNIQUE (id, user_id), CONSTRAINT"
        " fk_action_resource_reservations_user_id_users FOREIGN KEY(user_id) REFERENCES"
        " users (id) ON DELETE RESTRICT )"
    )
    op.execute(
        "CREATE INDEX ix_action_resource_active ON action_resource_reservations "
        "(user_id, resource_kind, resource_key, status)"
    )
    op.execute(
        "CREATE INDEX ix_action_resource_reservations_user_id ON "
        "action_resource_reservations (user_id)"
    )
    op.execute("ALTER TABLE simulated_bank_postings ALTER COLUMN ledger_key TYPE VARCHAR(160)")
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD COLUMN ledger_dimension VARCHAR(32) "
        "NOT NULL DEFAULT 'ECONOMIC', ADD COLUMN ledger_metadata JSONB NOT NULL DEFAULT"
        " '{}'::jsonb, ADD COLUMN operation_id UUID, ADD COLUMN leg_ref VARCHAR(160)"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings DROP CONSTRAINT "
        "fk_simulated_bank_postings_position_id_asset_positions, DROP CONSTRAINT "
        "ck_simulated_bank_postings_ledger_identity, DROP CONSTRAINT "
        "ck_simulated_bank_postings_entry"
    )
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_evidence_level")
    op.execute(
        "INSERT INTO bank_operations "
        "(id,user_id,created_at,action_plan_id,legacy_redemption_id,closing_position_id,operation_type,business_key,idempotency_key,request,request_hash,requested_at,available_at,settled_at,status)"
        " SELECT "
        "id,user_id,created_at,action_plan_id,id,position_id,'LEGACY_REDEMPTION','close:'"
        " || "
        "position_id::text,idempotency_key,request,request_hash,requested_at,available_at,settled_at,status"
        " FROM simulated_bank_redemptions"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings DISABLE TRIGGER simulated_bank_postings_immutable"
    )
    op.execute(
        "UPDATE simulated_bank_postings SET "
        "operation_id=redemption_id,leg_ref=entry_kind WHERE redemption_id IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ENABLE TRIGGER simulated_bank_postings_immutable"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "ck_simulated_bank_postings_entry CHECK ((entry_kind = 'OPENING' AND "
        "redemption_id IS NULL AND operation_id IS NULL AND leg_ref IS NULL AND "
        "sequence_number = 1 AND previous_posting_id IS NULL AND balance_before_cents ="
        " 0 AND delta_cents >= 0) OR (entry_kind <> 'OPENING' AND operation_id IS NOT "
        "NULL AND leg_ref IS NOT NULL AND previous_posting_id IS NOT NULL AND "
        "sequence_number > 1))"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "ck_simulated_bank_postings_ledger_identity CHECK ((ledger_dimension = "
        "'ECONOMIC' AND ((account_id IS NOT NULL AND position_id IS NULL AND ledger_key"
        " = 'CASH:' || account_id::text) OR (account_id IS NULL AND position_id IS NOT "
        "NULL AND ledger_key = 'POSITION:' || position_id::text) OR (account_id IS NULL"
        " AND position_id IS NULL AND (ledger_key LIKE 'PAYEE:%%' OR ledger_key LIKE "
        "'FEE:%%' OR ledger_key LIKE 'LOSS:%%')))) OR (ledger_dimension IN "
        "('GOAL_OWNERSHIP', 'INCOME_LOCATION', 'LIABILITY') AND position_id IS NULL))"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "ck_simulated_bank_postings_ledger_metadata CHECK "
        "(jsonb_typeof(ledger_metadata) = 'object')"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "fk_simulated_bank_postings_operation_id_bank_operations FOREIGN "
        "KEY(operation_id, user_id) REFERENCES bank_operations (id, user_id) ON DELETE "
        "RESTRICT"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "uq_bank_posting_operation_leg UNIQUE (operation_id, leg_ref)"
    )
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_evidence_level "
        "CHECK (evidence_level IN ('BANK_CONFIRMED', 'BANK_OBSERVED', 'USER_DECLARED', "
        "'MODEL_INFERRED', 'USER_CONFIRMED_POLICY', 'USER_CONFIRMED_ACTION'))"
    )
    op.execute("""
        CREATE FUNCTION protect_bank_operation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW)-'status'-'settled_at') IS DISTINCT FROM
               (to_jsonb(OLD)-'status'-'settled_at') OR
               (OLD.status IN ('SETTLED','REJECTED') AND
                to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) OR
               (NEW.status = 'ACCEPTED' AND OLD.status <> 'ACCEPTED') THEN
                RAISE EXCEPTION 'Bank operation economics and terminal outcomes are immutable'
                USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER bank_operations_immutable BEFORE UPDATE ON bank_operations FOR "
        "EACH ROW EXECUTE FUNCTION protect_bank_operation()"
    )
    op.execute(
        "CREATE FUNCTION protect_action_resource() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN IF (to_jsonb(NEW)-'status'-'resolved_at') IS DISTINCT FROM "
        "(to_jsonb(OLD)-'status'-'resolved_at') OR (OLD.status<>'RESERVED' AND "
        "to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN RAISE EXCEPTION 'Action "
        "resource identity and resolved outcome are immutable' USING ERRCODE='23514'; "
        "END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER action_resource_immutable BEFORE UPDATE ON "
        "action_resource_reservations FOR EACH ROW EXECUTE FUNCTION "
        "protect_action_resource()"
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline downgrade is disabled")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Downgrade requires a generated bf_test_<32 hex> database")
    if (
        op.get_bind()
        .exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM bank_operations WHERE legacy_redemption_id IS NULL)"
        )
        .scalar()
    ):
        raise ValueError("Generic economic history cannot be represented by the old schema")
    op.execute(
        "ALTER TABLE simulated_bank_postings DROP CONSTRAINT "
        "ck_simulated_bank_postings_entry, DROP CONSTRAINT "
        "ck_simulated_bank_postings_ledger_identity, DROP CONSTRAINT "
        "ck_simulated_bank_postings_ledger_metadata, DROP CONSTRAINT "
        "uq_bank_posting_operation_leg, DROP CONSTRAINT "
        "fk_simulated_bank_postings_operation_id_bank_operations"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings DROP COLUMN operation_id, DROP COLUMN "
        "leg_ref, DROP COLUMN ledger_dimension, DROP COLUMN ledger_metadata, ALTER "
        "COLUMN ledger_key TYPE VARCHAR(64)"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "fk_simulated_bank_postings_position_id_asset_positions FOREIGN "
        "KEY(position_id,user_id) REFERENCES asset_positions(id,user_id) ON DELETE "
        "RESTRICT"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "ck_simulated_bank_postings_ledger_identity CHECK ((account_id IS NOT NULL AND "
        "position_id IS NULL AND ledger_key = 'CASH:' || account_id::text) OR "
        "(account_id IS NULL AND position_id IS NOT NULL AND ledger_key = 'POSITION:' "
        "|| position_id::text))"
    )
    op.execute(
        "ALTER TABLE simulated_bank_postings ADD CONSTRAINT "
        "ck_simulated_bank_postings_entry CHECK ((entry_kind = 'OPENING' AND "
        "redemption_id IS NULL AND sequence_number = 1 AND previous_posting_id IS NULL "
        "AND balance_before_cents = 0 AND delta_cents >= 0) OR (entry_kind = "
        "'PRINCIPAL_DEBIT' AND redemption_id IS NOT NULL AND position_id IS NOT NULL "
        "AND previous_posting_id IS NOT NULL AND sequence_number > 1 AND delta_cents < "
        "0) OR (entry_kind = 'CASH_CREDIT' AND redemption_id IS NOT NULL AND account_id"
        " IS NOT NULL AND previous_posting_id IS NOT NULL AND sequence_number > 1 AND "
        "delta_cents > 0))"
    )
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_evidence_level")
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_evidence_level "
        "CHECK (evidence_level IN "
        "('BANK_CONFIRMED','BANK_OBSERVED','USER_DECLARED','MODEL_INFERRED','USER_CONFIRMED_POLICY'))"
    )
    op.execute("DROP TABLE action_resource_reservations")
    op.execute("DROP TABLE bank_operations")
    op.execute("DROP FUNCTION protect_action_resource()")
    op.execute("DROP FUNCTION protect_bank_operation()")
