package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"time"

	"github.com/jackc/pgx/v5/pgconn"

	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type recoveryOperatorRequest struct {
	Protocol       string                     `json:"protocol"`
	Namespace      string                     `json:"namespace"`
	Action         string                     `json:"action"`
	OwnerID        funds.ID                   `json:"owner_id,omitempty"`
	Expected       *app.RecoveryWriterControl `json:"expected,omitempty"`
	RunID          funds.ID                   `json:"run_id,omitempty"`
	UserID         funds.ID                   `json:"user_id,omitempty"`
	OperationID    funds.ID                   `json:"operation_id,omitempty"`
	Family         string                     `json:"family,omitempty"`
	QuiescenceFile string                     `json:"quiescence_file,omitempty"`
}

func boundedRecoveryFile(path string, limit int64) ([]byte, error) {
	invalid := errors.New("INVALID_RECOVERY_OPERATOR_FILE")
	info, e := os.Lstat(path)
	if e != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > limit {
		return nil, invalid
	}
	raw, e := os.ReadFile(path)
	if e != nil || int64(len(raw)) > limit {
		return nil, invalid
	}
	if _, e = audit.ParseRawObject(raw); e != nil {
		return nil, invalid
	}
	return raw, nil
}

func readRecoveryOperatorRequest(path, namespace string) (recoveryOperatorRequest, error) {
	var out recoveryOperatorRequest
	raw, e := boundedRecoveryFile(path, 65536)
	if e != nil {
		return out, e
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&out) != nil || d.Decode(new(any)) != io.EOF || out.Protocol != "zhiyu-recovery-operator-v1" || out.Namespace != namespace {
		return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
	}
	fields := map[string][]string{
		"READ": {}, "ARM": {"owner_id"}, "READ_ONLY": {"expected"}, "HANDOFF": {"expected", "owner_id"}, "SCAN": {}, "ACTIVATE": {"run_id", "quiescence_file"}, "RECOVER": {"user_id", "operation_id", "family"},
	}
	allowed, ok := fields[out.Action]
	if !ok {
		return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
	}
	keys := map[string]json.RawMessage{}
	_ = json.Unmarshal(raw, &keys)
	for _, base := range []string{"protocol", "namespace", "action"} {
		delete(keys, base)
	}
	for _, field := range allowed {
		if _, ok := keys[field]; !ok {
			return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
		}
		delete(keys, field)
	}
	if len(keys) != 0 {
		return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
	}
	if (out.Action == "ARM" || out.Action == "HANDOFF") && out.OwnerID == (funds.ID{}) || (out.Action == "READ_ONLY" || out.Action == "HANDOFF") && out.Expected == nil || out.Action == "ACTIVATE" && (out.RunID == (funds.ID{}) || out.QuiescenceFile == "") || out.Action == "RECOVER" && (out.UserID == (funds.ID{}) || out.OperationID == (funds.ID{}) || (out.Family != "LEGACY" && out.Family != "NATIVE")) {
		return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
	}
	if c := out.Expected; c != nil && (c.EnvironmentID == (funds.ID{}) || c.OwnerID == (funds.ID{}) || c.Generation == 0 || c.Generation > math.MaxInt64 || c.Version == 0 || c.Version > math.MaxInt64 || (c.Mode != "ACTIVE" && c.Mode != "READ_ONLY" && c.Mode != "RECOVERING" && c.Mode != "FENCED")) {
		return out, errors.New("INVALID_RECOVERY_OPERATOR_REQUEST")
	}
	return out, nil
}

