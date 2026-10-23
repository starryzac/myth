package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type NativePaymentCommand struct {
	PreviewID    funds.ID `json:"preview_id"`
	ReviewedHash string   `json:"reviewed_hash"`
}
type nativePaymentApproval struct {
	Protocol      string                    `json:"protocol"`
	PrincipalKind string                    `json:"principal_kind"`
	Scope         policy.TemplateOwnerScope `json:"scope"`
	ID            funds.ID                  `json:"id"`
	SessionID     funds.ID                  `json:"session_id"`
	Command       NativePaymentCommand      `json:"command"`
	CommandHash   string                    `json:"command_hash"`
	EffectHash    string                    `json:"effect_hash"`
	ConfirmedAt   time.Time                 `json:"confirmed_at"`
	ExpiresAt     time.Time                 `json:"expires_at"`
}

func (s *Service) paymentBankLiability(ctx context.Context, p Principal, id string) (bankv2.LiabilityState, time.Time, error) {
	var zero bankv2.LiabilityState
	reader, ok := s.NativeBank.(nativePaymentBankReader)
	if !ok {
		return zero, time.Time{}, reject("BANK_UNAVAILABLE", nil)
	}
	snapshot, e := reader.Liabilities(ctx, s.Namespace, p.UserID.String())
	if e != nil {
		return zero, time.Time{}, reject("BANK_UNAVAILABLE", e)
	}
	now := s.now()
	if snapshot.Validate() != nil || snapshot.Namespace != s.Namespace || snapshot.UserID != p.UserID.String() || snapshot.CapturedAt.After(now.Add(time.Second)) || now.Sub(snapshot.CapturedAt) > 30*time.Second {
		return zero, time.Time{}, reject("BANK_SNAPSHOT_STALE", nil)
	}
	for _, row := range snapshot.Liabilities {
		if row.Original.ID == id {
			return row, snapshot.CapturedAt, nil
		}
	}
	return zero, time.Time{}, reject("PAYMENT_BANK_ORIGINAL_REQUIRED", nil)
}

