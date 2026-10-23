package bankv2

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"
)

// GoalRegistration creates an identity with zero ownership only. A full scoped
// permit authenticates the server's request; no balance/assigned field exists.
type GoalRegistration struct {
	Protocol      string    `json:"protocol"`
	Namespace     string    `json:"namespace"`
	UserID        string    `json:"user_id"`
	GoalID        string    `json:"goal_id"`
	AccountID     string    `json:"account_id"`
	ReferenceHash string    `json:"reference_hash"`
	RequestedAt   time.Time `json:"requested_at"`
	ExpiresAt     time.Time `json:"expires_at"`
}

func (r GoalRegistration) CanonicalBytes() ([]byte, error) {
	if r.Protocol != Protocol+"/zero-goal" || !namespace.MatchString(r.Namespace) || !validIDs(r.UserID, r.GoalID, r.AccountID) || !isHash(r.ReferenceHash) || r.RequestedAt.IsZero() || !r.ExpiresAt.After(r.RequestedAt) {
		return nil, ErrInvalid
	}
	r.RequestedAt = r.RequestedAt.UTC()
	r.ExpiresAt = r.ExpiresAt.UTC()
	return json.Marshal(r)
}
func SignGoalRegistration(r GoalRegistration, secret []byte) (string, error) {
	raw, err := r.CanonicalBytes()
	if err != nil || len(secret) < 32 {
		return "", ErrInvalid
	}
	h := hmac.New(sha256.New, secret)
	h.Write([]byte(Protocol + "/zero-goal-permit\x00"))
	h.Write(raw)
	return hex.EncodeToString(h.Sum(nil)), nil
}
func VerifyGoalRegistration(r GoalRegistration, signature string, secret []byte) bool {
	want, err := SignGoalRegistration(r, secret)
	if err != nil {
		return false
	}
	a, e := hex.DecodeString(signature)
	b, _ := hex.DecodeString(want)
	return e == nil && hmac.Equal(a, b)
}
