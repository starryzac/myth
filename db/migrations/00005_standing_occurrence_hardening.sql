-- +goose Up
-- Migration 00004 was first applied while its occurrence hardening was still
-- being drafted. Preserve all receipts/inbox originals from the retained RED.
ALTER TABLE standing_inbox ADD COLUMN occurrence_owner boolean NOT NULL DEFAULT true;

WITH ranked AS (
 SELECT i.environment_id,i.user_id,i.accept_key,
 row_number() OVER (
  PARTITION BY i.environment_id,i.user_id,i.consumer_name,i.origin_id,i.policy_id,i.policy_version,i.grant_id,i.grant_version,i.family
  ORDER BY EXISTS(SELECT 1 FROM operations o WHERE o.environment_id=i.environment_id AND o.user_id=i.user_id AND o.idempotency_key=i.accept_key) DESC,
   (i.operation_id IS NOT NULL) DESC,i.created_at,i.event_id
 ) AS ordinal
 FROM standing_inbox i
)
UPDATE standing_inbox i SET occurrence_owner=false,paused=true,
 state=CASE WHEN i.state IN ('READY','RUNNING') THEN 'BLOCKED' ELSE i.state END,
 lease_owner=NULL,lease_until=NULL,
 last_error=CASE WHEN i.state IN ('READY','RUNNING') THEN 'DUPLICATE_ORIGIN_OCCURRENCE_RETAINED' ELSE i.last_error END,
 finished_at=COALESCE(i.finished_at,clock_timestamp())
FROM ranked r WHERE i.environment_id=r.environment_id AND i.user_id=r.user_id AND i.accept_key=r.accept_key AND r.ordinal>1;

ALTER TABLE standing_inbox ADD CONSTRAINT standing_nonowner_quarantined CHECK(
 occurrence_owner OR (paused AND state IN ('ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED') AND lease_owner IS NULL AND lease_until IS NULL)
);
CREATE UNIQUE INDEX standing_origin_occurrence_owner ON standing_inbox(
 environment_id,user_id,consumer_name,origin_id,policy_id,policy_version,grant_id,grant_version,family
) WHERE occurrence_owner;

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION protect_standing_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.consumer_name,NEW.event_id,NEW.origin_id,NEW.policy_id,NEW.policy_version,NEW.grant_id,NEW.grant_version,NEW.family,NEW.accept_key,NEW.created_at,NEW.occurrence_owner)
 IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.consumer_name,OLD.event_id,OLD.origin_id,OLD.policy_id,OLD.policy_version,OLD.grant_id,OLD.grant_version,OLD.family,OLD.accept_key,OLD.created_at,OLD.occurrence_owner) THEN RAISE EXCEPTION 'immutable standing action identity' USING ERRCODE='23514'; END IF;
 IF OLD.plan_id IS NOT NULL AND NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN RAISE EXCEPTION 'standing plan can only be pinned once' USING ERRCODE='23514'; END IF;
 IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN RAISE EXCEPTION 'standing original operation cannot be replaced' USING ERRCODE='23514'; END IF;
 IF NOT OLD.occurrence_owner AND ROW(NEW.plan_id,NEW.operation_id) IS DISTINCT FROM ROW(OLD.plan_id,OLD.operation_id) THEN RAISE EXCEPTION 'quarantined occurrence cannot acquire new financial identity' USING ERRCODE='23514'; END IF;
 IF NEW.claim_generation<OLD.claim_generation THEN RAISE EXCEPTION 'standing claim generation cannot move backwards' USING ERRCODE='23514'; END IF;
 IF OLD.state IN ('ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED') AND NEW.state<>OLD.state THEN RAISE EXCEPTION 'terminal standing result cannot be silently requeued' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd

-- +goose Down
-- Evidence quarantine must never become executable merely through rollback.
-- +goose StatementBegin
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM standing_inbox WHERE NOT occurrence_owner) THEN
  RAISE EXCEPTION 'cannot remove occurrence quarantine while retained duplicates exist' USING ERRCODE='23514';
 END IF;
END $$;
-- +goose StatementEnd
DROP INDEX standing_origin_occurrence_owner;
ALTER TABLE standing_inbox DROP CONSTRAINT standing_nonowner_quarantined;
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION protect_standing_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.consumer_name,NEW.event_id,NEW.origin_id,NEW.policy_id,NEW.policy_version,NEW.grant_id,NEW.grant_version,NEW.family,NEW.accept_key,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.consumer_name,OLD.event_id,OLD.origin_id,OLD.policy_id,OLD.policy_version,OLD.grant_id,OLD.grant_version,OLD.family,OLD.accept_key,OLD.created_at) THEN RAISE EXCEPTION 'immutable standing action identity' USING ERRCODE='23514'; END IF;
 IF OLD.plan_id IS NOT NULL AND NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN RAISE EXCEPTION 'standing plan can only be pinned once' USING ERRCODE='23514'; END IF;
 IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN RAISE EXCEPTION 'standing original operation cannot be replaced' USING ERRCODE='23514'; END IF;
 IF NEW.claim_generation<OLD.claim_generation THEN RAISE EXCEPTION 'standing claim generation cannot move backwards' USING ERRCODE='23514'; END IF;
 IF OLD.state IN ('ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED') AND NEW.state<>OLD.state THEN RAISE EXCEPTION 'terminal standing result cannot be silently requeued' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
ALTER TABLE standing_inbox DROP COLUMN occurrence_owner;
