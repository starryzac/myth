// Package bankv2 defines the independent multi-asset simulated-bank protocol.
// No v1 request, digest domain, golden vector or original is reinterpreted.
package bankv2

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const Protocol = "zhiyu-bank-v2"
const ProductProtocol = "zhiyu-bank-product-v2"
const AvailabilityProtocol = "zhiyu-bank-availability-v2"
const MaxUses = 100
const MaxAuthorities = 8
const MaxRequestBytes = 128 << 10

type Kind string

const (
	Transfer Kind = "TRANSFER_INTERNAL"
	Allocate Kind = "ALLOCATE_GOAL"
	MoveGoal Kind = "TRANSFER_GOAL"
	Purchase Kind = "PURCHASE_ASSET"
	Redeem   Kind = "REDEEM_ASSET"
	Mature   Kind = "MATURE_ASSET"
	Payment  Kind = "PAY_RECURRING"
)

var ErrInvalid = errors.New("invalid bank v2 contract")
var identity = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
var namespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)
var period = regexp.MustCompile(`^[0-9]{4}-(0[1-9]|1[0-2])$`)

func hash(domain string, b []byte) string {
	sum := sha256.Sum256(append([]byte(domain+"\x00"), b...))
	return hex.EncodeToString(sum[:])
}
func digest(v any, domain string) (string, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return hash(domain, raw), nil
}
func isHash(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'f' || c >= '0' && c <= '9') {
			return false
		}
	}
	return true
}
func validIDs(values ...string) bool {
	for _, value := range values {
		if !identity.MatchString(value) {
			return false
		}
	}
	return true
}

// ProductTerms is immutable at one version. Availability has a separate version
// and cannot change the contract of an already purchased position.
type ProductTerms struct {
	Protocol            string      `json:"protocol"`
	ProductID           string      `json:"product_id"`
	Version             int64       `json:"version"`
	AssetClass          string      `json:"asset_class"`
	MinimumPurchase     funds.Money `json:"minimum_purchase_cents"`
	TermDays            int         `json:"term_days"`
	LockDays            int         `json:"lock_days"`
	RedemptionDelayDays int         `json:"redemption_delay_days"`
	RedemptionFeeBPS    int64       `json:"redemption_fee_bps"`
	EarlyLossBPS        int64       `json:"early_loss_bps"`
	PrincipalGuaranteed bool        `json:"principal_guaranteed"`
	AutoRollover        bool        `json:"auto_rollover"`
}

func (p ProductTerms) Validate() error {
	if p.Protocol != ProductProtocol || !validIDs(p.ProductID) || p.Version < 1 || p.MinimumPurchase.IsZero() || !p.PrincipalGuaranteed || p.AutoRollover || p.LockDays < 0 || p.LockDays > 3660 || p.RedemptionDelayDays < 0 || p.RedemptionDelayDays > 1 || p.RedemptionFeeBPS < 0 || p.RedemptionFeeBPS > 10000 || p.EarlyLossBPS < 0 || p.EarlyLossBPS > 10000 || p.RedemptionFeeBPS+p.EarlyLossBPS > 10000 {
		return ErrInvalid
	}
	switch p.AssetClass {
	case "CASH_MGMT_T0":
		if p.TermDays != 0 || p.RedemptionDelayDays != 0 {
			return ErrInvalid
		}
	case "CASH_MGMT_T1":
		if p.TermDays != 0 || p.RedemptionDelayDays != 1 {
			return ErrInvalid
		}
	case "FIXED_DEPOSIT":
		if p.TermDays < 1 || p.TermDays > 3660 || p.LockDays > p.TermDays {
			return ErrInvalid
		}
	default:
		return ErrInvalid
	}
	return nil
}
func (p ProductTerms) Digest() (string, error) {
	if err := p.Validate(); err != nil {
		return "", err
	}
	return digest(p, ProductProtocol)
}

