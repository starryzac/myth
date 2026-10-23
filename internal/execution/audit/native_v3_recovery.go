package executionaudit

import (
	"bytes"
	"fmt"
	"math/big"
	"sort"
	"time"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankrecovery"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type nativeRecoveryWriterV3 struct {
	EnvironmentID funds.ID `json:"environment_id"`
	OwnerID       funds.ID `json:"owner_id"`
	Generation    uint64   `json:"generation"`
	Version       uint64   `json:"version"`
	Mode          string   `json:"mode"`
}
type nativeRecoveryControlEventV3 struct {
	Protocol      string                  `json:"protocol"`
	ID            funds.ID                `json:"id"`
	EnvironmentID funds.ID                `json:"environment_id"`
	Before        *nativeRecoveryWriterV3 `json:"before,omitempty"`
	After         nativeRecoveryWriterV3  `json:"after"`
	Reason        string                  `json:"reason"`
	At            time.Time               `json:"at"`
}
type nativeRecoveryQuiescenceV3 struct {
	OldProcessesStopped     bool   `json:"old_processes_stopped"`
	ExternalRequestsDrained bool   `json:"external_requests_drained"`
	EvidenceHash            string `json:"evidence_hash"`
}
type nativeRecoveryEventV3 struct {
	nativeRecoveryControlEventV3
	RunID      *funds.ID                   `json:"run_id,omitempty"`
	Quiescence *nativeRecoveryQuiescenceV3 `json:"quiescence,omitempty"`
}
type nativeRecoveryUserV3 struct {
	ID            funds.ID `json:"id"`
	FundsVersion  uint64   `json:"funds_version"`
	SourceVersion uint64   `json:"source_version"`
}
type nativeRecoveryObservationV3 struct {
	Key            string    `json:"key"`
	Version        int64     `json:"version"`
	Classification string    `json:"classification"`
	OperationID    *funds.ID `json:"operation_id,omitempty"`
	Original       []byte    `json:"original"`
	OriginalHash   string    `json:"original_hash"`
}
type nativeRecoveryScanV3 struct {
	UserID    funds.ID                      `json:"user_id"`
	Family    string                        `json:"family"`
	Watermark int64                         `json:"watermark"`
	NextAfter int64                         `json:"next_after"`
	Complete  bool                          `json:"complete"`
	Results   []nativeRecoveryObservationV3 `json:"results"`
}
type nativeRecoveryRunV3 struct {
	Protocol        string                       `json:"protocol"`
	ID              funds.ID                     `json:"id"`
	EnvironmentID   funds.ID                     `json:"environment_id"`
	Namespace       string                       `json:"namespace"`
	Writer          nativeRecoveryWriterV3       `json:"writer"`
	Subjects        bankrecovery.SubjectSnapshot `json:"subjects"`
	Users           []nativeRecoveryUserV3       `json:"users"`
	Scans           []nativeRecoveryScanV3       `json:"scans"`
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

func (g *nativeGraphV3) checkRecoveryV3() error {
	if g.snapshot.Revision < 23 {
		return nil
	}
	controls := g.rows["writer_controls"]
	if len(controls) == 0 {
		for _, name := range []string{"writer_control_events", "recovery_runs", "recovery_bank_scans", "recovery_bank_observations"} {
			if len(g.rows[name]) != 0 {
				return businessInvalid("native recovery original exists without actual environment writer control")
			}
		}
		return nil
	}
	if len(controls) != 1 {
		return businessInvalid("native environment has multiple current writer controls")
	}
	events := []nativeRecoveryEventV3{}
	for _, row := range g.rows["writer_control_events"] {
		var event nativeRecoveryEventV3
		nativeOriginalV3(row, "original", &event)
		if nativeHashFieldV3(row, "original", "original_hash") != nil || event.Protocol != "zhiyu-recovery-cutover-v1/control" || event.ID.String() != row.text("id") || event.EnvironmentID.String() != g.manifest.Scope.EnvironmentID || event.After.EnvironmentID != event.EnvironmentID || event.After.OwnerID == (funds.ID{}) || event.After.Generation < 1 || event.After.Version < 1 || !event.At.Equal(row.instant("recorded_at")) || event.At.After(g.manifest.Source.CapturedAt) {
			return businessInvalid("native immutable writer event scope/header")
		}
		events = append(events, event)
	}
	sort.Slice(events, func(i, j int) bool { return events[i].After.Version < events[j].After.Version })
	for i, event := range events {
		if event.After.Version != uint64(i+1) || i == 0 && event.Before != nil || i > 0 && (event.Before == nil || *event.Before != events[i-1].After || event.At.Before(events[i-1].At)) {
			return businessInvalid("native writer exact predecessor/CAS sequence")
		}
		switch event.Reason {
		case "INITIAL_CONTROL", "INITIAL_READ_ONLY":
			expected := "RECOVERING"
			if event.Reason == "INITIAL_READ_ONLY" {
				expected = "READ_ONLY"
			}
			if i != 0 || event.After.Generation < 2 || event.After.Mode != expected || event.RunID != nil || event.Quiescence != nil {
				return businessInvalid("native initial writer control")
			}
		case "OWNER_HANDOFF":
			if i == 0 || event.After.Generation != event.Before.Generation+1 || event.After.Mode != "RECOVERING" || event.RunID != nil || event.Quiescence != nil {
				return businessInvalid("native writer owner handoff transition")
			}
		case "ENTER_READ_ONLY":
			if i == 0 || event.After.Generation != event.Before.Generation || event.After.OwnerID != event.Before.OwnerID || event.After.Mode != "READ_ONLY" || event.RunID != nil || event.Quiescence != nil {
				return businessInvalid("native writer read-only CAS transition")
			}
		case "RECOVERY_RECONCILED":
			if i == 0 || event.Before.Mode != "RECOVERING" || event.After.Generation != event.Before.Generation || event.After.OwnerID != event.Before.OwnerID || event.After.Mode != "ACTIVE" || event.RunID == nil || event.Quiescence == nil || !event.Quiescence.OldProcessesStopped || !event.Quiescence.ExternalRequestsDrained || !nativeRecoveryHashV3(event.Quiescence.EvidenceHash) {
				return businessInvalid("native recovery activation needs explicit operator quiescence original")
			}
		default:
			return fmt.Errorf("%w: writer control event reason", evidence.ErrUnsupportedVersion)
		}
	}
	if len(events) == 0 {
		return businessInvalid("native writer head lacks full immutable event chain")
	}
	head := g.row("writer_controls")
	last := events[len(events)-1]
	if head == nil || head.text("event_id") != last.ID.String() || head.text("owner_id") != last.After.OwnerID.String() || uint64(head.positive("generation")) != last.After.Generation || uint64(head.positive("version")) != last.After.Version || head.text("mode") != last.After.Mode || !head.instant("updated_at").Equal(last.At) || int64(last.After.Generation) != g.snapshot.Watermarks["writer_generation"] {
		return businessInvalid("native writer head and environment source generation")
	}
	runs := map[string]nativeRecoveryRunV3{}
	scans := map[*ledgerRow]bool{}
	observations := map[*ledgerRow]bool{}
	for _, row := range g.rows["recovery_runs"] {
		var run nativeRecoveryRunV3
		nativeOriginalV3(row, "original", &run)
		if nativeHashFieldV3(row, "original", "original_hash") != nil || run.Protocol != "zhiyu-recovery-cutover-v1/run" || run.ID.String() != row.text("id") || run.EnvironmentID.String() != g.manifest.Scope.EnvironmentID || run.Namespace != g.manifest.Scope.Namespace || run.Writer.EnvironmentID != run.EnvironmentID || run.Writer.Mode != "RECOVERING" || run.Writer.OwnerID.String() != row.text("owner_id") || run.Writer.Generation != uint64(row.positive("generation")) || !run.CapturedAt.Equal(row.instant("created_at")) || run.CapturedAt.After(g.manifest.Source.CapturedAt) || run.BankAuthority || run.Subjects.Validate() != nil || run.Subjects.Namespace != run.Namespace || len(run.Users) != 1 || len(run.CurrentStates) != 1 || len(run.Scans) != 2 || run.Users[0].ID.String() != g.manifest.Scope.UserID || run.Users[0].FundsVersion < 1 || run.Users[0].SourceVersion < 1 || run.Users[0].FundsVersion > uint64(g.snapshot.Watermarks["funds_version"]) || run.Users[0].SourceVersion > uint64(g.snapshot.Watermarks["source_version"]) {
			return businessInvalid("native recovery exact single-USER full run original")
		}
		for _, subject := range run.Subjects.UserIDs {
			if subject != g.manifest.Scope.UserID {
				return fmt.Errorf("%w: environment recovery run contains foreign subject", evidence.ErrIncomplete)
			}
		}
		originalControl := false
		for _, event := range events {
			if event.After == run.Writer && !event.At.After(run.CapturedAt) {
				originalControl = true
			}
		}
		if !originalControl {
			return businessInvalid("native recovery run original writer authority chain")
		}
		if run.MissingUsers < 0 || run.Orphans < 0 || run.Mismatches < 0 || run.Unresolved < 0 || run.StateMismatches < 0 || run.MissingUsers+run.Orphans+run.Mismatches+run.Unresolved+run.StateMismatches > 10000 {
			return businessInvalid("native recovery bounded nonnegative counters")
		}
		ready := run.MissingUsers+run.Orphans+run.Mismatches+run.Unresolved+run.StateMismatches == 0
		if ready != (run.State == "READY") || !ready && run.State != "INCOMPLETE" {
			return businessInvalid("native recovery complete READY counter rule")
		}
		state := row.text("state")
		version := row.positive("version")
		if state == "ACTIVATED" {
			if !ready || version != 2 {
				return businessInvalid("native activated recovery projection")
			}
		} else if state != run.State || version != 1 {
			return businessInvalid("native recovery original state projection")
		}
		seenFamilies := map[string]bool{}
		orphans, mismatches := 0, 0
		for _, scan := range run.Scans {
			if scan.UserID.String() != g.manifest.Scope.UserID || scan.Family != "LEGACY" && scan.Family != "NATIVE" || seenFamilies[scan.Family] || !scan.Complete || scan.Watermark < 0 || scan.NextAfter != scan.Watermark || len(scan.Results) > 1000 {
				return businessInvalid("native recovery complete single-USER bank scan")
			}
			seenFamilies[scan.Family] = true
			scanRow := g.row("recovery_bank_scans", run.ID.String(), scan.Family)
			if scanRow == nil || nativeHashFieldV3(scanRow, "original", "original_hash") != nil || !bytes.Equal(nativeRawV3(scanRow, "original"), mustJSONV3(scan)) || scanRow.integer("watermark") != scan.Watermark || scanRow.integer("next_after") != scan.NextAfter || !scanRow.boolean("complete") || !scanRow.instant("recorded_at").Equal(run.CapturedAt) {
				return businessInvalid("native recovery scan full original row closure")
			}
			scans[scanRow] = true
			lastSequence := int64(0)
			keys := map[string]bool{}
			for _, observation := range scan.Results {
				obsRow := g.row("recovery_bank_observations", run.ID.String(), scan.Family, observation.Key, fmt.Sprint(observation.Version))
				if obsRow == nil || keys[observation.Key] || observation.Version < 1 || nakedHash(observation.Original) != observation.OriginalHash || nativeHashFieldV3(obsRow, "original", "original_hash") != nil || !bytes.Equal(nativeRawV3(obsRow, "original"), observation.Original) || obsRow.text("original_hash") != observation.OriginalHash || obsRow.text("classification") != observation.Classification || obsRow.optional("operation_id") != nativeOptionalIDV3(observation.OperationID) || !obsRow.instant("recorded_at").Equal(run.CapturedAt) {
					return businessInvalid("native recovery exact bank observation original")
				}
				keys[observation.Key] = true
				observations[obsRow] = true
				sequence, err := g.recoveryObservationV3(scan.Family, observation, ready, run.CapturedAt)
				if err != nil {
					return err
				}
				if sequence <= lastSequence || sequence > scan.Watermark {
					return businessInvalid("native recovery bank sequence prefix")
				}
				lastSequence = sequence
				switch observation.Classification {
				case "ORPHAN":
					orphans++
				case "MISMATCH":
					mismatches++
				case "KNOWN_MATCH":
				default:
					return fmt.Errorf("%w: recovery bank classification", evidence.ErrUnsupportedVersion)
				}
			}
			if lastSequence != scan.Watermark {
				return businessInvalid("native recovery scan terminal watermark")
			}
		}
		if run.Orphans != orphans || run.Mismatches < mismatches {
			return businessInvalid("native recovery observed mismatch/orphan counter")
		}
		stock := run.CurrentStates[0]
		if stock.Protocol != bankv2.Protocol || stock.Namespace != run.Namespace || stock.UserID != g.manifest.Scope.UserID || stock.CapturedAt.IsZero() || len(stock.Accounts) > 16 || len(stock.Positions) > 1000 || len(stock.Goals) > 100 || len(stock.Products) > 100 {
			return businessInvalid("native recovery independent stock scope/capacity")
		}
		for _, product := range stock.Products {
			if product.Validate() != nil {
				return businessInvalid("native recovery independent product original")
			}
		}
		if ready {
			if err := g.recoveryReadyStockV3(run); err != nil {
				return err
			}
		}
		runs[run.ID.String()] = run
	}
	for _, row := range g.rows["recovery_bank_scans"] {
		if !scans[row] {
			return businessInvalid("native orphan or extra recovery scan")
		}
	}
	for _, row := range g.rows["recovery_bank_observations"] {
		if !observations[row] {
			return businessInvalid("native orphan or extra recovery bank original")
		}
	}
	activated := map[string]bool{}
	for _, event := range events {
		if event.Reason != "RECOVERY_RECONCILED" {
			continue
		}
		run, ok := runs[event.RunID.String()]
		if !ok || run.State != "READY" || run.Writer != *event.Before || event.At.Before(run.CapturedAt) || event.At.Sub(run.CapturedAt) > 30*time.Second || activated[run.ID.String()] || g.row("recovery_runs", run.ID.String()).text("state") != "ACTIVATED" {
			return businessInvalid("native activation consumes one exact fresh READY run")
		}
		activated[run.ID.String()] = true
	}
	for id := range runs {
		if (g.row("recovery_runs", id).text("state") == "ACTIVATED") != activated[id] {
			return businessInvalid("native recovery activated run lacks exact control event")
		}
	}
	return g.checkedRowsV3()
}

func (g *nativeGraphV3) recoveryObservationV3(family string, observation nativeRecoveryObservationV3, ready bool, at time.Time) (int64, error) {
	var key, requestHash, user, namespace, status string
	var version, sequence int64
	var requestedAt time.Time
	if family == "NATIVE" {
		var r bankv2.Result
		if nativeCompactOriginalV3(observation.Original, &r) != nil || r.Request == nil || bankv2.VerifyResult(*r.Request, r) != nil {
			return 0, businessInvalid("native recovery typed original bank result")
		}
		key, requestHash, user, namespace, status, version, sequence, requestedAt = r.Key, r.RequestHash, r.UserID, r.Namespace, string(r.Status), r.Version, r.Sequence, r.Request.RequestedAt
	} else {
		var r execution.BankResult
		if nativeCompactOriginalV3(observation.Original, &r) != nil || execution.VerifyBankResult(r.Request, r) != nil {
			return 0, businessInvalid("legacy recovery typed original bank result")
		}
		key, requestHash, user, namespace, status, version, sequence, requestedAt = r.Key, r.RequestHash, r.UserID, r.Namespace, string(r.Status), r.Version, r.Sequence, r.Request.RequestedAt
	}
	if key != observation.Key || version != observation.Version || user != g.manifest.Scope.UserID || namespace != g.manifest.Scope.Namespace || requestedAt.After(at) || status == "NOT_FOUND" {
		return 0, businessInvalid("native recovery exact bank key/version/scope/time")
	}
	if observation.Classification == "ORPHAN" {
		if observation.OperationID != nil || ready {
			return 0, businessInvalid("native orphan bank result cannot create USER/authority")
		}
		return sequence, nil
	}
	if observation.OperationID == nil {
		return 0, businessInvalid("native classified recovery observation lacks actual operation")
	}
	table := "native_operations"
	if family == "LEGACY" {
		table = "operations"
	}
	op := g.row(table, observation.OperationID.String())
	if op == nil {
		return 0, fmt.Errorf("%w: recovery local financial original missing", evidence.ErrIncomplete)
	}
	if observation.Classification == "KNOWN_MATCH" && (op.text("bank_key") != key || op.text("bank_request_hash") != requestHash || op.optional("permit_signature") == "") {
		return 0, businessInvalid("native recovery classification cannot replace original request/USER/T2")
	}
	if ready && (observation.Classification != "KNOWN_MATCH" || status != "SETTLED" && status != "REJECTED") {
		return 0, businessInvalid("native READY recovery retains unresolved external effect")
	}
	return sequence, nil
}

func (g *nativeGraphV3) recoveryReadyStockV3(run nativeRecoveryRunV3) error {
	// A READY run can be historical. Reconstruct its cash from the independently
	// read current bank stock by reversing only bank effects absent from that run,
	// and independently recorded external credits arriving after its capture.
	settled := map[string]bool{}
	legacySettled := map[string]bool{}
	for _, scan := range run.Scans {
		if scan.Family == "LEGACY" {
			for _, o := range scan.Results {
				var r execution.BankResult
				nativeCompactOriginalV3(o.Original, &r)
				if r.Status == execution.BankSettled {
					legacySettled[r.Key] = true
				}
			}
		}
		if scan.Family == "NATIVE" {
			for _, o := range scan.Results {
				var r bankv2.Result
				nativeCompactOriginalV3(o.Original, &r)
				if r.Status == bankv2.Settled {
					settled[r.Key] = true
				}
			}
		}
	}
	balances := map[string]*big.Int{}
	for _, a := range g.bank.Stock.Accounts {
		balances[a.ID] = big.NewInt(a.Balance.Cents())
	}
	add := func(account string, delta int64) error {
		v, ok := balances[account]
		if !ok {
			return businessInvalid("native recovery bank account omitted")
		}
		v.Add(v, big.NewInt(delta))
		return nil
	}
	for _, r := range g.bank.Native {
		if r.Status != bankv2.Settled || settled[r.Key] {
			continue
		}
		e := r.Request.Effect
		switch e.Kind {
		case bankv2.Allocate:
		case bankv2.Transfer, bankv2.MoveGoal:
			for _, cash := range e.CashUses {
				if err := add(cash.AccountID, cash.Amount.Cents()); err != nil {
					return err
				}
			}
			if err := add(e.DestinationAccountID, -e.Amount.Cents()); err != nil {
				return err
			}
		case bankv2.Purchase, bankv2.Payment:
			for _, cash := range e.CashUses {
				if err := add(cash.AccountID, cash.Amount.Cents()); err != nil {
					return err
				}
			}
		case bankv2.Redeem, bankv2.Mature:
			if err := add(e.DestinationAccountID, -e.Net.Cents()); err != nil {
				return err
			}
		default:
			return fmt.Errorf("%w: recovery bank economic family", evidence.ErrUnsupportedVersion)
		}
	}
	for _, entry := range g.bank.Credit.Credits {
		if entry.Record.CreditedAt.After(run.CapturedAt) {
			if err := add(entry.Record.Event.AccountID, -entry.Record.Event.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	goalValues := map[string][3]*big.Int{}
	for _, goal := range g.bank.Stock.Goals {
		goalValues[goal.ID] = [3]*big.Int{big.NewInt(goal.Cash.Cents()), big.NewInt(goal.Principal.Cents()), big.NewInt(goal.Loss.Cents())}
	}
	addGoal := func(id string, dimension int, amount int64) error {
		values, ok := goalValues[id]
		if !ok {
			return businessInvalid("native historical recovery goal missing from complete current bank")
		}
		values[dimension].Add(values[dimension], big.NewInt(amount))
		return nil
	}
	for _, r := range g.bank.Native {
		if r.Status != bankv2.Settled || settled[r.Key] {
			continue
		}
		e := r.Request.Effect
		switch e.Kind {
		case bankv2.Allocate:
			if err := addGoal(e.DestinationGoalID, 0, -e.Amount.Cents()); err != nil {
				return err
			}
		case bankv2.Purchase:
			if e.SourceGoalID != "" {
				if err := addGoal(e.SourceGoalID, 0, e.Amount.Cents()); err != nil {
					return err
				}
				if err := addGoal(e.SourceGoalID, 1, -e.Amount.Cents()); err != nil {
					return err
				}
			}
		case bankv2.Redeem, bankv2.Mature:
			if e.SourceGoalID != "" {
				for _, delta := range []struct {
					dimension int
					amount    int64
				}{{0, -e.Net.Cents()}, {1, e.Amount.Cents()}, {2, -e.Loss.Cents()}} {
					if err := addGoal(e.SourceGoalID, delta.dimension, delta.amount); err != nil {
						return err
					}
				}
			}
		case bankv2.MoveGoal:
			if err := addGoal(e.SourceGoalID, 0, e.Amount.Cents()); err != nil {
				return err
			}
			if err := addGoal(e.DestinationGoalID, 0, -e.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	for _, r := range g.bank.Legacy {
		if r.Status == execution.BankSettled && !legacySettled[r.Key] {
			if err := addGoal(r.Request.GoalID, 0, -r.Request.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	expectedPositions := map[string]bankv2.Position{}
	for _, p := range g.bank.Stock.Positions {
		if !settled[p.Opening.OpeningKey] && (p.Opening.SourceKind != "TRUSTED_HISTORICAL_IMPORT" || p.Opening.PurchasedAt.After(run.CapturedAt)) {
			continue
		}
		p.Closed = settled[p.ClosingKey]
		if !p.Closed {
			p.ClosingKey = ""
		}
		expectedPositions[p.Opening.PositionID] = p
	}
	seenGoals := map[string]bool{}
	for _, goal := range run.CurrentStates[0].Goals {
		values, ok := goalValues[goal.ID]
		if !ok || seenGoals[goal.ID] || values[0].Cmp(big.NewInt(goal.Cash.Cents())) != 0 || values[1].Cmp(big.NewInt(goal.Principal.Cents())) != 0 || values[2].Cmp(big.NewInt(goal.Loss.Cents())) != 0 {
			return businessInvalid("native READY recovery historical goal cash/principal/loss")
		}
		seenGoals[goal.ID] = true
	}
	for id, values := range goalValues {
		if !seenGoals[id] && (values[0].Sign() != 0 || values[1].Sign() != 0 || values[2].Sign() != 0) {
			return businessInvalid("native READY recovery omitted owned goal")
		}
	}
	if len(run.CurrentStates[0].Positions) != len(expectedPositions) {
		return businessInvalid("native READY recovery full historical position inventory")
	}
	for _, p := range run.CurrentStates[0].Positions {
		expected, ok := expectedPositions[p.Opening.PositionID]
		if !ok || !exactJSON(expected, p) {
			return businessInvalid("native READY recovery exact historical position state")
		}
		delete(expectedPositions, p.Opening.PositionID)
	}
	stock := run.CurrentStates[0]
	seen := map[string]bool{}
	for _, a := range stock.Accounts {
		expected, ok := balances[a.ID]
		if !ok || seen[a.ID] || expected.Cmp(big.NewInt(a.Balance.Cents())) != 0 || !a.Reserved.IsZero() {
			return businessInvalid("native READY recovery historical independent cash/reservation")
		}
		seen[a.ID] = true
	}
	for id, amount := range balances {
		if !seen[id] && amount.Sign() != 0 {
			return businessInvalid("native READY recovery missing nonzero account")
		}
	}
	for _, p := range stock.Positions {
		hash, err := p.Opening.Digest()
		if err != nil || hash != p.OpeningHash || p.Opening.UserID != g.manifest.Scope.UserID || p.Opening.Namespace != g.manifest.Scope.Namespace || !settled[p.Opening.OpeningKey] {
			return businessInvalid("native READY recovery actual position opening")
		}
		if p.Closed && !settled[p.ClosingKey] {
			return businessInvalid("native READY recovery actual principal return closure")
		}
	}
	return nil
}

func nativeRecoveryHashV3(value string) bool {
	_, err := funds.ParseEvidenceHash(value)
	return err == nil
}
