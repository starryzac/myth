"""Retained intervention messages, separate from all financial delivery protocols."""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_intervention_delivery"
down_revision: str | Sequence[str] | None = "0011_transaction_category_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES_SQL = """
CREATE TABLE intervention_outbox (
	epoch_id UUID NOT NULL, 
	protocol_version VARCHAR(40) DEFAULT 'full-intervention-message-v1' NOT NULL, 
	source_kind VARCHAR(32) NOT NULL, 
	source_run_id UUID NOT NULL, 
	source_trace_hash VARCHAR(64) NOT NULL, 
	semantic_key VARCHAR(64) NOT NULL, 
	session_id UUID, 
	question_id UUID, 
	question_revision INTEGER, 
	payload JSONB NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	state VARCHAR(24) DEFAULT 'PENDING' NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	invalidation_reason TEXT, 
	user_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_intervention_outbox PRIMARY KEY (id), 
        CONSTRAINT fk_intervention_outbox_epoch_id_audit_epochs FOREIGN KEY(epoch_id, user_id)
    REFERENCES audit_epochs (id, user_id) ON DELETE RESTRICT,
	CONSTRAINT uq_intervention_semantic UNIQUE (user_id, epoch_id, semantic_key), 
        CONSTRAINT ck_intervention_outbox_protocol CHECK (protocol_version =
    'full-intervention-message-v1'),
        CONSTRAINT ck_intervention_outbox_hashes CHECK (source_trace_hash ~ '^[0-9a-f]{64}$' AND
    semantic_key ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_intervention_outbox_source CHECK ((source_kind = 'QUESTION' AND session_id
    IS NOT NULL AND question_id IS NOT NULL AND question_revision IS NOT NULL AND
    question_revision >= 1) OR (source_kind = 'SINGLE_ACTION_BOUNDARY' AND session_id IS NULL
    AND question_id IS NULL AND question_revision IS NULL)),
        CONSTRAINT ck_intervention_outbox_payload CHECK (jsonb_typeof(payload) = 'object' AND
    octet_length(payload::text) <= 1048576),
        CONSTRAINT ck_intervention_outbox_state CHECK (state IN ('PENDING', 'ACKNOWLEDGED',
    'INVALIDATED', 'RECORDED_ONLY', 'DEFERRED')),
        CONSTRAINT ck_intervention_outbox_time CHECK (available_at >= created_at AND updated_at
    >= created_at),
	CONSTRAINT uq_intervention_outbox_id UNIQUE (id, user_id), 
        CONSTRAINT fk_intervention_outbox_user_id_users FOREIGN KEY(user_id) REFERENCES users
    (id) ON DELETE RESTRICT
)

;
CREATE INDEX ix_intervention_outbox_user_id ON intervention_outbox (user_id);
CREATE INDEX ix_intervention_pending ON intervention_outbox (user_id, epoch_id, state,
    available_at, id);

CREATE TABLE intervention_inbox (
	outbox_id UUID NOT NULL, 
	consumer_ref VARCHAR(80) DEFAULT 'intervention-center-v1' NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	state VARCHAR(24) DEFAULT 'RECEIVED' NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	acknowledged_at TIMESTAMP WITH TIME ZONE, 
	acknowledgment_key VARCHAR(160), 
	acknowledgment_request JSONB, 
	acknowledgment_request_hash VARCHAR(64), 
	original_receipt JSONB, 
	user_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_intervention_inbox PRIMARY KEY (id), 
        CONSTRAINT fk_intervention_inbox_outbox_id_intervention_outbox FOREIGN KEY(outbox_id,
    user_id) REFERENCES intervention_outbox (id, user_id) ON DELETE RESTRICT,
	CONSTRAINT uq_intervention_consumer UNIQUE (outbox_id, consumer_ref), 
	CONSTRAINT uq_intervention_ack_key UNIQUE (user_id, acknowledgment_key), 
        CONSTRAINT ck_intervention_inbox_identity CHECK (consumer_ref = 'intervention-center-v1'
    AND payload_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_intervention_inbox_state CHECK (state IN ('RECEIVED', 'ACKNOWLEDGED',
    'INVALIDATED')),
        CONSTRAINT ck_intervention_inbox_acknowledgment CHECK ((state = 'ACKNOWLEDGED' AND
    acknowledged_at IS NOT NULL AND acknowledgment_key IS NOT NULL AND
    length(acknowledgment_key) BETWEEN 1 AND 160 AND acknowledgment_request IS NOT NULL AND
    acknowledgment_request_hash IS NOT NULL AND original_receipt IS NOT NULL AND
    acknowledgment_request_hash ~ '^[0-9a-f]{64}$') OR (state <> 'ACKNOWLEDGED' AND
    acknowledged_at IS NULL AND acknowledgment_key IS NULL AND acknowledgment_request IS NULL
    AND acknowledgment_request_hash IS NULL AND original_receipt IS NULL)),
        CONSTRAINT ck_intervention_inbox_json CHECK ((acknowledgment_request IS NULL OR
    (jsonb_typeof(acknowledgment_request) = 'object' AND
    octet_length(acknowledgment_request::text) <= 1048576)) AND (original_receipt IS NULL OR
    (jsonb_typeof(original_receipt) = 'object' AND octet_length(original_receipt::text) <=
    1048576))),
        CONSTRAINT ck_intervention_inbox_time CHECK (received_at >= created_at AND updated_at >=
    received_at AND (acknowledged_at IS NULL OR acknowledged_at >= received_at)),
	CONSTRAINT uq_intervention_inbox_id UNIQUE (id, user_id), 
        CONSTRAINT fk_intervention_inbox_user_id_users FOREIGN KEY(user_id) REFERENCES users
    (id) ON DELETE RESTRICT
)

;
CREATE INDEX ix_intervention_inbox_user_id ON intervention_inbox (user_id);
"""

GUARDS = r"""
CREATE FUNCTION guard_intervention_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.id,NEW.user_id,NEW.created_at,NEW.epoch_id,NEW.protocol_version,
     NEW.source_kind,NEW.source_run_id,NEW.source_trace_hash,NEW.semantic_key,
     NEW.session_id,NEW.question_id,NEW.question_revision,NEW.payload,NEW.payload_hash)
 IS DISTINCT FROM
    (OLD.id,OLD.user_id,OLD.created_at,OLD.epoch_id,OLD.protocol_version,
     OLD.source_kind,OLD.source_run_id,OLD.source_trace_hash,OLD.semantic_key,
     OLD.session_id,OLD.question_id,OLD.question_revision,OLD.payload,OLD.payload_hash)
 THEN RAISE EXCEPTION 'intervention original is immutable'; END IF;
 IF NEW.updated_at < OLD.updated_at THEN
  RAISE EXCEPTION 'intervention metadata clock cannot go backwards';
 END IF;
 IF OLD.state IN ('ACKNOWLEDGED','INVALIDATED') AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'intervention terminal state is immutable';
 ELSIF OLD.state = 'RECORDED_ONLY' AND NEW.state NOT IN ('RECORDED_ONLY','INVALIDATED') THEN
  RAISE EXCEPTION 'recorded intervention cannot become a question';
 ELSIF OLD.state = 'DEFERRED' AND NEW.state NOT IN ('DEFERRED','PENDING','INVALIDATED') THEN
  RAISE EXCEPTION 'invalid deferred intervention transition';
 ELSIF OLD.state = 'PENDING' AND NEW.state NOT IN
    ('PENDING','ACKNOWLEDGED','INVALIDATED','DEFERRED') THEN
  RAISE EXCEPTION 'invalid pending intervention transition';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER intervention_outbox_update BEFORE UPDATE ON intervention_outbox
FOR EACH ROW EXECUTE FUNCTION guard_intervention_outbox();

CREATE FUNCTION guard_intervention_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE original_hash TEXT;
BEGIN
 SELECT payload_hash INTO STRICT original_hash FROM intervention_outbox
 WHERE id=NEW.outbox_id AND user_id=NEW.user_id;
 IF NEW.payload_hash IS DISTINCT FROM original_hash THEN
  RAISE EXCEPTION 'intervention inbox must bind the original payload';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.user_id,NEW.created_at,NEW.outbox_id,NEW.consumer_ref,
      NEW.payload_hash,NEW.received_at)
  IS DISTINCT FROM
     (OLD.id,OLD.user_id,OLD.created_at,OLD.outbox_id,OLD.consumer_ref,
      OLD.payload_hash,OLD.received_at)
  THEN RAISE EXCEPTION 'intervention consumer identity is immutable'; END IF;
  IF NEW.updated_at < OLD.updated_at THEN
   RAISE EXCEPTION 'intervention consumer clock cannot go backwards';
  END IF;
  IF OLD.state <> 'RECEIVED' AND NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'intervention acknowledgment is immutable';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER intervention_inbox_write BEFORE INSERT OR UPDATE ON intervention_inbox
