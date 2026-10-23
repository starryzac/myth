-- Frozen source must be hashed before copying into the embedded migrations.
-- +goose Up
-- Migration 010 used an obsolete column in this as-yet-unused payment guard.
-- Keep the historical migration unchanged. The real non-SPEND receipt RED is
-- retained in RF-020/native-payment-second.txt (42703), then regression-tested.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION check_template_payment_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total numeric;bound cents;settled boolean;
BEGIN
 SELECT amount_cents INTO bound FROM template_obligations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.obligation_id FOR UPDATE;
 SELECT sum(amount_cents) INTO total FROM template_obligation_payments WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND obligation_id=NEW.obligation_id;
 SELECT o.status='SETTLED' AND o.receipt_id=NEW.receipt_id AND r.operation_id=o.id AND convert_from(o.bank_request,'UTF8')::jsonb->>'kind'='SPEND' INTO settled FROM operations o JOIN receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.id=NEW.receipt_id) WHERE o.environment_id=NEW.environment_id AND o.user_id=NEW.user_id AND o.id=NEW.operation_id;
 IF total>bound OR settled IS DISTINCT FROM true THEN RAISE EXCEPTION 'template obligation payment must bind settled original receipt and cannot overpay' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
-- +goose StatementEnd
CREATE TABLE native_joint_plans (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,
 protocol_version text NOT NULL CHECK(protocol_version='native-joint-plan-go-v1'),
 status text NOT NULL CHECK(status IN('OPTIMAL','INFEASIBLE','UNKNOWN')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 16777216),original_hash hash256 NOT NULL,
 input bytea NOT NULL CHECK(octet_length(input) BETWEEN 1 AND 8388608),input_hash hash256 NOT NULL,
 result bytea NOT NULL CHECK(octet_length(result) BETWEEN 1 AND 4194304),result_hash hash256 NOT NULL,
 dependencies bytea NOT NULL CHECK(octet_length(dependencies) BETWEEN 1 AND 65536),dependencies_hash hash256 NOT NULL,
 source_hash hash256 NOT NULL,inventory_hash hash256 NOT NULL,
 effect_hash text NOT NULL CHECK(effect_hash='' OR effect_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+interval '2 minutes'),
 PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 CHECK((status='OPTIMAL')=(effect_hash<>''))
);
-- +goose StatementBegin
CREATE FUNCTION check_native_joint_plan_original() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o jsonb;i jsonb;r jsonb;d jsonb;
BEGIN
 o:=convert_from(NEW.original,'UTF8')::jsonb;i:=convert_from(NEW.input,'UTF8')::jsonb;
 r:=convert_from(NEW.result,'UTF8')::jsonb;d:=convert_from(NEW.dependencies,'UTF8')::jsonb;
 IF NEW.original_hash<>encode(sha256(NEW.original),'hex') OR NEW.input_hash<>encode(sha256(NEW.input),'hex') OR NEW.result_hash<>encode(sha256(NEW.result),'hex') OR NEW.dependencies_hash<>encode(sha256(NEW.dependencies),'hex')
 OR o->>'protocol' IS DISTINCT FROM NEW.protocol_version OR o->>'id' IS DISTINCT FROM NEW.id::text
 OR o->'scope'->>'environment_id' IS DISTINCT FROM NEW.environment_id::text OR o->'scope'->>'user_id' IS DISTINCT FROM NEW.user_id::text
 OR o->>'session_id' IS DISTINCT FROM NEW.session_id::text OR o->>'source_hash' IS DISTINCT FROM NEW.source_hash::text OR o->>'inventory_hash' IS DISTINCT FROM NEW.inventory_hash::text
 OR (o->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR (o->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at
 OR o->'input' IS DISTINCT FROM i OR o->'result' IS DISTINCT FROM r OR o->'dependencies' IS DISTINCT FROM d
 OR o->>'input_hash' IS DISTINCT FROM NEW.input_hash::text OR o->>'result_hash' IS DISTINCT FROM NEW.result_hash::text
 OR r->>'status' IS DISTINCT FROM NEW.status OR r->>'effect_hash' IS DISTINCT FROM NEW.effect_hash
 OR o->>'bank_authority' IS DISTINCT FROM 'false' OR r->>'bank_authority' IS DISTINCT FROM 'false'
 OR jsonb_typeof(r->'allocations') IS DISTINCT FROM 'array' OR jsonb_typeof(r->'uses') IS DISTINCT FROM 'array' OR jsonb_typeof(r->'children') IS DISTINCT FROM 'array'
 OR (NEW.status<>'OPTIMAL' AND (COALESCE(jsonb_array_length(r->'allocations'),-1)<>0 OR COALESCE(jsonb_array_length(r->'uses'),-1)<>0 OR COALESCE(jsonb_array_length(r->'children'),-1)<>0)) THEN
 RAISE EXCEPTION 'native joint original, scope or result differs from frozen columns' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_joint_original_exact BEFORE INSERT ON native_joint_plans FOR EACH ROW EXECUTE FUNCTION check_native_joint_plan_original();
CREATE TRIGGER immutable_native_joint_plan BEFORE UPDATE OR DELETE ON native_joint_plans FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
GRANT SELECT,INSERT,UPDATE ON native_joint_plans TO zygo_app;

-- +goose Down
DROP TABLE native_joint_plans;
DROP FUNCTION check_native_joint_plan_original();
-- The corrected guard remains valid for the retained 010 objects; Down never
-- deliberately restores an undefined-column bug in an existing financial guard.