type ProductAvailability struct {
	Protocol       string    `json:"protocol"`
	ProductID      string    `json:"product_id"`
	Version        int64     `json:"version"`
	TermsVersion   int64     `json:"terms_version"`
	TermsHash      string    `json:"terms_hash"`
	Status         string    `json:"status"`
	EffectiveFrom  time.Time `json:"effective_from"`
	EffectiveUntil time.Time `json:"effective_until"`
}

func (a ProductAvailability) Validate() error {
	if a.Protocol != AvailabilityProtocol || !validIDs(a.ProductID) || a.Version < 1 || a.TermsVersion < 1 || !isHash(a.TermsHash) || (a.Status != "AVAILABLE" && a.Status != "UNAVAILABLE") || a.EffectiveFrom.IsZero() || !a.EffectiveUntil.After(a.EffectiveFrom) {
		return ErrInvalid
	}
	return nil
}
func (a ProductAvailability) Digest() (string, error) {
	if a.Validate() != nil {
		return "", ErrInvalid
	}
	a.EffectiveFrom = a.EffectiveFrom.UTC()
	a.EffectiveUntil = a.EffectiveUntil.UTC()
	return digest(a, AvailabilityProtocol)
}

type ProductSnapshot struct {
	Terms            ProductTerms        `json:"terms"`
	TermsHash        string              `json:"terms_hash"`
	Availability     ProductAvailability `json:"availability"`
	AvailabilityHash string              `json:"availability_hash"`
}

func (p ProductSnapshot) Validate() error {
	h, err := p.Terms.Digest()
	a, ae := p.Availability.Digest()
	if err != nil || ae != nil || h != p.TermsHash || a != p.AvailabilityHash || p.Availability.ProductID != p.Terms.ProductID || p.Availability.TermsVersion != p.Terms.Version || p.Availability.TermsHash != h {
		return ErrInvalid
	}
	return nil
}

type CashUse struct {
	AccountID string      `json:"account_id"`
	Amount    funds.Money `json:"amount_cents"`
}

// OriginUse always names an existing income identity. ASSIGNED_GOAL purchases
// preserve qualification; AVAILABLE purchases consume qualification exactly once.
type OriginUse struct {
	OriginID  string      `json:"origin_id"`
	AccountID string      `json:"account_id"`
	Amount    funds.Money `json:"amount_cents"`
	Bucket    string      `json:"bucket"`
}
type ReturnUse struct {
	OperationKey string      `json:"operation_key"`
	PositionID   string      `json:"position_id"`
	AccountID    string      `json:"account_id"`
	Amount       funds.Money `json:"amount_cents"`
}
type LiabilityReference struct {
	ID           string      `json:"id"`
	Kind         string      `json:"kind"`
	PolicyID     string      `json:"policy_id"`
	Period       string      `json:"period"`
	FinalTotal   funds.Money `json:"final_total_cents"`
	PayeeID      string      `json:"payee_id"`
	EvidenceHash string      `json:"evidence_hash"`
}

func (l LiabilityReference) Validate() error {
	if !validIDs(l.ID, l.PayeeID) || !isHash(l.EvidenceHash) || l.FinalTotal.IsZero() {
		return ErrInvalid
	}
	if l.Kind == "BILL" {
		if l.PolicyID != "" || l.Period != "" {
			return ErrInvalid
		}
	} else if l.Kind == "OCCURRENCE" {
		if !validIDs(l.PolicyID) || !period.MatchString(l.Period) {
			return ErrInvalid
		}
	} else {
		return ErrInvalid
	}
	return nil
}