FOR EACH ROW EXECUTE FUNCTION guard_intervention_inbox();

CREATE FUNCTION guard_intervention_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'retained intervention history cannot be removed';
 RETURN NULL;
END $$;
CREATE TRIGGER intervention_outbox_delete BEFORE DELETE ON intervention_outbox
FOR EACH ROW EXECUTE FUNCTION guard_intervention_retention();
CREATE TRIGGER intervention_inbox_delete BEFORE DELETE ON intervention_inbox
FOR EACH ROW EXECUTE FUNCTION guard_intervention_retention();
CREATE TRIGGER intervention_outbox_truncate BEFORE TRUNCATE ON intervention_outbox
FOR EACH STATEMENT EXECUTE FUNCTION guard_intervention_retention();
CREATE TRIGGER intervention_inbox_truncate BEFORE TRUNCATE ON intervention_inbox
FOR EACH STATEMENT EXECUTE FUNCTION guard_intervention_retention();
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM intervention_outbox) OR EXISTS (SELECT 1 FROM intervention_inbox)
 THEN RAISE EXCEPTION 'Refusing to discard retained intervention history'; END IF;
END $$;
""")
    op.drop_table("intervention_inbox")
    op.drop_table("intervention_outbox")
    op.execute("DROP FUNCTION guard_intervention_inbox()")
    op.execute("DROP FUNCTION guard_intervention_outbox()")
    op.execute("DROP FUNCTION guard_intervention_retention()")
