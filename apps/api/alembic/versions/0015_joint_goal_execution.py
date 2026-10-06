"""Retained whole joint plans and fixed child identities; no existing history edits."""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_joint_goal_execution"
down_revision: str | Sequence[str] | None = "0014_global_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES_SQL = """
CREATE TABLE full_joint_goal_execution_plans (
        epoch_id UUID NOT NULL,
        full_policy_id UUID NOT NULL,
        full_policy_version_id UUID NOT NULL,
        idempotency_key VARCHAR(160) NOT NULL,
        request JSONB NOT NULL,
        request_hash VARCHAR(64) NOT NULL,
        plan JSONB NOT NULL,
        plan_hash VARCHAR(64) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        user_id UUID NOT NULL,
        id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_full_joint_goal_execution_plans PRIMARY KEY (id),
        CONSTRAINT fk_full_joint_goal_execution_plans_epoch_id_audit_epochs FOREIGN
  KEY(epoch_id, user_id) REFERENCES audit_epochs (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_full_joint_goal_execution_plans_full_policy_id_full_policies FOREIGN
  KEY(full_policy_id, epoch_id, user_id) REFERENCES full_policies (id, epoch_id, user_id) ON
  DELETE RESTRICT,
        CONSTRAINT fk_full_joint_goal_execution_plans_full_policy_version__68c6 FOREIGN
  KEY(full_policy_version_id, full_policy_id, user_id) REFERENCES full_policy_versions (id,
  policy_id, user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_full_joint_plan_owner_epoch UNIQUE (id, user_id, epoch_id),
        CONSTRAINT uq_full_joint_plan_key UNIQUE (user_id, epoch_id, idempotency_key),
        CONSTRAINT ck_full_joint_goal_execution_plans_key CHECK (length(idempotency_key) BETWEEN
  1 AND 160),
        CONSTRAINT ck_full_joint_goal_execution_plans_hashes CHECK (request_hash ~
  '^[0-9a-f]{64}$' AND plan_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_full_joint_goal_execution_plans_json CHECK (jsonb_typeof(request) =
  'object' AND jsonb_typeof(plan) = 'object' AND octet_length(request::text) <= 1048576 AND
  octet_length(plan::text) <= 10485760),
        CONSTRAINT ck_full_joint_goal_execution_plans_time CHECK (expires_at > created_at),
        CONSTRAINT uq_full_joint_goal_execution_plans_id UNIQUE (id, user_id),
        CONSTRAINT fk_full_joint_goal_execution_plans_user_id_users FOREIGN KEY(user_id)
  REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_joint_goal_execution_plans_user_id ON full_joint_goal_execution_plans
  (user_id);

CREATE TABLE full_joint_goal_execution_children (
        plan_id UUID NOT NULL,
        epoch_id UUID NOT NULL,
        child_number INTEGER NOT NULL,
        goal_id UUID NOT NULL,
        original_mvp_version_id UUID NOT NULL,
        action_plan_id UUID NOT NULL,
        bank_idempotency_key VARCHAR(160) NOT NULL,
        command JSONB NOT NULL,
        command_hash VARCHAR(64) NOT NULL,
        user_id UUID NOT NULL,
        id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_full_joint_goal_execution_children PRIMARY KEY (id),
        CONSTRAINT fk_full_joint_goal_execution_children_plan_id_full_join_5582 FOREIGN
  KEY(plan_id, user_id, epoch_id) REFERENCES full_joint_goal_execution_plans (id, user_id,
  epoch_id) ON DELETE RESTRICT,
        CONSTRAINT uq_full_joint_child_order UNIQUE (plan_id, child_number),
        CONSTRAINT uq_full_joint_child_action UNIQUE (action_plan_id),
        CONSTRAINT uq_full_joint_child_bank_key UNIQUE (user_id, bank_idempotency_key),
        CONSTRAINT ck_full_joint_goal_execution_children_identity CHECK (child_number BETWEEN 1
  AND 8 AND length(bank_idempotency_key) BETWEEN 1 AND 160),
        CONSTRAINT ck_full_joint_goal_execution_children_hashes CHECK (command_hash ~
  '^[0-9a-f]{64}$'),
        CONSTRAINT ck_full_joint_goal_execution_children_json CHECK (jsonb_typeof(command) =
  'object' AND octet_length(command::text) <= 1048576),
        CONSTRAINT uq_full_joint_goal_execution_children_id UNIQUE (id, user_id),
        CONSTRAINT fk_full_joint_goal_execution_children_user_id_users FOREIGN KEY(user_id)
  REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_joint_goal_execution_children_user_id ON full_joint_goal_execution_children
  (user_id);

CREATE TABLE full_joint_goal_execution_consents (
        plan_id UUID NOT NULL,
        epoch_id UUID NOT NULL,
        idempotency_key VARCHAR(160) NOT NULL,
        request JSONB NOT NULL,
        request_hash VARCHAR(64) NOT NULL,
        plan_hash VARCHAR(64) NOT NULL,
        evidence_id UUID NOT NULL,
        evidence_hash VARCHAR(64) NOT NULL,
        original_evidence JSONB NOT NULL,
        user_id UUID NOT NULL,
        id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_full_joint_goal_execution_consents PRIMARY KEY (id),
        CONSTRAINT fk_full_joint_goal_execution_consents_plan_id_full_join_2e2b FOREIGN
  KEY(plan_id, user_id, epoch_id) REFERENCES full_joint_goal_execution_plans (id, user_id,
  epoch_id) ON DELETE RESTRICT,
        CONSTRAINT uq_full_joint_consent_plan UNIQUE (plan_id),
        CONSTRAINT uq_full_joint_consent_key UNIQUE (user_id, epoch_id, idempotency_key),
        CONSTRAINT ck_full_joint_goal_execution_consents_key CHECK (length(idempotency_key)
  BETWEEN 1 AND 160),
        CONSTRAINT ck_full_joint_goal_execution_consents_hashes CHECK (request_hash ~
  '^[0-9a-f]{64}$' AND plan_hash ~ '^[0-9a-f]{64}$' AND evidence_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_full_joint_goal_execution_consents_json CHECK (jsonb_typeof(request) =
  'object' AND jsonb_typeof(original_evidence) = 'object' AND octet_length(request::text) <=
  1048576 AND octet_length(original_evidence::text) <= 1048576),
        CONSTRAINT uq_full_joint_goal_execution_consents_id UNIQUE (id, user_id),
        CONSTRAINT fk_full_joint_goal_execution_consents_user_id_users FOREIGN KEY(user_id)
  REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_full_joint_goal_execution_consents_user_id ON full_joint_goal_execution_consents
  (user_id);

"""

GUARDS = """
CREATE FUNCTION guard_full_joint_goal_execution_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_clock timestamptz; parent_expiry timestamptz;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM audit_epochs WHERE id=NEW.epoch_id AND user_id=NEW.user_id
  AND status='OPEN' AND opened_at <= NEW.created_at)
 THEN RAISE EXCEPTION 'new joint execution metadata requires its actual open owner epoch'; END IF;
 IF TG_TABLE_NAME <> 'full_joint_goal_execution_plans' THEN
  SELECT created_at, expires_at INTO parent_clock,parent_expiry
   FROM full_joint_goal_execution_plans
   WHERE id=NEW.plan_id AND user_id=NEW.user_id AND epoch_id=NEW.epoch_id;
  IF parent_clock IS NULL OR NEW.created_at < parent_clock OR NEW.created_at >= parent_expiry
  THEN RAISE EXCEPTION 'joint execution child requires its original unexpired owner plan'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION guard_full_joint_goal_execution_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'immutable joint execution originals must be retained';
 RETURN NULL;
END $$;
DO $$ DECLARE table_name text; BEGIN
 FOREACH table_name IN ARRAY ARRAY['full_joint_goal_execution_plans',
  'full_joint_goal_execution_children', 'full_joint_goal_execution_consents'] LOOP
  EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION ' ||
   'guard_full_joint_goal_execution_insert()', table_name || '_insert', table_name);
  EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ' ||
   'guard_full_joint_goal_execution_retention()', table_name || '_immutable', table_name);
  EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION ' ||
   'guard_full_joint_goal_execution_retention()', table_name || '_truncate', table_name);
 END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM full_joint_goal_execution_plans)
 OR EXISTS (SELECT 1 FROM full_joint_goal_execution_children)
 OR EXISTS (SELECT 1 FROM full_joint_goal_execution_consents)
 THEN RAISE EXCEPTION 'Refusing to discard retained joint execution originals'; END IF;
END $$;
""")
    op.drop_table("full_joint_goal_execution_consents")
    op.drop_table("full_joint_goal_execution_children")
    op.drop_table("full_joint_goal_execution_plans")
    op.execute("DROP FUNCTION guard_full_joint_goal_execution_insert()")
    op.execute("DROP FUNCTION guard_full_joint_goal_execution_retention()")