// Effect contains only frozen economics. Its digest can be reviewed before a
// USER cost confirmation exists, avoiding a circular signature/confirmation hash.
type Effect struct {
	Kind                 Kind                `json:"kind"`
	BusinessKey          string              `json:"business_key"`
	Amount               funds.Money         `json:"amount_cents"`
	CashUses             []CashUse           `json:"cash_uses"`
	OriginUses           []OriginUse         `json:"origin_uses"`
	ReturnUses           []ReturnUse         `json:"return_uses"`
	DestinationAccountID string              `json:"destination_account_id"`
	SourceGoalID         string              `json:"source_goal_id"`
	DestinationGoalID    string              `json:"destination_goal_id"`
	Product              *ProductSnapshot    `json:"product"`
	PositionID           string              `json:"position_id"`
	PositionOpeningHash  string              `json:"position_opening_hash"`
	ReturnAccountID      string              `json:"return_account_id"`
	PrincipalAvailableBy time.Time           `json:"principal_available_by"`
	PlannedRedeemAt      time.Time           `json:"planned_redeem_at"`
	Fee                  funds.Money         `json:"fee_cents"`
	Loss                 funds.Money         `json:"loss_cents"`
	Net                  funds.Money         `json:"net_cents"`
	QuoteID              string              `json:"quote_id"`
	Liability            *LiabilityReference `json:"liability"`
	LatestArrivalAt      time.Time           `json:"latest_arrival_at"`
}

