-- +goose Up
ALTER TABLE outbox ADD COLUMN event_sequence bigint GENERATED ALWAYS AS IDENTITY;
CREATE UNIQUE INDEX outbox_sequence_identity ON outbox(event_sequence);
CREATE INDEX outbox_user_activity ON outbox(environment_id,user_id,created_at DESC,id DESC) INCLUDE(event_sequence,kind,aggregate_id,aggregate_version);

CREATE TABLE operation_projection_events (
 event_sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 environment_id uuid NOT NULL, user_id uuid NOT NULL, operation_id uuid NOT NULL,
 aggregate_version bigint NOT NULL CHECK(aggregate_version>0), previous_version bigint NOT NULL CHECK(previous_version>=0 AND previous_version<aggregate_version),
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'), created_at timestamptz NOT NULL,
 UNIQUE(environment_id,user_id,operation_id,aggregate_version),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE INDEX operation_projection_pending ON operation_projection_events(environment_id,user_id,event_sequence);
CREATE TABLE operation_read_models (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, operation_id uuid NOT NULL,
 source_sequence bigint NOT NULL REFERENCES operation_projection_events(event_sequence),
 aggregate_version bigint NOT NULL CHECK(aggregate_version>0), snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE INDEX operation_read_models_recent ON operation_read_models(environment_id,user_id,created_at DESC,operation_id DESC);
CREATE TABLE operation_projection_offsets (
 environment_id uuid NOT NULL, user_id uuid NOT NULL,
 last_sequence bigint NOT NULL DEFAULT 0 CHECK(last_sequence>=0), projected_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id), FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);

-- +goose StatementBegin
CREATE FUNCTION operation_display_snapshot(o operations) RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
 SELECT jsonb_build_object('id',o.id,'plan_id',o.plan_id,'state',o.status,
  'amount_cents',convert_from(o.bank_request,'UTF8')::jsonb->>'amount_cents',
  'bank_key',o.bank_key,'bank_observed_status',o.bank_observed_status,
  'processing_health',o.processing_health,'receipt_id',o.receipt_id,
  'created_at',o.created_at,'updated_at',o.updated_at,'version',o.version);
$$;
CREATE FUNCTION capture_operation_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND operation_display_snapshot(NEW)=operation_display_snapshot(OLD) THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND NEW.version<=OLD.version THEN
  RAISE EXCEPTION 'operation display version must advance' USING ERRCODE='23514';
 END IF;
 INSERT INTO operation_projection_events(environment_id,user_id,operation_id,aggregate_version,previous_version,snapshot,created_at)
 VALUES(NEW.environment_id,NEW.user_id,NEW.id,NEW.version,CASE WHEN TG_OP='INSERT' THEN 0 ELSE OLD.version END,operation_display_snapshot(NEW),NEW.created_at);
 RETURN NEW;
END; $$;
CREATE FUNCTION protect_outbox_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'processed_at') IS DISTINCT FROM (to_jsonb(OLD)-'processed_at') THEN
  RAISE EXCEPTION 'immutable outbox source event' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$;
-- +goose StatementEnd
CREATE TRIGGER outbox_original_guard BEFORE UPDATE OR DELETE ON outbox FOR EACH ROW EXECUTE FUNCTION protect_outbox_original();
CREATE TRIGGER operation_projection_event_guard BEFORE UPDATE OR DELETE ON operation_projection_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER operation_projection_capture AFTER INSERT OR UPDATE ON operations FOR EACH ROW EXECUTE FUNCTION capture_operation_projection();

-- Existing retained operations establish one explicit migration baseline,
-- preserving all original bytes and economic history. They do not invent
-- intermediate historical notifications that were never retained.
INSERT INTO operation_projection_events(environment_id,user_id,operation_id,aggregate_version,previous_version,snapshot,created_at)
 SELECT environment_id,user_id,id,version,0,operation_display_snapshot(operations),created_at FROM operations ORDER BY environment_id,user_id,created_at,id;
-- +goose StatementBegin
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='zygo_app') THEN
  GRANT USAGE,SELECT ON SEQUENCE outbox_event_sequence_seq,operation_projection_events_event_sequence_seq TO zygo_app;
  REVOKE UPDATE ON operation_projection_events FROM zygo_app;
 END IF;
END $$;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'forward-only: retained projection event history must not be discarded'; END $$;
-- +goose StatementEnd
