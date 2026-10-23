// Package domain contains deterministic policy and permission rules. A source
// object is supplied by a trusted application boundary; it is not authentication.
package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const Protocol = "single-goal-policy-v1"

type Status string

const (
	Active  Status = "ACTIVE"
	Paused  Status = "PAUSED"
	Revoked Status = "REVOKED"
)

var (
	ErrPolicy       = errors.New("invalid or inactive policy")
	ErrConfirmation = errors.New("confirmation source does not bind the reviewed object")
	ErrGrant        = errors.New("grant does not cover the exact policy and effect")
)

// Scope is complete for the first local simulated ALLOCATE_GOAL chain.
// Account and goal ownership must be established before entering the domain.
type Scope struct {
	EnvironmentID        funds.ID       `json:"environment_id"`
	UserID               funds.ID       `json:"user_id"`
	GoalID               funds.ID       `json:"goal_id"`
	SourceAccountID      funds.ID       `json:"source_account_id"`
	DestinationAccountID funds.ID       `json:"destination_account_id"`
	Currency             funds.Currency `json:"currency"`
}

func (s Scope) Validate() error {
	if s.EnvironmentID == (funds.ID{}) || s.UserID == (funds.ID{}) || s.GoalID == (funds.ID{}) || s.SourceAccountID == (funds.ID{}) || s.DestinationAccountID == (funds.ID{}) {
		return ErrPolicy
	}
	return s.Currency.Validate()
}

// ConfirmationSource records a server-authenticated human review. Setting
// PrincipalKind in untrusted JSON cannot make this object authoritative. HTTP
// must discard client source objects, authenticate USER, and load/write the
// immutable source in its transaction. A model cannot create this source.
type ConfirmationSource struct {
	ID            funds.ID  `json:"id"`
	PrincipalKind string    `json:"principal_kind"`
	EnvironmentID funds.ID  `json:"environment_id"`
	UserID        funds.ID  `json:"user_id"`
	TargetKind    string    `json:"target_kind"`
	TargetID      funds.ID  `json:"target_id"`
	TargetVersion uint64    `json:"target_version"`
	ReviewedHash  string    `json:"reviewed_hash"`
	ConfirmedAt   time.Time `json:"confirmed_at"`
	// ExpiresAt applies to a single-use confirmation at command acceptance.
	// Policy and standing-grant sources are historical attestations; their
	// ongoing permission uses the separately frozen policy/grant window.
	ExpiresAt time.Time `json:"expires_at,omitempty"`
}

func (s ConfirmationSource) Check(scope Scope, kind string, id funds.ID, version uint64, hash string, now time.Time) error {
	if s.ID == (funds.ID{}) || s.PrincipalKind != "USER" || s.EnvironmentID != scope.EnvironmentID || s.UserID != scope.UserID || s.TargetKind != kind || s.TargetID != id || s.TargetVersion != version || s.ReviewedHash != hash || !validHash(hash) || s.ConfirmedAt.IsZero() || now.IsZero() || s.ConfirmedAt.After(now) {
		return ErrConfirmation
	}
	return nil
}

// Policy is a persisted immutable version. Status/current-version identity
// comes from locked application state and must be reloaded before submission.
type Policy struct {
	ID            funds.ID           `json:"id"`
	Version       uint64             `json:"version"`
	Scope         Scope              `json:"scope"`
	MaxAmount     funds.Money        `json:"max_amount"`
	MonthlyMin    funds.Money        `json:"monthly_min"`
	MonthlyTarget funds.Money        `json:"monthly_target"`
	MonthlyMax    funds.Money        `json:"monthly_max"`
	Timezone      string             `json:"timezone,omitempty"`
	Deadline      string             `json:"deadline,omitempty"`
	ValidFrom     time.Time          `json:"valid_from"`
	ValidUntil    time.Time          `json:"valid_until"`
	Status        Status             `json:"status"`
	Confirmation  ConfirmationSource `json:"confirmation"`
}

