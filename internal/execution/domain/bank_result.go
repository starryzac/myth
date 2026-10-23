package domain

import (
	"errors"
	"fmt"
)

// VerifyBankResult checks complete original request binding and every economic
// leg before an application can project a bank observation. Authenticating the
// configured bank transport remains the caller's infrastructure responsibility.
// NOT_FOUND is a valid observation and never a final no-effect proof.
func VerifyBankResult(original BankRequest, result BankResult) error {
	digest, err := original.Digest()
	if err != nil {
		return err
	}
	invalid := errors.New("bank observation does not match original request")
	if result.Namespace != original.Namespace || result.UserID != original.UserID || result.Key != original.Key {
		return invalid
	}
	if result.Status == BankNotFound {
		if result.AbsenceIsFinal || result.RequestHash != "" || len(result.Postings) != 0 || result.Sequence != 0 || result.Version != 0 || result.AcceptedAt != nil || result.AvailableAt != nil || result.SettledAt != nil || result.RejectionCode != "" || result.Request != (BankRequest{}) {
			return invalid
		}
		return nil
	}
	observedDigest, err := result.Request.Digest()
	if err != nil || observedDigest != digest || result.RequestHash != digest || result.Sequence < 1 {
		return invalid
	}
	switch result.Status {
	case BankRejected:
		if !result.AbsenceIsFinal || result.RejectionCode == "" || result.Version != 1 || result.AcceptedAt != nil || result.AvailableAt != nil || result.SettledAt != nil || len(result.Postings) != 0 {
			return invalid
		}
	case BankAccepted:
		if result.AbsenceIsFinal || result.RejectionCode != "" || result.Version != 1 || result.AcceptedAt == nil || result.AvailableAt == nil || result.SettledAt != nil || len(result.Postings) != 0 {
			return invalid
		}
		if result.AcceptedAt.Before(original.RequestedAt) || result.AvailableAt.Before(*result.AcceptedAt) || result.AvailableAt.After(original.LatestArrivalAt) {
			return invalid
		}
		if !result.AvailableAt.Equal(result.AcceptedAt.UTC().AddDate(0, 0, original.SettlementDelayDays)) {
			return invalid
		}
	case BankSettled:
		if result.AbsenceIsFinal || result.RejectionCode != "" || result.Version != 2 || result.AcceptedAt == nil || result.AvailableAt == nil || result.SettledAt == nil || len(result.Postings) != 4 {
			return invalid
		}
		if !result.SettledAt.Equal(*result.AvailableAt) || result.AcceptedAt.Before(original.RequestedAt) || result.AvailableAt.Before(*result.AcceptedAt) || result.AvailableAt.After(original.LatestArrivalAt) {
			return invalid
		}
		if !result.AvailableAt.Equal(result.AcceptedAt.UTC().AddDate(0, 0, original.SettlementDelayDays)) {
			return invalid
		}
		type expected struct{ dimension, ledger, direction string }
		var want []expected
		if original.Kind == "TRANSFER" {
			want = []expected{{"ECONOMIC", "CASH:" + original.SourceAccountID, "DEBIT"}, {"ECONOMIC", "CASH:" + original.DestinationAccountID, "CREDIT"}, {"INCOME_LOCATION", "AVAILABLE:" + original.IncomeOriginID + ":" + original.SourceAccountID, "DEBIT"}, {"INCOME_LOCATION", "AVAILABLE:" + original.IncomeOriginID + ":" + original.DestinationAccountID, "CREDIT"}}
		} else {
			want = []expected{{"GOAL_OWNERSHIP", "UNASSIGNED:" + original.SourceAccountID, "DEBIT"}, {"GOAL_OWNERSHIP", "GOAL:" + original.GoalID, "CREDIT"}, {"INCOME_LOCATION", "AVAILABLE:" + original.IncomeOriginID + ":" + original.SourceAccountID, "DEBIT"}, {"INCOME_LOCATION", "ASSIGNED:" + original.IncomeOriginID + ":" + original.SourceAccountID, "CREDIT"}}
		}
		seen := map[string]bool{}
		for _, posting := range result.Postings {
			matched := false
			for i, leg := range want {
				if posting.ID == fmt.Sprintf("%s:%d", digest, i+1) && posting.Dimension == leg.dimension && posting.LedgerID == leg.ledger && posting.Direction == leg.direction && posting.Amount == original.Amount && posting.OccurredAt.Equal(*result.SettledAt) {
					matched = true
					break
				}
			}
			if !matched || seen[posting.ID] {
				return invalid
			}
			seen[posting.ID] = true
		}
	default:
		return invalid
	}
	return nil
}
