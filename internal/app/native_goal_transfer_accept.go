package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type nativeGoalTransferApproval struct {
	Protocol       string                     `json:"protocol"`
	PrincipalKind  string                     `json:"principal_kind"`
	Scope          policy.TemplateOwnerScope  `json:"scope"`
	ID             funds.ID                   `json:"id"`
	Role           string                     `json:"role"`
	PreviewID      funds.ID                   `json:"preview_id"`
	SessionID      funds.ID                   `json:"session_id"`
	Command        NativeGoalTransferCommand  `json:"command"`
	CommandHash    string                     `json:"command_hash"`
	BankEffectHash string                     `json:"bank_effect_hash"`
	ExecuteID      funds.ID                   `json:"execute_id"`
	Release        planning.PairedGoalConsent `json:"release"`
	Receive        planning.PairedGoalConsent `json:"receive"`
	ConfirmedAt    time.Time                  `json:"confirmed_at"`
	ExpiresAt      time.Time                  `json:"expires_at"`
}

func goalTransferUserConsents(preview NativeGoalTransferPreview, release, receive funds.ID, now time.Time) (planning.PairedGoalConsent, planning.PairedGoalConsent) {
	base := planning.PairedGoalConsent{ID: release, Scope: preview.Scope, Role: "RELEASE_SOURCE", PrincipalKind: "USER", PolicyID: preview.PairedEffect.PolicyID, PolicyVersionID: preview.PairedEffect.PolicyVersionID, SourceGoalID: preview.PairedEffect.SourceGoalID, TargetGoalID: preview.PairedEffect.TargetGoalID, Amount: preview.PairedEffect.Amount, ReviewedEffectHash: preview.PairedEffect.EffectHash, ConfirmedAt: now, ValidUntil: preview.ExpiresAt, Current: true, Verified: true}
	other := base
	other.ID, other.Role = receive, "RECEIVE_TARGET"
	return base, other
}

func validateGoalTransferCommand(command NativeGoalTransferCommand) error {
	if command.PreviewID == (funds.ID{}) || !nativeActionHash(command.ReviewedHash) || !command.ReleaseSource || !command.ReceiveTarget {
		return reject("GOAL_TRANSFER_TWO_USER_CONFIRMATIONS_REQUIRED", nil)
	}
	return nil
}

func loadGoalTransferOperation(ctx context.Context, tx pgx.Tx, p Principal, id funds.ID) (NativeOperation, error) {
	var group string
	err := tx.QueryRow(ctx, `SELECT group_id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND command_kind='TRANSFER_GOAL'`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&group)
	if err != nil {
		return NativeOperation{}, reject("NOT_FOUND", err)
	}
	groupID, err := funds.ParseID(group)
	if err != nil {
		return NativeOperation{}, err
	}
	value, err := loadNativeGroup(ctx, tx, p, groupID)
	if err != nil {
		return NativeOperation{}, err
	}
	if len(value.Operations) != 1 || value.Operations[0].ID != id || value.Operations[0].Kind != bankv2.MoveGoal {
		return NativeOperation{}, reject("EVIDENCE_INVALID", nil)
	}
	return value.Operations[0], nil
}

func (s *Service) NativeGoalTransferOperation(ctx context.Context, p Principal, id funds.ID) (NativeOperation, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeOperation{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeOperation{}, err
	}
	defer rollback(ctx, tx)
	return loadGoalTransferOperation(ctx, tx, p, id)
}

