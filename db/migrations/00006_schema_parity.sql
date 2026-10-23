-- +goose Up
-- Final source vs. a database that had already applied draft versions 2 and 4
-- differed by these three redundant historical constraints. Preserve the
-- scoped constraints and occurrence-owner index established in versions 3/5.
-- No rows, original identities, reservations, receipts or jobs are rewritten.
ALTER TABLE confirmations DROP CONSTRAINT IF EXISTS confirmations_session_id_fkey;
-- The fresh version-2 FK selected the then-available unnamed unique index.
-- Rebind only this known scoped FK before removing that redundant index;
-- never CASCADE unknown dependencies. Already-applied version 6 has the same
-- final FK/index binding, so its source-order repair needs no data replay.
ALTER TABLE confirmations DROP CONSTRAINT confirmation_session_scope;
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_environment_id_user_id_id_key;
ALTER TABLE confirmations ADD CONSTRAINT confirmation_session_scope FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id);
ALTER TABLE standing_inbox DROP CONSTRAINT IF EXISTS standing_inbox_environment_id_user_id_consumer_name_origin__key;

-- +goose StatementBegin
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='sessions'::regclass AND conname='sessions_scope_identity' AND contype='u')
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='confirmations'::regclass AND conname='confirmation_session_scope' AND contype='f')
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='standing_inbox'::regclass AND conname='standing_nonowner_quarantined' AND contype='c')
 OR NOT EXISTS(SELECT 1 FROM pg_index WHERE indexrelid='standing_origin_occurrence_owner'::regclass AND indisunique AND indisvalid) THEN
  RAISE EXCEPTION 'required scoped/occurrence constraints missing' USING ERRCODE='23514';
 END IF;
END $$;
-- +goose StatementEnd

-- +goose Down
-- Reintroducing different historical duplicate/naked constraints would make
-- fresh and upgraded databases diverge again. Canonical protections remain.
SELECT 1;
