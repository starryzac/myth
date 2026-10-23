package domain

import (
	"errors"
	"sort"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const NativeStandingProtocol = "native-standing-authority-v1"

type NativeFamily string

const (
	NativeSingleGoal       NativeFamily = "SINGLE_GOAL"
	NativeJointGoals       NativeFamily = "JOINT_GOALS"
	NativeAssetAllocation  NativeFamily = "ASSET_ALLOCATION"
	NativeMaturityReinvest NativeFamily = "MATURITY_REINVEST"
	NativePeriodicPayment  NativeFamily = "PERIODIC_PAYMENT"
)

var ErrNativeStanding = errors.New("NATIVE_STANDING_AUTHORITY_INVALID")
var ErrNativeStandingCap = errors.New("NATIVE_STANDING_CAP_EXCEEDED")

func (f NativeFamily) Valid() bool {
	switch f {
	case NativeSingleGoal, NativeJointGoals, NativeAssetAllocation, NativeMaturityReinvest, NativePeriodicPayment:
		return true
	}
	return false
}

// NativePolicyReference pins immutable configuration, not a convenient current
// replacement. The application reloads current policy/status before T1 and T2.
type NativePolicyReference struct {
	PolicyID          funds.ID     `json:"policy_id"`
	VersionID         funds.ID     `json:"version_id"`
	Version           uint64       `json:"version"`
	Template          TemplateName `json:"template_name"`
	ConfigurationHash string       `json:"configuration_hash"`
	GoalID            *funds.ID    `json:"goal_id,omitempty"`
}

type NativeStandingTerms struct {
	Protocol      string                  `json:"protocol"`
	Scope         TemplateOwnerScope      `json:"scope"`
	Family        NativeFamily            `json:"family"`
	Policies      []NativePolicyReference `json:"policies"`
	AccountIDs    []funds.ID              `json:"account_ids"`
	GoalIDs       []funds.ID              `json:"goal_ids"`
	ProductIDs    []string                `json:"product_ids"`
	PayeeIDs      []string                `json:"payee_ids"`
	SingleCap     funds.Money             `json:"single_cap_cents"`
	LifetimeCap   funds.Money             `json:"lifetime_cap_cents"`
	MonthlyCap    funds.Money             `json:"monthly_cap_cents"`
	Timezone      string                  `json:"timezone"`
	EffectiveFrom time.Time               `json:"effective_from"`
	ExpiresAt     time.Time               `json:"expires_at"`
}

func nativeSortedIDs(ids []funds.ID, maximum int) bool {
	if len(ids) > maximum {
		return false
	}
	last := ""
	for _, id := range ids {
		if id == (funds.ID{}) || id.String() <= last {
			return false
		}
		last = id.String()
	}
	return true
}

func nativeSortedStrings(ids []string, maximum int) bool {
	if len(ids) > maximum {
		return false
	}
	last := ""
	for _, id := range ids {
		if len(id) == 0 || len(id) > 128 || id <= last {
			return false
		}
		for _, c := range id {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-' || c == '.' || c == ':') {
				return false
			}
		}
		last = id
	}
	return true
}

