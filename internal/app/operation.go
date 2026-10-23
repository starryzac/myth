package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type OperationRequest struct {
	PlanID  funds.ID  `json:"plan_id"`
	GrantID *funds.ID `json:"grant_id,omitempty"`
	Mode    string    `json:"mode"`
}
type Operation struct {
	ID                 funds.ID    `json:"id"`
	State              string      `json:"state"`
	Amount             funds.Money `json:"amount_cents"`
	PlanID             funds.ID    `json:"plan_id"`
	BankKey            string      `json:"bank_key"`
	BankObservedStatus *string     `json:"bank_observed_status,omitempty"`
	ProcessingHealth   string      `json:"processing_health"`
	ReceiptID          *string     `json:"receipt_id,omitempty"`
	CreatedAt          time.Time   `json:"created_at"`
	UpdatedAt          time.Time   `json:"updated_at"`
	Version            uint64      `json:"version"`
}

func scanOperation(row pgx.Row) (Operation, error) {
	var v Operation
	var id, plan string
	var raw []byte
	err := row.Scan(&id, &plan, &v.State, &v.BankKey, &v.BankObservedStatus, &v.ProcessingHealth, &v.ReceiptID, &v.CreatedAt, &v.UpdatedAt, &v.Version, &raw)
	if err != nil {
		return v, err
	}
	var req execution.BankRequest
	if err = decode(raw, &req); err != nil {
		return v, err
	}
	if err = req.Validate(); err != nil {
		return v, err
	}
	v.ID, err = funds.ParseID(id)
	if err != nil {
		return v, err
	}
	v.PlanID, err = funds.ParseID(plan)
	v.Amount = req.Amount
	return v, err
}

const operationColumns = `id::text,plan_id::text,status,bank_key,bank_observed_status,processing_health,receipt_id::text,created_at,updated_at,version,bank_request`

func (s *Service) Operation(ctx context.Context, p Principal, id funds.ID) (Operation, error) {
	if err := s.operationReadScope(ctx, p); err != nil {
		return Operation{}, err
	}
	v, err := scanOperation(s.Pool.QueryRow(ctx, "SELECT "+operationColumns+" FROM operations WHERE environment_id=$1 AND user_id=$2 AND id=$3", p.EnvironmentID.String(), p.UserID.String(), id.String()))
	if errors.Is(err, pgx.ErrNoRows) {
		err = reject("NOT_FOUND", err)
	}
	return v, err
}
func (s *Service) OperationByKey(ctx context.Context, p Principal, key string) (Operation, error) {
	if err := s.operationReadScope(ctx, p); err != nil {
		return Operation{}, err
	}
	if err := validateKey(key); err != nil {
		return Operation{}, err
	}
	v, err := scanOperation(s.Pool.QueryRow(ctx, "SELECT "+operationColumns+" FROM operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='ALLOCATE_GOAL' AND idempotency_key=$3", p.EnvironmentID.String(), p.UserID.String(), key))
	if errors.Is(err, pgx.ErrNoRows) {
		err = reject("NOT_FOUND", err)
	}
	return v, err
}

func (s *Service) operationReadScope(ctx context.Context, p Principal) error {
	env, err := s.environment(ctx, s.Pool, false)
	if err != nil {
		return err
	}
	return sameUser(p, env.ID)
}

