package app

import (
	"context"
	"errors"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankrecovery"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type recoveryLegacyInventory interface {
	Inventory(context.Context, string, string, int64, int64, int) (execution.BankInventory, error)
}
type recoveryNativeInventory interface {
	Subjects(context.Context, string) (bankrecovery.SubjectSnapshot, error)
	Inventory(context.Context, string, string, int64, int64, int) (bankv2.Inventory, error)
	CurrentState(context.Context, string, string) (bankv2.CurrentState, error)
}

// RecoveryQuiescence is operator evidence, never browser/model input. Database
// fencing cannot retract a previously committed request already on the network.
// Activation therefore also requires the old processes stopped and their
// external requests drained, with an independently retained evidence digest.
type RecoveryQuiescence struct {
	OldProcessesStopped     bool   `json:"old_processes_stopped"`
	ExternalRequestsDrained bool   `json:"external_requests_drained"`
	EvidenceHash            string `json:"evidence_hash"`
}
type RecoveryUserBasis struct {
	ID            funds.ID `json:"id"`
	FundsVersion  uint64   `json:"funds_version"`
	SourceVersion uint64   `json:"source_version"`
}
type RecoveryBankScan struct {
	UserID    funds.ID                  `json:"user_id"`
	Family    string                    `json:"family"`
	Watermark int64                     `json:"watermark"`
	NextAfter int64                     `json:"next_after"`
	Complete  bool                      `json:"complete"`
	Results   []jsonRecoveryObservation `json:"results"`
}
type jsonRecoveryObservation struct {
	Key            string    `json:"key"`
	Version        int64     `json:"version"`
	Classification string    `json:"classification"`
	OperationID    *funds.ID `json:"operation_id,omitempty"`
	Original       []byte    `json:"original"`
	OriginalHash   string    `json:"original_hash"`
}
type RecoveryRun struct {
	Protocol        string                       `json:"protocol"`
	ID              funds.ID                     `json:"id"`
	EnvironmentID   funds.ID                     `json:"environment_id"`
	Namespace       string                       `json:"namespace"`
	Writer          RecoveryWriterControl        `json:"writer"`
	Subjects        bankrecovery.SubjectSnapshot `json:"subjects"`
	Users           []RecoveryUserBasis          `json:"users"`
	Scans           []RecoveryBankScan           `json:"scans"`
	CurrentStates   []bankv2.CurrentState        `json:"current_states"`
	MissingUsers    int                          `json:"missing_users"`
	Orphans         int                          `json:"orphans"`
	Mismatches      int                          `json:"mismatches"`
	Unresolved      int                          `json:"unresolved"`
	StateMismatches int                          `json:"state_mismatches"`
	State           string                       `json:"state"`
	CapturedAt      time.Time                    `json:"captured_at"`
	BankAuthority   bool                         `json:"bank_authority"`
}

func (s *Service) recoveryReadControl(ctx context.Context) (Environment, RecoveryWriterControl, error) {
	e, err := s.environment(ctx, s.Pool, false)
	if err != nil {
		return e, RecoveryWriterControl{}, err
	}
	c, ok, err := recoveryControl(ctx, s.Pool, e.ID)
	if err != nil {
		return e, c, err
	}
	if !ok || s.WriterPin == nil || s.WriterPin.OwnerID != c.OwnerID || s.WriterPin.Generation != c.Generation || e.WriterGeneration != c.Generation {
		return e, c, reject("WRITER_FENCED", nil)
	}
	if c.Mode != "RECOVERING" {
		return e, c, reject("RECOVERY_MODE_REQUIRED", nil)
	}
	return e, c, nil
}