func digest(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func validHash(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

// ConfigurationHash covers all economic configuration; lifecycle state is
// checked separately and never rewrites the immutable reviewed version.
func (p Policy) ConfigurationHash() string {
	return digest(struct {
		Protocol                                         string
		ID                                               funds.ID
		Version                                          uint64
		Scope                                            Scope
		MaxAmount, MonthlyMin, MonthlyTarget, MonthlyMax funds.Money
		Timezone, Deadline                               string
		ValidFrom, ValidUntil                            string
	}{Protocol, p.ID, p.Version, p.Scope, p.MaxAmount, p.MonthlyMin, p.MonthlyTarget, p.MonthlyMax, p.Timezone, p.Deadline, instant(p.ValidFrom), instant(p.ValidUntil)})
}

func instant(t time.Time) string { return t.UTC().Format(time.RFC3339Nano) }

func (p Policy) EffectiveFrom(now time.Time) (time.Time, error) {
	if err := p.Scope.Validate(); err != nil {
		return time.Time{}, err
	}
	if p.ID == (funds.ID{}) || p.Version == 0 || p.MaxAmount.IsZero() || p.Status != Active || p.ValidFrom.IsZero() || p.ValidUntil.IsZero() || !p.ValidUntil.After(p.ValidFrom) || !now.Before(p.ValidUntil) {
		return time.Time{}, ErrPolicy
	}
	if p.MonthlyMin.Cents() > p.MonthlyTarget.Cents() || p.MonthlyTarget.Cents() > p.MonthlyMax.Cents() {
		return time.Time{}, ErrPolicy
	}
	monthly := !p.MonthlyMin.IsZero() || !p.MonthlyTarget.IsZero() || !p.MonthlyMax.IsZero()
	if monthly || p.Deadline != "" || p.Timezone != "" {
		zone, err := p.BusinessLocation()
		if err != nil {
			return time.Time{}, err
		}
		if p.Deadline != "" {
			date, err := time.Parse("2006-01-02", p.Deadline)
			if err != nil || date.Format("2006-01-02") != p.Deadline || now.In(zone).Format("2006-01-02") > p.Deadline {
				return time.Time{}, ErrPolicy
			}
		}
	}
	if err := p.Confirmation.Check(p.Scope, "POLICY", p.ID, p.Version, p.ConfigurationHash(), now); err != nil {
		return time.Time{}, err
	}
	start := p.ValidFrom
	if p.Confirmation.ConfirmedAt.After(start) {
		start = p.Confirmation.ConfirmedAt
	}
	if now.Before(start) {
		return time.Time{}, ErrPolicy
	}
	return start, nil
}

// BusinessLocation supports the existing explicit business-day contracts
// without consulting process environment or the host time zone database.
func (p Policy) BusinessLocation() (*time.Location, error) {
	switch p.Timezone {
	case "UTC":
		return time.UTC, nil
	case "Asia/Shanghai":
		return time.FixedZone("Asia/Shanghai", 8*3600), nil
	default:
		return nil, ErrPolicy
	}
}

type Grant struct {
	ID            funds.ID           `json:"id"`
	Version       uint64             `json:"version"`
	PolicyID      funds.ID           `json:"policy_id"`
	PolicyVersion uint64             `json:"policy_version"`
	Scope         Scope              `json:"scope"`
	MaxAmount     funds.Money        `json:"max_amount"`
	EffectiveFrom time.Time          `json:"effective_from"`
	ValidUntil    time.Time          `json:"valid_until"`
	Status        Status             `json:"status"`
	Confirmation  ConfirmationSource `json:"confirmation"`
}

func (g Grant) ConfigurationHash() string {
	return digest(struct {
		Protocol                  string
		ID                        funds.ID
		Version                   uint64
		PolicyID                  funds.ID
		PolicyVersion             uint64
		Scope                     Scope
		MaxAmount                 funds.Money
		EffectiveFrom, ValidUntil string
	}{Protocol, g.ID, g.Version, g.PolicyID, g.PolicyVersion, g.Scope, g.MaxAmount, instant(g.EffectiveFrom), instant(g.ValidUntil)})
}

// Boundary is necessary permission at one explicit instant, not a durable
// submission permit. The application must check the locked version vector and
// reserve funds atomically, then obtain a submission permit before bank I/O.
type Boundary struct {
	EffectiveFrom time.Time   `json:"effective_from"`
	ValidUntil    time.Time   `json:"valid_until"`
	MaxAmount     funds.Money `json:"max_amount"`
}

func CheckGrant(p Policy, g Grant, now time.Time) (Boundary, error) {
	start, err := p.EffectiveFrom(now)
	if err != nil {
		return Boundary{}, err
	}
	if g.ID == (funds.ID{}) || g.Version == 0 || g.PolicyID != p.ID || g.PolicyVersion != p.Version || g.Scope != p.Scope || g.MaxAmount.IsZero() || g.Status != Active || g.EffectiveFrom.IsZero() || g.ValidUntil.IsZero() || !g.ValidUntil.After(g.EffectiveFrom) || !now.Before(g.ValidUntil) {
		return Boundary{}, ErrGrant
	}
	if err := g.Confirmation.Check(g.Scope, "GRANT", g.ID, g.Version, g.ConfigurationHash(), now); err != nil {
		return Boundary{}, err
	}
	if g.EffectiveFrom.After(start) {
		start = g.EffectiveFrom
	}
	if g.Confirmation.ConfirmedAt.After(start) {
		start = g.Confirmation.ConfirmedAt
	}
	if now.Before(start) {
		return Boundary{}, ErrGrant
	}
	end := p.ValidUntil
	if g.ValidUntil.Before(end) {
		end = g.ValidUntil
	}
	maximum := p.MaxAmount
	if g.MaxAmount.Cents() < maximum.Cents() {
		maximum = g.MaxAmount
	}
	return Boundary{start, end, maximum}, nil
}
