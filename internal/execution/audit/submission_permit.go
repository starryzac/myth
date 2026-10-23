package executionaudit

import (
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const SubmissionPermitProtocol = "zhiyu-submission-permit-v1"

// SubmissionPermitEvidence records the exact T2 authorization decision in the
// transaction that installs the durable permit. DecisionAt is a business
// instant, not a claim about the database's eventual commit timestamp. HMAC
// authenticity still requires an independent trusted bank/capture boundary.
type SubmissionPermitEvidence struct {
	Protocol             string                     `json:"protocol"`
	EnvironmentID        funds.ID                   `json:"environment_id"`
	UserID               funds.ID                   `json:"user_id"`
	OperationID          funds.ID                   `json:"operation_id"`
	Namespace            string                     `json:"namespace"`
	PermitID             string                     `json:"permit_id"`
	BankRequestHash      string                     `json:"bank_request_hash"`
	EffectHash           string                     `json:"effect_hash"`
	Dependencies         planning.DependencyVector  `json:"dependencies"`
	Policy               policy.Policy              `json:"policy"`
	Grant                *policy.Grant              `json:"grant,omitempty"`
	Confirmation         *policy.ConfirmationSource `json:"confirmation,omitempty"`
	DecisionAt           time.Time                  `json:"decision_at"`
	ReservedFundsVersion uint64                     `json:"reserved_funds_version"`
	SourceVersion        uint64                     `json:"source_version"`
	SignatureHash        string                     `json:"signature_hash"`
}
