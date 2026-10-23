-- Frozen additive payment admission, original liability and receipt contract.
-- +goose Up
CREATE TABLE native_payment_sources (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,operation_id uuid NOT NULL,obligation_id text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,
 observed_at timestamptz NOT NULL,accepted_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,obligation_id) REFERENCES template_obligations(environment_id,user_id,id)
);
CREATE TABLE native_obligation_payments (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,obligation_id text NOT NULL,
 operation_id uuid NOT NULL,receipt_id uuid NOT NULL,amount_cents cents NOT NULL CHECK(amount_cents>0),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,recorded_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_payment_sources(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,receipt_id) REFERENCES native_receipts(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,obligation_id) REFERENCES template_obligations(environment_id,user_id,id)
);
CREATE UNIQUE INDEX native_one_active_liability_claim ON native_holds(environment_id,user_id,resource_id) WHERE resource_kind='LIABILITY' AND state='RESERVED';
-- +goose StatementBegin
CREATE FUNCTION native_payment_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations; b jsonb; s jsonb;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.operation_id;
 b:=convert_from(o.bank_request,'UTF8')::jsonb;s:=convert_from(NEW.original,'UTF8')::jsonb;
 IF o.id IS NULL OR o.command_kind<>'PAY_RECURRING' OR o.status<>'QUEUED' OR o.permit_signature IS NOT NULL OR
  NEW.accepted_xid<>pg_current_xact_id() OR NEW.accepted_xid<>o.accepted_xid OR
  encode(sha256(NEW.original),'hex')<>NEW.original_hash OR s->'original' IS DISTINCT FROM b->'effect'->'liability' OR
  s->'original'->>'id' IS DISTINCT FROM NEW.obligation_id OR coalesce(s->>'original_hash','') !~ '^[0-9a-f]{64}$' OR
  s->>'original_hash' IS DISTINCT FROM (SELECT convert_from(p.original,'UTF8')::jsonb->'liability'->>'original_hash' FROM native_action_previews p WHERE p.environment_id=NEW.environment_id AND p.user_id=NEW.user_id AND p.id=o.preview_id) OR
  s->>'paid_cents' IS NULL OR (s->>'paid_cents')::numeric<0 OR (s->>'reserved_cents')::numeric IS DISTINCT FROM 0::numeric THEN
  RAISE EXCEPTION 'native payment requires exact independently observed original in T1' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_payment_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations;r native_receipts;s native_payment_sources;b jsonb;p jsonb;total numeric;bound numeric;final numeric;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.operation_id;
 SELECT * INTO r FROM native_receipts WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.receipt_id;
 SELECT * INTO s FROM native_payment_sources WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.operation_id;
 SELECT amount_cents INTO bound FROM template_obligations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.obligation_id FOR UPDATE;
 SELECT coalesce(sum(amount_cents),0) INTO total FROM native_obligation_payments WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND obligation_id=NEW.obligation_id;
 SELECT total+coalesce(sum(amount_cents),0) INTO total FROM template_obligation_payments WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND obligation_id=NEW.obligation_id;
 b:=convert_from(o.bank_request,'UTF8')::jsonb;p:=convert_from(NEW.original,'UTF8')::jsonb;
 final:=(b->'effect'->'liability'->>'final_total_cents')::numeric;
 IF o.id IS NULL OR r.id IS NULL OR s.operation_id IS NULL OR o.command_kind<>'PAY_RECURRING' OR o.status<>'SETTLED' OR
  o.receipt_id IS DISTINCT FROM NEW.receipt_id OR r.operation_id IS DISTINCT FROM o.id OR s.obligation_id IS DISTINCT FROM NEW.obligation_id OR
  b->'effect'->>'kind' IS DISTINCT FROM 'PAY_RECURRING' OR b->'effect'->'liability'->>'id' IS DISTINCT FROM NEW.obligation_id OR
  (b->'effect'->>'amount_cents')::numeric IS DISTINCT FROM NEW.amount_cents::numeric OR total>bound OR total>final OR
  convert_from(r.original,'UTF8')::jsonb->'bank_result'->'request' IS DISTINCT FROM b OR
  encode(sha256(NEW.original),'hex')<>NEW.original_hash OR p->>'protocol' IS DISTINCT FROM 'native-obligation-payment-v1' OR
  p->>'id' IS DISTINCT FROM NEW.id::text OR p->>'operation_id' IS DISTINCT FROM NEW.operation_id::text OR
  p->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text OR p->>'obligation_id' IS DISTINCT FROM NEW.obligation_id OR
  p->>'liability_hash' IS DISTINCT FROM convert_from(s.original,'UTF8')::jsonb->>'original_hash' OR
  (p->>'amount_cents')::numeric IS DISTINCT FROM NEW.amount_cents::numeric OR (p->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.recorded_at THEN
  RAISE EXCEPTION 'native debt payment requires settled exact receipt and cannot overpay' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION native_payment_admission_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations;s native_payment_sources;b jsonb;want text;held numeric;counted bigint;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.id;
 IF o.command_kind<>'PAY_RECURRING' THEN RETURN NULL;END IF;
 SELECT * INTO s FROM native_payment_sources WHERE environment_id=o.environment_id AND user_id=o.user_id AND operation_id=o.id;
 b:=convert_from(o.bank_request,'UTF8')::jsonb;
 want:=CASE WHEN o.status='SETTLED' THEN 'COMMITTED' WHEN o.status IN('REJECTED','CANCELLED') THEN 'RELEASED' ELSE 'RESERVED' END;
 SELECT count(*),coalesce(sum(amount_cents),0) INTO counted,held FROM native_holds WHERE environment_id=o.environment_id AND user_id=o.user_id AND operation_id=o.id AND resource_kind='LIABILITY' AND resource_id=s.obligation_id AND state=want;
 IF s.operation_id IS NULL OR counted<>1 OR held IS DISTINCT FROM (b->'effect'->>'amount_cents')::numeric THEN
  RAISE EXCEPTION 'native payment requires one exact debt source and original liability pin' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_payment_source_exact BEFORE INSERT ON native_payment_sources FOR EACH ROW EXECUTE FUNCTION native_payment_source_guard();
CREATE TRIGGER native_payment_source_immutable BEFORE UPDATE OR DELETE ON native_payment_sources FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_obligation_payment_immutable BEFORE UPDATE OR DELETE ON native_obligation_payments FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER native_obligation_payment_exact AFTER INSERT ON native_obligation_payments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_payment_binding_guard();
CREATE CONSTRAINT TRIGGER native_payment_admission_exact AFTER INSERT OR UPDATE ON native_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_payment_admission_guard();
GRANT SELECT,INSERT ON native_payment_sources,native_obligation_payments TO zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native payment originals are forward only';END $$;
-- +goose StatementEnd
