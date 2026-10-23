-- +goose Up
CREATE TABLE audit_anchors (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL,
 key_id text NOT NULL CHECK(length(key_id) BETWEEN 1 AND 160), protocol_version text NOT NULL,
 algorithm text NOT NULL, manifest_digest hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 16384), original_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,id), UNIQUE(environment_id,user_id,manifest_digest,key_id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE audit_runs (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 128),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 65536), request_hash hash256 NOT NULL,
 require_anchor boolean NOT NULL DEFAULT false,
 state text NOT NULL DEFAULT 'QUEUED' CHECK(state IN ('QUEUED','RUNNING','COMPLETED','EXHAUSTED','CANCELLED')),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0), lease_owner text, lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), max_attempts integer NOT NULL DEFAULT 8 CHECK(max_attempts BETWEEN 1 AND 32),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), last_error text NOT NULL DEFAULT '',
 capture_artifact_id uuid, result_artifact_id uuid, anchor_id uuid,
 capture_kind text GENERATED ALWAYS AS ('CAPTURE'::text) STORED,
 result_kind text GENERATED ALWAYS AS ('RESULT'::text) STORED,
 verification_status text CHECK(verification_status IN ('VALID','INVALID','INCOMPLETE','UNSUPPORTED_VERSION')),
 assurance text CHECK(assurance IN ('INTERNAL_CONSISTENCY','ANCHORED_INTEGRITY')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 PRIMARY KEY(environment_id,user_id,id), UNIQUE(environment_id,user_id,idempotency_key),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,anchor_id) REFERENCES audit_anchors(environment_id,user_id,id),
 CHECK(attempts<=max_attempts), CHECK((lease_owner IS NULL)=(lease_until IS NULL)),
 CHECK((state='RUNNING')=(lease_owner IS NOT NULL)),
 CHECK((state IN ('COMPLETED','EXHAUSTED','CANCELLED'))=(finished_at IS NOT NULL)),
 CHECK(state<>'COMPLETED' OR (capture_artifact_id IS NOT NULL AND result_artifact_id IS NOT NULL AND verification_status IS NOT NULL AND assurance IS NOT NULL))
);
CREATE TABLE audit_artifacts (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, run_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('CAPTURE','RESULT')), protocol_version text NOT NULL,
 manifest_digest hash256 NOT NULL, original bytea NOT NULL CHECK(octet_length(original)>0), original_hash hash256 NOT NULL,
 verification_status text CHECK(verification_status IN ('VALID','INVALID','INCOMPLETE','UNSUPPORTED_VERSION')),
 assurance text CHECK(assurance IN ('INTERNAL_CONSISTENCY','ANCHORED_INTEGRITY')), anchor_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,id), UNIQUE(environment_id,user_id,run_id,kind),
 UNIQUE(environment_id,user_id,id,run_id,kind),
 FOREIGN KEY(environment_id,user_id,run_id) REFERENCES audit_runs(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,anchor_id) REFERENCES audit_anchors(environment_id,user_id,id),
 CHECK((kind='RESULT')=(verification_status IS NOT NULL)),
 CHECK((kind='RESULT')=(assurance IS NOT NULL)),
 CHECK((kind='CAPTURE' AND octet_length(original)<=33554432 AND anchor_id IS NULL) OR (kind='RESULT' AND octet_length(original)<=1048576)),
 CHECK(assurance IS DISTINCT FROM 'ANCHORED_INTEGRITY' OR anchor_id IS NOT NULL)
);
ALTER TABLE audit_runs ADD CONSTRAINT audit_capture_owned FOREIGN KEY(environment_id,user_id,capture_artifact_id,id,capture_kind) REFERENCES audit_artifacts(environment_id,user_id,id,run_id,kind) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE audit_runs ADD CONSTRAINT audit_result_owned FOREIGN KEY(environment_id,user_id,result_artifact_id,id,result_kind) REFERENCES audit_artifacts(environment_id,user_id,id,run_id,kind) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX audit_ready ON audit_runs(environment_id,next_attempt_at,id) WHERE state IN ('QUEUED','RUNNING');

