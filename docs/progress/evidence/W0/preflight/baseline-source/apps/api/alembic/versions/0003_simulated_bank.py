"""Independent simulated bank requests and immutable economic postings."""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0003_simulated_bank"
down_revision: str | Sequence[str] | None = "0002_immutable_policy_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE simulated_bank_redemptions (
        action_plan_id UUID NOT NULL,
        position_id UUID NOT NULL,
        destination_account_id UUID NOT NULL,
        product_id UUID NOT NULL,
        goal_id UUID,
        principal_cents BIGINT NOT NULL,
        idempotency_key VARCHAR(160) NOT NULL,
        request JSONB NOT NULL,
        request_hash VARCHAR(64) NOT NULL,
        requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
        available_at TIMESTAMP WITH TIME ZONE NOT NULL,
        settled_at TIMESTAMP WITH TIME ZONE,
        status VARCHAR(24) DEFAULT 'ACCEPTED' NOT NULL,
        user_id UUID NOT NULL,
        id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_simulated_bank_redemptions PRIMARY KEY (id),
        CONSTRAINT fk_simulated_bank_redemptions_action_plan_id_action_plans FOREIGN
        KEY(action_plan_id, user_id) REFERENCES action_plans (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_simulated_bank_redemptions_position_id_asset_positions FOREIGN
        KEY(position_id, user_id) REFERENCES asset_positions (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_simulated_bank_redemptions_destination_account_id_accounts FOREIGN
        KEY(destination_account_id, user_id) REFERENCES accounts (id, user_id) ON DELETE
        RESTRICT,
        CONSTRAINT fk_simulated_bank_redemptions_goal_id_goals FOREIGN KEY(goal_id, user_id)
        REFERENCES goals (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_simulated_bank_redemptions_action UNIQUE (action_plan_id),
        CONSTRAINT uq_simulated_bank_redemptions_position UNIQUE (position_id),
        CONSTRAINT uq_simulated_bank_redemptions_key UNIQUE (user_id, idempotency_key),
        CONSTRAINT ck_simulated_bank_redemptions_principal CHECK (principal_cents > 0),
        CONSTRAINT ck_simulated_bank_redemptions_request_hash CHECK (request_hash ~
        '^[0-9a-f]{64}$'),
        CONSTRAINT ck_simulated_bank_redemptions_request_object CHECK (jsonb_typeof(request) =
        'object'),
        CONSTRAINT ck_simulated_bank_redemptions_availability CHECK (available_at >=
        requested_at),
        CONSTRAINT ck_simulated_bank_redemptions_status CHECK (status IN ('ACCEPTED', 'SETTLED',
        'UNKNOWN')),
        CONSTRAINT ck_simulated_bank_redemptions_settlement CHECK ((status = 'SETTLED' AND
        settled_at IS NOT NULL AND settled_at >= available_at) OR (status <> 'SETTLED' AND
        settled_at IS NULL)),
        CONSTRAINT uq_simulated_bank_redemptions_id UNIQUE (id, user_id),
        CONSTRAINT fk_simulated_bank_redemptions_product_id_asset_products FOREIGN
        KEY(product_id) REFERENCES asset_products (id) ON DELETE RESTRICT,
        CONSTRAINT fk_simulated_bank_redemptions_user_id_users FOREIGN KEY(user_id) REFERENCES
        users (id) ON DELETE RESTRICT
        )
    """
    )
    op.execute(
        """
        CREATE TABLE simulated_bank_postings (
        ledger_key VARCHAR(64) NOT NULL,
        account_id UUID,
        position_id UUID,
        redemption_id UUID,
        previous_posting_id UUID,
        sequence_number INTEGER NOT NULL,
        entry_kind VARCHAR(32) NOT NULL,
        balance_before_cents BIGINT NOT NULL,
        delta_cents BIGINT NOT NULL,
        balance_after_cents BIGINT NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        user_id UUID NOT NULL,
        id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_simulated_bank_postings PRIMARY KEY (id),
        CONSTRAINT fk_simulated_bank_postings_account_id_accounts FOREIGN KEY(account_id,
        user_id) REFERENCES accounts (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_simulated_bank_postings_position_id_asset_positions FOREIGN
        KEY(position_id, user_id) REFERENCES asset_positions (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_simulated_bank_postings_redemption_id_simulated_bank_f434 FOREIGN
        KEY(redemption_id, user_id) REFERENCES simulated_bank_redemptions (id, user_id) ON DELETE
        RESTRICT,
        CONSTRAINT fk_simulated_bank_postings_previous_posting_id_simulate_82b5 FOREIGN
        KEY(previous_posting_id, user_id) REFERENCES simulated_bank_postings (id, user_id) ON
        DELETE RESTRICT,
        CONSTRAINT uq_bank_posting_sequence UNIQUE (user_id, ledger_key, sequence_number),
        CONSTRAINT uq_bank_posting_redemption_leg UNIQUE (redemption_id, entry_kind),
        CONSTRAINT ck_simulated_bank_postings_sequence CHECK (sequence_number > 0),
        CONSTRAINT ck_simulated_bank_postings_conservation CHECK (balance_before_cents >= 0 AND
        balance_after_cents >= 0 AND balance_after_cents = balance_before_cents + delta_cents),
        CONSTRAINT ck_simulated_bank_postings_ledger_identity CHECK ((account_id IS NOT NULL AND
        position_id IS NULL AND ledger_key = 'CASH:' || account_id::text) OR (account_id IS NULL
        AND position_id IS NOT NULL AND ledger_key = 'POSITION:' || position_id::text)),
        CONSTRAINT ck_simulated_bank_postings_entry CHECK ((entry_kind = 'OPENING' AND
        redemption_id IS NULL AND sequence_number = 1 AND previous_posting_id IS NULL AND
        balance_before_cents = 0 AND delta_cents >= 0) OR (entry_kind = 'PRINCIPAL_DEBIT' AND
        redemption_id IS NOT NULL AND position_id IS NOT NULL AND previous_posting_id IS NOT NULL
        AND sequence_number > 1 AND delta_cents < 0) OR (entry_kind = 'CASH_CREDIT' AND
        redemption_id IS NOT NULL AND account_id IS NOT NULL AND previous_posting_id IS NOT NULL
        AND sequence_number > 1 AND delta_cents > 0)),
        CONSTRAINT uq_simulated_bank_postings_id UNIQUE (id, user_id),
        CONSTRAINT fk_simulated_bank_postings_user_id_users FOREIGN KEY(user_id) REFERENCES users
        (id) ON DELETE RESTRICT
        )
    """
    )
    op.execute(
        "CREATE INDEX ix_simulated_bank_redemptions_user_id ON simulated_bank_redemptions (user_id)"
    )
    op.execute(
        "CREATE INDEX ix_simulated_bank_postings_user_id ON simulated_bank_postings (user_id)"
    )
    op.execute("""
        CREATE FUNCTION protect_simulated_bank_posting() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Bank postings are immutable economic facts' USING ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER simulated_bank_postings_immutable BEFORE UPDATE ON simulated_bank_postings
        FOR EACH ROW EXECUTE FUNCTION protect_simulated_bank_posting()
    """)
    op.execute("""
        CREATE FUNCTION protect_simulated_bank_request() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status' - 'settled_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'settled_at') OR
               (OLD.status = 'SETTLED' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
                RAISE EXCEPTION 'Bank request economics are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER simulated_bank_redemptions_immutable
        BEFORE UPDATE ON simulated_bank_redemptions
        FOR EACH ROW EXECUTE FUNCTION protect_simulated_bank_request()
    """)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline downgrade is disabled; use a generated bf_test_ database")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Downgrade requires a generated bf_test_<32 hex> database")
    op.drop_table("simulated_bank_postings")
    op.drop_table("simulated_bank_redemptions")
    op.execute("DROP FUNCTION protect_simulated_bank_posting()")
    op.execute("DROP FUNCTION protect_simulated_bank_request()")
