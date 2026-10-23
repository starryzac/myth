-- Frozen native bridge group originals; forward-only activation after actual transfer receipt.
-- +goose Up
CREATE TABLE native_group_batches (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,plan_id uuid NOT NULL,preview_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 4096),request_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,
 session_id uuid NOT NULL,reserved_funds_version bigint NOT NULL CHECK(reserved_funds_version>0),source_version bigint NOT NULL CHECK(source_version>0),
 writer_generation bigint NOT NULL CHECK(writer_generation>0),control_version bigint NOT NULL CHECK(control_version>0),
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at),accepted_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),UNIQUE(environment_id,user_id,preview_id),
 FOREIGN KEY(environment_id,user_id,plan_id) REFERENCES native_joint_plans(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,preview_id) REFERENCES native_previews(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE native_next_steps (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,group_id uuid NOT NULL,number integer NOT NULL CHECK(number BETWEEN 1 AND 16),
 child_number integer NOT NULL CHECK(child_number BETWEEN 1 AND 8),operation_id uuid NOT NULL,previous_operation_id uuid,
 command_kind text NOT NULL CHECK(command_kind IN ('BRIDGE_TRANSFER','BRIDGE_ALLOCATE')),initial_source boolean NOT NULL,
 bank_request bytea NOT NULL CHECK(octet_length(bank_request) BETWEEN 1 AND 131072),bank_request_hash hash256 NOT NULL,effect_hash hash256 NOT NULL,
 resources bytea NOT NULL CHECK(octet_length(resources) BETWEEN 1 AND 131072),resources_hash hash256 NOT NULL,
 confirmation_id uuid NOT NULL,goal_id uuid NOT NULL,
 PRIMARY KEY(environment_id,user_id,group_id,number),UNIQUE(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,group_id) REFERENCES native_group_batches(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES native_confirmations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 CHECK((number=1)=(previous_operation_id IS NULL))
);
CREATE TRIGGER native_group_original BEFORE UPDATE OR DELETE ON native_group_batches FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_step_original BEFORE UPDATE OR DELETE ON native_next_steps FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
-- +goose StatementBegin
CREATE FUNCTION check_bridge_activation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE step native_next_steps%ROWTYPE;batch native_group_batches%ROWTYPE;
BEGIN
 IF NEW.command_kind NOT IN ('BRIDGE_TRANSFER','BRIDGE_ALLOCATE') THEN RETURN NEW;END IF;
 SELECT * INTO step FROM native_next_steps WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'bridge operation missing original staged step' USING ERRCODE='23514';END IF;
 SELECT * INTO batch FROM native_group_batches WHERE environment_id=step.environment_id AND user_id=step.user_id AND id=step.group_id;
 IF ROW(NEW.command_kind,NEW.group_id,NEW.child_number,NEW.plan_id,NEW.preview_id,NEW.confirmation_id,NEW.bank_request,NEW.bank_request_hash,NEW.effect_hash,NEW.mode,NEW.reserved_funds_version,NEW.subject_goal_ids) IS DISTINCT FROM ROW(step.command_kind,step.group_id,step.number,batch.plan_id,batch.preview_id,step.confirmation_id,step.bank_request,step.bank_request_hash,step.effect_hash,'ONCE'::text,batch.reserved_funds_version,ARRAY[step.goal_id]) OR NEW.permit_signature IS NOT NULL OR NEW.status<>'QUEUED' OR NEW.accepted_xid<>pg_current_xact_id() THEN RAISE EXCEPTION 'bridge activation differs from whole reviewed staged original' USING ERRCODE='23514';END IF;
 IF step.initial_source THEN
  IF batch.accepted_xid<>pg_current_xact_id() THEN RAISE EXCEPTION 'initial bridge source must reserve in whole-group T1' USING ERRCODE='23514';END IF;
  IF NEW.previous_operation_id IS DISTINCT FROM (CASE WHEN EXISTS(SELECT 1 FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=step.previous_operation_id) THEN step.previous_operation_id ELSE NULL::uuid END) THEN RAISE EXCEPTION 'initial bridge dependency differs from original existing prefix' USING ERRCODE='23514';END IF;
 ELSE
  IF step.command_kind<>'BRIDGE_ALLOCATE' OR NEW.previous_operation_id IS DISTINCT FROM step.previous_operation_id OR NOT EXISTS(SELECT 1 FROM native_operations o JOIN native_receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.operation_id=o.id AND r.id=o.receipt_id) WHERE o.environment_id=NEW.environment_id AND o.user_id=NEW.user_id AND o.id=step.previous_operation_id AND o.group_id=step.group_id AND o.command_kind='BRIDGE_TRANSFER' AND o.status='SETTLED') THEN RAISE EXCEPTION 'future allocation requires original settled transfer receipt' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;
END $$;
CREATE FUNCTION check_bridge_step_original_set() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM native_group_batches WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.group_id AND accepted_xid=pg_current_xact_id()) THEN RAISE EXCEPTION 'complete bridge step set must freeze in whole-group T1' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_bridge_activation BEFORE INSERT ON native_operations FOR EACH ROW EXECUTE FUNCTION check_bridge_activation();
CREATE TRIGGER native_bridge_step_set AFTER INSERT ON native_next_steps FOR EACH ROW EXECUTE FUNCTION check_bridge_step_original_set();

-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native bridge originals are forward only';END $$;
-- +goose StatementEnd
