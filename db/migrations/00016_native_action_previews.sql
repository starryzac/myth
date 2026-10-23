-- Frozen shared pre-confirmation originals; not a bank request or USER consent.
-- +goose Up
CREATE TABLE native_action_previews (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('PURCHASE_ASSET','REDEEM_ASSET','MATURE_ASSET','PAY_RECURRING','TRANSFER_GOAL')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8388608),original_hash hash256 NOT NULL,effect_hash hash256 NOT NULL,
 dependencies jsonb NOT NULL CHECK(jsonb_typeof(dependencies)='object'),
 dependencies_original bytea NOT NULL CHECK(octet_length(dependencies_original) BETWEEN 1 AND 8192),dependencies_hash hash256 NOT NULL,
 source_hash hash256 NOT NULL,inventory_hash hash256 NOT NULL,created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '2 minutes')
);
-- +goose StatementBegin
CREATE FUNCTION native_action_preview_original_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;deps jsonb;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.dependencies_original),'hex')<>NEW.dependencies_hash THEN
  RAISE EXCEPTION 'native action preview original hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;deps:=convert_from(NEW.dependencies_original,'UTF8')::jsonb;
 IF jsonb_typeof(raw) IS DISTINCT FROM 'object' OR jsonb_typeof(deps) IS DISTINCT FROM 'object' OR deps IS DISTINCT FROM NEW.dependencies OR raw->'dependencies' IS DISTINCT FROM deps OR
  raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR raw->>'kind' IS DISTINCT FROM NEW.kind OR
  raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR
  raw->>'effect_hash' IS DISTINCT FROM NEW.effect_hash::text OR raw->>'source_hash' IS DISTINCT FROM NEW.source_hash::text OR raw->>'inventory_hash' IS DISTINCT FROM NEW.inventory_hash::text OR
  (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR (raw->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at OR
  raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb THEN
  RAISE EXCEPTION 'native action preview identity or proof differs' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_action_preview_original BEFORE INSERT ON native_action_previews FOR EACH ROW EXECUTE FUNCTION native_action_preview_original_guard();
CREATE TRIGGER native_action_preview_immutable BEFORE UPDATE OR DELETE ON native_action_previews FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
GRANT SELECT,INSERT ON native_action_previews TO zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native action preview originals are forward only';END $$;
-- +goose StatementEnd