-- +goose StatementBegin
CREATE FUNCTION protect_audit_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.id,NEW.idempotency_key,NEW.request,NEW.request_hash,NEW.require_anchor,NEW.max_attempts,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.id,OLD.idempotency_key,OLD.request,OLD.request_hash,OLD.require_anchor,OLD.max_attempts,OLD.created_at) THEN RAISE EXCEPTION 'immutable audit request identity' USING ERRCODE='23514'; END IF;
 IF OLD.state IN ('COMPLETED','EXHAUSTED','CANCELLED') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal audit run is immutable' USING ERRCODE='23514'; END IF;
 IF OLD.capture_artifact_id IS NOT NULL AND NEW.capture_artifact_id IS DISTINCT FROM OLD.capture_artifact_id THEN RAISE EXCEPTION 'audit capture can only be pinned once' USING ERRCODE='23514'; END IF;
 IF OLD.result_artifact_id IS NOT NULL AND NEW.result_artifact_id IS DISTINCT FROM OLD.result_artifact_id THEN RAISE EXCEPTION 'audit result can only be pinned once' USING ERRCODE='23514'; END IF;
 IF OLD.anchor_id IS NOT NULL AND NEW.anchor_id IS DISTINCT FROM OLD.anchor_id THEN RAISE EXCEPTION 'audit anchor can only be pinned once' USING ERRCODE='23514'; END IF;
 IF NEW.claim_generation<OLD.claim_generation OR NEW.attempts<OLD.attempts THEN RAISE EXCEPTION 'audit lease generation and attempts cannot regress' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION check_completed_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c record;r record;a record;
BEGIN
 IF NEW.state<>'COMPLETED' THEN RETURN NULL; END IF;
 SELECT * INTO c FROM audit_artifacts WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.capture_artifact_id AND run_id=NEW.id AND kind='CAPTURE';
 SELECT * INTO r FROM audit_artifacts WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.result_artifact_id AND run_id=NEW.id AND kind='RESULT';
 IF c.id IS NULL OR r.id IS NULL OR c.manifest_digest<>r.manifest_digest OR r.verification_status IS DISTINCT FROM NEW.verification_status OR r.assurance IS DISTINCT FROM NEW.assurance OR r.anchor_id IS DISTINCT FROM NEW.anchor_id THEN RAISE EXCEPTION 'completed audit does not bind its exact capture and result' USING ERRCODE='23514'; END IF;
 IF NEW.require_anchor AND NEW.verification_status='VALID' AND NEW.assurance<>'ANCHORED_INTEGRITY' THEN RAISE EXCEPTION 'required audit anchor is missing' USING ERRCODE='23514'; END IF;
 IF NEW.assurance='ANCHORED_INTEGRITY' THEN
  SELECT * INTO a FROM audit_anchors WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.anchor_id;
  IF a.id IS NULL OR a.manifest_digest<>c.manifest_digest THEN RAISE EXCEPTION 'audit anchor binds a different captured manifest' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;
-- +goose StatementEnd
CREATE TRIGGER immutable_audit_anchor BEFORE UPDATE OR DELETE ON audit_anchors FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_audit_artifact BEFORE UPDATE OR DELETE ON audit_artifacts FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_audit_run BEFORE UPDATE OR DELETE ON audit_runs FOR EACH ROW EXECUTE FUNCTION protect_audit_run();
CREATE CONSTRAINT TRIGGER bound_completed_audit AFTER INSERT OR UPDATE ON audit_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_completed_audit();

-- +goose Down
ALTER TABLE audit_runs DROP CONSTRAINT audit_capture_owned,DROP CONSTRAINT audit_result_owned;
DROP TABLE audit_artifacts,audit_runs,audit_anchors;
DROP FUNCTION check_completed_audit(),protect_audit_run();