func (t NativeStandingTerms) Validate() error {
	if t.Protocol != NativeStandingProtocol || !t.Family.Valid() || t.Scope.EnvironmentID == (funds.ID{}) || t.Scope.UserID == (funds.ID{}) || len(t.Policies) == 0 || len(t.Policies) > 16 || len(t.AccountIDs) == 0 || !nativeSortedIDs(t.AccountIDs, 16) || !nativeSortedIDs(t.GoalIDs, 8) || !nativeSortedStrings(t.ProductIDs, 100) || !nativeSortedStrings(t.PayeeIDs, 1) || t.SingleCap.IsZero() || t.LifetimeCap.Cents() < t.SingleCap.Cents() || t.MonthlyCap.Cents() < t.SingleCap.Cents() || t.MonthlyCap.Cents() > t.LifetimeCap.Cents() || t.EffectiveFrom.IsZero() || t.EffectiveFrom.Nanosecond()%1000 != 0 || t.ExpiresAt.Nanosecond()%1000 != 0 || !t.ExpiresAt.After(t.EffectiveFrom) || t.ExpiresAt.After(t.EffectiveFrom.Add(366*24*time.Hour)) {
		return ErrNativeStanding
	}
	if _, err := TemplateLocation(t.Timezone); err != nil {
		return ErrNativeStanding
	}
	last := ""
	goalRefs := map[funds.ID]bool{}
	hasAsset, hasPayment := false, false
	for _, p := range t.Policies {
		if p.PolicyID == (funds.ID{}) || p.VersionID == (funds.ID{}) || p.Version == 0 || !validHash(p.ConfigurationHash) || p.PolicyID.String() <= last {
			return ErrNativeStanding
		}
		last = p.PolicyID.String()
		if p.GoalID != nil {
			if *p.GoalID == (funds.ID{}) || !nativeContainsID(t.GoalIDs, *p.GoalID) {
				return ErrNativeStanding
			}
			goalRefs[*p.GoalID] = true
		}
		switch t.Family {
		case NativeSingleGoal, NativeJointGoals:
			if p.Template != LongTermGoalTemplate || p.GoalID == nil {
				return ErrNativeStanding
			}
		case NativeAssetAllocation, NativeMaturityReinvest:
			if p.Template != AssetAuthorizationTemplate && (t.Family != NativeMaturityReinvest || p.Template != RecoveryTemplate) {
				return ErrNativeStanding
			}
			hasAsset = hasAsset || p.Template == AssetAuthorizationTemplate
		case NativePeriodicPayment:
			if p.Template != RecurringObligationTemplate && p.Template != PeriodicTransferTemplate || p.GoalID != nil {
				return ErrNativeStanding
			}
			hasPayment = true
		}
	}
	if t.Family == NativeSingleGoal && (len(t.GoalIDs) != 1 || len(t.Policies) != 1) || t.Family == NativeJointGoals && (len(t.GoalIDs) < 2 || len(t.Policies) != len(t.GoalIDs)) {
		return ErrNativeStanding
	}
	if t.Family == NativeSingleGoal || t.Family == NativeJointGoals {
		if len(goalRefs) != len(t.GoalIDs) || len(t.ProductIDs) != 0 || len(t.PayeeIDs) != 0 {
			return ErrNativeStanding
		}
	} else if t.Family == NativePeriodicPayment {
		if !hasPayment || len(t.Policies) != 1 || len(t.PayeeIDs) != 1 || len(t.ProductIDs) != 0 || len(t.GoalIDs) != 0 || len(t.AccountIDs) != 1 {
			return ErrNativeStanding
		}
	} else if !hasAsset || len(t.ProductIDs) == 0 || len(t.PayeeIDs) != 0 {
		return ErrNativeStanding
	}
	return nil
}

func (t NativeStandingTerms) Digest() (string, error) {
	if err := t.Validate(); err != nil {
		return "", err
	}
	return digest(t), nil
}

func nativeContainsID(ids []funds.ID, id funds.ID) bool {
	i := sort.Search(len(ids), func(i int) bool { return ids[i].String() >= id.String() })
	return i < len(ids) && ids[i] == id
}
func nativeContainsString(ids []string, id string) bool {
	i := sort.SearchStrings(ids, id)
	return i < len(ids) && ids[i] == id
}

// A source identity survives retries/restart. A new grant never turns an old
// income event into income occurring after that grant. Principal returns keep
// a separate identity and cannot qualify as income.
type NativeOccurrenceSource struct {
	Kind         string    `json:"kind"`
	ID           string    `json:"id"`
	OriginalHash string    `json:"original_hash"`
	Period       string    `json:"period,omitempty"`
	OccurredAt   time.Time `json:"occurred_at"`
	AvailableAt  time.Time `json:"available_at"`
}

func (s NativeOccurrenceSource) Validate(family NativeFamily) error {
	if !family.Valid() || !nativeSortedStrings([]string{s.ID}, 1) || !validHash(s.OriginalHash) || s.OccurredAt.IsZero() || s.AvailableAt.IsZero() || s.OccurredAt.Nanosecond()%1000 != 0 || s.AvailableAt.Nanosecond()%1000 != 0 {
		return ErrNativeStanding
	}
	switch family {
	case NativeSingleGoal, NativeJointGoals, NativeAssetAllocation:
		if s.Kind != "INCOME_EVENT" || s.Period != "" || s.AvailableAt.Before(s.OccurredAt) {
			return ErrNativeStanding
		}
	case NativeMaturityReinvest:
		if s.Kind != "PRINCIPAL_RETURN" || s.Period != "" || s.AvailableAt.Before(s.OccurredAt) {
			return ErrNativeStanding
		}
	case NativePeriodicPayment:
		if s.Kind != "PAYMENT_OCCURRENCE" || len(s.Period) != 7 {
			return ErrNativeStanding
		}
		if _, err := time.Parse("2006-01", s.Period); err != nil {
			return ErrNativeStanding
		}
	}
	return nil
}

