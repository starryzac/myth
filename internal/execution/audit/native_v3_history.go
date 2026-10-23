package executionaudit

import (
	"crypto/ed25519"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// This public profile is supplied by the operator's independent configuration,
// separately from the audited database. It authenticates a simulated historical
// statement issuer; it does not claim that its contents are real bank money.
type NativeHistoryAuthorityV3 struct {
	IssuerID     string     `json:"issuer_id"`
	Namespace    string     `json:"namespace"`
	UserID       funds.ID   `json:"user_id"`
	AccountIDs   []funds.ID `json:"account_ids"`
	PublicKeyHex string     `json:"public_key_hex"`
}
type NativeConfiguredHistoryAuthorityV3 struct {
	SourceID  string                   `json:"source_id"`
	Authority NativeHistoryAuthorityV3 `json:"authority"`
}

// Trust is supplied outside the captured database/bundle by the verifier's
// operator configuration. Retained approved hashes support old captures after
// public issuer rotation without accepting a replacement key from the bundle.
type NativeVerifierTrustV3 struct{ HistoryAuthorityProfileHashes []string }

func NativeHistoryAuthorityProfileHashV3(profiles []NativeConfiguredHistoryAuthorityV3) string {
	if profiles == nil {
		profiles = []NativeConfiguredHistoryAuthorityV3{}
	}
	return nakedHash(mustJSONV3(profiles))
}

type nativeHistoryCaptureV3 struct {
	Protocol      string                          `json:"protocol"`
	ID            string                          `json:"id"`
	SourceID      string                          `json:"source_id"`
	EnvironmentID funds.ID                        `json:"environment_id"`
	UserID        funds.ID                        `json:"user_id"`
	Namespace     string                          `json:"namespace"`
	Authority     NativeHistoryAuthorityV3        `json:"authority"`
	AuthorityHash string                          `json:"authority_hash"`
	Envelope      policy.HistoryStatementEnvelope `json:"envelope"`
	RecordedAt    time.Time                       `json:"recorded_at"`
	SessionID     funds.ID                        `json:"session_id"`
}
type nativeHistoryImportV3 struct {
	Protocol      string    `json:"protocol"`
	ID            string    `json:"id"`
	SourceID      string    `json:"source_id"`
	StatementID   string    `json:"statement_id"`
	StatementHash string    `json:"statement_hash"`
	FromDate      string    `json:"from_date"`
	ThroughDate   string    `json:"through_date"`
	RecordedAt    time.Time `json:"recorded_at"`
	SourceVersion uint64    `json:"source_version"`
	Replayed      bool      `json:"replayed"`
}
type nativeHistoryCommandV3 struct {
	Protocol string                `json:"protocol"`
	SourceID string                `json:"source_id"`
	Result   nativeHistoryImportV3 `json:"result"`
}
type nativeVerifiedHistoryV3 struct {
	capture   nativeHistoryCaptureV3
	statement policy.HistoryStatement
}

func nativeHistoryIdentifierV3(s string, max int) bool {
	return s != "" && len(s) <= max && !strings.ContainsAny(s, "/\\\x00\r\n")
}

func (g *nativeGraphV3) historyOriginalsV3() (map[string]nativeVerifiedHistoryV3, error) {
	configured := map[string]NativeHistoryAuthorityV3{}
	if g.bank != nil {
		if len(g.bank.HistoryAuthorities) > 0 {
			profileHash := NativeHistoryAuthorityProfileHashV3(g.bank.HistoryAuthorities)
			approved := false
			for _, hash := range g.verifierTrust.HistoryAuthorityProfileHashes {
				approved = approved || hash == profileHash
			}
			if !approved {
				return nil, fmt.Errorf("%w: externally approved historical public profile hash missing", evidence.ErrIncomplete)
			}
		}
		previous := ""
		for _, source := range g.bank.HistoryAuthorities {
			a := source.Authority
			public, err := hex.DecodeString(a.PublicKeyHex)
			if !nativeHistoryIdentifierV3(source.SourceID, 64) || source.SourceID <= previous || !nativeHistoryIdentifierV3(a.IssuerID, 128) || a.Namespace != g.manifest.Scope.Namespace || a.UserID.String() != g.manifest.Scope.UserID || err != nil || len(public) != ed25519.PublicKeySize || hex.EncodeToString(public) != a.PublicKeyHex || len(a.AccountIDs) < 1 || len(a.AccountIDs) > 100 {
				return nil, businessInvalid("native independently configured history authority")
			}
			previous = source.SourceID
			last := ""
			for _, id := range a.AccountIDs {
				if id.String() <= last || g.row("accounts", id.String()) == nil {
					return nil, businessInvalid("native configured history account scope")
				}
				last = id.String()
			}
			configured[source.SourceID] = a
		}
	}
	originals := map[string]nativeVerifiedHistoryV3{}
	for _, row := range g.rows["evidence_blobs"] {
		if row.text("kind") != "FORECAST_HISTORY_STATEMENT_V1" {
			continue
		}
		if len(originals) >= 100 || nativeHashFieldV3(row, "canonical", "digest") != nil {
			return nil, businessInvalid("native historical source raw capacity/hash")
		}
		var capture nativeHistoryCaptureV3
		nativeOriginalV3(row, "canonical", &capture)
		a, configuredOK := configured[capture.SourceID]
		if !configuredOK {
			return nil, fmt.Errorf("%w: independent history issuer profile unavailable", evidence.ErrIncomplete)
		}
		public, _ := hex.DecodeString(a.PublicKeyHex)
		statement, _, err := policy.VerifyHistoryEnvelope(capture.Envelope, ed25519.PublicKey(public))
		if err != nil || capture.Protocol != "forecast-history-capture-v1" || capture.ID != row.text("id") || capture.ID != "forecast-history:"+capture.SourceID+":"+statement.StatementID || capture.EnvironmentID.String() != g.manifest.Scope.EnvironmentID || capture.UserID.String() != g.manifest.Scope.UserID || capture.Namespace != g.manifest.Scope.Namespace || !exactJSON(capture.Authority, a) || capture.AuthorityHash != nakedHash(mustJSONV3(a)) || statement.IssuerID != a.IssuerID || statement.Namespace != a.Namespace || statement.UserID != a.UserID || !exactJSON(statement.AccountIDs, a.AccountIDs) || statement.IssuedAt.After(capture.RecordedAt) || capture.RecordedAt.After(g.manifest.Source.CapturedAt) || !capture.RecordedAt.Equal(row.instant("created_at")) {
			return nil, businessInvalid("native historical exact signature/scope/issuer/time/source mode")
		}
		if err = g.humanAtV3(capture.SessionID, capture.RecordedAt); err != nil {
			return nil, err
		}
		originals[capture.ID] = nativeVerifiedHistoryV3{capture, statement}
	}
	return originals, g.checkedRowsV3()
}

func nativeHistoryAppendV3(old, next policy.HistoryStatement) bool {
	if old.StatementID == next.StatementID || !next.IssuedAt.After(old.IssuedAt) || next.Timezone != old.Timezone || next.SourceMode != old.SourceMode || next.ThroughDate < old.ThroughDate || next.FromDate < old.FromDate {
		return false
	}
	leftRows, rightRows := map[string]string{}, map[string]string{}
	leftWindows, rightWindows := map[string]string{}, map[string]string{}
	for _, side := range []struct {
		s             policy.HistoryStatement
		rows, windows map[string]string
	}{{old, leftRows, leftWindows}, {next, rightRows, rightWindows}} {
		for _, row := range side.s.Transactions {
			if row.Date >= next.FromDate && row.Date <= old.ThroughDate {
				side.rows[row.ID.String()] = nakedHash(mustJSONV3(row))
			}
		}
		for _, w := range side.s.SeasonalWindows {
			if w.Start >= next.FromDate && w.End <= old.ThroughDate {
				side.windows[w.HolidayCode+":"+w.Start] = nakedHash(mustJSONV3(w))
			}
		}
	}
	return exactJSON(leftRows, rightRows) && exactJSON(leftWindows, rightWindows)
}

func (g *nativeGraphV3) checkHistorySourcesV3() error {
	originals, err := g.historyOriginalsV3()
	if err != nil {
		return err
	}
	sources := map[string][]nativeVerifiedHistoryV3{}
	for _, h := range originals {
		sources[h.capture.SourceID] = append(sources[h.capture.SourceID], h)
	}
	for _, history := range sources {
		sort.Slice(history, func(i, j int) bool { return history[i].statement.IssuedAt.Before(history[j].statement.IssuedAt) })
		for i, h := range history {
			if i > 0 && (!nativeHistoryAppendV3(history[i-1].statement, h.statement) || h.capture.RecordedAt.Before(history[i-1].capture.RecordedAt)) {
				return businessInvalid("native historical statement fork or rewritten overlap")
			}
			if err := g.historyPaymentsV3(h); err != nil {
				return err
			}
		}
	}
	commands := map[string]int{}
	for _, row := range g.rows["evidence_blobs"] {
		if row.text("kind") != "FORECAST_HISTORY_IMPORT_COMMAND_V1" {
			continue
		}
		var command nativeHistoryCommandV3
		nativeOriginalV3(row, "canonical", &command)
		h, ok := originals[command.Result.ID]
		r := command.Result
		if !ok || nativeHashFieldV3(row, "canonical", "digest") != nil || command.Protocol != "forecast-history-import-command-v1" || !strings.HasPrefix(row.text("id"), "forecast-history-command:") || !nativeHistoryIdentifierV3(strings.TrimPrefix(row.text("id"), "forecast-history-command:"), 128) || command.SourceID != h.capture.SourceID || r.Protocol != "forecast-history-import-v1" || r.SourceID != command.SourceID || r.StatementID != h.statement.StatementID || r.StatementHash != h.capture.Envelope.RawSHA256 || r.FromDate != h.statement.FromDate || r.ThroughDate != h.statement.ThroughDate || !r.RecordedAt.Equal(h.capture.RecordedAt) || row.instant("created_at").Before(r.RecordedAt) || r.SourceVersion < 1 || r.SourceVersion > uint64(g.snapshot.Watermarks["source_version"]) {
			return businessInvalid("native historical import original-key result/watermark")
		}
		commands[r.ID]++
	}
	for id := range originals {
		if commands[id] < 1 {
			return fmt.Errorf("%w: signed historical source import command absent", evidence.ErrIncomplete)
		}
	}
	return g.checkedRowsV3()
}

func (g *nativeGraphV3) historyPaymentsV3(h nativeVerifiedHistoryV3) error {
	rows := map[string]policy.TemplateHistoryTransaction{}
	for _, row := range h.statement.Transactions {
		rows[row.ID.String()] = row
	}
	accounts := map[string]bool{}
	for _, id := range h.statement.AccountIDs {
		accounts[id.String()] = true
	}
	zone, _ := policy.TemplateLocation(h.statement.Timezone)
	for id, receipt := range g.nativeReceipts {
		if receipt.BankResult.SettledAt == nil || receipt.BankResult.SettledAt.After(h.capture.RecordedAt) {
			continue
		}
		request := g.nativeRequests[id]
		date := receipt.BankResult.SettledAt.In(zone).Format("2006-01-02")
		if date < h.statement.FromDate || date > h.statement.ThroughDate {
			continue
		}
		covered := false
		sourceAccounts := map[string]bool{}
		for _, cash := range request.Effect.CashUses {
			covered = covered || accounts[cash.AccountID]
			sourceAccounts[cash.AccountID] = true
		}
		if !covered {
			continue
		}
		row, present := rows[id]
		if request.Effect.Kind != bankv2.Payment {
			if present && row.EconomicRole == "CONSUMPTION" {
				return businessInvalid("native historical internal movement labelled consumption")
			}
			continue
		}
		if len(sourceAccounts) != 1 {
			return fmt.Errorf("%w: historical payment multi-account closure", evidence.ErrUnsupportedVersion)
		}
		if !present || row.Date != date || !sourceAccounts[row.AccountID.String()] || row.Amount != request.Effect.Amount || row.Direction != "DEBIT" || row.EconomicRole != "CONSUMPTION" {
			return businessInvalid("native signed history omitted or relabelled actual settled payment")
		}
	}
	return nil
}

func (g *nativeGraphV3) checkHistoryFactsV3(facts policy.TemplateFacts, at time.Time, timezone string, sourceVersion uint64) error {
	originals, err := g.historyOriginalsV3()
	if err != nil {
		return err
	}
	latest := map[string]nativeVerifiedHistoryV3{}
	firstVersion := map[string]uint64{}
	for _, row := range g.rows["evidence_blobs"] {
		if row.text("kind") != "FORECAST_HISTORY_IMPORT_COMMAND_V1" {
			continue
		}
		var command nativeHistoryCommandV3
		nativeOriginalV3(row, "canonical", &command)
		v, old := command.Result.SourceVersion, firstVersion[command.Result.ID]
		if old == 0 || v < old {
			firstVersion[command.Result.ID] = v
		}
	}
	for _, h := range originals {
		if h.capture.RecordedAt.After(at) {
			continue
		}
		// Explicit source watermarks distinguish two commits at the same injected
		// business instant. A later import must not rewrite an earlier preview.
		v := firstVersion[h.capture.ID]
		if v == 0 {
			return fmt.Errorf("%w: historical source admission watermark", evidence.ErrIncomplete)
		}
		if sourceVersion != 0 && v > sourceVersion {
			continue
		}
		if sourceVersion == 0 && h.capture.RecordedAt.Equal(at) && len(facts.HistoryAccounts) == 0 {
			return fmt.Errorf("%w: historical same-instant source order", evidence.ErrIncomplete)
		}
		old, ok := latest[h.capture.SourceID]
		if !ok || h.statement.IssuedAt.After(old.statement.IssuedAt) {
			latest[h.capture.SourceID] = h
		}
	}
	accounts := []funds.ID{}
	coverage := []policy.TemplateHistoryCoverage{}
	transactions := []policy.TemplateHistoryTransaction{}
	windows := []policy.TemplateSeasonalWindow{}
	if len(latest) > 0 {
		for _, a := range facts.Accounts {
			accounts = append(accounts, a.ID)
		}
		covered := map[funds.ID]bool{}
		seen := map[funds.ID]bool{}
		available := map[funds.ID]bool{}
		for _, id := range accounts {
			available[id] = true
		}
		for _, h := range latest {
			s := h.statement
			if s.Timezone != timezone {
				return businessInvalid("native history prediction timezone")
			}
			for _, id := range s.AccountIDs {
				if !available[id] || covered[id] {
					return businessInvalid("native history prediction overlapping account coverage")
				}
				covered[id] = true
				from, _ := policy.TemplateDate(s.FromDate)
				through, _ := policy.TemplateDate(s.ThroughDate)
				dates := []string{}
				for d := from; !d.After(through); d = d.AddDate(0, 0, 1) {
					dates = append(dates, d.Format("2006-01-02"))
				}
				coverage = append(coverage, policy.TemplateHistoryCoverage{AccountID: id, Dates: dates})
			}
			for _, row := range s.Transactions {
				if seen[row.ID] {
					return businessInvalid("native history prediction duplicate transaction")
				}
				seen[row.ID] = true
				transactions = append(transactions, row)
			}
			for _, w := range s.SeasonalWindows {
				windows = append(windows, policy.TemplateSeasonalWindow{SourceProtocol: policy.HistoryStatementProtocol, HolidayCode: w.HolidayCode, Start: w.Start, End: w.End, Verified: true, EvidenceHash: h.capture.Envelope.RawSHA256})
			}
		}
	}
	sort.Slice(accounts, func(i, j int) bool { return accounts[i].String() < accounts[j].String() })
	sort.Slice(coverage, func(i, j int) bool { return coverage[i].AccountID.String() < coverage[j].AccountID.String() })
	sort.Slice(transactions, func(i, j int) bool {
		if transactions[i].Date != transactions[j].Date {
			return transactions[i].Date < transactions[j].Date
		}
		return transactions[i].ID.String() < transactions[j].ID.String()
	})
	sort.Slice(windows, func(i, j int) bool {
		if windows[i].HolidayCode != windows[j].HolidayCode {
			return windows[i].HolidayCode < windows[j].HolidayCode
		}
		return windows[i].Start < windows[j].Start
	})
	if !exactJSON(accounts, facts.HistoryAccounts) || !exactJSON(coverage, facts.Coverage) || !exactJSON(transactions, facts.History) || !exactJSON(windows, facts.SeasonalWindows) {
		return businessInvalid("native forecast historical facts differ from exact signed closed source")
	}
	return nil
}