func (e Effect) Validate() error {
	if !validIDs(e.BusinessKey) || e.Amount.IsZero() || e.LatestArrivalAt.IsZero() || len(e.CashUses) > MaxUses || len(e.OriginUses) > MaxUses || len(e.ReturnUses) > MaxUses || e.CashUses == nil || e.OriginUses == nil || e.ReturnUses == nil {
		return ErrInvalid
	}
	funding := map[string]funds.Money{}
	var cashTotal funds.Money
	for _, use := range e.CashUses {
		if !validIDs(use.AccountID) || use.Amount.IsZero() || !funding[use.AccountID].IsZero() {
			return ErrInvalid
		}
		funding[use.AccountID] = use.Amount
		var err error
		cashTotal, err = cashTotal.Add(use.Amount)
		if err != nil {
			return err
		}
	}
	classified := map[string]funds.Money{}
	seen := map[string]bool{}
	for _, use := range e.OriginUses {
		key := "origin:" + use.OriginID + ":" + use.AccountID
		if !validIDs(use.OriginID, use.AccountID) || use.Amount.IsZero() || seen[key] || (use.Bucket != "AVAILABLE" && use.Bucket != "ASSIGNED_GOAL") {
			return ErrInvalid
		}
		seen[key] = true
		var err error
		classified[use.AccountID], err = classified[use.AccountID].Add(use.Amount)
		if err != nil {
			return err
		}
		if use.Bucket == "ASSIGNED_GOAL" && ((e.Kind != Purchase && e.Kind != MoveGoal) || e.SourceGoalID == "") {
			return ErrInvalid
		}
	}
	for _, use := range e.ReturnUses {
		key := "return:" + use.OperationKey + ":" + use.AccountID
		if !validIDs(use.OperationKey, use.PositionID, use.AccountID) || use.Amount.IsZero() || seen[key] {
			return ErrInvalid
		}
		seen[key] = true
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
	asset := e.Kind == Purchase || e.Kind == Redeem || e.Kind == Mature
	if asset {
		if e.Product == nil || e.Product.Validate() != nil || !validIDs(e.PositionID, e.ReturnAccountID) {
			return ErrInvalid
		}
	} else if e.Product != nil || e.PositionID != "" || e.PositionOpeningHash != "" || e.ReturnAccountID != "" || !e.PrincipalAvailableBy.IsZero() || !e.PlannedRedeemAt.IsZero() || e.QuoteID != "" {
		return ErrInvalid
	}
	if e.Kind != Redeem && (!e.Fee.IsZero() || !e.Loss.IsZero()) {
		return ErrInvalid
	}
	if e.Kind != Payment && e.Liability != nil {
		return ErrInvalid
	}
	switch e.Kind {
	case Transfer:
		if cashTotal != e.Amount || len(e.CashUses) != 1 || !validIDs(e.DestinationAccountID) || e.CashUses[0].AccountID == e.DestinationAccountID || e.SourceGoalID != "" || e.DestinationGoalID != "" || !e.Net.IsZero() {
			return ErrInvalid
		}
	case Allocate:
		if cashTotal != e.Amount || len(e.CashUses) != 1 || !validIDs(e.DestinationGoalID) || e.SourceGoalID != "" || e.DestinationAccountID != "" || !e.Net.IsZero() {
			return ErrInvalid
		}
		if classified[e.CashUses[0].AccountID] != e.Amount {
			return ErrInvalid
		}
	case MoveGoal:
		if cashTotal != e.Amount || len(e.CashUses) != 1 || !validIDs(e.SourceGoalID, e.DestinationGoalID, e.DestinationAccountID) || e.SourceGoalID == e.DestinationGoalID || !e.Net.IsZero() || classified[e.CashUses[0].AccountID] != e.Amount {
			return ErrInvalid
		}
		for _, use := range e.OriginUses {
			if use.Bucket != "ASSIGNED_GOAL" {
				return ErrInvalid
			}
		}
	case Purchase:
		if cashTotal != e.Amount || e.DestinationAccountID != "" || e.DestinationGoalID != "" || e.PositionOpeningHash != "" || e.PrincipalAvailableBy.IsZero() || !e.Net.IsZero() || e.QuoteID != "" || funding[e.ReturnAccountID].IsZero() || e.Amount.Cents() < e.Product.Terms.MinimumPurchase.Cents() {
			return ErrInvalid
		}
		if e.SourceGoalID != "" {
			if !validIDs(e.SourceGoalID) || len(e.CashUses) != 1 {
				return ErrInvalid
			}
			for _, use := range e.OriginUses {
				if use.Bucket != "ASSIGNED_GOAL" {
					return ErrInvalid
				}
			}
			if classified[e.CashUses[0].AccountID] != e.Amount {
				return ErrInvalid
			}
		}
		if e.Product.Terms.AssetClass == "FIXED_DEPOSIT" {
			if !e.PlannedRedeemAt.IsZero() {
				return ErrInvalid
			}
		} else if e.PlannedRedeemAt.IsZero() || e.PrincipalAvailableBy.Before(e.PlannedRedeemAt.UTC().AddDate(0, 0, e.Product.Terms.RedemptionDelayDays)) {
			return ErrInvalid
		}
	case Redeem, Mature:
		if len(e.CashUses) != 0 || len(e.OriginUses) != 0 || len(e.ReturnUses) != 0 || !validIDs(e.DestinationAccountID, e.QuoteID) || !isHash(e.PositionOpeningHash) || e.DestinationAccountID != e.ReturnAccountID || e.DestinationGoalID != "" || !e.PrincipalAvailableBy.IsZero() || !e.PlannedRedeemAt.IsZero() {
			return ErrInvalid
		}
		total, err := funds.SumMoney(e.Net, e.Fee, e.Loss)
		if err != nil || total != e.Amount {
			return ErrInvalid
		}
		if e.Kind == Mature && (e.Product.Terms.AssetClass != "FIXED_DEPOSIT" || !e.Fee.IsZero() || !e.Loss.IsZero()) {
			return ErrInvalid
		}
	case Payment:
		if cashTotal != e.Amount || e.Liability == nil || e.Liability.Validate() != nil || e.SourceGoalID != "" || e.DestinationGoalID != "" || e.DestinationAccountID != "" || !e.Net.IsZero() {
			return ErrInvalid
		}
	default:
		return ErrInvalid
	}
	return nil
}
func (e Effect) canonical() Effect {
	e.LatestArrivalAt = e.LatestArrivalAt.UTC()
	if !e.PrincipalAvailableBy.IsZero() {
		e.PrincipalAvailableBy = e.PrincipalAvailableBy.UTC()
	}
	if !e.PlannedRedeemAt.IsZero() {
		e.PlannedRedeemAt = e.PlannedRedeemAt.UTC()
	}
	if e.Product != nil {
		p := *e.Product
		p.Availability.EffectiveFrom = p.Availability.EffectiveFrom.UTC()
		p.Availability.EffectiveUntil = p.Availability.EffectiveUntil.UTC()
		e.Product = &p
	}
	return e
}
func (e Effect) Digest() (string, error) {
	if err := e.Validate(); err != nil {
		return "", err
	}
	return digest(e.canonical(), Protocol+"/effect")
}

// Authority is a scoped reference installed by a trusted application T2. USER
// text alone is never accepted: the full Request is authenticated by a separate
// v2 HMAC domain and the application retains the actual authorization evidence.
type Authority struct {
	Role          string `json:"role"`
	SourceID      string `json:"source_id"`
	Version       int64  `json:"version"`
	GoalID        string `json:"goal_id"`
	PolicyID      string `json:"policy_id"`
	PolicyVersion int64  `json:"policy_version"`
	AllowsFee     bool   `json:"allows_fee"`
	AllowsLoss    bool   `json:"allows_loss"`
}
type CostConsent struct {
	ConfirmationID     string    `json:"confirmation_id"`
	ReviewedEffectHash string    `json:"reviewed_effect_hash"`
	PrincipalKind      string    `json:"principal_kind"`
	ConfirmedAt        time.Time `json:"confirmed_at"`
	ExpiresAt          time.Time `json:"expires_at"`
}
type Request struct {
	Protocol      string       `json:"protocol"`
	Namespace     string       `json:"namespace"`
	Key           string       `json:"key"`
	OperationID   string       `json:"operation_id"`
	UserID        string       `json:"user_id"`
	PermitID      string       `json:"permit_id"`
	ReferenceHash string       `json:"reference_hash"`
	RequestedAt   time.Time    `json:"requested_at"`
	ExpiresAt     time.Time    `json:"expires_at"`
	Effect        Effect       `json:"effect"`
	Authorities   []Authority  `json:"authorities"`
	CostConsent   *CostConsent `json:"cost_consent"`
}

func (r Request) Validate() error {
	if r.Protocol != Protocol || !namespace.MatchString(r.Namespace) || !validIDs(r.Key, r.OperationID, r.UserID, r.PermitID) || !isHash(r.ReferenceHash) || r.RequestedAt.IsZero() || !r.ExpiresAt.After(r.RequestedAt) || r.Effect.LatestArrivalAt.Before(r.RequestedAt) || r.Effect.Validate() != nil || len(r.Authorities) < 1 || len(r.Authorities) > MaxAuthorities {
		return ErrInvalid
	}
	roles := map[string]Authority{}
	for _, a := range r.Authorities {
		if !validIDs(a.SourceID, a.PolicyID) || a.Version < 1 || a.PolicyVersion < 1 || roles[a.Role].Role != "" {
			return ErrInvalid
		}
		switch a.Role {
		case "EXECUTE", "GOAL_RELEASE", "GOAL_RECEIVE", "POSITION_ORIGINAL":
		default:
			return ErrInvalid
		}
		if a.GoalID != "" && !validIDs(a.GoalID) {
			return ErrInvalid
		}
		roles[a.Role] = a
	}
	if roles["EXECUTE"].Role == "" {
		return ErrInvalid
	}
	if r.Effect.Kind == Payment && r.Effect.Liability.Kind == "OCCURRENCE" && roles["EXECUTE"].PolicyID != r.Effect.Liability.PolicyID {
		return ErrInvalid
	}
	if (r.Effect.Kind == Redeem || r.Effect.Kind == Mature) && roles["POSITION_ORIGINAL"].Role == "" {
		return ErrInvalid
	}
	if r.Effect.Kind == MoveGoal && (roles["GOAL_RELEASE"].GoalID != r.Effect.SourceGoalID || roles["GOAL_RECEIVE"].GoalID != r.Effect.DestinationGoalID) {
		return ErrInvalid
	}
	if r.Effect.SourceGoalID != "" && r.Effect.Kind != MoveGoal && roles["EXECUTE"].GoalID != r.Effect.SourceGoalID {
		return ErrInvalid
	}
	if r.Effect.DestinationGoalID != "" && r.Effect.Kind != MoveGoal && roles["EXECUTE"].GoalID != r.Effect.DestinationGoalID {
		return ErrInvalid
	}
	cost := !r.Effect.Fee.IsZero() || !r.Effect.Loss.IsZero()
	if cost {
		h, _ := r.Effect.Digest()
		c := r.CostConsent
		if c == nil || !validIDs(c.ConfirmationID) || c.PrincipalKind != "USER" || c.ReviewedEffectHash != h || c.ConfirmedAt.IsZero() || c.ConfirmedAt.After(r.RequestedAt) || !c.ExpiresAt.After(r.RequestedAt) {
			return ErrInvalid
		}
		for _, role := range []string{"EXECUTE", "POSITION_ORIGINAL"} {
			a := roles[role]
			if a.Role == "" || !r.Effect.Fee.IsZero() && !a.AllowsFee || !r.Effect.Loss.IsZero() && !a.AllowsLoss {
				return ErrInvalid
			}
		}
	} else if r.CostConsent != nil {
		return ErrInvalid
	}
	return nil
}
func (r Request) CanonicalBytes() ([]byte, error) {
	if err := r.Validate(); err != nil {
		return nil, err
	}
	r.RequestedAt = r.RequestedAt.UTC()
	r.ExpiresAt = r.ExpiresAt.UTC()
	r.Effect = r.Effect.canonical()
	if r.CostConsent != nil {
		c := *r.CostConsent
		c.ConfirmedAt = c.ConfirmedAt.UTC()
		c.ExpiresAt = c.ExpiresAt.UTC()
		r.CostConsent = &c
	}
	raw, err := json.Marshal(r)
	if err != nil {
		return nil, err
	}
	if len(raw) > MaxRequestBytes {
		return nil, ErrInvalid
	}
	return raw, nil
}
func (r Request) Digest() (string, error) {
	b, err := r.CanonicalBytes()
	if err != nil {
		return "", err
	}
	return hash(Protocol, b), nil
}
func SignPermit(r Request, secret []byte) (string, error) {
	if len(secret) < 32 {
		return "", ErrInvalid
	}
	b, err := r.CanonicalBytes()
	if err != nil {
		return "", err
	}
	h := hmac.New(sha256.New, secret)
	h.Write([]byte(Protocol + "/permit\x00"))
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil)), nil
}
func VerifyPermit(r Request, signature string, secret []byte) bool {
	want, err := SignPermit(r, secret)
	if err != nil {
		return false
	}
	a, e := hex.DecodeString(signature)
	b, _ := hex.DecodeString(want)
	return e == nil && hmac.Equal(a, b)
}