func NativeOccurrenceKey(scope TemplateOwnerScope, grant funds.ID, family NativeFamily, source NativeOccurrenceSource) (string, error) {
	if scope.EnvironmentID == (funds.ID{}) || scope.UserID == (funds.ID{}) || grant == (funds.ID{}) || source.Validate(family) != nil {
		return "", ErrNativeStanding
	}
	source.OccurredAt = source.OccurredAt.UTC()
	source.AvailableAt = source.AvailableAt.UTC()
	return digest(struct {
		Protocol string                 `json:"protocol"`
		Scope    TemplateOwnerScope     `json:"scope"`
		GrantID  funds.ID               `json:"grant_id"`
		Family   NativeFamily           `json:"family"`
		Source   NativeOccurrenceSource `json:"source"`
	}{NativeStandingProtocol + "/occurrence", scope, grant, family, source}), nil
}

type NativeStandingUsage struct {
	LifetimeCommitted funds.Money
	LifetimeReserved  funds.Money
	MonthCommitted    funds.Money
	MonthReserved     funds.Money
	Period            string
}

type NativeStandingCandidate struct {
	Scope      TemplateOwnerScope      `json:"scope"`
	Family     NativeFamily            `json:"family"`
	Source     NativeOccurrenceSource  `json:"source"`
	Policies   []NativePolicyReference `json:"policies"`
	AccountIDs []funds.ID              `json:"account_ids"`
	GoalIDs    []funds.ID              `json:"goal_ids"`
	ProductID  string                  `json:"product_id"`
	PayeeID    string                  `json:"payee_id"`
	Amount     funds.Money             `json:"amount_cents"`
	Fee        funds.Money             `json:"fee_cents"`
	Loss       funds.Money             `json:"loss_cents"`
}

// AuthorizeNativeStanding checks deterministic coverage only. A trusted
// application must first authenticate/load the USER grant and exact current
// versions, and atomically reserve both money and these budgets. This function
// cannot make a string supplied by HTTP into an authenticated principal.
func AuthorizeNativeStanding(terms NativeStandingTerms, state string, c NativeStandingCandidate, usage NativeStandingUsage, now time.Time) error {
	if terms.Validate() != nil || state != "ACTIVE" || now.IsZero() || now.Before(terms.EffectiveFrom) || !now.Before(terms.ExpiresAt) || c.Scope != terms.Scope || c.Family != terms.Family || c.Source.Validate(c.Family) != nil || c.Source.AvailableAt.After(now) || c.Amount.IsZero() || !c.Fee.IsZero() || !c.Loss.IsZero() || !nativeSortedIDs(c.AccountIDs, 16) || !nativeSortedIDs(c.GoalIDs, 8) || len(c.AccountIDs) == 0 || len(c.Policies) != len(terms.Policies) {
		return ErrNativeStanding
	}
	if (c.Family == NativeSingleGoal || c.Family == NativeJointGoals || c.Family == NativeAssetAllocation) && (c.Source.OccurredAt.Before(terms.EffectiveFrom) || c.Source.OccurredAt.After(now)) {
		return ErrNativeStanding
	}
	for i, p := range c.Policies {
		if digest(p) != digest(terms.Policies[i]) {
			return ErrNativeStanding
		}
	}
	for _, id := range c.AccountIDs {
		if !nativeContainsID(terms.AccountIDs, id) {
			return ErrNativeStanding
		}
	}
	for _, id := range c.GoalIDs {
		if !nativeContainsID(terms.GoalIDs, id) {
			return ErrNativeStanding
		}
	}
	if c.Family == NativeSingleGoal && len(c.GoalIDs) != 1 || c.Family == NativeJointGoals && len(c.GoalIDs) < 2 || (c.Family == NativeAssetAllocation || c.Family == NativeMaturityReinvest) && !nativeContainsString(terms.ProductIDs, c.ProductID) || c.Family == NativePeriodicPayment && !nativeContainsString(terms.PayeeIDs, c.PayeeID) {
		return ErrNativeStanding
	}
	if len(terms.ProductIDs) == 0 && c.ProductID != "" || len(terms.PayeeIDs) == 0 && c.PayeeID != "" {
		return ErrNativeStanding
	}
	zone, _ := TemplateLocation(terms.Timezone)
	if usage.Period != now.In(zone).Format("2006-01") {
		return ErrNativeStanding
	}
	lifetime, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved, c.Amount)
	monthly, e2 := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved, c.Amount)
	if e != nil || e2 != nil || c.Amount.Cents() > terms.SingleCap.Cents() || lifetime.Cents() > terms.LifetimeCap.Cents() || monthly.Cents() > terms.MonthlyCap.Cents() {
		return ErrNativeStandingCap
	}
	return nil
}
