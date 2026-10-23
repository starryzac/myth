package app

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const RecoveryProtocol = "zhiyu-recovery-cutover-v1"

// RecoveryWriterPin is trusted local process configuration, never a request DTO.
type RecoveryWriterPin struct {
	OwnerID    funds.ID `json:"owner_id"`
	Generation uint64   `json:"generation"`
}
type RecoveryWriterControl struct {
	EnvironmentID funds.ID `json:"environment_id"`
	OwnerID       funds.ID `json:"owner_id"`
	Generation    uint64   `json:"generation"`
	Version       uint64   `json:"version"`
	Mode          string   `json:"mode"`
}
type RecoveryControlEvent struct {
	Protocol      string                 `json:"protocol"`
	ID            funds.ID               `json:"id"`
	EnvironmentID funds.ID               `json:"environment_id"`
	Before        *RecoveryWriterControl `json:"before,omitempty"`
	After         RecoveryWriterControl  `json:"after"`
	Reason        string                 `json:"reason"`
	At            time.Time              `json:"at"`
}
type recoveryIntent struct {
	OperationID  funds.ID
	UserID       funds.ID
	Family, Mode string
}
type recoveryIntentKey struct{}

// writerExec admits a standalone durable write under the same fixed identity
// and environment-lock boundary as a normal application transaction. Captured
// candidates keep their old dependency vector; this never refreshes authority.
func (s *Service) writerExec(ctx context.Context, query string, args ...any) (pgconn.CommandTag, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return pgconn.CommandTag{}, err
	}
	defer rollback(ctx, tx)
	if _, err = s.environment(ctx, tx, true); err != nil {
		return pgconn.CommandTag{}, err
	}
	tag, err := tx.Exec(ctx, query, args...)
	if err != nil {
		return tag, err
	}
	return tag, tx.Commit(ctx)
}

func (s *Service) installRecoveryWriter(ctx context.Context, tx pgx.Tx) error {
	owner, generation := "", ""
	if s.WriterPin != nil {
		owner, generation = s.WriterPin.OwnerID.String(), fmt.Sprint(s.WriterPin.Generation)
	}
	intent, _ := ctx.Value(recoveryIntentKey{}).(recoveryIntent)
	op, user := "", ""
	if intent.OperationID != (funds.ID{}) {
		op, user = intent.OperationID.String(), intent.UserID.String()
	}
	_, err := tx.Exec(ctx, `SELECT set_config('zhiyu.writer_owner',$1,true),set_config('zhiyu.writer_generation',$2,true),set_config('zhiyu.recovery_operation',$3,true),set_config('zhiyu.recovery_user',$4,true),set_config('zhiyu.recovery_family',$5,true),set_config('zhiyu.recovery_mode',$6,true)`, owner, generation, op, user, intent.Family, intent.Mode)
	return err
}

func recoveryControl(ctx context.Context, q queryRow, id funds.ID) (RecoveryWriterControl, bool, error) {
	var out RecoveryWriterControl
	var present bool
	if err := q.QueryRow(ctx, `SELECT to_regclass('public.writer_controls') IS NOT NULL`).Scan(&present); err != nil || !present {
		return out, false, err
	}
	var owner string
	err := q.QueryRow(ctx, `SELECT owner_id::text,generation,version,mode FROM writer_controls WHERE environment_id=$1 FOR SHARE`, id.String()).Scan(&owner, &out.Generation, &out.Version, &out.Mode)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, false, nil
	}
	if err != nil {
		return out, false, err
	}
	out.EnvironmentID = id
	out.OwnerID, err = funds.ParseID(owner)
	return out, true, err
}
func (s *Service) checkRecoveryWriter(ctx context.Context, q queryRow, env Environment) error {
	c, present, err := recoveryControl(ctx, q, env.ID)
	if err != nil || !present {
		return err
	}
	if s.WriterPin == nil || s.WriterPin.OwnerID != c.OwnerID || s.WriterPin.Generation != c.Generation || env.WriterGeneration != c.Generation {
		return reject("WRITER_FENCED", nil)
	}
	if c.Mode == "ACTIVE" {
		return nil
	}
	i, _ := ctx.Value(recoveryIntentKey{}).(recoveryIntent)
	if c.Mode != "RECOVERING" || i.OperationID == (funds.ID{}) || (i.Mode != "SIGNED" && i.Mode != "CANCEL_UNSIGNED") {
		return reject("WRITER_RECOVERING", nil)
	}
	return nil // SQL fence additionally checks the exact durable operation/signature.
}

