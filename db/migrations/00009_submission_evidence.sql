-- +goose Up
CREATE TABLE submission_permits (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, operation_id uuid NOT NULL,
 permit_id uuid NOT NULL, protocol_version text NOT NULL CHECK(protocol_version='zhiyu-submission-permit-v1'),
 original bytea NOT NULL CHECK(octet_length(original)>0 AND octet_length(original)<=131072), original_hash hash256 NOT NULL,
 decision_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,operation_id), UNIQUE(environment_id,user_id,permit_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE TRIGGER immutable_submission_permit BEFORE UPDATE OR DELETE ON submission_permits FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();

-- The view deliberately excludes all token, login-code and CSRF material.
CREATE VIEW audit_session_subjects WITH (security_barrier=true) AS SELECT id,environment_id,user_id,expires_at,revoked FROM sessions;
CREATE INDEX outbox_user_sequence ON outbox(environment_id,user_id,event_sequence);

-- +goose StatementBegin
CREATE FUNCTION check_new_submission_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE record_permit uuid;
BEGIN
 IF NEW.permit_signature IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND OLD.permit_signature IS NOT DISTINCT FROM NEW.permit_signature THEN RETURN NULL; END IF;
 SELECT permit_id INTO record_permit FROM submission_permits WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.id;
 IF record_permit IS DISTINCT FROM NEW.permit_id THEN RAISE EXCEPTION 'new submission must atomically retain exact T2 evidence' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='zygo_app') THEN GRANT SELECT ON audit_session_subjects TO zygo_app; END IF;
END $$;
-- +goose StatementEnd
CREATE CONSTRAINT TRIGGER retained_t2_submission AFTER INSERT OR UPDATE ON operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_new_submission_evidence();

-- No historical permit is invented. Pre-existing signed operations remain
-- recoverable by their original key, with an explicit audit completeness gap.

-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'forward-only: retained T2 evidence must not be discarded'; END $$;
-- +goose StatementEnd
