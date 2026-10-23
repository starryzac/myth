-- Frozen additive standing bridge contract; existing 001-021 originals unchanged.
-- +goose Up
CREATE TABLE native_standing_bridge_groups (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8388608),original_hash hash256 NOT NULL,
 reserved_funds_version bigint NOT NULL CHECK(reserved_funds_version>0),created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 accepted_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,id) REFERENCES native_grant_budget_claims(environment_id,user_id,occurrence_id),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 CHECK(encode(sha256(original),'hex')=original_hash),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '2 minutes')
);
CREATE TABLE native_standing_bridge_steps (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,group_id uuid NOT NULL,number integer NOT NULL CHECK(number BETWEEN 1 AND 16),
 child_number integer NOT NULL CHECK(child_number BETWEEN 1 AND 8),operation_id uuid NOT NULL,previous_operation_id uuid,
 initial_source boolean NOT NULL,command_kind text NOT NULL CHECK(command_kind IN ('STANDING_BRIDGE_TRANSFER','STANDING_BRIDGE_ALLOCATE')),goal_id uuid NOT NULL,
 bank_request bytea NOT NULL CHECK(octet_length(bank_request) BETWEEN 1 AND 131072),bank_request_hash hash256 NOT NULL,effect_hash hash256 NOT NULL,
 resources bytea NOT NULL CHECK(octet_length(resources) BETWEEN 1 AND 131072),resources_hash hash256 NOT NULL,
 PRIMARY KEY(environment_id,user_id,group_id,number),UNIQUE(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,group_id) REFERENCES native_standing_bridge_groups(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 CHECK((number=1)=(previous_operation_id IS NULL)),CHECK(encode(sha256(resources),'hex')=resources_hash),
 CHECK(encode(sha256(convert_to('zhiyu-bank-v2','UTF8')||decode('00','hex')||bank_request),'hex')=bank_request_hash)
);
-- A closed suffix has no fabricated future operation/receipt. This immutable
-- record blocks activation and preserves the reason for every unborn step.
CREATE TABLE native_standing_bridge_closures (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,group_id uuid NOT NULL,
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 256),closed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,group_id),
 FOREIGN KEY(environment_id,user_id,group_id) REFERENCES native_standing_bridge_groups(environment_id,user_id,id)
);
-- +goose StatementBegin
CREATE FUNCTION standing_bridge_group_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;b native_grant_budget_claims;
BEGIN
 raw:=convert_from(NEW.original,'UTF8')::jsonb;
 SELECT * INTO b FROM native_grant_budget_claims WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND occurrence_id=NEW.id;
 IF NEW.accepted_xid<>pg_current_xact_id() OR raw->>'protocol' IS DISTINCT FROM 'native-standing-bridge-v1' OR raw#>>'{scope,environment_id}' IS DISTINCT FROM NEW.environment_id::text OR raw#>>'{scope,user_id}' IS DISTINCT FROM NEW.user_id::text OR raw#>>'{occurrence,id}' IS DISTINCT FROM NEW.id::text OR raw#>>'{grant,id}' IS DISTINCT FROM NEW.grant_id::text OR raw#>>'{grant,version}' IS DISTINCT FROM NEW.grant_version::text OR (raw#>>'{dependencies,funds_version}')::bigint+1 IS DISTINCT FROM NEW.reserved_funds_version OR (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR (raw->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at OR jsonb_array_length(raw->'steps') NOT BETWEEN 1 AND 16 OR b.grant_id IS DISTINCT FROM NEW.grant_id OR b.grant_version IS DISTINCT FROM NEW.grant_version OR b.state IS DISTINCT FROM 'RESERVED' THEN RAISE EXCEPTION 'standing bridge missing frozen whole-group grant budget' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION standing_bridge_step_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g native_standing_bridge_groups;s jsonb;r jsonb;
