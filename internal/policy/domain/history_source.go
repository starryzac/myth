package domain

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const HistoryStatementProtocol = "forecast-history-statement-v1"
const HistoryEnvelopeProtocol = "forecast-history-envelope-v1"
const MaxHistoryStatementBytes = 2 << 20
const historySignatureDomain = "zhiyu-forecast-history-statement-signature-v1\x00"

var ErrHistoryStatement = errors.New("invalid forecast history statement")
var historyNamespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

// A signed complete historical statement is an independent read source. It
// never creates income eligibility, account cash, permissions or a bank result.
type HistoryStatement struct {
	Protocol        string                       `json:"protocol"`
	IssuerID        string                       `json:"issuer_id"`
	StatementID     string                       `json:"statement_id"`
	Namespace       string                       `json:"namespace"`
	UserID          funds.ID                     `json:"user_id"`
	AccountIDs      []funds.ID                   `json:"account_ids"`
	Currency        funds.Currency               `json:"currency"`
	Timezone        string                       `json:"timezone"`
	FromDate        string                       `json:"from_date"`
	ThroughDate     string                       `json:"through_date"`
	Complete        bool                         `json:"complete"`
	SourceMode      string                       `json:"source_mode"`
	IssuedAt        time.Time                    `json:"issued_at"`
	Transactions    []TemplateHistoryTransaction `json:"transactions"`
	SeasonalWindows []HistoryStatementWindow     `json:"seasonal_windows"`
}

// Windows identify actual historical date ranges. Amounts are calculated
// from covered consumption originals and the selected rule's categories.
type HistoryStatementWindow struct {
	HolidayCode string `json:"holiday_code"`
	Start       string `json:"start"`
	End         string `json:"end"`
}

type HistoryStatementEnvelope struct {
	Protocol     string `json:"protocol"`
	RawHex       string `json:"raw_hex"`
	RawSHA256    string `json:"raw_sha256"`
	SignatureHex string `json:"signature_hex"`
}

func historyIdentifier(s string) bool {
	return s != "" && len(s) <= 128 && utf8.ValidString(s) && !strings.ContainsAny(s, "/\\\x00\r\n")
}

func (s HistoryStatement) Validate() error {
	zone, ze := TemplateLocation(s.Timezone)
	from, fe := TemplateDate(s.FromDate)
	through, te := TemplateDate(s.ThroughDate)
	if s.Protocol != HistoryStatementProtocol || !historyIdentifier(s.IssuerID) || !historyIdentifier(s.StatementID) || !historyNamespace.MatchString(s.Namespace) || s.UserID == (funds.ID{}) || s.Currency != funds.CNY || ze != nil || fe != nil || te != nil || through.Before(from) || through.Sub(from) > 365*24*time.Hour || s.IssuedAt.IsZero() || s.ThroughDate >= s.IssuedAt.In(zone).Format("2006-01-02") || !s.Complete || (s.SourceMode != "CONTROLLED_HISTORICAL_SIMULATION" && s.SourceMode != "INDEPENDENT_SIMULATED_STATEMENT") || len(s.AccountIDs) < 1 || len(s.AccountIDs) > 100 || s.Transactions == nil || len(s.Transactions) > 10000 || s.SeasonalWindows == nil || len(s.SeasonalWindows) > 10 {
		return ErrHistoryStatement
	}
	accounts := map[funds.ID]bool{}
	previous := ""
	for _, id := range s.AccountIDs {
		if id == (funds.ID{}) || id.String() <= previous {
			return ErrHistoryStatement
		}
		accounts[id] = true
		previous = id.String()
	}
	seen := map[funds.ID]bool{}
	previous = ""
	for _, row := range s.Transactions {
		key := row.Date + ":" + row.ID.String()
		if row.ID == (funds.ID{}) || seen[row.ID] || !accounts[row.AccountID] || row.Amount.IsZero() || row.Date < s.FromDate || row.Date > s.ThroughDate || (row.Direction != "CREDIT" && row.Direction != "DEBIT") || (row.EconomicRole != "CONSUMPTION" && row.EconomicRole != "NON_CONSUMPTION") || (row.EconomicRole == "CONSUMPTION" && row.Direction != "DEBIT") || len(row.Category) > 64 || !utf8.ValidString(row.Category) || strings.ContainsAny(row.Category, "\x00\r\n") || key <= previous {
			return ErrHistoryStatement
		}
		if _, e := TemplateDate(row.Date); e != nil {
			return ErrHistoryStatement
		}
		seen[row.ID] = true
		previous = key
	}
	previous = ""
	ends := map[string]string{}
	for _, w := range s.SeasonalWindows {
		if !historyIdentifier(w.HolidayCode) || len(w.HolidayCode) > 48 || w.Start < s.FromDate || w.End > s.ThroughDate || w.Start > w.End || (ends[w.HolidayCode] != "" && w.Start <= ends[w.HolidayCode]) || w.HolidayCode+":"+w.Start <= previous {
			return ErrHistoryStatement
		}
		if _, e := TemplateDate(w.Start); e != nil {
			return ErrHistoryStatement
		}
		if _, e := TemplateDate(w.End); e != nil {
			return ErrHistoryStatement
		}
		ends[w.HolidayCode] = w.End
		previous = w.HolidayCode + ":" + w.Start
	}
	return nil
}

