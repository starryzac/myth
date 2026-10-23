package bankv2

import (
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type Quote struct {
	Protocol            string          `json:"protocol"`
	Namespace           string          `json:"namespace"`
	UserID              string          `json:"user_id"`
	ID                  string          `json:"id"`
	Kind                Kind            `json:"kind"`
	PositionID          string          `json:"position_id"`
	PositionOpeningHash string          `json:"position_opening_hash"`
	Product             ProductSnapshot `json:"product"`
	Principal           funds.Money     `json:"principal_cents"`
	Net                 funds.Money     `json:"net_cents"`
	Fee                 funds.Money     `json:"fee_cents"`
	Loss                funds.Money     `json:"loss_cents"`
	RequestedAt         time.Time       `json:"requested_at"`
	AvailableAt         time.Time       `json:"available_at"`
	ExpiresAt           time.Time       `json:"expires_at"`
}

func (q Quote) Validate() error {
	if q.Protocol != Protocol+"/quote" || !namespace.MatchString(q.Namespace) || !validIDs(q.UserID, q.ID, q.PositionID) || !isHash(q.PositionOpeningHash) || q.Product.Validate() != nil || (q.Kind != Redeem && q.Kind != Mature) || q.Principal.IsZero() || q.RequestedAt.IsZero() || q.AvailableAt.IsZero() || !q.ExpiresAt.After(q.RequestedAt) {
		return ErrInvalid
	}
	total, err := funds.SumMoney(q.Net, q.Fee, q.Loss)
	if err != nil || total != q.Principal {
		return ErrInvalid
	}
	return nil
}
func (q Quote) Digest() (string, error) {
	if q.Validate() != nil {
		return "", ErrInvalid
	}
	q.RequestedAt = q.RequestedAt.UTC()
	q.AvailableAt = q.AvailableAt.UTC()
	q.ExpiresAt = q.ExpiresAt.UTC()
	return digest(q, Protocol+"/quote")
}

type Position struct {
	Opening     PositionOpening `json:"opening"`
	OpeningHash string          `json:"opening_hash"`
	ClosingKey  string          `json:"closing_key"`
	Closed      bool            `json:"closed"`
}
type Account struct {
	ID       string      `json:"id"`
	Balance  funds.Money `json:"balance_cents"`
	Reserved funds.Money `json:"reserved_cents"`
}
type Goal struct {
	ID        string      `json:"id"`
	AccountID string      `json:"account_id"`
	Cash      funds.Money `json:"cash_cents"`
	Principal funds.Money `json:"principal_cents"`
	Loss      funds.Money `json:"loss_cents"`
}

// CurrentState is a bounded RR view at CapturedAt. Operations are separately
// discoverable by immutable sequence watermark. No past-key watermark is
// misrepresented as a historical reconstruction of today's positions/catalog.
type CurrentState struct {
	Protocol           string            `json:"protocol"`
	Namespace          string            `json:"namespace"`
	UserID             string            `json:"user_id"`
	CapturedAt         time.Time         `json:"captured_at"`
	OperationWatermark int64             `json:"operation_watermark"`
	CatalogWatermark   int64             `json:"catalog_watermark"`
	Products           []ProductSnapshot `json:"products"`
	Positions          []Position        `json:"positions"`
	Accounts           []Account         `json:"accounts"`
	Goals              []Goal            `json:"goals"`
}