BEGIN
 SELECT * INTO g FROM native_standing_bridge_groups WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.group_id;
 s:=convert_from(g.original,'UTF8')::jsonb->'steps'->(NEW.number-1);r:=convert_from(NEW.bank_request,'UTF8')::jsonb;
 IF g.accepted_xid IS DISTINCT FROM pg_current_xact_id() OR s IS NULL OR ROW(s->>'number',s->>'child_number',s->>'operation_id',s->>'previous_operation_id',s->>'initial_source',s->>'command_kind',s->>'goal_id',s->>'bank_request_hash',s->>'effect_hash') IS DISTINCT FROM ROW(NEW.number::text,NEW.child_number::text,NEW.operation_id::text,NEW.previous_operation_id::text,NEW.initial_source::text,NEW.command_kind,NEW.goal_id::text,NEW.bank_request_hash::text,NEW.effect_hash::text) OR s->'bank_request' IS DISTINCT FROM r OR s->'resources' IS DISTINCT FROM convert_from(NEW.resources,'UTF8')::jsonb THEN RAISE EXCEPTION 'standing bridge staged step differs from creation T1 original' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION standing_bridge_complete_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM native_standing_bridge_steps WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND group_id=NEW.id) IS DISTINCT FROM jsonb_array_length(convert_from(NEW.original,'UTF8')::jsonb->'steps')::bigint THEN RAISE EXCEPTION 'standing bridge complete step set missing from whole-group T1' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION standing_bridge_activation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s native_standing_bridge_steps;g native_standing_bridge_groups;raw jsonb;r jsonb;goals uuid[];
BEGIN
 IF NEW.command_kind NOT IN ('STANDING_BRIDGE_TRANSFER','STANDING_BRIDGE_ALLOCATE') THEN RETURN NEW;END IF;
 SELECT * INTO s FROM native_standing_bridge_steps WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'standing bridge missing original staged key' USING ERRCODE='23514';END IF;
 SELECT * INTO g FROM native_standing_bridge_groups WHERE environment_id=s.environment_id AND user_id=s.user_id AND id=s.group_id;
 raw:=convert_from(g.original,'UTF8')::jsonb;r:=convert_from(s.bank_request,'UTF8')::jsonb;SELECT array_agg(v::uuid ORDER BY v) INTO goals FROM jsonb_array_elements_text(raw#>'{grant,terms,goal_ids}') v;
 IF ROW(NEW.mode,NEW.command_kind,NEW.group_id,NEW.plan_id,NEW.child_number,NEW.grant_id,NEW.grant_version,NEW.reserved_funds_version,NEW.bank_request,NEW.bank_request_hash,NEW.effect_hash,NEW.permit_signature,NEW.status,NEW.subject_goal_ids,NEW.bank_namespace,NEW.bank_key,NEW.permit_id,NEW.dependencies) IS DISTINCT FROM ROW('STANDING'::text,s.command_kind,s.group_id,s.group_id,s.number,g.grant_id,g.grant_version,g.reserved_funds_version,s.bank_request,s.bank_request_hash,s.effect_hash,NULL::text,'QUEUED'::text,goals,r->>'namespace',r->>'key',(r->>'permit_id')::uuid,raw->'dependencies') OR NEW.accepted_xid<>pg_current_xact_id() OR EXISTS(SELECT 1 FROM native_standing_bridge_closures WHERE environment_id=g.environment_id AND user_id=g.user_id AND group_id=g.id) OR NOT EXISTS(SELECT 1 FROM native_grant_budget_claims WHERE environment_id=g.environment_id AND user_id=g.user_id AND occurrence_id=g.id AND state='RESERVED') OR NOT EXISTS(SELECT 1 FROM native_grant_states WHERE environment_id=g.environment_id AND user_id=g.user_id AND grant_id=g.grant_id AND grant_version=g.grant_version AND state='ACTIVE') THEN RAISE EXCEPTION 'standing bridge activation differs from exact frozen grant step' USING ERRCODE='23514';END IF;
 IF s.initial_source THEN
  IF g.accepted_xid<>pg_current_xact_id() OR NEW.previous_operation_id IS DISTINCT FROM (CASE WHEN EXISTS(SELECT 1 FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=s.previous_operation_id) THEN s.previous_operation_id ELSE NULL::uuid END) THEN RAISE EXCEPTION 'standing bridge initial sources must reserve in whole-group T1' USING ERRCODE='23514';END IF;
 ELSE
  IF s.command_kind<>'STANDING_BRIDGE_ALLOCATE' OR NEW.previous_operation_id IS DISTINCT FROM s.previous_operation_id OR NOT EXISTS(SELECT 1 FROM native_operations o JOIN native_receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.operation_id=o.id AND r.id=o.receipt_id) WHERE o.environment_id=NEW.environment_id AND o.user_id=NEW.user_id AND o.id=s.previous_operation_id AND o.group_id=s.group_id AND o.command_kind='STANDING_BRIDGE_TRANSFER' AND o.status='SETTLED') THEN RAISE EXCEPTION 'standing bridge allocation requires original transfer arrival receipt' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION standing_bridge_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s native_standing_bridge_steps;economic bigint;