func ParseHistoryStatement(raw []byte) (HistoryStatement, error) {
	var out HistoryStatement
	if len(raw) > MaxHistoryStatementBytes {
		return out, ErrHistoryStatement
	}
	if _, e := audit.ParseRawObject(raw); e != nil {
		return out, ErrHistoryStatement
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&out) != nil || out.Validate() != nil {
		return HistoryStatement{}, ErrHistoryStatement
	}
	return out, nil
}

func HistoryRawHash(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }
func historySignatureMessage(raw []byte) []byte {
	sum := sha256.Sum256(raw)
	return append([]byte(historySignatureDomain), sum[:]...)
}

func SignHistoryRaw(raw []byte, private ed25519.PrivateKey) (HistoryStatementEnvelope, error) {
	if len(private) != ed25519.PrivateKeySize {
		return HistoryStatementEnvelope{}, ErrHistoryStatement
	}
	if _, e := ParseHistoryStatement(raw); e != nil {
		return HistoryStatementEnvelope{}, e
	}
	return HistoryStatementEnvelope{Protocol: HistoryEnvelopeProtocol, RawHex: hex.EncodeToString(raw), RawSHA256: HistoryRawHash(raw), SignatureHex: hex.EncodeToString(ed25519.Sign(private, historySignatureMessage(raw)))}, nil
}

func VerifyHistoryEnvelope(e HistoryStatementEnvelope, public ed25519.PublicKey) (HistoryStatement, []byte, error) {
	var zero HistoryStatement
	if e.Protocol != HistoryEnvelopeProtocol || len(public) != ed25519.PublicKeySize || len(e.RawHex) > 2*MaxHistoryStatementBytes {
		return zero, nil, ErrHistoryStatement
	}
	raw, err := hex.DecodeString(e.RawHex)
	if err != nil || e.RawSHA256 != HistoryRawHash(raw) {
		return zero, nil, ErrHistoryStatement
	}
	sig, err := hex.DecodeString(e.SignatureHex)
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(public, historySignatureMessage(raw), sig) {
		return zero, nil, ErrHistoryStatement
	}
	statement, err := ParseHistoryStatement(raw)
	return statement, raw, err
}

// HistorySeasonalAmount is used only for the new explicitly labelled source.
// Older standalone protocol vectors keep their original Amount interpretation.
func HistorySeasonalAmount(facts TemplateFacts, window TemplateSeasonalWindow, categories []string) (funds.Money, error) {
	coverage := map[funds.ID]map[string]bool{}
	for _, c := range facts.Coverage {
		if coverage[c.AccountID] != nil {
			return funds.Money{}, ErrTemplateFacts
		}
		coverage[c.AccountID] = map[string]bool{}
		for _, date := range c.Dates {
			coverage[c.AccountID][date] = true
		}
	}
	from, fe := TemplateDate(window.Start)
	through, te := TemplateDate(window.End)
	if fe != nil || te != nil || through.Before(from) || len(facts.HistoryAccounts) == 0 {
		return funds.Money{}, ErrTemplateFacts
	}
	accounts := map[funds.ID]bool{}
	for _, id := range facts.HistoryAccounts {
		if accounts[id] {
			return funds.Money{}, ErrTemplateFacts
		}
		accounts[id] = true
		for d := from; !d.After(through); d = d.AddDate(0, 0, 1) {
			if !coverage[id][d.Format("2006-01-02")] {
				return funds.Money{}, ErrTemplateFacts
			}
		}
	}
	selected := map[string]bool{}
	for _, category := range categories {
		selected[category] = true
	}
	amount := funds.Money{}
	seen := map[funds.ID]bool{}
	for _, row := range facts.History {
		if seen[row.ID] {
			return funds.Money{}, ErrTemplateFacts
		}
		seen[row.ID] = true
		if accounts[row.AccountID] && row.Date >= window.Start && row.Date <= window.End {
			if row.EconomicRole == "UNKNOWN" {
				return funds.Money{}, ErrTemplateFacts
			}
			if row.Direction == "DEBIT" && row.EconomicRole == "CONSUMPTION" && row.CategoryConfirmed && selected[row.Category] {
				var err error
				amount, err = amount.Add(row.Amount)
				if err != nil {
					return funds.Money{}, err
				}
			}
		}
	}
	return amount, nil
}