// ScanRecoveryNamespace stores a bounded whole-namespace inventory. Subjects
// absent from the backup are retained without a users FK and prevent READY.
// No bank request, permission, income or missing USER original is manufactured.
func (s *Service) ScanRecoveryNamespace(ctx context.Context) (RecoveryRun, error) {
	var out RecoveryRun
	legacy, lok := s.Bank.(recoveryLegacyInventory)
	native, nok := s.NativeBank.(recoveryNativeInventory)
	if !lok || !nok {
		return out, reject("RECOVERY_BANK_CAPABILITY_REQUIRED", nil)
	}
	env, control, err := s.recoveryReadControl(ctx)
	if err != nil {
		return out, err
	}
	id, err := newID()
	if err != nil {
		return out, err
	}
	out = RecoveryRun{Protocol: RecoveryProtocol + "/run", ID: id, EnvironmentID: env.ID, Namespace: s.Namespace, Writer: control, Users: []RecoveryUserBasis{}, Scans: []RecoveryBankScan{}, CurrentStates: []bankv2.CurrentState{}, State: "INCOMPLETE", CapturedAt: s.now()}
	rows, err := s.Pool.Query(ctx, `SELECT id::text,funds_version,source_version FROM users WHERE environment_id=$1 ORDER BY id LIMIT 1001`, env.ID.String())
	if err != nil {
		return out, err
	}
	for rows.Next() {
		var u RecoveryUserBasis
		var text string
		if err = rows.Scan(&text, &u.FundsVersion, &u.SourceVersion); err != nil {
			break
		}
		u.ID, err = funds.ParseID(text)
		if err != nil {
			break
		}
		out.Users = append(out.Users, u)
	}
	rows.Close()
	if err != nil {
		return out, err
	}
	if rows.Err() != nil {
		return out, rows.Err()
	}
	if len(out.Users) > 1000 {
		return out, reject("RECOVERY_CAPACITY", nil)
	}
	out.Subjects, err = native.Subjects(ctx, s.Namespace)
	if err != nil {
		return out, reject("RECOVERY_BANK_UNAVAILABLE", err)
	}
	if out.Subjects.Validate() != nil || out.Subjects.Namespace != s.Namespace {
		return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
	}
	known := map[string]bool{}
	users := map[string]bool{}
	for _, u := range out.Users {
		known[u.ID.String()] = true
		users[u.ID.String()] = true
	}
	for _, u := range out.Subjects.UserIDs {
		users[u] = true
		if !known[u] {
			out.MissingUsers++
		}
	}
	ordered := []string{}
	for u := range users {
		ordered = append(ordered, u)
	}
	sort.Strings(ordered)
	if len(ordered) > 1000 {
		return out, reject("RECOVERY_CAPACITY", nil)
	}
	total := 0
	for _, user := range ordered {
		uid, parseErr := funds.ParseID(user)
		if parseErr != nil {
			return out, reject("RECOVERY_UNSUPPORTED_SUBJECT", parseErr)
		}
		for _, family := range []string{"LEGACY", "NATIVE"} {
			scan := RecoveryBankScan{UserID: uid, Family: family, Results: []jsonRecoveryObservation{}}
			for page := 0; page < 100; page++ {
				var more bool
				var next, watermark int64
				if family == "LEGACY" {
					v, e := legacy.Inventory(ctx, s.Namespace, user, scan.NextAfter, scan.Watermark, 100)
					if e != nil {
						return out, reject("RECOVERY_BANK_UNAVAILABLE", e)
					}
					if v.Namespace != s.Namespace || v.UserID != user {
						return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
					}
					next, watermark, more = v.NextAfter, v.Watermark, v.HasMore
					for _, r := range v.Operations {
						if execution.VerifyBankResult(r.Request, r) != nil {
							return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
						}
						raw, _ := encode(r)
						o, e := s.classifyRecovery(ctx, env.ID, uid, family, r.Key, r.Version, r.RequestHash, raw)
						if e != nil {
							return out, e
						}
						scan.Results = append(scan.Results, o)
					}
				} else {
					v, e := native.Inventory(ctx, s.Namespace, user, scan.NextAfter, scan.Watermark, 100)
					if e != nil {
						return out, reject("RECOVERY_BANK_UNAVAILABLE", e)
					}
					if v.Protocol != bankv2.Protocol || v.Namespace != s.Namespace || v.UserID != user {
						return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
					}
					next, watermark, more = v.NextAfter, v.Watermark, v.HasMore
					for _, r := range v.Operations {
						if r.Request == nil || bankv2.VerifyResult(*r.Request, r) != nil {
							return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
						}
						raw, _ := encode(r)
						o, e := s.classifyRecovery(ctx, env.ID, uid, family, r.Key, r.Version, r.RequestHash, raw)
						if e != nil {
							return out, e
						}
						scan.Results = append(scan.Results, o)
					}
				}
				if watermark < scan.Watermark || next < scan.NextAfter || (more && next == scan.NextAfter) {
					return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
				}
				scan.NextAfter, scan.Watermark = next, watermark
				if !more {
					scan.Complete = true
					break
				}
			}
			if !scan.Complete {
				return out, reject("RECOVERY_CAPACITY", nil)
			}
			total += len(scan.Results)
			if total > 10000 {
				return out, reject("RECOVERY_CAPACITY", nil)
			}
			for _, o := range scan.Results {
				if o.Classification == "ORPHAN" {
					out.Orphans++
				}
				if o.Classification == "MISMATCH" {
					out.Mismatches++
				}
			}
			out.Scans = append(out.Scans, scan)
			missing, e := s.recoveryMissingOriginalKeys(ctx, env.ID, uid, scan)
			if e != nil {
				return out, e
			}
			out.Mismatches += missing
		}
		state, e := native.CurrentState(ctx, s.Namespace, user)
		if e != nil {
			return out, reject("RECOVERY_BANK_UNAVAILABLE", e)
		}
		if state.Namespace != s.Namespace || state.UserID != user {
			return out, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
		}
		out.CurrentStates = append(out.CurrentStates, state)
		if known[user] {
			bad, e := s.recoveryCashMismatch(ctx, env.ID, uid, state)
			if e != nil {
				return out, e
			}
			out.StateMismatches += bad
		}
	}
	// A second subject/head observation detects new bank identities appearing
	// during this bounded scan. It is not called an atomic historical snapshot.
	last, err := native.Subjects(ctx, s.Namespace)
	if err != nil {
		return out, reject("RECOVERY_BANK_UNAVAILABLE", err)
	}
	a, _ := encode(last.UserIDs)
	b, _ := encode(out.Subjects.UserIDs)
	if last.Validate() != nil || last.Namespace != s.Namespace || string(a) != string(b) {
		return out, reject("RECOVERY_BANK_CHANGED", nil)
	}
	for _, scan := range out.Scans {
		var watermark int64
		if scan.Family == "LEGACY" {
			v, e := legacy.Inventory(ctx, s.Namespace, scan.UserID.String(), 0, 0, 1)
			if e != nil {
				return out, e
			}
			watermark = v.Watermark
		} else {
			v, e := native.Inventory(ctx, s.Namespace, scan.UserID.String(), 0, 0, 1)
			if e != nil {
				return out, e
			}
			watermark = v.Watermark
		}
		if watermark != scan.Watermark {
			return out, reject("RECOVERY_BANK_CHANGED", nil)
		}
	}
	err = s.Pool.QueryRow(ctx, `SELECT (SELECT count(*) FROM operations WHERE environment_id=$1 AND status NOT IN('SETTLED','REJECTED','CANCELLED'))+(SELECT count(*) FROM native_operations WHERE environment_id=$1 AND status NOT IN('SETTLED','REJECTED','CANCELLED'))`, env.ID.String()).Scan(&out.Unresolved)
	if err != nil {
		return out, err
	}
	if out.MissingUsers+out.Orphans+out.Mismatches+out.Unresolved+out.StateMismatches == 0 {
		out.State = "READY"
	}
	if err = s.persistRecoveryRun(ctx, out); err != nil {
		return out, err
	}
	return out, nil
}

