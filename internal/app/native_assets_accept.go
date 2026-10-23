package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type nativeAssetApproval struct {
	Protocol                 string `json:"protocol"`
	PrincipalKind            string `json:"principal_kind"`
	ID, SessionID, PreviewID funds.ID
	Scope                    policy.TemplateOwnerScope `json:"scope"`
	PolicyVersionID          funds.ID                  `json:"policy_version_id"`
	ConfigurationHash        string                    `json:"configuration_hash"`
	Command                  NativeAssetCommand        `json:"command"`
	CommandHash              string                    `json:"command_hash"`
	EffectHash               string                    `json:"effect_hash"`
	ConfirmedAt              time.Time                 `json:"confirmed_at"`
	ExpiresAt                time.Time                 `json:"expires_at"`
}

func assetRequestAuthorities(preview NativeAssetPreview, approval funds.ID) ([]bankv2.Authority, error) {
	v := preview.Policy.Version
	allowCost := false
	config, e := v.Compiled.Decode()
	if e != nil {
		return nil, e
	}
	if asset, ok := config.(*policy.AssetAuthorizationConfig); ok {
		allowCost = asset.AllowEarlyWithdrawalWithPenalty
	}
	goal := preview.BankEffect.SourceGoalID
	a := []bankv2.Authority{{Role: "EXECUTE", SourceID: approval.String(), Version: 1, PolicyID: v.PolicyID.String(), PolicyVersion: int64(v.Version), GoalID: goal, AllowsFee: allowCost, AllowsLoss: allowCost}}
	if preview.Kind != bankv2.Purchase {
		if preview.Position == nil {
			return nil, nativeResourceConflict()
		}
		original := preview.Position.SourceAuthority
		original.Role = "POSITION_ORIGINAL"
		a = append(a, original)
	}
	return a, nil
}
func assetEquivalentEconomic(current, frozen NativeAssetPreview) error {
	if current.Policy.Version.VersionID != frozen.Policy.Version.VersionID || current.Policy.Version.Compiled.Hash != frozen.Policy.Version.Compiled.Hash || current.Kind != frozen.Kind || current.InventoryHash != frozen.InventoryHash {
		return reject("SOURCE_CHANGED", nil)
	}
	effect := current.BankEffect
	// The frozen contract covers every acceptance instant before its expiry.
	// It is never replaced by the later preview's moving two-minute window.
	effect.LatestArrivalAt = frozen.BankEffect.LatestArrivalAt
	effect.PrincipalAvailableBy = frozen.BankEffect.PrincipalAvailableBy
	effect.PlannedRedeemAt = frozen.BankEffect.PlannedRedeemAt
	a, e := effect.Digest()
	b, be := frozen.BankEffect.Digest()
	if e != nil || be != nil || a != b {
		return reject("SOURCE_CHANGED", e)
	}
	x, _ := encode(current.CashResources)
	y, _ := encode(frozen.CashResources)
	u, _ := encode(current.GoalUses)
	v, _ := encode(frozen.GoalUses)
	if string(x) != string(y) || string(u) != string(v) {
		return reject("SOURCE_CHANGED", nil)
	}
	return nil
}
func loadAssetOperation(ctx context.Context, tx pgx.Tx, p Principal, id funds.ID) (NativeOperation, error) {
	var text, kind string
	if e := tx.QueryRow(ctx, `SELECT group_id::text,command_kind FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&text, &kind); e != nil {
		return NativeOperation{}, reject("NOT_FOUND", e)
	}
	group, e := funds.ParseID(text)
	if e != nil || !nativeAssetKind(bankv2.Kind(kind)) {
		return NativeOperation{}, reject("NOT_FOUND", e)
	}
	value, e := loadNativeGroup(ctx, tx, p, group)
	if e != nil {
		return NativeOperation{}, e
	}
	if len(value.Operations) != 1 || value.Operations[0].ID != id {
		return NativeOperation{}, reject("EVIDENCE_INVALID", nil)
	}
	return value.Operations[0], nil
}
func (s *Service) NativeAssetOperation(ctx context.Context, p Principal, id funds.ID) (NativeOperation, error) {
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeOperation{}, e
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return NativeOperation{}, e
	}
	defer rollback(ctx, tx)
	return loadAssetOperation(ctx, tx, p, id)
}
func (s *Service) NativeAssetByKey(ctx context.Context, p Principal, kind bankv2.Kind, key string) (NativeOperation, error) {
	if e := validateKey(key); e != nil {
		return NativeOperation{}, e
	}
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeOperation{}, e
	}
	if !nativeAssetKind(kind) {
		return NativeOperation{}, reject("INVALID_NATIVE_ASSET", nil)
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return NativeOperation{}, e
	}
	defer rollback(ctx, tx)
	var text string
	if e = tx.QueryRow(ctx, `SELECT id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind=$3 AND idempotency_key=$4`, p.EnvironmentID.String(), p.UserID.String(), string(kind), key).Scan(&text); e != nil {
		return NativeOperation{}, reject("NOT_FOUND", e)
	}
	id, e := funds.ParseID(text)
	if e != nil {
		return NativeOperation{}, e
	}
	return loadAssetOperation(ctx, tx, p, id)
}

func (s *Service) AcceptNativeAsset(ctx context.Context, p Principal, key string, in NativeAssetCommand) (NativeOperation, error) {
	var zero NativeOperation
	if e := validateKey(key); e != nil {
		return zero, e
	}
	if in.PreviewID == (funds.ID{}) || !nativeActionHash(in.ReviewedHash) {
		return zero, reject("INVALID_NATIVE_ASSET", nil)
	}
	if e := s.auditHuman(ctx, p); e != nil {
		return zero, e
	}
	var kindText string
	if e := s.Pool.QueryRow(ctx, `SELECT kind FROM native_action_previews WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), in.PreviewID.String()).Scan(&kindText); e != nil {
		return zero, reject("NOT_FOUND", e)
	}
	kind := bankv2.Kind(kindText)
	if !nativeAssetKind(kind) {
		return zero, reject("INVALID_NATIVE_ASSET", nil)
	}
	// Bank reads precede the lock. An exact API-key replay below returns the
	// existing operation even if this optional current read is unavailable.
	var state bankv2.CurrentState
	var bankErr error
	reader, ok := s.NativeBank.(nativeAssetBankReader)
	if ok {
		state, bankErr = reader.CurrentState(ctx, s.Namespace, p.UserID.String())
	} else {
		bankErr = reject("BANK_UNAVAILABLE", nil)
	}
	raw, e := encode(in)
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
	var previous, hash string
	e = tx.QueryRow(ctx, `SELECT id::text,request_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind=$3 AND idempotency_key=$4`, env.ID.String(), p.UserID.String(), string(kind), key).Scan(&previous, &hash)
	if e == nil {
		if hash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, ie := funds.ParseID(previous)
		if ie != nil {
			return zero, ie
		}
		return loadAssetOperation(ctx, tx, p, id)
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return zero, e
	}
	if bankErr != nil {
		return zero, reject("BANK_UNAVAILABLE", nil)
	}
	preview, e := loadNativeAssetPreview(ctx, tx, p, in.PreviewID, kind)
	if e != nil {
		return zero, e
	}
	now := s.now()
	if preview.ReviewedHash != in.ReviewedHash {
		return zero, reject("REVIEW_MISMATCH", nil)
	}
	if now.Before(preview.CreatedAt) || !now.Before(preview.ExpiresAt) {
		return zero, reject("PLAN_EXPIRED", nil)
	}
	cost := !preview.BankEffect.Fee.IsZero() || !preview.BankEffect.Loss.IsZero()
	if cost != in.CostReviewed {
		return zero, reject("ASSET_EXACT_COST_REVIEW_REQUIRED", nil)
	}
	d := preview.Dependencies
	if fv != d.FundsVersion || sv != d.SourceEvidenceVersion || env.WriterGeneration != d.WriterGeneration || env.ControlVersion != d.ControlVersion {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	if preview.BankEffect.SourceGoalID != "" {
		goal, _ := funds.ParseID(preview.BankEffect.SourceGoalID)
		if e = ensureGoalAvailable(ctx, tx, p, goal); e != nil {
			return zero, e
		}
	}
	snapshot, e := s.loadTemplateSnapshot(ctx, tx, p, now, preview.Timezone)
	if e != nil {
		return zero, e
	}
	if snapshot.Facts.SourceHash != preview.SourceHash || Hash(snapshot.InventoryRaw) != preview.InventoryHash {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	pending, e := assetPendingPurchases(ctx, tx, p, s.Namespace)
	if e != nil {
		return zero, e
	}
	current, e := deriveNativeAssetPreview(snapshot, preview.Request, preview.ID, p.SessionID, state, preview.Quote, pending, preview.ExpiresAt)
	if e != nil {
		return zero, e
	}
	if e = assetEquivalentEconomic(current, preview); e != nil {
		return zero, e
	}
	op, e := newID()
	if e != nil {
		return zero, e
	}
	permit, e := newID()
	if e != nil {
		return zero, e
	}
	confirmation, e := newID()
	if e != nil {
		return zero, e
	}
	authorities, e := assetRequestAuthorities(preview, confirmation)
	if e != nil {
		return zero, e
	}
	request := bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: op.String(), OperationID: op.String(), UserID: p.UserID.String(), PermitID: permit.String(), ReferenceHash: preview.ReviewedHash, RequestedAt: now, ExpiresAt: preview.ExpiresAt, Effect: preview.BankEffect, Authorities: authorities}
	if cost {
		request.CostConsent = &bankv2.CostConsent{ConfirmationID: confirmation.String(), ReviewedEffectHash: preview.EffectHash, PrincipalKind: "USER", ConfirmedAt: now, ExpiresAt: preview.ExpiresAt}
	}
	bankRaw, e := request.CanonicalBytes()
	if e != nil {
		return zero, reject("ASSET_COST_NOT_AUTHORIZED", e)
	}
	bankHash, e := request.Digest()
	if e != nil {
		return zero, e
	}
	previewRaw, e := encode(preview)
	if e != nil {
		return zero, e
	}
	if len(previewRaw) > 1<<20 {
		return zero, reject("ASSET_CAPACITY", nil)
	}
	depsRaw, _ := encode(preview.Dependencies)
	refsRaw, _ := encode([]policy.TemplateVersion{preview.Policy.Version})
	_, e = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, env.ID.String(), p.UserID.String(), preview.ID.String(), string(kind), previewRaw, Hash(previewRaw), preview.EffectHash, refsRaw, preview.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, preview.ExpiresAt)
	if e != nil {
		return zero, e
	}
	approval := nativeAssetApproval{Protocol: NativeAssetProtocol + "/USER", PrincipalKind: "USER", ID: confirmation, SessionID: p.SessionID, PreviewID: preview.ID, Scope: preview.Scope, PolicyVersionID: preview.Policy.Version.VersionID, ConfigurationHash: preview.Policy.Version.Compiled.Hash, Command: in, CommandHash: Hash(raw), EffectHash: preview.EffectHash, ConfirmedAt: now, ExpiresAt: preview.ExpiresAt}
	approvalRaw, _ := encode(approval)
	hashesRaw, _ := encode([]string{preview.EffectHash})
	_, e = tx.Exec(ctx, `INSERT INTO native_confirmations(environment_id,user_id,id,preview_id,session_id,role,policy_id,policy_version,effect_hash,child_effect_hashes,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,'EXECUTE',$6,$7,$8,$9,$10,$11,$12,$13)`, env.ID.String(), p.UserID.String(), confirmation.String(), preview.ID.String(), p.SessionID.String(), preview.Policy.Version.PolicyID.String(), preview.Policy.Version.Version, preview.EffectHash, hashesRaw, approvalRaw, Hash(approvalRaw), now, preview.ExpiresAt)
	if e != nil {
		return zero, e
	}
	goals := []string{}
	if request.Effect.SourceGoalID != "" {
		goals = append(goals, request.Effect.SourceGoalID)
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,preview_id,group_id,child_number,subject_goal_ids,mode,confirmation_id,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$20,$9,$10,$11,$12,$13,$14,$14,1,$15::uuid[],'ONCE',$16,$17,$18,$19,$19)`, env.ID.String(), p.UserID.String(), op.String(), string(kind), key, raw, Hash(raw), s.Namespace, bankRaw, bankHash, preview.EffectHash, depsRaw, Hash(depsRaw), preview.ID.String(), goals, confirmation.String(), permit.String(), fv+1, now, request.Key)
	if e != nil {
		return zero, e
	}
	ledger := cloneNativeAssetLedger(*snapshot.Native)
	if e = reserveNativeAssetResources(&ledger, op, preview); e != nil {
		return zero, e
	}
	if e = s.Store.PersistNativeLedger(ctx, tx, env.ID, p.UserID, ledger); e != nil {
		return zero, e
	}
	if e = bridgeCreateJob(ctx, tx, p, op); e != nil {
		return zero, e
	}
	if _, e = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); e != nil {
		return zero, e
	}
	value, e := loadAssetOperation(ctx, tx, p, op)
	if e != nil {
		return zero, e
	}
	if e = tx.Commit(ctx); e != nil {
		return zero, e
	}
	return value, nil
}
