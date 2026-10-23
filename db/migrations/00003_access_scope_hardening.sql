-- +goose Up
-- Version 2 was applied during integration. Upgrade the live constraints with
-- a new version rather than pretending that editing version 2 reruns it.
-- +goose StatementBegin
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='sessions'::regclass AND conname='sessions_scope_identity') THEN
  ALTER TABLE sessions ADD CONSTRAINT sessions_scope_identity UNIQUE(environment_id,user_id,id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='confirmations'::regclass AND conname='confirmation_session_scope') THEN
  ALTER TABLE confirmations ADD CONSTRAINT confirmation_session_scope FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='operations'::regclass AND conname='operation_confirmation_scope') THEN
  ALTER TABLE operations ADD CONSTRAINT operation_confirmation_scope FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES confirmations(environment_id,user_id,id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='operations'::regclass AND conname='operation_receipt_scope') THEN
  ALTER TABLE operations ADD CONSTRAINT operation_receipt_scope FOREIGN KEY(environment_id,user_id,receipt_id) REFERENCES receipts(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION protect_operation_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.reserved_funds_version,NEW.authorization_kind,NEW.confirmation_id) IS DISTINCT FROM ROW(OLD.reserved_funds_version,OLD.authorization_kind,OLD.confirmation_id) THEN RAISE EXCEPTION 'immutable accepted authorization and reservation identity'; END IF;
 IF OLD.bank_observed_status IN ('ACCEPTED','SETTLED') AND (NEW.bank_observed_status IS NULL OR NEW.bank_observed_status NOT IN ('ACCEPTED','SETTLED')) THEN RAISE EXCEPTION 'known bank acceptance cannot be erased'; END IF;
 IF OLD.bank_observed_status='SETTLED' AND NEW.bank_observed_status<>'SETTLED' THEN RAISE EXCEPTION 'known settlement cannot go backwards'; END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='operations'::regclass AND tgname='immutable_accepted_identity' AND NOT tgisinternal) THEN
  CREATE TRIGGER immutable_accepted_identity BEFORE UPDATE ON operations FOR EACH ROW EXECUTE FUNCTION protect_operation_acceptance();
 END IF;
END $$;
-- +goose StatementEnd
-- +goose Down
-- These additive security constraints remain on a version-3 rollback. Version
-- 2 removes the extension's columns/tables during a full test-only downgrade.
SELECT 1;