func (s *Service) classifyRecovery(ctx context.Context, env, user funds.ID, family, key string, version int64, requestHash string, raw []byte) (jsonRecoveryObservation, error) {
	o := jsonRecoveryObservation{Key: key, Version: version, Original: raw, OriginalHash: Hash(raw), Classification: "ORPHAN"}
	table := "operations"
	if family == "NATIVE" {
		table = "native_operations"
	}
	var id, hash, status string
	var receipt *string
	var signed bool
	err := s.Pool.QueryRow(ctx, `SELECT id::text,bank_request_hash::text,status,receipt_id::text,permit_signature IS NOT NULL FROM `+table+` WHERE environment_id=$1 AND user_id=$2 AND bank_key=$3`, env.String(), user.String(), key).Scan(&id, &hash, &status, &receipt, &signed)
	if errors.Is(err, pgx.ErrNoRows) {
		return o, nil
	}
	if err != nil {
		return o, err
	}
	op, err := funds.ParseID(id)
	if err != nil {
		return o, err
	}
	o.OperationID = &op
	o.Classification = "KNOWN_MATCH"
	if !signed || hash != requestHash {
		o.Classification = "MISMATCH"
	}
	if family == "LEGACY" {
		var r execution.BankResult
		if decode(raw, &r) != nil {
			return o, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
		}
		if (status == "SETTLED" && (r.Status != execution.BankSettled || receipt == nil)) || (status == "REJECTED" && r.Status != execution.BankRejected) || (status == "CANCELLED") {
			o.Classification = "MISMATCH"
		}
	} else {
		var r bankv2.Result
		if decode(raw, &r) != nil {
			return o, reject("RECOVERY_BANK_EVIDENCE_INVALID", nil)
		}
		if (status == "SETTLED" && (r.Status != bankv2.Settled || receipt == nil)) || (status == "REJECTED" && r.Status != bankv2.Rejected) || (status == "CANCELLED") {
			o.Classification = "MISMATCH"
		}
	}
	return o, nil
}