func (s *Service) NativeGoalTransferByKey(ctx context.Context, p Principal, key string) (NativeOperation, error) {
	if err := validateKey(key); err != nil {
		return NativeOperation{}, err
	}
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeOperation{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeOperation{}, err
	}
	defer rollback(ctx, tx)
	var raw string
	if err = tx.QueryRow(ctx, `SELECT id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='TRANSFER_GOAL' AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&raw); err != nil {
		return NativeOperation{}, reject("NOT_FOUND", err)
	}
	id, err := funds.ParseID(raw)
	if err != nil {
		return NativeOperation{}, err
	}
	return loadGoalTransferOperation(ctx, tx, p, id)
}

func (s *Service) AcceptNativeGoalTransfer(ctx context.Context, p Principal, key string, in NativeGoalTransferCommand) (NativeOperation, error) {
	var zero NativeOperation
	if err := validateKey(key); err != nil {
		return zero, err
	}
	if err := validateGoalTransferCommand(in); err != nil {
		return zero, err
	}
	raw, err := encode(in)
	if err != nil {
		return zero, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return zero, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return zero, err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return zero, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return zero, err
	}
	var prior, requestHash string
	err = tx.QueryRow(ctx, `SELECT id::text,request_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='TRANSFER_GOAL' AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&prior, &requestHash)
	if err == nil {
		if requestHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, e := funds.ParseID(prior)
		if e != nil {
			return zero, e
		}
		return loadGoalTransferOperation(ctx, tx, p, id)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	if s.NativeBank == nil {
		return zero, reject("BANK_UNAVAILABLE", nil)
	}
	preview, err := loadGoalTransferPreview(ctx, tx, p, in.PreviewID)
	if err != nil {
		return zero, err
	}
	if preview.ReviewedHash != in.ReviewedHash {
		return zero, reject("REVIEW_MISMATCH", nil)
	}
	now := s.now()
	if now.Before(preview.CreatedAt) || !now.Before(preview.ExpiresAt) {
		return zero, reject("PLAN_EXPIRED", nil)
	}
	d := preview.Dependencies
	if fv != d.FundsVersion || sv != d.SourceEvidenceVersion || env.WriterGeneration != d.WriterGeneration || env.ControlVersion != d.ControlVersion {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	for _, goal := range []funds.ID{preview.PairedEffect.SourceGoalID, preview.PairedEffect.TargetGoalID} {
		if err = ensureGoalAvailable(ctx, tx, p, goal); err != nil {
			return zero, err
		}
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, preview.Timezone)
	if err != nil {
		return zero, err
	}
	if snapshot.Facts.SourceHash != preview.SourceHash || Hash(snapshot.InventoryRaw) != preview.InventoryHash {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	usage, err := goalTransferUsage(ctx, tx, p, s.Namespace, preview.PairedEffect.PolicyID)
	if err != nil {
		return zero, err
	}
	current, err := deriveGoalTransferPreview(snapshot, preview.Request, preview.ID, p.SessionID, usage)
	if err != nil {
		return zero, err
	}
	a, _ := encode(current.PairedEffect)
	b, _ := encode(preview.PairedEffect)
	u, _ := encode(current.LotUses)
	v, _ := encode(preview.LotUses)
	if string(a) != string(b) || string(u) != string(v) {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	op, err := newID()
	if err != nil {
		return zero, err
	}
	permit, err := newID()
	if err != nil {
		return zero, err
	}
	execute, err := newID()
	if err != nil {
		return zero, err
	}
	releaseID, err := newID()
	if err != nil {
		return zero, err
	}
	receiveID, err := newID()
	if err != nil {
		return zero, err
	}
	release, receive := goalTransferUserConsents(preview, releaseID, receiveID, now)
	request := bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: op.String(), OperationID: op.String(), UserID: p.UserID.String(), PermitID: permit.String(), ReferenceHash: preview.ReviewedHash, RequestedAt: now, ExpiresAt: preview.ExpiresAt, Effect: preview.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: execute.String(), Version: 1, PolicyID: preview.PairedInput.Policy.PolicyID.String(), PolicyVersion: int64(preview.PairedInput.Policy.Version)}, {Role: "GOAL_RELEASE", SourceID: releaseID.String(), Version: 1, GoalID: preview.PairedEffect.SourceGoalID.String(), PolicyID: preview.PairedInput.Source.PolicyID.String(), PolicyVersion: int64(preview.PairedInput.Source.GoalVersion.Version)}, {Role: "GOAL_RECEIVE", SourceID: receiveID.String(), Version: 1, GoalID: preview.PairedEffect.TargetGoalID.String(), PolicyID: preview.PairedInput.Target.PolicyID.String(), PolicyVersion: int64(preview.PairedInput.Target.GoalVersion.Version)}}}
	bankRaw, err := request.CanonicalBytes()
	if err != nil {
		return zero, reject("GOAL_TRANSFER_INVALID", err)
	}
	bankHash, err := request.Digest()
	if err != nil {
		return zero, err
	}
	previewRaw, err := encode(preview)
	if err != nil {
		return zero, err
	}
	refsRaw, err := encode([]policy.TemplateVersion{preview.PairedInput.Policy, *preview.PairedInput.Source.GoalVersion, *preview.PairedInput.Target.GoalVersion})
	if err != nil {
		return zero, err
	}
	depsRaw, err := encode(preview.Dependencies)
	if err != nil {
		return zero, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,'TRANSFER_GOAL',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, env.ID.String(), p.UserID.String(), preview.ID.String(), previewRaw, Hash(previewRaw), preview.EffectHash, refsRaw, preview.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, preview.ExpiresAt)
	if err != nil {
		return zero, err
	}
	hashesRaw, _ := encode([]string{preview.EffectHash})
	for i, identity := range []funds.ID{execute, releaseID, receiveID} {
		role := []string{"EXECUTE", "RELEASE_SOURCE", "RECEIVE_TARGET"}[i]
		authority := request.Authorities[i]
		approval := nativeGoalTransferApproval{Protocol: NativeGoalTransferProtocol + "/USER", PrincipalKind: "USER", Scope: preview.Scope, ID: identity, Role: role, PreviewID: preview.ID, SessionID: p.SessionID, Command: in, CommandHash: Hash(raw), BankEffectHash: preview.EffectHash, ExecuteID: execute, Release: release, Receive: receive, ConfirmedAt: now, ExpiresAt: preview.ExpiresAt}
		original, e := encode(approval)
		if e != nil {
			return zero, e
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_confirmations(environment_id,user_id,id,preview_id,session_id,role,policy_id,policy_version,effect_hash,child_effect_hashes,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, env.ID.String(), p.UserID.String(), identity.String(), preview.ID.String(), p.SessionID.String(), role, authority.PolicyID, authority.PolicyVersion, preview.EffectHash, hashesRaw, original, Hash(original), now, preview.ExpiresAt)
		if err != nil {
			return zero, err
		}
	}
	if _, err = planning.CheckPairedGoalTransfer(current.PairedInput, release, receive); err != nil {
		return zero, reject("GOAL_TRANSFER_TWO_USER_CONFIRMATIONS_REQUIRED", err)
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,preview_id,group_id,child_number,subject_goal_ids,mode,confirmation_id,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3,'TRANSFER_GOAL',$4,$5,$6,$7,$20,$8,$9,$10,$11,$12,$13,$13,1,ARRAY[$14::uuid,$15::uuid],'ONCE',$16,$17,$18,$19,$19)`, env.ID.String(), p.UserID.String(), op.String(), key, raw, Hash(raw), s.Namespace, bankRaw, bankHash, preview.EffectHash, depsRaw, Hash(depsRaw), preview.ID.String(), preview.PairedEffect.SourceGoalID.String(), preview.PairedEffect.TargetGoalID.String(), execute.String(), permit.String(), fv+1, now, request.Key)
	if err != nil {
		return zero, err
	}
	ledger, err := reserveGoalTransferResources(*snapshot.Native, op, request.Effect, preview.LotUses)
	if err != nil {
		return zero, err
	}
	if err = s.Store.PersistNativeLedger(ctx, tx, env.ID, p.UserID, ledger); err != nil {
		return zero, err
	}
	if err = bridgeCreateJob(ctx, tx, p, op); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); err != nil {
		return zero, err
	}
	value, err := loadGoalTransferOperation(ctx, tx, p, op)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return value, nil
}