// ArmRecoveryWriter explicitly enables SQL fencing. No service pin is mutated.
// The caller must create a newly pinned Service after this commits.
func (s *Service) ArmRecoveryWriter(ctx context.Context, owner funds.ID) (RecoveryWriterControl, error) {
	return s.transitionRecoveryWriter(ctx, nil, owner, "RECOVERING", "INITIAL_CONTROL")
}

func (s *Service) ArmReadOnlyRecoveryWriter(ctx context.Context, owner funds.ID) (RecoveryWriterControl, error) {
	return s.transitionRecoveryWriter(ctx, nil, owner, "READ_ONLY", "INITIAL_READ_ONLY")
}

func (s *Service) WriterControl(ctx context.Context) (RecoveryWriterControl, error) {
	env, err := s.environment(ctx, s.Pool, false)
	if err != nil {
		return RecoveryWriterControl{}, err
	}
	c, present, err := recoveryControl(ctx, s.Pool, env.ID)
	if err == nil && !present {
		err = reject("WRITER_CONTROL_MISSING", nil)
	}
	return c, err
}

// ReadOnlyRecoveryWriter closes financial admission and local T3 before an
// operator drains old external requests. Reads and authentication still work.
func (s *Service) ReadOnlyRecoveryWriter(ctx context.Context, expected RecoveryWriterControl) (RecoveryWriterControl, error) {
	var out RecoveryWriterControl
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	var env string
	if err = tx.QueryRow(ctx, `SELECT id::text FROM environments WHERE namespace=$1 FOR UPDATE`, s.Namespace).Scan(&env); err != nil {
		return out, err
	}
	id, err := funds.ParseID(env)
	if err != nil {
		return out, err
	}
	c, present, err := recoveryControl(ctx, tx, id)
	if err != nil {
		return out, err
	}
	if !present || c != expected || s.WriterPin == nil || s.WriterPin.OwnerID != c.OwnerID || s.WriterPin.Generation != c.Generation {
		return out, reject("WRITER_FENCED", nil)
	}
	out = c
	out.Mode = "READ_ONLY"
	out.Version++
	eventID, err := newID()
	if err != nil {
		return out, err
	}
	event := RecoveryControlEvent{RecoveryProtocol + "/control", eventID, id, &c, out, "ENTER_READ_ONLY", s.now()}
	raw, _ := encode(event)
	if _, err = tx.Exec(ctx, `SELECT set_config('zhiyu.recovery_mode','CONTROL',true)`); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE environments SET control_version=control_version+1 WHERE id=$1`, env); err != nil {
		return out, err
	}
	if err = invalidateRecoveryLeases(ctx, tx, env); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO writer_control_events(environment_id,id,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5)`, env, eventID.String(), raw, Hash(raw), event.At); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE writer_controls SET mode='READ_ONLY',version=$2,event_id=$3,updated_at=$4 WHERE environment_id=$1`, env, out.Version, eventID.String(), event.At); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

// HandoffRecoveryWriter invalidates existing queue leases and changes owner at
// the environment lock boundary. Already committed external calls retain their
// bank keys; the old owner cannot commit their local T3 after this boundary.
func (s *Service) HandoffRecoveryWriter(ctx context.Context, expected RecoveryWriterControl, owner funds.ID) (RecoveryWriterControl, error) {
	return s.transitionRecoveryWriter(ctx, &expected, owner, "RECOVERING", "OWNER_HANDOFF")
}

func (s *Service) transitionRecoveryWriter(ctx context.Context, expected *RecoveryWriterControl, owner funds.ID, mode, reason string) (RecoveryWriterControl, error) {
	var out RecoveryWriterControl
	if owner == (funds.ID{}) {
		return out, reject("INVALID_WRITER_CONTROL", nil)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	var eid string
	var generation, control uint64
	err = tx.QueryRow(ctx, `SELECT id::text,writer_generation,control_version FROM environments WHERE namespace=$1 FOR UPDATE`, s.Namespace).Scan(&eid, &generation, &control)
	if err != nil {
		return out, err
	}
	env, err := funds.ParseID(eid)
	if err != nil {
		return out, err
	}
	current, present, err := recoveryControl(ctx, tx, env)
	if err != nil {
		return out, err
	}
	if expected == nil {
		if present {
			return out, reject("WRITER_CONTROL_EXISTS", nil)
		}
	} else if !present || current != *expected || s.WriterPin == nil || s.WriterPin.OwnerID != current.OwnerID || s.WriterPin.Generation != current.Generation {
		return out, reject("WRITER_FENCED", nil)
	}
	version := uint64(1)
	if present {
		version = current.Version + 1
	}
	out = RecoveryWriterControl{env, owner, generation + 1, version, mode}
	id, err := newID()
	if err != nil {
		return out, err
	}
	event := RecoveryControlEvent{Protocol: RecoveryProtocol + "/control", ID: id, EnvironmentID: env, Before: expected, After: out, Reason: reason, At: s.now()}
	raw, err := encode(event)
	if err != nil {
		return out, err
	}
	// CONTROL only permits generation, control-row and lease transitions in SQL.
	if _, err = tx.Exec(ctx, `SELECT set_config('zhiyu.recovery_mode','CONTROL',true)`); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE environments SET writer_generation=$2,control_version=$3 WHERE id=$1`, eid, out.Generation, control+1); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO writer_control_events(environment_id,id,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5)`, eid, id.String(), raw, Hash(raw), event.At); err != nil {
		return out, err
	}
	if err = invalidateRecoveryLeases(ctx, tx, eid); err != nil {
		return out, err
	}
	if !present {
		_, err = tx.Exec(ctx, `INSERT INTO writer_controls(environment_id,owner_id,generation,version,mode,event_id,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, eid, owner.String(), out.Generation, version, mode, id.String(), event.At)
	} else {
		_, err = tx.Exec(ctx, `UPDATE writer_controls SET owner_id=$2,generation=$3,version=$4,mode=$5,event_id=$6,updated_at=$7 WHERE environment_id=$1`, eid, owner.String(), out.Generation, version, mode, id.String(), event.At)
	}
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