func (s *Service) recoveryMissingOriginalKeys(ctx context.Context, env, user funds.ID, scan RecoveryBankScan) (int, error) {
	table := "operations"
	if scan.Family == "NATIVE" {
		table = "native_operations"
	}
	rows, err := s.Pool.Query(ctx, `SELECT bank_key FROM `+table+` WHERE environment_id=$1 AND user_id=$2 AND permit_signature IS NOT NULL AND status<>'CANCELLED' LIMIT 10001`, env.String(), user.String())
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	seen := map[string]bool{}
	for _, o := range scan.Results {
		seen[o.Key] = true
	}
	missing, count := 0, 0
	for rows.Next() {
		var key string
		if err = rows.Scan(&key); err != nil {
			return 0, err
		}
		count++
		if !seen[key] {
			missing++
		}
	}
	if count > 10000 {
		return 0, reject("RECOVERY_CAPACITY", nil)
	}
	return missing, rows.Err()
}

func (s *Service) recoveryCashMismatch(ctx context.Context, env, user funds.ID, state bankv2.CurrentState) (int, error) {
	rows, err := s.Pool.Query(ctx, `SELECT id::text,balance_cents::text,goal_owned_cents::text FROM accounts WHERE environment_id=$1 AND user_id=$2 ORDER BY id`, env.String(), user.String())
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	balances := map[string]string{}
	owned := map[string]string{}
	for rows.Next() {
		var id, b, o string
		if err = rows.Scan(&id, &b, &o); err != nil {
			return 0, err
		}
		balances[id] = b
		owned[id] = o
	}
	if rows.Err() != nil {
		return 0, rows.Err()
	}
	if len(balances) != len(state.Accounts) {
		return 1, nil
	}
	for _, a := range state.Accounts {
		if balances[a.ID] != a.Balance.String() || !a.Reserved.IsZero() {
			return 1, nil
		}
	}
	// Owned cash is the sum of bank goal cash for that account, independently
	// of local read projections. Principal is not invented as cash.
	goalCash := map[string]funds.Money{}
	for _, g := range state.Goals {
		v, e := goalCash[g.AccountID].Add(g.Cash)
		if e != nil {
			return 0, e
		}
		goalCash[g.AccountID] = v
	}
	for account, want := range owned {
		if goalCash[account].String() != want {
			return 1, nil
		}
	}
	return s.recoveryPositionAndGoalMismatch(ctx, env, user, state)
}

