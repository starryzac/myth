-- Draft forward-only mode-specific standing payment source admission.
-- +goose Up
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION native_payment_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations; b jsonb; s jsonb; reviewed jsonb; plan jsonb;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.operation_id;
 b:=convert_from(o.bank_request,'UTF8')::jsonb;s:=convert_from(NEW.original,'UTF8')::jsonb;
 IF o.mode='ONCE' THEN
  SELECT convert_from(p.original,'UTF8')::jsonb INTO reviewed FROM native_action_previews p WHERE p.environment_id=NEW.environment_id AND p.user_id=NEW.user_id AND p.id=o.preview_id;
 ELSIF o.mode='STANDING' THEN
  SELECT convert_from(p.original,'UTF8')::jsonb INTO plan FROM native_standing_plans p WHERE p.environment_id=NEW.environment_id AND p.user_id=NEW.user_id AND p.id=o.plan_id AND p.id=o.id AND p.grant_id=o.grant_id AND p.grant_version=o.grant_version;
  IF plan->>'protocol' IS DISTINCT FROM 'native-automation-v1/payment-plan' OR plan->>'id' IS DISTINCT FROM o.id::text OR plan->'grant'->>'id' IS DISTINCT FROM o.grant_id::text OR (plan->'grant'->>'version')::numeric IS DISTINCT FROM o.grant_version::numeric OR plan->'preview'->>'id' IS DISTINCT FROM o.preview_id::text OR plan->'preview'->>'session_id' IS DISTINCT FROM '00000000-0000-0000-0000-000000000000' THEN
   RAISE EXCEPTION 'standing payment requires its original SYSTEM plan and USER grant' USING ERRCODE='23514';
  END IF;
  reviewed:=plan->'preview';
 ELSE
  RAISE EXCEPTION 'payment permission mode is unsupported' USING ERRCODE='23514';
 END IF;
 IF o.id IS NULL OR o.command_kind<>'PAY_RECURRING' OR o.status<>'QUEUED' OR o.permit_signature IS NOT NULL OR
  NEW.accepted_xid<>pg_current_xact_id() OR NEW.accepted_xid<>o.accepted_xid OR
  encode(sha256(NEW.original),'hex')<>NEW.original_hash OR s->'original' IS DISTINCT FROM b->'effect'->'liability' OR
  s->'original'->>'id' IS DISTINCT FROM NEW.obligation_id OR coalesce(s->>'original_hash','') !~ '^[0-9a-f]{64}$' OR
  s->>'original_hash' IS DISTINCT FROM reviewed->'liability'->>'original_hash' OR reviewed->'liability' IS DISTINCT FROM s OR
  reviewed->'bank_effect' IS DISTINCT FROM b->'effect' OR reviewed->>'id' IS DISTINCT FROM o.preview_id::text OR
  s->>'paid_cents' IS NULL OR (s->>'paid_cents')::numeric<0 OR (s->>'reserved_cents')::numeric IS DISTINCT FROM 0::numeric THEN
  RAISE EXCEPTION 'native payment requires exact independently observed original in T1' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'standing payment originals are forward only';END $$;
-- +goose StatementEnd