// This is a local operator attestation, produced by the owned process runner.
// It is never browser/model input, and is not a substitute for the current
// namespace scan, writer CAS, or original-operation checks in the application.
func readRecoveryQuiescence(path, namespace string) (app.RecoveryQuiescence, error) {
	var out app.RecoveryQuiescence
	raw, e := boundedRecoveryFile(path, 1<<20)
	if e != nil {
		return out, e
	}
	var proof struct {
		Protocol    string    `json:"protocol"`
		Namespace   string    `json:"namespace"`
		AllStopped  bool      `json:"all_registered_processes_stopped"`
		BankStopped bool      `json:"bank_process_stopped"`
		Drained     bool      `json:"external_requests_drained"`
		CheckedAt   time.Time `json:"checked_at"`
		Processes   []struct {
			Role   string `json:"role"`
			PID    int    `json:"pid"`
			Exited bool   `json:"verified_exited"`
			SHA256 string `json:"sha256"`
		} `json:"processes"`
	}
	if json.Unmarshal(raw, &proof) != nil || proof.Protocol != "zhiyu-owned-process-quiescence-v1" || proof.Namespace != namespace || !proof.AllStopped || !proof.BankStopped || !proof.Drained || proof.CheckedAt.IsZero() || proof.CheckedAt.After(time.Now().Add(time.Minute)) || time.Since(proof.CheckedAt) > 10*time.Minute || len(proof.Processes) < 3 || len(proof.Processes) > 6 {
		return out, errors.New("RECOVERY_QUIESCENCE_REQUIRED")
	}
	roles := map[string]bool{}
	pids := map[int]bool{}
	for _, p := range proof.Processes {
		digest, decodeErr := hex.DecodeString(p.SHA256)
		knownRole := p.Role == "bank" || p.Role == "api" || p.Role == "worker" || p.Role == "projection" || p.Role == "audit" || p.Role == "native_standing"
		if p.PID < 1 || pids[p.PID] || roles[p.Role] || !knownRole || !p.Exited || decodeErr != nil || len(digest) != 32 {
			return out, errors.New("RECOVERY_QUIESCENCE_REQUIRED")
		}
		roles[p.Role] = true
		pids[p.PID] = true
	}
	if !roles["bank"] || !roles["api"] || !roles["worker"] {
		return out, errors.New("RECOVERY_QUIESCENCE_REQUIRED")
	}
	return app.RecoveryQuiescence{OldProcessesStopped: true, ExternalRequestsDrained: true, EvidenceHash: app.Hash(raw)}, nil
}

func recoveryOperator(ctx context.Context, s *app.Service, path string, out io.Writer) error {
	in, e := readRecoveryOperatorRequest(path, s.Namespace)
	if e != nil {
		return e
	}
	var result any
	switch in.Action {
	case "READ":
		result, e = s.WriterControl(ctx)
	case "ARM":
		result, e = s.ArmReadOnlyRecoveryWriter(ctx, in.OwnerID)
	case "READ_ONLY":
		result, e = s.ReadOnlyRecoveryWriter(ctx, *in.Expected)
	case "HANDOFF":
		result, e = s.HandoffRecoveryWriter(ctx, *in.Expected, in.OwnerID)
	case "SCAN":
		var run app.RecoveryRun
		run, e = s.ScanRecoveryNamespace(ctx)
		result = map[string]any{"protocol": run.Protocol, "run_id": run.ID, "state": run.State, "missing_users": run.MissingUsers, "orphans": run.Orphans, "mismatches": run.Mismatches, "unresolved": run.Unresolved, "state_mismatches": run.StateMismatches, "bank_authority": false}
	case "ACTIVATE":
		proofPath := in.QuiescenceFile
		if !filepath.IsAbs(proofPath) {
			proofPath = filepath.Join(filepath.Dir(path), proofPath)
		}
		var q app.RecoveryQuiescence
		q, e = readRecoveryQuiescence(proofPath, s.Namespace)
		if e == nil {
			result, e = s.ActivateRecoveryWriter(ctx, in.RunID, q)
		}
	case "RECOVER":
		var control app.RecoveryWriterControl
		control, e = s.WriterControl(ctx)
		if e == nil {
			e = s.RecoverOriginalOperation(ctx, app.Principal{EnvironmentID: control.EnvironmentID, UserID: in.UserID, Kind: "SYSTEM"}, in.OperationID, in.Family)
		}
		result = map[string]any{"status": "ORIGINAL_OPERATION_RECOVERY_PROCESSED", "operation_id": in.OperationID, "family": in.Family, "new_authority_created": false}
	}
	if e != nil {
		return recoveryOperatorError(e)
	}
	return json.NewEncoder(out).Encode(result)
}

func recoveryOperatorError(cause error) error {
	code := "RECOVERY_OPERATOR_FAILED_" + app.Code(cause)
	var pg *pgconn.PgError
	if app.Code(cause) == "INTERNAL_ERROR" && errors.As(cause, &pg) && len(pg.Code) == 5 {
		// SQLSTATE is sufficient to distinguish permission/constraint failures;
		// SQL text, arguments, server detail and private configuration stay local.
		code += "_SQLSTATE_" + pg.Code
	}
	return errors.New(code)
}