func invalidateRecoveryLeases(ctx context.Context, tx pgx.Tx, environment string) error {
	for _, table := range []string{"jobs", "native_jobs", "standing_inbox", "native_occurrences", "native_income_intakes"} {
		advance := "claim_generation=claim_generation+1,"
		if table == "native_income_intakes" {
			// 018 advances an intake generation only when acquiring RUNNING.
			// Ending its old lease must retain that sequence. The cleared owner
			// invalidates its exact claim, and the writer pin fences old commits.
			advance = ""
		}
		if _, err := tx.Exec(ctx, `UPDATE `+table+` SET `+advance+`lease_owner=NULL,lease_until=NULL,state=CASE WHEN state='RUNNING' THEN 'READY' ELSE state END WHERE environment_id=$1 AND state IN('READY','RUNNING')`, environment); err != nil {
			return err
		}
	}
	return nil
}

// recoveryClaim is exact-operation admission, not a general Worker. It neither
// manufactures a permit nor reauthorizes a historical bank request.
func (s *Service) recoveryClaim(ctx context.Context, p Principal, id funds.ID, family string) (context.Context, Claim, error) {
	var out Claim
	if family != "LEGACY" && family != "NATIVE" {
		return ctx, out, reject("INVALID_RECOVERY_SCOPE", nil)
	}
	table, jobs := "operations", "jobs"
	if family == "NATIVE" {
		table, jobs = "native_operations", "native_jobs"
	}
	var signed bool
	var state string
	if err := s.Pool.QueryRow(ctx, `SELECT permit_signature IS NOT NULL,status FROM `+table+` WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&signed, &state); err != nil {
		return ctx, out, err
	}
	mode := "SIGNED"
	if !signed {
		mode = "CANCEL_UNSIGNED"
	}
	ctx = context.WithValue(ctx, recoveryIntentKey{}, recoveryIntent{id, p.UserID, family, mode})
	tx, err := s.begin(ctx)
	if err != nil {
		return ctx, out, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return ctx, out, err
	}
	if sameUser(p, env.ID) != nil {
		return ctx, out, reject("FORBIDDEN", nil)
	}
	if state == "SETTLED" || state == "REJECTED" || state == "CANCELLED" {
		return ctx, out, reject("RECOVERY_OPERATION_TERMINAL", nil)
	}
	var job string
	err = tx.QueryRow(ctx, `UPDATE `+jobs+` SET claim_generation=claim_generation+1,lease_owner='recovery-owned',lease_until=clock_timestamp()+interval '1 minute',paused=false,state='RUNNING' WHERE environment_id=$1 AND user_id=$2 AND operation_id=$3 RETURNING id::text,claim_generation`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&job, &out.Generation)
	if err != nil {
		return ctx, out, err
	}
	out.JobID, err = funds.ParseID(job)
	if err != nil {
		return ctx, out, err
	}
	out.Principal = p
	out.Principal.Kind = "SYSTEM"
	out.Principal.SessionID = funds.ID{}
	out.OperationID = id
	out.Owner = "recovery-owned"
	return ctx, out, tx.Commit(ctx)
}

func (s *Service) RecoverOriginalOperation(ctx context.Context, p Principal, id funds.ID, family string) error {
	ctx, c, err := s.recoveryClaim(ctx, p, id, family)
	if err != nil {
		return err
	}
	if family == "NATIVE" {
		return s.ProcessNativeClaim(ctx, c)
	}
	return s.ProcessClaim(ctx, c)
}
