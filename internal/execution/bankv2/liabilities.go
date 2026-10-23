package bankv2

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// LiabilitySnapshot is a complete, bounded current read. CapturedAt is not a
// historical watermark and the original's final total cannot be revised here.
type LiabilitySnapshot struct {
	Protocol    string           `json:"protocol"`
	Namespace   string           `json:"namespace"`
	UserID      string           `json:"user_id"`
	CapturedAt  time.Time        `json:"captured_at"`
	Complete    bool             `json:"complete"`
	Liabilities []LiabilityState `json:"liabilities"`
}
type LiabilityState struct {
	Original     LiabilityReference `json:"original"`
	OriginalHash string             `json:"original_hash"`
	Paid         funds.Money        `json:"paid_cents"`
	Reserved     funds.Money        `json:"reserved_cents"`
}

func (l LiabilityState) Validate() error {
	if l.Original.Validate() != nil {
		return ErrInvalid
	}
	raw, err := json.Marshal(l.Original)
	sum := sha256.Sum256(raw)
	if err != nil || hex.EncodeToString(sum[:]) != l.OriginalHash || l.Paid.Cents() > l.Original.FinalTotal.Cents() || l.Reserved.Cents() > l.Original.FinalTotal.Cents()-l.Paid.Cents() {
		return ErrInvalid
	}
	return nil
}
func (l LiabilitySnapshot) Validate() error {
	if l.Protocol != Protocol+"/liabilities" || !namespace.MatchString(l.Namespace) || !validIDs(l.UserID) || l.CapturedAt.IsZero() || !l.Complete || l.Liabilities == nil || len(l.Liabilities) > 1000 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, row := range l.Liabilities {
		if row.Validate() != nil || seen[row.Original.ID] {
			return ErrInvalid
		}
		seen[row.Original.ID] = true
	}
	return nil
}
