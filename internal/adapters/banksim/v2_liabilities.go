package banksim

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/url"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func (s *Store) CurrentLiabilitiesV2(ctx context.Context, namespace, user string, now time.Time) (bankv2.LiabilitySnapshot, error) {
	out := bankv2.LiabilitySnapshot{Protocol: bankv2.Protocol + "/liabilities", Namespace: namespace, UserID: user, CapturedAt: now.UTC(), Complete: true, Liabilities: []bankv2.LiabilityState{}}
	if !validIdentity(namespace, user) {
		return out, bankv2.ErrInvalid
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT o.original,o.original_hash,o.final_total_cents,s.paid_cents,s.reserved_cents FROM bank.v2_liability_originals o LEFT JOIN bank.v2_liability_state s USING(namespace,user_id,liability_id) WHERE o.namespace=$1 AND o.user_id=$2 ORDER BY o.liability_id LIMIT 1001`, namespace, user)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var row bankv2.LiabilityState
		var raw []byte
		var total, paid, reserved int64
		if err = rows.Scan(&raw, &row.OriginalHash, &total, &paid, &reserved); err != nil {
			return out, err
		}
		sum := sha256.Sum256(raw)
		if len(out.Liabilities) >= 1000 || hex.EncodeToString(sum[:]) != row.OriginalHash || json.Unmarshal(raw, &row.Original) != nil || row.Original.FinalTotal.Cents() != total {
			return out, bankv2.ErrInvalid
		}
		row.Paid, err = funds.NewMoney(paid)
		if err != nil {
			return out, err
		}
		row.Reserved, err = funds.NewMoney(reserved)
		if err != nil {
			return out, err
		}
		if row.Validate() != nil {
			return out, bankv2.ErrInvalid
		}
		out.Liabilities = append(out.Liabilities, row)
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	if out.Validate() != nil {
		return out, bankv2.ErrInvalid
	}
	return out, tx.Commit(ctx)
}

func registerLiabilityReadV2(mux *http.ServeMux, s *Store, clock func() time.Time) {
	mux.HandleFunc("GET /v2/liabilities", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		out, err := s.CurrentLiabilitiesV2(r.Context(), q.Get("namespace"), q.Get("user_id"), clock())
		if err != nil {
			v2HTTPError(w, err)
			return
		}
		writeJSON(w, 200, out)
	})
}
func (c *ClientV2) Liabilities(ctx context.Context, namespace, user string) (bankv2.LiabilitySnapshot, error) {
	var out bankv2.LiabilitySnapshot
	if !validIdentity(namespace, user) {
		return out, bankv2.ErrInvalid
	}
	q := url.Values{"namespace": {namespace}, "user_id": {user}}
	if err := c.call(ctx, "GET", "/v2/liabilities?"+q.Encode(), nil, "", "", &out); err != nil {
		return out, err
	}
	if out.Namespace != namespace || out.UserID != user || out.Validate() != nil {
		return out, bankv2.ErrInvalid
	}
	return out, nil
}
