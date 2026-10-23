-- +goose Up
CREATE TABLE login_credentials (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, secret_hash text NOT NULL CHECK(secret_hash ~ '^[0-9a-f]{64}$'), disabled boolean NOT NULL DEFAULT false,
 PRIMARY KEY(environment_id,user_id), UNIQUE(environment_id,secret_hash), FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE sessions (
 id uuid PRIMARY KEY, environment_id uuid NOT NULL, user_id uuid NOT NULL, token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[0-9a-f]{64}$'), csrf_hash text NOT NULL CHECK(csrf_hash ~ '^[0-9a-f]{64}$'), expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false, UNIQUE(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
ALTER TABLE confirmations ADD COLUMN session_id uuid;
ALTER TABLE confirmations ADD CONSTRAINT confirmation_session_scope FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id);
CREATE TABLE protection_snapshots (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, version bigint NOT NULL CHECK(version>0), original bytea NOT NULL, original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'), observed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,version), FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE evidence_blobs (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id text NOT NULL, kind text NOT NULL, canonical bytea NOT NULL, digest text NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id), FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
ALTER TABLE operations ADD COLUMN processing_health text NOT NULL DEFAULT 'HEALTHY';
ALTER TABLE operations ADD COLUMN bank_observed_status text;
ALTER TABLE operations ADD COLUMN receipt_id uuid;
ALTER TABLE operations ADD COLUMN reserved_funds_version bigint;
ALTER TABLE operations ADD COLUMN authorization_kind text NOT NULL DEFAULT 'STANDING_GRANT';
ALTER TABLE operations ADD COLUMN confirmation_id uuid;
ALTER TABLE operations ADD CONSTRAINT operation_confirmation_scope FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES confirmations(environment_id,user_id,id);
ALTER TABLE operations ADD CONSTRAINT operation_receipt_scope FOREIGN KEY(environment_id,user_id,receipt_id) REFERENCES receipts(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED;

-- +goose StatementBegin
CREATE FUNCTION protect_access_originals() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable original'; END $$;
CREATE FUNCTION protect_operation_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.reserved_funds_version,NEW.authorization_kind,NEW.confirmation_id) IS DISTINCT FROM ROW(OLD.reserved_funds_version,OLD.authorization_kind,OLD.confirmation_id) THEN
  RAISE EXCEPTION 'immutable accepted authorization and reservation identity';
 END IF;
 IF OLD.bank_observed_status IN ('ACCEPTED','SETTLED') AND (NEW.bank_observed_status IS NULL OR NEW.bank_observed_status NOT IN ('ACCEPTED','SETTLED')) THEN
  RAISE EXCEPTION 'known bank acceptance cannot be erased';
 END IF;
 IF OLD.bank_observed_status='SETTLED' AND NEW.bank_observed_status<>'SETTLED' THEN RAISE EXCEPTION 'known settlement cannot go backwards'; END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER immutable_protection BEFORE UPDATE OR DELETE ON protection_snapshots FOR EACH ROW EXECUTE FUNCTION protect_access_originals();
CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON evidence_blobs FOR EACH ROW EXECUTE FUNCTION protect_access_originals();
CREATE TRIGGER immutable_accepted_identity BEFORE UPDATE ON operations FOR EACH ROW EXECUTE FUNCTION protect_operation_acceptance();

-- +goose Down
DROP TRIGGER immutable_evidence ON evidence_blobs;
DROP TRIGGER immutable_protection ON protection_snapshots;
DROP TRIGGER immutable_accepted_identity ON operations;
DROP FUNCTION protect_operation_acceptance();
DROP FUNCTION protect_access_originals();
ALTER TABLE operations DROP CONSTRAINT operation_confirmation_scope,DROP CONSTRAINT operation_receipt_scope;
ALTER TABLE operations DROP COLUMN confirmation_id,DROP COLUMN authorization_kind,DROP COLUMN reserved_funds_version,DROP COLUMN receipt_id,DROP COLUMN bank_observed_status,DROP COLUMN processing_health;
ALTER TABLE confirmations DROP CONSTRAINT confirmation_session_scope;
ALTER TABLE confirmations DROP COLUMN session_id;
DROP TABLE evidence_blobs,protection_snapshots,sessions,login_credentials;