BEGIN
 SELECT * INTO s FROM native_standing_bridge_steps WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.operation_id;
 IF NOT FOUND THEN RETURN NEW;END IF;
 economic:=CASE WHEN s.initial_source THEN (convert_from(s.resources,'UTF8')::jsonb->>'amount_cents')::bigint ELSE 0 END;
 IF NEW.occurrence_id IS DISTINCT FROM s.group_id OR NEW.economic_amount_cents IS DISTINCT FROM economic THEN RAISE EXCEPTION 'standing bridge budget counts each child first economic action exactly once' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION standing_bridge_budget_final_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g native_standing_bridge_groups;
BEGIN
 IF NEW.state<>'FINAL' THEN RETURN NEW;END IF;
 SELECT * INTO g FROM native_standing_bridge_groups WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.occurrence_id;
 IF NOT FOUND THEN RETURN NEW;END IF;
 IF NOT EXISTS(SELECT 1 FROM native_standing_bridge_closures WHERE environment_id=g.environment_id AND user_id=g.user_id AND group_id=g.id) AND (SELECT count(*) FROM native_operations WHERE environment_id=g.environment_id AND user_id=g.user_id AND group_id=g.id AND status='SETTLED') IS DISTINCT FROM jsonb_array_length(convert_from(g.original,'UTF8')::jsonb->'steps')::bigint THEN RAISE EXCEPTION 'standing bridge cannot finalize before every declared step or retained closure' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER standing_bridge_group_original BEFORE INSERT ON native_standing_bridge_groups FOR EACH ROW EXECUTE FUNCTION standing_bridge_group_guard();
CREATE TRIGGER standing_bridge_group_immutable BEFORE UPDATE OR DELETE ON native_standing_bridge_groups FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER standing_bridge_step_original BEFORE INSERT ON native_standing_bridge_steps FOR EACH ROW EXECUTE FUNCTION standing_bridge_step_guard();
CREATE TRIGGER standing_bridge_step_immutable BEFORE UPDATE OR DELETE ON native_standing_bridge_steps FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER standing_bridge_closure_immutable BEFORE UPDATE OR DELETE ON native_standing_bridge_closures FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER standing_bridge_group_complete AFTER INSERT ON native_standing_bridge_groups DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION standing_bridge_complete_guard();
CREATE TRIGGER standing_bridge_operation_activation BEFORE INSERT ON native_operations FOR EACH ROW EXECUTE FUNCTION standing_bridge_activation_guard();
CREATE TRIGGER standing_bridge_budget_binding BEFORE INSERT ON native_occurrence_operations FOR EACH ROW EXECUTE FUNCTION standing_bridge_binding_guard();
CREATE TRIGGER standing_bridge_budget_final BEFORE UPDATE ON native_grant_budget_claims FOR EACH ROW EXECUTE FUNCTION standing_bridge_budget_final_guard();
GRANT SELECT,INSERT ON native_standing_bridge_groups,native_standing_bridge_steps,native_standing_bridge_closures TO zygo_app;
REVOKE UPDATE,DELETE ON native_standing_bridge_groups,native_standing_bridge_steps,native_standing_bridge_closures FROM zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'standing bridge originals are forward only';END $$;
-- +goose StatementEnd
