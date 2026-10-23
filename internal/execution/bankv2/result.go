package bankv2

import (
	"encoding/json"
	"fmt"
	"math/big"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type Status string

const (
	NotFound Status = "NOT_FOUND"
	Accepted Status = "ACCEPTED"
	Settled  Status = "SETTLED"
	Rejected Status = "REJECTED"
)

type PositionOpening struct {
	Protocol           string       `json:"protocol"`
	Namespace          string       `json:"namespace"`
	UserID             string       `json:"user_id"`
	PositionID         string       `json:"position_id"`
	OpeningKey         string       `json:"opening_key"`
	OpeningRequestHash string       `json:"opening_request_hash"`
	SourceKind         string       `json:"source_kind"`
	SourceEvidenceHash string       `json:"source_evidence_hash"`
	Principal          funds.Money  `json:"principal_cents"`
	Terms              ProductTerms `json:"terms"`
	TermsHash          string       `json:"terms_hash"`
	CashUses           []CashUse    `json:"cash_uses"`
	OriginUses         []OriginUse  `json:"origin_uses"`
	ReturnUses         []ReturnUse  `json:"return_uses"`
	GoalID             string       `json:"goal_id"`
	ReturnAccountID    string       `json:"return_account_id"`
	SourceAuthority    Authority    `json:"source_authority"`
	PurchasedAt        time.Time    `json:"purchased_at"`
	MaturityAt         time.Time    `json:"maturity_at"`
}

func (p PositionOpening) canonical() PositionOpening {
	p.PurchasedAt = p.PurchasedAt.UTC()
	if !p.MaturityAt.IsZero() {
		p.MaturityAt = p.MaturityAt.UTC()
	}
	return p
}
func (p PositionOpening) Validate() error {
	termsHash, err := p.Terms.Digest()
	if p.Protocol != Protocol+"/position" || !namespace.MatchString(p.Namespace) || !validIDs(p.UserID, p.PositionID, p.OpeningKey, p.ReturnAccountID) || !isHash(p.OpeningRequestHash) || err != nil || p.TermsHash != termsHash || p.Principal.IsZero() || p.PurchasedAt.IsZero() || p.CashUses == nil || p.OriginUses == nil || p.ReturnUses == nil || len(p.CashUses) > MaxUses || len(p.OriginUses) > MaxUses || len(p.ReturnUses) > MaxUses || p.SourceAuthority.Role != "EXECUTE" || !validIDs(p.SourceAuthority.SourceID, p.SourceAuthority.PolicyID) || p.SourceAuthority.Version < 1 || p.SourceAuthority.PolicyVersion < 1 || p.SourceAuthority.GoalID != p.GoalID {
		return ErrInvalid
	}
	if p.GoalID != "" && !validIDs(p.GoalID) {
		return ErrInvalid
	}
	if p.SourceKind == "BANK_PURCHASE" {
		if p.SourceEvidenceHash != "" {
			return ErrInvalid
		}
	} else if p.SourceKind == "TRUSTED_HISTORICAL_IMPORT" {
		if !isHash(p.SourceEvidenceHash) {
			return ErrInvalid
		}
	} else {
		return ErrInvalid
	}
	if p.Terms.AssetClass == "FIXED_DEPOSIT" {
		if !p.MaturityAt.Equal(p.PurchasedAt.UTC().AddDate(0, 0, p.Terms.TermDays)) {
			return ErrInvalid
		}
	} else if !p.MaturityAt.IsZero() {
		return ErrInvalid
	}
	var amount funds.Money
	seen := map[string]bool{}
	for _, use := range p.CashUses {
		if !validIDs(use.AccountID) || use.Amount.IsZero() || seen[use.AccountID] {
			return ErrInvalid
		}
		seen[use.AccountID] = true
		var err error
		amount, err = amount.Add(use.Amount)
		if err != nil {
			return err
		}
	}
	if amount != p.Principal || !seen[p.ReturnAccountID] {
		return ErrInvalid
	}
	funding := map[string]funds.Money{}
	classified := map[string]funds.Money{}
	sourceIDs := map[string]bool{}
	for _, use := range p.CashUses {
		funding[use.AccountID] = use.Amount
	}
	for _, use := range p.OriginUses {
		key := "income:" + use.OriginID + ":" + use.AccountID
		if !validIDs(use.OriginID, use.AccountID) || use.Amount.IsZero() || sourceIDs[key] || (use.Bucket != "AVAILABLE" && use.Bucket != "ASSIGNED_GOAL") || use.Bucket == "ASSIGNED_GOAL" && p.GoalID == "" {
			return ErrInvalid
		}
		sourceIDs[key] = true
		var err error
		classified[use.AccountID], err = classified[use.AccountID].Add(use.Amount)
		if err != nil {
			return err
		}
	}
	for _, use := range p.ReturnUses {
		key := "return:" + use.OperationKey + ":" + use.AccountID
		if !validIDs(use.OperationKey, use.PositionID, use.AccountID) || use.PositionID == p.PositionID || use.Amount.IsZero() || sourceIDs[key] {
			return ErrInvalid
		}
		sourceIDs[key] = true
		var err error
		classified[use.AccountID], err = classified[use.AccountID].Add(use.Amount)
		if err != nil {
			return err
		}
	}
	for account, n := range classified {
		if n.Cents() > funding[account].Cents() {
			return ErrInvalid
		}
	}
	return nil
}
func (p PositionOpening) Digest() (string, error) {
	if p.Validate() != nil {
		return "", ErrInvalid
	}
	return digest(p.canonical(), Protocol+"/position")
}

func OpenedPosition(r Request, purchasedAt time.Time) (PositionOpening, error) {
	if r.Validate() != nil || r.Effect.Kind != Purchase || purchasedAt.Before(r.RequestedAt) {
		return PositionOpening{}, ErrInvalid
	}
	h, _ := r.Digest()
	e := r.Effect
	p := PositionOpening{Protocol: Protocol + "/position", Namespace: r.Namespace, UserID: r.UserID, PositionID: e.PositionID, OpeningKey: r.Key, OpeningRequestHash: h, SourceKind: "BANK_PURCHASE", Principal: e.Amount, Terms: e.Product.Terms, TermsHash: e.Product.TermsHash, CashUses: e.CashUses, OriginUses: e.OriginUses, ReturnUses: e.ReturnUses, GoalID: e.SourceGoalID, ReturnAccountID: e.ReturnAccountID, PurchasedAt: purchasedAt.UTC()}
	for _, a := range r.Authorities {
		if a.Role == "EXECUTE" {
			p.SourceAuthority = a
		}
	}
	if p.Terms.AssetClass == "FIXED_DEPOSIT" {
		p.MaturityAt = p.PurchasedAt.AddDate(0, 0, p.Terms.TermDays)
	}
	if p.Validate() != nil {
		return PositionOpening{}, ErrInvalid
	}
	return p, nil
}

type Posting struct {
	ID         string      `json:"id"`
	Dimension  string      `json:"dimension"`
	LedgerID   string      `json:"ledger_id"`
	Direction  string      `json:"direction"`
	Amount     funds.Money `json:"amount_cents"`
	OccurredAt time.Time   `json:"occurred_at"`
}
type PrincipalReturn struct {
	Role                string      `json:"role"`
	PositionID          string      `json:"position_id"`
	PositionOpeningHash string      `json:"position_opening_hash"`
	OperationKey        string      `json:"operation_key"`
	RequestHash         string      `json:"request_hash"`
	AccountID           string      `json:"account_id"`
	GoalID              string      `json:"goal_id"`
	Amount              funds.Money `json:"amount_cents"`
	OccurredAt          time.Time   `json:"occurred_at"`
}
type Result struct {
	Protocol        string           `json:"protocol"`
	Status          Status           `json:"status"`
	Namespace       string           `json:"namespace"`
	Key             string           `json:"key"`
	UserID          string           `json:"user_id"`
	Request         *Request         `json:"request"`
	RequestHash     string           `json:"request_hash"`
	Sequence        int64            `json:"sequence"`
	Version         int64            `json:"version"`
	AcceptedAt      *time.Time       `json:"accepted_at"`
	AvailableAt     *time.Time       `json:"available_at"`
	SettledAt       *time.Time       `json:"settled_at"`
	RejectionCode   string           `json:"rejection_code"`
	AbsenceIsFinal  bool             `json:"absence_is_final"`
	Position        *PositionOpening `json:"position"`
	PrincipalReturn *PrincipalReturn `json:"principal_return"`
	Postings        []Posting        `json:"postings"`
}
type Inventory struct {
	Protocol   string   `json:"protocol"`
	Namespace  string   `json:"namespace"`
	UserID     string   `json:"user_id"`
	Watermark  int64    `json:"watermark"`
	NextAfter  int64    `json:"next_after"`
	HasMore    bool     `json:"has_more"`
	Operations []Result `json:"operations"`
}

// Availability recomputes the actual agreement from the bank acceptance clock.
// A late maturity observation retains its contractual event date; it does not
// pretend that a test clock was actually allowed to run for the term duration.
func Availability(r Request, p *PositionOpening, acceptedAt time.Time) (time.Time, error) {
	if r.Validate() != nil || acceptedAt.Before(r.RequestedAt) || !acceptedAt.Before(r.ExpiresAt) {
		return time.Time{}, ErrInvalid
	}
	e := r.Effect
	at := acceptedAt.UTC()
	if e.Kind == Purchase {
		terms := e.Product.Terms
		if terms.AssetClass == "FIXED_DEPOSIT" {
			if at.AddDate(0, 0, terms.TermDays+terms.RedemptionDelayDays).After(e.PrincipalAvailableBy) {
				return time.Time{}, ErrInvalid
			}
		} else if e.PlannedRedeemAt.Before(at.AddDate(0, 0, terms.LockDays)) || e.PlannedRedeemAt.AddDate(0, 0, terms.RedemptionDelayDays).After(e.PrincipalAvailableBy) {
			return time.Time{}, ErrInvalid
		}
	}
	if e.Kind == Redeem || e.Kind == Mature {
		if p == nil || p.Validate() != nil {
			return time.Time{}, ErrInvalid
		}
		h, _ := p.Digest()
		if p.Namespace != r.Namespace || p.UserID != r.UserID || p.PositionID != e.PositionID || h != e.PositionOpeningHash || p.Principal != e.Amount || p.ReturnAccountID != e.ReturnAccountID || p.GoalID != e.SourceGoalID || p.TermsHash != e.Product.TermsHash {
			return time.Time{}, ErrInvalid
		}
		net, fee, loss, available, err := RedemptionQuote(p.Principal, p.Terms, p.PurchasedAt, at, e.Kind == Mature)
		if err != nil || net != e.Net || fee != e.Fee || loss != e.Loss {
			return time.Time{}, ErrInvalid
		}
		at = available
		for _, a := range r.Authorities {
			if a.Role == "POSITION_ORIGINAL" && (a.PolicyID != p.SourceAuthority.PolicyID || a.PolicyVersion != p.SourceAuthority.PolicyVersion || a.GoalID != p.GoalID || a.AllowsFee != p.SourceAuthority.AllowsFee || a.AllowsLoss != p.SourceAuthority.AllowsLoss) {
				return time.Time{}, ErrInvalid
			}
		}
	}
	if at.After(e.LatestArrivalAt) {
		return time.Time{}, ErrInvalid
	}
	return at, nil
}

func ExpectedPostings(r Request, at time.Time) ([]Posting, error) {
	h, err := r.Digest()
	if err != nil {
		return nil, err
	}
	e := r.Effect
	out := []Posting{}
	add := func(dimension, ledger, direction string, n funds.Money) {
		if !n.IsZero() {
			out = append(out, Posting{ID: fmt.Sprintf("%s:v2:%d", h, len(out)+1), Dimension: dimension, LedgerID: ledger, Direction: direction, Amount: n, OccurredAt: at.UTC()})
		}
	}
	switch e.Kind {
	case Transfer, Payment, Purchase:
		for _, use := range e.CashUses {
			add("ECONOMIC", "CASH:"+use.AccountID, "DEBIT", use.Amount)
		}
		if e.Kind == Transfer {
			add("ECONOMIC", "CASH:"+e.DestinationAccountID, "CREDIT", e.Amount)
		}
		if e.Kind == Payment {
			add("ECONOMIC", "PAYEE:"+e.Liability.PayeeID, "CREDIT", e.Amount)
			add("LIABILITY", "DUE:"+e.Liability.ID, "DEBIT", e.Amount)
			add("LIABILITY", "PAID:"+e.Liability.ID, "CREDIT", e.Amount)
		}
		if e.Kind == Purchase {
			add("ECONOMIC", "POSITION:"+e.PositionID, "CREDIT", e.Amount)
			if e.SourceGoalID != "" {
				add("GOAL_OWNERSHIP", "GOAL_CASH:"+e.SourceGoalID, "DEBIT", e.Amount)
				add("GOAL_OWNERSHIP", "GOAL_PRINCIPAL:"+e.SourceGoalID, "CREDIT", e.Amount)
			}
		}
	case Allocate:
		add("GOAL_OWNERSHIP", "UNASSIGNED:"+e.CashUses[0].AccountID, "DEBIT", e.Amount)
		add("GOAL_OWNERSHIP", "GOAL_CASH:"+e.DestinationGoalID, "CREDIT", e.Amount)
	case MoveGoal:
		if e.CashUses[0].AccountID != e.DestinationAccountID {
			add("ECONOMIC", "CASH:"+e.CashUses[0].AccountID, "DEBIT", e.Amount)
			add("ECONOMIC", "CASH:"+e.DestinationAccountID, "CREDIT", e.Amount)
		}
		add("GOAL_OWNERSHIP", "GOAL_CASH:"+e.SourceGoalID, "DEBIT", e.Amount)
		add("GOAL_OWNERSHIP", "GOAL_CASH:"+e.DestinationGoalID, "CREDIT", e.Amount)
	case Redeem, Mature:
		add("ECONOMIC", "POSITION:"+e.PositionID, "DEBIT", e.Amount)
		add("ECONOMIC", "CASH:"+e.DestinationAccountID, "CREDIT", e.Net)
		add("ECONOMIC", "FEE:"+r.UserID, "CREDIT", e.Fee)
		add("ECONOMIC", "LOSS:"+r.UserID, "CREDIT", e.Loss)
		if e.SourceGoalID != "" {
			cost, _ := e.Fee.Add(e.Loss)
			add("GOAL_OWNERSHIP", "GOAL_PRINCIPAL:"+e.SourceGoalID, "DEBIT", e.Amount)
			add("GOAL_OWNERSHIP", "GOAL_CASH:"+e.SourceGoalID, "CREDIT", e.Net)
			add("GOAL_OWNERSHIP", "GOAL_LOSS:"+e.SourceGoalID, "CREDIT", cost)
		}
	}
	for _, use := range e.OriginUses {
		from := "AVAILABLE:" + use.OriginID + ":" + use.AccountID
		to := ""
		bucket := "AVAILABLE"
		if use.Bucket == "ASSIGNED_GOAL" {
			bucket = "ASSIGNED"
			from = "ASSIGNED:" + use.OriginID + ":" + use.AccountID
		}
		switch e.Kind {
		case Allocate, Purchase:
			if use.Bucket == "AVAILABLE" {
				to = "ASSIGNED:" + use.OriginID + ":" + use.AccountID
			}
		case Transfer, MoveGoal:
			if e.CashUses[0].AccountID != e.DestinationAccountID {
				to = bucket + ":" + use.OriginID + ":" + e.DestinationAccountID
			}
		case Payment:
			to = "SPENT:" + use.OriginID + ":" + use.AccountID
		}
		if to != "" {
			add("INCOME_LOCATION", from, "DEBIT", use.Amount)
			add("INCOME_LOCATION", to, "CREDIT", use.Amount)
		}
	}
	return out, nil
}

func equalJSON(a, b any) bool {
	x, e := json.Marshal(a)
	y, f := json.Marshal(b)
	return e == nil && f == nil && string(x) == string(y)
}
func VerifyResult(original Request, result Result) error {
	h, err := original.Digest()
	if err != nil {
		return err
	}
	if result.Protocol != Protocol || result.Namespace != original.Namespace || result.Key != original.Key || result.UserID != original.UserID || result.Postings == nil {
		return ErrInvalid
	}
	if result.Status == NotFound {
		if result.Request != nil || result.RequestHash != "" || result.Sequence != 0 || result.Version != 0 || result.AcceptedAt != nil || result.AvailableAt != nil || result.SettledAt != nil || result.RejectionCode != "" || result.AbsenceIsFinal || result.Position != nil || result.PrincipalReturn != nil || len(result.Postings) != 0 {
			return ErrInvalid
		}
		return nil
	}
	if result.Request == nil || result.Sequence < 1 || result.RequestHash != h {
		return ErrInvalid
	}
	observed, err := result.Request.Digest()
	if err != nil || observed != h {
		return ErrInvalid
	}
	if result.Status == Rejected {
		if result.AcceptedAt != nil || result.AvailableAt != nil || result.SettledAt != nil || result.RejectionCode == "" || !result.AbsenceIsFinal || result.Version != 1 || result.Position != nil || result.PrincipalReturn != nil || len(result.Postings) != 0 {
			return ErrInvalid
		}
		return nil
	}
	if result.Status != Accepted && result.Status != Settled || result.AcceptedAt == nil || result.AvailableAt == nil || result.RejectionCode != "" || result.AbsenceIsFinal {
		return ErrInvalid
	}
	available, err := Availability(original, result.Position, *result.AcceptedAt)
	if err != nil || !available.Equal(*result.AvailableAt) {
		return ErrInvalid
	}
	asset := original.Effect.Kind == Purchase || original.Effect.Kind == Redeem || original.Effect.Kind == Mature
	if asset && result.Position == nil || !asset && result.Position != nil {
		return ErrInvalid
	}
	if original.Effect.Kind == Purchase {
		expected, err := OpenedPosition(original, *result.AcceptedAt)
		if err != nil || !equalJSON(expected.canonical(), result.Position.canonical()) {
			return ErrInvalid
		}
	}
	if result.Status == Accepted {
		if result.Version != 1 || result.SettledAt != nil || result.PrincipalReturn != nil || len(result.Postings) != 0 {
			return ErrInvalid
		}
		return nil
	}
	if result.Version != 2 || result.SettledAt == nil || !result.SettledAt.Equal(*result.AvailableAt) {
		return ErrInvalid
	}
	postings, err := ExpectedPostings(original, *result.SettledAt)
	if err != nil || len(postings) != len(result.Postings) {
		return ErrInvalid
	}
	for i, want := range postings {
		got := result.Postings[i]
		if got.ID != want.ID || got.Dimension != want.Dimension || got.LedgerID != want.LedgerID || got.Direction != want.Direction || got.Amount != want.Amount || !got.OccurredAt.Equal(want.OccurredAt) {
			return ErrInvalid
		}
	}
	balances := map[string]*big.Int{}
	seen := map[string]bool{}
	for _, p := range result.Postings {
		if seen[p.ID] {
			return ErrInvalid
		}
		seen[p.ID] = true
		if balances[p.Dimension] == nil {
			balances[p.Dimension] = new(big.Int)
		}
		delta := p.Amount.Cents()
		if p.Direction == "CREDIT" {
			delta = -delta
		}
		balances[p.Dimension].Add(balances[p.Dimension], big.NewInt(delta))
	}
	for _, sum := range balances {
		if sum.Sign() != 0 {
			return ErrInvalid
		}
	}
	if original.Effect.Kind == Redeem || original.Effect.Kind == Mature {
		e := original.Effect
		expected := PrincipalReturn{Role: "PRINCIPAL_RETURN", PositionID: e.PositionID, PositionOpeningHash: e.PositionOpeningHash, OperationKey: original.Key, RequestHash: h, AccountID: e.ReturnAccountID, GoalID: e.SourceGoalID, Amount: e.Net, OccurredAt: result.SettledAt.UTC()}
		if result.PrincipalReturn == nil {
			return ErrInvalid
		}
		actual := *result.PrincipalReturn
		if !actual.OccurredAt.Equal(expected.OccurredAt) {
			return ErrInvalid
		}
		actual.OccurredAt = expected.OccurredAt
		if actual != expected {
			return ErrInvalid
		}
	} else if result.PrincipalReturn != nil {
		return ErrInvalid
	}
	return nil
}