func (s *Service) recoveryPositionAndGoalMismatch(ctx context.Context, env, user funds.ID, state bankv2.CurrentState) (int, error) {
	rows, err := s.Pool.Query(ctx, `SELECT p.id,p.original_hash::text,s.closed FROM native_positions p LEFT JOIN native_position_states s ON(s.environment_id=p.environment_id AND s.user_id=p.user_id AND s.position_id=p.id) WHERE p.environment_id=$1 AND p.user_id=$2`, env.String(), user.String())
	if err != nil {
		return 0, err
	}
	type position struct {
		hash   string
		closed *bool
	}
	positions := map[string]position{}
	for rows.Next() {
		var id string
		var p position
		if err = rows.Scan(&id, &p.hash, &p.closed); err != nil {
			break
		}
		positions[id] = p
	}
	rows.Close()
	if err != nil {
		return 0, err
	}
	if rows.Err() != nil {
		return 0, rows.Err()
	}
	if len(positions) != len(state.Positions) {
		return 1, nil
	}
	for _, bank := range state.Positions {
		p, ok := positions[bank.Opening.PositionID]
		digest, e := bank.Opening.Digest()
		if e != nil || !ok || p.closed == nil || p.hash != bank.OpeningHash || digest != bank.OpeningHash || *p.closed != bank.Closed {
			return 1, nil
		}
	}
	rows, err = s.Pool.Query(ctx, `SELECT g.id::text,coalesce(n.cash_cents,g.assigned_cents)::text,coalesce(n.principal_cents,0)::text,coalesce(n.loss_cents,0)::text FROM goals g LEFT JOIN native_goal_balances n ON(n.environment_id=g.environment_id AND n.user_id=g.user_id AND n.goal_id=g.id) WHERE g.environment_id=$1 AND g.user_id=$2`, env.String(), user.String())
	if err != nil {
		return 0, err
	}
	goals := map[string][3]string{}
	for rows.Next() {
		var id string
		var amounts [3]string
		if err = rows.Scan(&id, &amounts[0], &amounts[1], &amounts[2]); err != nil {
			break
		}
		goals[id] = amounts
	}
	rows.Close()
	if err != nil {
		return 0, err
	}
	if rows.Err() != nil {
		return 0, rows.Err()
	}
	for _, g := range state.Goals {
		want, ok := goals[g.ID]
		if !ok || want != ([3]string{g.Cash.String(), g.Principal.String(), g.Loss.String()}) {
			return 1, nil
		}
		delete(goals, g.ID)
	}
	for _, amounts := range goals {
		if amounts != ([3]string{"0", "0", "0"}) {
			return 1, nil
		}
	}
	return 0, nil
}

