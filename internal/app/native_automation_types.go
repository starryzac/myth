package app

import (
	"context"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const NativeAutomationProtocol = "native-automation-v1"

type NativeStandingGrantRequest struct {
	Family           policy.NativeFamily `json:"family"`
	PolicyVersionIDs []funds.ID          `json:"policy_version_ids"`
	AccountIDs       []funds.ID          `json:"account_ids"`
	ProductIDs       []string            `json:"product_ids"`
	SingleCap        funds.Money         `json:"single_cap_cents"`
	LifetimeCap      funds.Money         `json:"lifetime_cap_cents"`
	MonthlyCap       funds.Money         `json:"monthly_cap_cents"`
	Timezone         string              `json:"timezone"`
	ExpiresAt        time.Time           `json:"expires_at"`
}

type NativeStandingGrantPreview struct {
	Protocol     string                     `json:"protocol"`
	ID           funds.ID                   `json:"id"`
	Scope        policy.TemplateOwnerScope  `json:"scope"`
	SessionID    funds.ID                   `json:"session_id"`
	Terms        policy.NativeStandingTerms `json:"terms"`
	ReviewedHash string                     `json:"reviewed_hash"`
	CreatedAt    time.Time                  `json:"created_at"`
	ExpiresAt    time.Time                  `json:"expires_at"`
}

type NativeStandingGrantConfirmation struct {
	PreviewID    funds.ID `json:"preview_id"`
	ReviewedHash string   `json:"reviewed_hash"`
}

// Confirmation may only narrow EffectiveFrom to the actual USER confirmation
// instant. Both reviewed and final terms survive in this immutable original.
type NativeStandingGrant struct {
	Protocol      string                     `json:"protocol"`
	ID            funds.ID                   `json:"id"`
	Version       uint64                     `json:"version"`
	Scope         policy.TemplateOwnerScope  `json:"scope"`
	Terms         policy.NativeStandingTerms `json:"terms"`
	ReviewedTerms policy.NativeStandingTerms `json:"reviewed_terms"`
	PreviewID     funds.ID                   `json:"preview_id"`
	SessionID     funds.ID                   `json:"session_id"`
	ReviewedHash  string                     `json:"reviewed_hash"`
	PrincipalKind string                     `json:"principal_kind"`
	ConfirmedAt   time.Time                  `json:"confirmed_at"`
}

type NativeStandingGrantView struct {
	Grant         NativeStandingGrant `json:"grant"`
	State         string              `json:"state"`
	StateSequence uint64              `json:"state_sequence"`
	StateAt       time.Time           `json:"state_at"`
}

type NativeOccurrence struct {
	Protocol     string                        `json:"protocol"`
	ID           funds.ID                      `json:"id"`
	Scope        policy.TemplateOwnerScope     `json:"scope"`
	GrantID      funds.ID                      `json:"grant_id"`
	GrantVersion uint64                        `json:"grant_version"`
	Family       policy.NativeFamily           `json:"family"`
	Key          string                        `json:"key"`
	Source       policy.NativeOccurrenceSource `json:"source"`
	CreatedAt    time.Time                     `json:"created_at"`
}

type NativeOccurrenceClaim struct {
	Occurrence  NativeOccurrence
	Owner       string
	Generation  uint64
	Attempts    int
	MaxAttempts int
}

// The family adapter creates a fresh deterministic candidate and reserves
// money, grant budgets, occurrence and native operations in one transaction.
// This server-owned interface is never a request body or model tool argument.
type nativeOccurrenceAdmission interface {
	admitNativeOccurrence(context.Context, *Service, NativeOccurrenceClaim) error
}

func NativeStandingGrantReviewHash(preview NativeStandingGrantPreview) (string, error) {
	preview.ReviewedHash = ""
	raw, err := encode(preview)
	if err != nil {
		return "", err
	}
	return Hash(raw), nil
}