func loadNativePaymentOperation(ctx context.Context, tx pgx.Tx, p Principal, id funds.ID) (NativeOperation, error) {
	var groupText string
	if e := tx.QueryRow(ctx, `SELECT group_id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND command_kind='PAY_RECURRING'`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&groupText); e != nil {
		return NativeOperation{}, reject("NOT_FOUND", e)
	}
	group, e := funds.ParseID(groupText)
	if e != nil {
		return NativeOperation{}, e
	}
	out, e := loadNativeGroup(ctx, tx, p, group)
	if e != nil {
		return NativeOperation{}, e
	}
	if len(out.Operations) != 1 || out.Operations[0].ID != id || out.Operations[0].Kind != bankv2.Payment {
		return NativeOperation{}, reject("EVIDENCE_INVALID", nil)
	}
	return out.Operations[0], nil
}
func (s *Service) NativePaymentOperation(ctx context.Context, p Principal, id funds.ID) (NativeOperation, error) {
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeOperation{}, e
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return NativeOperation{}, e
	}
	defer rollback(ctx, tx)
	return loadNativePaymentOperation(ctx, tx, p, id)
}
func (s *Service) NativePaymentByKey(ctx context.Context, p Principal, key string) (NativeOperation, error) {
	if e := validateKey(key); e != nil {
		return NativeOperation{}, e
	}
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeOperation{}, e
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return NativeOperation{}, e
	}
	defer rollback(ctx, tx)
	var text string
	if e = tx.QueryRow(ctx, `SELECT id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='PAY_RECURRING' AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&text); e != nil {
		return NativeOperation{}, reject("NOT_FOUND", e)
	}
	id, e := funds.ParseID(text)
	if e != nil {
		return NativeOperation{}, e
	}
	return loadNativePaymentOperation(ctx, tx, p, id)
}

// Each new request reads the bank outside T1. Original-key replay needs no bank.
func (s *Service) AcceptNativePayment(ctx context.Context, p Principal, key string, in NativePaymentCommand) (NativeOperation, error) {
	var zero NativeOperation
	if e := validateKey(key); e != nil {
		return zero, e
	}
	if in.PreviewID == (funds.ID{}) || !nativeActionHash(in.ReviewedHash) {
		return zero, reject("PAYMENT_REVIEW_REQUIRED", nil)
	}
	if e := s.auditHuman(ctx, p); e != nil {
		return zero, e
	}
	raw, e := encode(in)
	if e != nil {
		return zero, e
	}
	var prior, priorHash string
	e = s.Pool.QueryRow(ctx, `SELECT id::text,request_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='PAY_RECURRING' AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&prior, &priorHash)
	if e == nil {
		if priorHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, e := funds.ParseID(prior)
		if e != nil {
			return zero, e
		}
		return s.NativePaymentOperation(ctx, p, id)
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return zero, e
	}
	original, e := loadNativePaymentPreview(ctx, s.Pool, p, in.PreviewID)
	if e != nil {
		return zero, e
	}
	bank, observed, e := s.paymentBankLiability(ctx, p, original.Debt.ID)
	if e != nil {
		return zero, e
	}
	tx, e := s.begin(ctx)
	if e != nil {
		return zero, e
	}
	defer rollback(ctx, tx)
	env, e := s.environment(ctx, tx, true)
	if e != nil {
		return zero, e
	}
	if e = sameUser(p, env.ID); e != nil {
		return zero, e
	}
	fv, sv, e := lockUser(ctx, tx, p)
	if e != nil {
		return zero, e
	}
	if e = s.human(ctx, tx, p); e != nil {
		return zero, e
	}
	e = tx.QueryRow(ctx, `SELECT id::text,request_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='PAY_RECURRING' AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&prior, &priorHash)
	if e == nil {
		if priorHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, e := funds.ParseID(prior)
		if e != nil {
			return zero, e
		}
		return loadNativePaymentOperation(ctx, tx, p, id)
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return zero, e
	}
	preview, e := loadNativePaymentPreview(ctx, tx, p, in.PreviewID)
	if e != nil {
		return zero, e
	}
	now := s.now()
	d := preview.Dependencies
	if preview.ReviewedHash != in.ReviewedHash {
		return zero, reject("REVIEW_MISMATCH", nil)
	}
	if now.Before(preview.CreatedAt) || !now.Before(preview.ExpiresAt) {
		return zero, reject("PLAN_EXPIRED", nil)
	}
	if fv != d.FundsVersion || sv != d.SourceEvidenceVersion || env.WriterGeneration != d.WriterGeneration || env.ControlVersion != d.ControlVersion {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	if bank != preview.Liability {
		return zero, reject("PAYMENT_BANK_SOURCE_CHANGED", nil)
	}
	snapshot, e := s.loadTemplateSnapshot(ctx, tx, p, now, preview.Timezone)
	if e != nil {
		return zero, e
	}
	if snapshot.Native == nil || snapshot.Facts.SourceHash != preview.SourceHash || Hash(snapshot.InventoryRaw) != preview.InventoryHash {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	current, e := currentNativePayment(snapshot, preview, bank)
	if e != nil {
		return zero, e
	}
	a, _ := encode(current)
	b, _ := encode(preview.Resources)
	if !templateSameBytes(a, b) {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	op, e := newID()
	if e != nil {
		return zero, e
	}
	permit, e := newID()
	if e != nil {
		return zero, e
	}
	confirm, e := newID()
	if e != nil {
		return zero, e
	}
	request := bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: op.String(), OperationID: op.String(), UserID: p.UserID.String(), PermitID: permit.String(), ReferenceHash: preview.ReviewedHash, RequestedAt: now, ExpiresAt: preview.ExpiresAt, Effect: preview.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: confirm.String(), Version: 1, PolicyID: preview.Policy.Version.PolicyID.String(), PolicyVersion: int64(preview.Policy.Version.Version)}}}
	bankRaw, e := request.CanonicalBytes()
	if e != nil {
		return zero, reject("PAYMENT_REQUEST_INVALID", e)
	}
	bankHash, _ := request.Digest()
	previewRaw, _ := encode(preview)
	refs, _ := encode([]policy.TemplateVersion{preview.Policy.Version})
	deps, _ := encode(d)
	_, e = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,'PAY_RECURRING',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, env.ID.String(), p.UserID.String(), preview.ID.String(), previewRaw, Hash(previewRaw), preview.EffectHash, refs, preview.SourceHash, deps, Hash(deps), bankRaw, bankHash, now, preview.ExpiresAt)
	if e != nil {
		return zero, e
	}
	approval := nativePaymentApproval{Protocol: "native-payment-USER-v1", PrincipalKind: "USER", Scope: preview.Scope, ID: confirm, SessionID: p.SessionID, Command: in, CommandHash: Hash(raw), EffectHash: preview.EffectHash, ConfirmedAt: now, ExpiresAt: preview.ExpiresAt}
	approvalRaw, _ := encode(approval)
	hashes, _ := encode([]string{preview.EffectHash})
	_, e = tx.Exec(ctx, `INSERT INTO native_confirmations(environment_id,user_id,id,preview_id,session_id,role,policy_id,policy_version,effect_hash,child_effect_hashes,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,'EXECUTE',$6,$7,$8,$9,$10,$11,$12,$13)`, env.ID.String(), p.UserID.String(), confirm.String(), preview.ID.String(), p.SessionID.String(), preview.Policy.Version.PolicyID.String(), preview.Policy.Version.Version, preview.EffectHash, hashes, approvalRaw, Hash(approvalRaw), now, preview.ExpiresAt)
	if e != nil {
		return zero, e
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,preview_id,group_id,child_number,mode,confirmation_id,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3,'PAY_RECURRING',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$14,1,'ONCE',$15,$16,$17,$18,$18)`, env.ID.String(), p.UserID.String(), op.String(), key, raw, Hash(raw), s.Namespace, request.Key, bankRaw, bankHash, preview.EffectHash, deps, Hash(deps), preview.ID.String(), confirm.String(), permit.String(), fv+1, now)
	if e != nil {
		return zero, e
	}
	ledger := *snapshot.Native
	if e = reserveNativeUnownedCash(&ledger, op, bankv2.Payment, preview.Resources); e != nil {
		return zero, e
	}
	ledger.Holds = append(ledger.Holds, postgres.NativeHold{OperationID: op, ResourceKind: "LIABILITY", ResourceID: preview.Debt.ID, Amount: preview.Resources.Amount, State: funds.Reserved})
	if e = s.Store.PersistNativeLedger(ctx, tx, env.ID, p.UserID, ledger); e != nil {
		return zero, e
	}
	sourceRaw, _ := encode(bank)
	if _, e = tx.Exec(ctx, `INSERT INTO native_payment_sources(environment_id,user_id,operation_id,obligation_id,original,original_hash,observed_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, env.ID.String(), p.UserID.String(), op.String(), preview.Debt.ID, sourceRaw, Hash(sourceRaw), observed); e != nil {
		return zero, e
	}
	if e = bridgeCreateJob(ctx, tx, p, op); e != nil {
		return zero, e
	}
	if _, e = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); e != nil {
		return zero, e
	}
	out, e := loadNativePaymentOperation(ctx, tx, p, op)
	if e != nil {
		return zero, e
	}
	if e = tx.Commit(ctx); e != nil {
		return zero, e
	}
	return out, nil
}

// Replays the current rule, exact adopted debt, unowned resources and every
// daily protection margin. A payment need not fit discretionary CurrentSafe.
func currentNativePayment(snapshot templateSnapshot, preview NativePaymentPreview, bank bankv2.LiabilityState) (NativeCashResources, error) {
	var zero NativeCashResources
	var version *policy.TemplateVersion
	for i := range snapshot.Views {
		if snapshot.Views[i].Version.VersionID == preview.Request.PolicyVersionID {
			version = &snapshot.Views[i].Version
		}
	}
	if version == nil || snapshot.Native == nil {
		return zero, reject("PAYMENT_RULE_INVALID", nil)
	}
	for _, hold := range snapshot.Native.Holds {
		if hold.ResourceKind == "LIABILITY" && hold.ResourceID == preview.Debt.ID && hold.State == funds.Reserved {
			return zero, reject("PAYMENT_OPERATION_PENDING", nil)
		}
	}
	for _, d := range snapshot.Retained {
		if d.ID == preview.Debt.ID {
			current := preview
			current.Policy.Version = *version
			current.Debt = d
			current.Liability = bank
			current.Timezone = snapshot.Timezone
			proof, e := nativePaymentCurrentBillProof(snapshot, preview, bank)
			if e != nil {
				return zero, e
			}
			current.BillProof = proof
			current.AdoptionProof, e = nativePaymentCurrentAdoptionProof(snapshot, preview, bank)
			if e != nil {
				return zero, e
			}
			amount, e := nativePaymentPreviewAmount(current, snapshot.AsOf)
			if e != nil {
				return zero, e
			}
			if amount != preview.Resources.Amount {
				return zero, reject("SOURCE_CHANGED", nil)
			}
			resources, e := selectNativeUnownedCash(*snapshot.Native, []funds.ID{preview.Request.SourceAccountID}, amount)
			if e != nil || resources.Amount != amount {
				return zero, reject("FUNDS_UNAVAILABLE", e)
			}
			beforeInput := snapshot.forecastInput(365)
			before, e := planning.Forecast365(beforeInput)
			if e != nil {
				return zero, e
			}
			afterInput, e := nativePaymentPreviewAfter(beforeInput, current, amount)
			if e != nil {
				return zero, e
			}
			after, e := planning.Forecast365(afterInput)
			if e != nil {
				return zero, e
			}
			if e = nativePaymentMargins(before, after); e != nil {
				return zero, e
			}
			return resources, nil
		}
	}
	return zero, reject("PAYMENT_DEBT_NOT_ADOPTED", nil)
}