func (s *Service) persistRecoveryRun(ctx context.Context, run RecoveryRun) error {
	raw, err := encode(run)
	if err != nil || len(raw) > 4<<20 {
		return reject("RECOVERY_CAPACITY", err)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	var id string
	if err = tx.QueryRow(ctx, `SELECT id::text FROM environments WHERE id=$1 FOR UPDATE`, run.EnvironmentID.String()).Scan(&id); err != nil {
		return err
	}
	c, present, err := recoveryControl(ctx, tx, run.EnvironmentID)
	if err != nil {
		return err
	}
	if !present || c != run.Writer {
		return reject("WRITER_FENCED", nil)
	}
	for _, u := range run.Users {
		var fv, sv uint64
		if err = tx.QueryRow(ctx, `SELECT funds_version,source_version FROM users WHERE environment_id=$1 AND id=$2`, id, u.ID.String()).Scan(&fv, &sv); err != nil {
			return err
		}
		if fv != u.FundsVersion || sv != u.SourceVersion {
			return reject("SOURCE_CHANGED", nil)
		}
	}
	_, err = tx.Exec(ctx, `INSERT INTO recovery_runs(environment_id,id,owner_id,generation,original,original_hash,created_at,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, id, run.ID.String(), c.OwnerID.String(), c.Generation, raw, Hash(raw), run.CapturedAt, run.State)
	if err != nil {
		return err
	}
	for _, scan := range run.Scans {
		raw, _ := encode(scan)
		_, err = tx.Exec(ctx, `INSERT INTO recovery_bank_scans(environment_id,run_id,user_id,protocol,watermark,next_after,complete,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, id, run.ID.String(), scan.UserID.String(), scan.Family, scan.Watermark, scan.NextAfter, scan.Complete, raw, Hash(raw), run.CapturedAt)
		if err != nil {
			return err
		}
		for _, o := range scan.Results {
			var op any
			if o.OperationID != nil {
				op = o.OperationID.String()
			}
			_, err = tx.Exec(ctx, `INSERT INTO recovery_bank_observations(environment_id,run_id,user_id,protocol,bank_key,version,classification,operation_id,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, id, run.ID.String(), scan.UserID.String(), scan.Family, o.Key, o.Version, o.Classification, op, o.Original, o.OriginalHash, run.CapturedAt)
			if err != nil {
				return err
			}
		}
	}
	return tx.Commit(ctx)
}

// ActivateRecoveryWriter consumes a fresh complete scan plus explicit external
// quiescence evidence. A stale/partial/foreign-owner run never opens new T1/T2.
func (s *Service) ActivateRecoveryWriter(ctx context.Context, runID funds.ID, q RecoveryQuiescence) (RecoveryWriterControl, error) {
	var out RecoveryWriterControl
	if !q.OldProcessesStopped || !q.ExternalRequestsDrained || len(q.EvidenceHash) != 64 {
		return out, reject("RECOVERY_QUIESCENCE_REQUIRED", nil)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	var eid string
	if err = tx.QueryRow(ctx, `SELECT id::text FROM environments WHERE namespace=$1 FOR UPDATE`, s.Namespace).Scan(&eid); err != nil {
		return out, err
	}
	env, _ := funds.ParseID(eid)
	c, present, err := recoveryControl(ctx, tx, env)
	if err != nil {
		return out, err
	}
	if !present || s.WriterPin == nil || s.WriterPin.OwnerID != c.OwnerID || s.WriterPin.Generation != c.Generation || c.Mode != "RECOVERING" {
		return out, reject("WRITER_FENCED", nil)
	}
	var raw []byte
	var hash, state string
	if err = tx.QueryRow(ctx, `SELECT original,original_hash,state FROM recovery_runs WHERE environment_id=$1 AND id=$2 FOR UPDATE`, eid, runID.String()).Scan(&raw, &hash, &state); err != nil {
		return out, err
	}
	var run RecoveryRun
	if Hash(raw) != hash || auditJSON(raw, &run) != nil || run.ID != runID || run.Writer != c || run.State != "READY" || state != "READY" || s.now().Before(run.CapturedAt) || s.now().Sub(run.CapturedAt) > 30*time.Second {
		return out, reject("RECOVERY_INCOMPLETE", nil)
	}
	for _, u := range run.Users {
		var fv, sv uint64
		if err = tx.QueryRow(ctx, `SELECT funds_version,source_version FROM users WHERE environment_id=$1 AND id=$2`, eid, u.ID.String()).Scan(&fv, &sv); err != nil {
			return out, err
		}
		if fv != u.FundsVersion || sv != u.SourceVersion {
			return out, reject("SOURCE_CHANGED", nil)
		}
	}
	out = c
	out.Mode = "ACTIVE"
	out.Version++
	id, err := newID()
	if err != nil {
		return out, err
	}
	event := struct {
		RecoveryControlEvent
		RunID      funds.ID           `json:"run_id"`
		Quiescence RecoveryQuiescence `json:"quiescence"`
	}{RecoveryControlEvent{RecoveryProtocol + "/control", id, env, &c, out, "RECOVERY_RECONCILED", s.now()}, runID, q}
	original, _ := encode(event)
	if _, err = tx.Exec(ctx, `INSERT INTO writer_control_events(environment_id,id,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5)`, eid, id.String(), original, Hash(original), event.At); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE writer_controls SET version=$2,mode='ACTIVE',event_id=$3,updated_at=$4 WHERE environment_id=$1`, eid, out.Version, id.String(), event.At); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE recovery_runs SET state='ACTIVATED',version=version+1 WHERE environment_id=$1 AND id=$2`, eid, runID.String()); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