// RedemptionQuote is derived from an independently held whole position at the
// actual bank instant. Net never appears as a new income credit.
func RedemptionQuote(principal funds.Money, terms ProductTerms, purchasedAt, now time.Time, maturity bool) (net, fee, loss funds.Money, available time.Time, err error) {
	if terms.Validate() != nil || principal.IsZero() || purchasedAt.IsZero() || now.Before(purchasedAt) {
		err = ErrInvalid
		return
	}
	if maturity {
		if terms.AssetClass != "FIXED_DEPOSIT" || now.Before(purchasedAt.UTC().AddDate(0, 0, terms.TermDays)) {
			err = fmt.Errorf("%w: contractual maturity not due", ErrInvalid)
			return
		}
		available = purchasedAt.UTC().AddDate(0, 0, terms.TermDays+terms.RedemptionDelayDays)
		net = principal
		return
	}
	if terms.AssetClass != "FIXED_DEPOSIT" && now.Before(purchasedAt.UTC().AddDate(0, 0, terms.LockDays)) {
		err = fmt.Errorf("%w: locked principal", ErrInvalid)
		return
	}
	fee, err = principal.MulRatio(terms.RedemptionFeeBPS, 10000, funds.RoundUp)
	if err != nil {
		return
	}
	if terms.AssetClass == "FIXED_DEPOSIT" && now.Before(purchasedAt.UTC().AddDate(0, 0, terms.TermDays)) {
		loss, err = principal.MulRatio(terms.EarlyLossBPS, 10000, funds.RoundUp)
		if err != nil {
			return
		}
	}
	cost, e := fee.Add(loss)
	if e != nil {
		err = e
		return
	}
	net, err = principal.Sub(cost)
	available = now.UTC().AddDate(0, 0, terms.RedemptionDelayDays)
	return
}