// Accept commits the original command, exact reservation and durable job in T1.
// It never calls the bank. The caller can recover a lost HTTP response by key.
func (s *Service) Accept(ctx context.Context, p Principal, key string, in OperationRequest) (Operation, error) {
	var empty Operation
	if err := validateKey(key); err != nil {
		return empty, err
	}
	if (in.Mode != "STANDING" && in.Mode != "ONCE") || (in.Mode == "STANDING" && in.GrantID == nil) || (in.Mode == "ONCE" && in.GrantID != nil) {
		return empty, reject("INVALID_COMMAND", nil)
	}
	raw, err := encode(in)
	if err != nil {
		return empty, err
	}
	commandHash := Hash(raw)
	tx, err := s.begin(ctx)
	if err != nil {
		return empty, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return empty, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return empty, err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return empty, err
	}
	if p.Kind == "USER" {
		if err = s.human(ctx, tx, p); err != nil {
			return empty, err
		}
	}
	var existingID, existingHash string
	err = tx.QueryRow(ctx, "SELECT id::text,request_hash FROM operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='ALLOCATE_GOAL' AND idempotency_key=$3", env.ID.String(), p.UserID.String(), key).Scan(&existingID, &existingHash)
	if err == nil {
		if existingHash != commandHash {
			return empty, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, err := funds.ParseID(existingID)
		if err != nil {
			return empty, err
		}
		v, err := scanOperation(tx.QueryRow(ctx, "SELECT "+operationColumns+" FROM operations WHERE environment_id=$1 AND user_id=$2 AND id=$3", env.ID.String(), p.UserID.String(), id.String()))
		return v, err
	}
	if err != pgx.ErrNoRows {
		return empty, err
	}
	if err = ensureNativeBillsReady(ctx, tx, p); err != nil {
		return empty, err
	}
	candidate, err := loadCandidate(ctx, tx, p, in.PlanID)
	if err != nil {
		return empty, err
	}
	if err = ensureGoalAvailable(ctx, tx, p, candidate.Scope.GoalID); err != nil {
		return empty, err
	}
	pol, err := loadPolicy(ctx, tx, p, candidate.PolicyID, true)
	if err != nil {
		return empty, err
	}
	if err = ensureLegacyGoalUnbound(ctx, tx, p, pol.Scope.GoalID); err != nil {
		return empty, err
	}
	now := s.now()
	dep := candidate.Dependencies
	if dep.FundsVersion != fv || dep.SourceEvidenceVersion != sv || dep.WriterGeneration != env.WriterGeneration || dep.ControlVersion != env.ControlVersion || dep.PolicyVersion != pol.Version {
		return empty, reject("SOURCE_CHANGED", nil)
	}
	var authorization planning.Authorization
	var grantID, grantVersion, confirmationID any
	if in.Mode == "STANDING" {
		g, err := loadGrant(ctx, tx, p, *in.GrantID, true)
		if err != nil {
			return empty, err
		}
		authorization, err = planning.AuthorizeStanding(candidate, pol, g, now)
		if err != nil {
			return empty, reject("AUTHORIZATION_REVOKED", err)
		}
		grantID = g.ID.String()
		grantVersion = g.Version
	} else {
		if err = s.human(ctx, tx, p); err != nil {
			return empty, err
		}
		cid, err := newID()
		if err != nil {
			return empty, err
		}
		c := policy.ConfirmationSource{ID: cid, PrincipalKind: "USER", EnvironmentID: env.ID, UserID: p.UserID, TargetKind: "PLAN", TargetID: candidate.ID, TargetVersion: 1, ReviewedHash: candidate.EffectHash, ConfirmedAt: now, ExpiresAt: now.Add(5 * time.Minute)}
		authorization, err = planning.AuthorizeOnce(candidate, pol, c, now)
		if err != nil {
			return empty, reject("CONFIRMATION_INVALID", err)
		}
		if err = saveConfirmation(ctx, tx, p, c); err != nil {
			return empty, err
		}
		confirmationID = cid.String()
	}
	if len(candidate.Uses) != 1 {
		return empty, reject("UNSUPPORTED_MULTI_SOURCE", nil)
	}
	opID, err := newID()
	if err != nil {
		return empty, err
	}
	permitID, err := newID()
	if err != nil {
		return empty, err
	}
	jobID, err := newID()
	if err != nil {
		return empty, err
	}
	bankRequest := execution.BankRequest{ProtocolVersion: execution.BankProtocol, Namespace: s.Namespace, Key: opID.String(), OperationID: opID.String(), UserID: p.UserID.String(), Kind: "ALLOCATE_GOAL", Amount: candidate.Amount, SourceAccountID: candidate.Scope.SourceAccountID.String(), DestinationAccountID: candidate.Scope.DestinationAccountID.String(), IncomeOriginID: candidate.Uses[0].OriginID.String(), GoalID: candidate.Scope.GoalID.String(), ReferenceHash: candidate.EffectHash, AuthorizationID: authorization.SourceID.String(), AuthorizationVersion: int64(authorization.SourceVersion), PermitID: permitID.String(), RequestedAt: now, LatestArrivalAt: now.Add(24 * time.Hour)}
	bankRaw, err := bankRequest.CanonicalBytes()
	if err != nil {
		return empty, err
	}
	bankHash, err := bankRequest.Digest()
	if err != nil {
		return empty, err
	}
	depRaw, err := encode(dep)
	if err != nil {
		return empty, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO operations(environment_id,user_id,id,plan_id,policy_id,policy_version,grant_id,grant_version,command_kind,idempotency_key,request_hash,request,bank_namespace,bank_key,bank_request_hash,bank_request,status,version,permit_id,dependencies,created_at,updated_at,reserved_funds_version,authorization_kind,confirmation_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'ALLOCATE_GOAL',$9,$10,$11,$12,$13,$14,$15,'QUEUED',1,$16,$17,$18,$18,$19,$20,$21)`, env.ID.String(), p.UserID.String(), opID.String(), candidate.ID.String(), pol.ID.String(), pol.Version, grantID, grantVersion, key, commandHash, raw, s.Namespace, opID.String(), bankHash, bankRaw, permitID.String(), depRaw, now, fv+1, authorization.Kind, confirmationID)
	if err != nil {
		return empty, err
	}
	ledger, err := s.Store.LoadLedger(ctx, tx, env.ID, p.UserID, now)
	if err != nil {
		return empty, reject("EVIDENCE_INVALID", err)
	}
	ledger, err = ledger.Reserve(opID, candidate.Uses, funds.AllocateGoal, now, nil)
	if err != nil {
		return empty, reject("INSUFFICIENT_FUNDS", err)
	}
	if err = s.Store.SaveLedger(ctx, tx, env.ID, p.UserID, ledger); err != nil {
		return empty, err
	}
	_, err = tx.Exec(ctx, "UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2", env.ID.String(), p.UserID.String())
	if err != nil {
		return empty, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO jobs(environment_id,user_id,id,operation_id,next_attempt_at) VALUES($1,$2,$3,$4,clock_timestamp())`, env.ID.String(), p.UserID.String(), jobID.String(), opID.String())
	if err != nil {
		return empty, err
	}
	view, err := scanOperation(tx.QueryRow(ctx, "SELECT "+operationColumns+" FROM operations WHERE environment_id=$1 AND user_id=$2 AND id=$3", env.ID.String(), p.UserID.String(), opID.String()))
	if err != nil {
		return empty, err
	}
	if err = tx.Commit(ctx); err != nil {
		return empty, err
	}
	if s.Failpoint != nil {
		if err = s.Failpoint("after_accept_commit"); err != nil {
			return empty, err
		}
	}
	return view, nil
}
