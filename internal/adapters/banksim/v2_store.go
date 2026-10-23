package banksim

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	bankv2 "zhiyu.local/zhiyu-go/internal/execution/bankv2"
)

var ErrReconciliation = errors.New("BANK_CHRONOLOGY_REQUIRES_RECONCILIATION")

func loadV2(ctx context.Context, q querier, namespace, user, key string) (bankv2.Result, error) {
	r := bankv2.Result{Protocol: bankv2.Protocol, Namespace: namespace, UserID: user, Key: key, Status: bankv2.NotFound, Postings: []bankv2.Posting{}}
	var raw, position []byte
	err := q.QueryRow(ctx, `SELECT request_original,request_hash,sequence,status,version,accepted_at,available_at,settled_at,rejection_code,position_original FROM bank.v2_operations WHERE namespace=$1 AND user_id=$2 AND operation_key=$3`, namespace, user, key).Scan(&raw, &r.RequestHash, &r.Sequence, &r.Status, &r.Version, &r.AcceptedAt, &r.AvailableAt, &r.SettledAt, &r.RejectionCode, &position)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, nil
	}
	if err != nil {
		return r, err
	}
	var request bankv2.Request
	if err = json.Unmarshal(raw, &request); err != nil {
		return r, err
	}
	r.Request = &request
	if position != nil {
		var p bankv2.PositionOpening
		if err = json.Unmarshal(position, &p); err != nil {
			return r, err
		}
		r.Position = &p
	}
	r.AbsenceIsFinal = r.Status == bankv2.Rejected
	if r.Status == bankv2.Settled {
		if request.Effect.Kind == bankv2.Redeem || request.Effect.Kind == bankv2.Mature {
			var raw []byte
			if err = q.QueryRow(ctx, "SELECT original FROM bank.v2_principal_returns WHERE namespace=$1 AND user_id=$2 AND operation_key=$3", namespace, user, key).Scan(&raw); err != nil {
				return r, err
			}
			var principal bankv2.PrincipalReturn
			if err = json.Unmarshal(raw, &principal); err != nil {
				return r, err
			}
			r.PrincipalReturn = &principal
		}
		rows, err := q.Query(ctx, "SELECT id,dimension,ledger_id,direction,amount_cents::text,occurred_at FROM bank.v2_postings WHERE namespace=$1 AND operation_key=$2", namespace, key)
		if err != nil {
			return r, err
		}
		byID := map[string]bankv2.Posting{}
		for rows.Next() {
			var p bankv2.Posting
			var amount string
			if err = rows.Scan(&p.ID, &p.Dimension, &p.LedgerID, &p.Direction, &amount, &p.OccurredAt); err != nil {
				rows.Close()
				return r, err
			}
			if err = json.Unmarshal([]byte(fmt.Sprintf("%q", amount)), &p.Amount); err != nil {
				rows.Close()
				return r, err
			}
			byID[p.ID] = p
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return r, err
		}
		expected, err := bankv2.ExpectedPostings(request, *r.SettledAt)
		if err != nil {
			return r, err
		}
		if len(expected) != len(byID) {
			return r, bankv2.ErrInvalid
		}
		for _, p := range expected {
			actual, exists := byID[p.ID]
			if !exists {
				return r, bankv2.ErrInvalid
			}
			r.Postings = append(r.Postings, actual)
		}
	}
	if err = bankv2.VerifyResult(request, r); err != nil {
		return r, err
	}
	return r, nil
}

func (s *Store) LookupV2(ctx context.Context, namespace, user, key string) (bankv2.Result, error) {
	if !validIdentity(namespace, user, key) {
		return bankv2.Result{}, bankv2.ErrInvalid
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return bankv2.Result{}, err
	}
	defer tx.Rollback(ctx)
	r, err := loadV2(ctx, tx, namespace, user, key)
	if err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}

func requireV2Order(ctx context.Context, tx pgx.Tx, namespace, user string, at time.Time, ownKey string) error {
	var old bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND available_at<=$3)`, namespace, user, at).Scan(&old); err != nil {
		return err
	}
	if old {
		return ErrPendingOrder
	}
	var first string
	err := tx.QueryRow(ctx, `SELECT operation_key FROM bank.v2_operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND available_at<=$3 ORDER BY available_at,sequence LIMIT 1`, namespace, user, at).Scan(&first)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if first != ownKey {
		return ErrPendingOrder
	}
	return nil
}

func (s *Store) SubmitV2(ctx context.Context, r bankv2.Request, signature string, now time.Time) (bankv2.Result, error) {
	if r.Validate() != nil {
		return bankv2.Result{}, bankv2.ErrInvalid
	}
	if !bankv2.VerifyPermit(r, signature, s.secret) {
		return bankv2.Result{}, ErrUnauthorized
	}
	now = now.UTC().Truncate(time.Microsecond)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return bankv2.Result{}, err
	}
	defer tx.Rollback(ctx)
	if err = keyLock(ctx, tx, r.Namespace, r.Key); err != nil {
		return bankv2.Result{}, err
	}
	if err = userLock(ctx, tx, r.Namespace, r.UserID); err != nil {
		return bankv2.Result{}, err
	}
	h, _ := r.Digest()
	existing, err := loadV2(ctx, tx, r.Namespace, r.UserID, r.Key)
	if err != nil {
		return existing, err
	}
	if existing.Status != bankv2.NotFound {
		if existing.RequestHash != h {
			return existing, ErrConflict
		}
		return existing, tx.Commit(ctx)
	}
	var collision bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM bank.v2_operations WHERE namespace=$1 AND (operation_key=$2 OR operation_id=$3)) OR EXISTS(SELECT 1 FROM bank.operations WHERE namespace=$1 AND (operation_key=$2 OR operation_id=$3))`, r.Namespace, r.Key, r.OperationID).Scan(&collision); err != nil {
		return bankv2.Result{}, err
	}
	if collision {
		return bankv2.Result{}, ErrConflict
	}
	if err = requireV2Order(ctx, tx, r.Namespace, r.UserID, now, ""); err != nil {
		return bankv2.Result{}, err
	}
	var duePosition string
	err = tx.QueryRow(ctx, `SELECT p.position_id FROM bank.v2_position_openings p JOIN bank.v2_position_state s USING(namespace,user_id,position_id) WHERE p.namespace=$1 AND p.user_id=$2 AND NOT s.closed AND s.closing_key IS NULL AND p.maturity_at IS NOT NULL AND p.maturity_at+((convert_from(p.original,'UTF8')::jsonb#>>'{terms,redemption_delay_days}')::integer*interval '1 day')<=$3 ORDER BY p.maturity_at,p.position_id LIMIT 1`, r.Namespace, r.UserID, now).Scan(&duePosition)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return bankv2.Result{}, err
	}
	if err == nil && (r.Effect.Kind != bankv2.Mature || r.Effect.PositionID != duePosition) {
		return bankv2.Result{}, ErrPendingOrder
	}
	code := ""
	var position *bankv2.PositionOpening
	available := now
	if r.RequestedAt.After(now) {
		code = "FUTURE_REQUEST"
	}
	if !now.Before(r.ExpiresAt) {
		code = "REQUEST_EXPIRED"
	}
	if code == "" {
		code, position, available, err = validateV2Agreement(ctx, tx, r, now)
		if err != nil {
			return bankv2.Result{}, err
		}
	}
	var holds []v2Hold
	if code == "" {
		holds, code, err = validateV2Funds(ctx, tx, r)
		if err != nil {
			return bankv2.Result{}, err
		}
	}
	raw, _ := r.CanonicalBytes()
	if code != "" {
		_, err = tx.Exec(ctx, `INSERT INTO bank.v2_operations(namespace,user_id,operation_key,operation_id,kind,business_key,request_original,request_hash,status,rejection_code) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'REJECTED',$9)`, r.Namespace, r.UserID, r.Key, r.OperationID, string(r.Effect.Kind), r.Effect.BusinessKey, raw, h, code)
	} else {
		var positionRaw any
		if position != nil {
			positionRaw, _ = json.Marshal(position)
		}
		_, err = tx.Exec(ctx, `INSERT INTO bank.v2_operations(namespace,user_id,operation_key,operation_id,kind,business_key,request_original,request_hash,status,accepted_at,available_at,position_original) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'ACCEPTED',$9,$10,$11)`, r.Namespace, r.UserID, r.Key, r.OperationID, string(r.Effect.Kind), r.Effect.BusinessKey, raw, h, now, available, positionRaw)
		if err == nil {
			err = reserveV2(ctx, tx, r, holds)
		}
		if err == nil && !available.After(now) {
			err = settleV2(ctx, tx, r, position, available)
		}
	}
	if err != nil {
		return bankv2.Result{}, err
	}
	result, err := loadV2(ctx, tx, r.Namespace, r.UserID, r.Key)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

func validateV2Agreement(ctx context.Context, tx pgx.Tx, r bankv2.Request, now time.Time) (string, *bankv2.PositionOpening, time.Time, error) {
	e := r.Effect
	available := now
	var position *bankv2.PositionOpening
	var consumed bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM bank.v2_operations WHERE namespace=$1 AND user_id=$2 AND kind=$3 AND business_key=$4 AND status<>'REJECTED')`, r.Namespace, r.UserID, string(e.Kind), e.BusinessKey).Scan(&consumed); err != nil {
		return "", nil, available, err
	}
	if consumed {
		return "BUSINESS_ALREADY_CONSUMED", nil, available, nil
	}
	if e.Product != nil {
		product, err := productV2(ctx, tx, r.Namespace, e.Product.Terms.ProductID, e.Product.Terms.Version)
		if errors.Is(err, pgx.ErrNoRows) {
			return "PRODUCT_NOT_FOUND", nil, available, nil
		}
		if err != nil {
			return "", nil, available, err
		}
		if product.TermsHash != e.Product.TermsHash || product.AvailabilityHash != e.Product.AvailabilityHash {
			return "PRODUCT_VERSION_CHANGED", nil, available, nil
		}
		if e.Kind == bankv2.Purchase && (product.Availability.Status != "AVAILABLE" || now.Before(product.Availability.EffectiveFrom) || !now.Before(product.Availability.EffectiveUntil)) {
			return "PRODUCT_UNAVAILABLE", nil, available, nil
		}
		if e.Kind == bankv2.Purchase {
			var latest int64
			if err = tx.QueryRow(ctx, "SELECT max(version) FROM bank.v2_product_terms WHERE namespace=$1 AND product_id=$2", r.Namespace, product.Terms.ProductID).Scan(&latest); err != nil {
				return "", nil, available, err
			}
			if latest != product.Terms.Version {
				return "PRODUCT_TERMS_CHANGED", nil, available, nil
			}
			var exists bool
			if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM bank.v2_position_openings WHERE namespace=$1 AND user_id=$2 AND position_id=$3)", r.Namespace, r.UserID, e.PositionID).Scan(&exists); err != nil {
				return "", nil, available, err
			}
			if exists {
				return "POSITION_ALREADY_EXISTS", nil, available, nil
			}
			opened, err := bankv2.OpenedPosition(r, now)
			if err != nil {
				return "POSITION_CONTRACT_INVALID", nil, available, nil
			}
			position = &opened
		} else {
			p, err := positionV2(ctx, tx, r.Namespace, r.UserID, e.PositionID)
			if errors.Is(err, pgx.ErrNoRows) {
				return "POSITION_NOT_FOUND", nil, available, nil
			}
			if err != nil {
				return "", nil, available, err
			}
			if p.Closed || p.ClosingKey != "" {
				return "POSITION_ALREADY_CLOSING", nil, available, nil
			}
			position = &p.Opening
			var quoteRaw []byte
			var quoteHash string
			err = tx.QueryRow(ctx, "SELECT original,original_hash FROM bank.v2_quotes WHERE namespace=$1 AND user_id=$2 AND quote_id=$3 AND position_id=$4", r.Namespace, r.UserID, e.QuoteID, e.PositionID).Scan(&quoteRaw, &quoteHash)
			if errors.Is(err, pgx.ErrNoRows) {
				return "QUOTE_NOT_FOUND", nil, available, nil
			}
			if err != nil {
				return "", nil, available, err
			}
			var quote bankv2.Quote
			if json.Unmarshal(quoteRaw, &quote) != nil {
				return "", nil, available, bankv2.ErrInvalid
			}
			qh, qe := quote.Digest()
			if qe != nil || qh != quoteHash {
				return "", nil, available, bankv2.ErrInvalid
			}
			if quote.Kind != e.Kind || quote.PositionOpeningHash != e.PositionOpeningHash || quote.Product.TermsHash != e.Product.TermsHash || quote.Product.AvailabilityHash != e.Product.AvailabilityHash || quote.Principal != e.Amount || quote.Net != e.Net || quote.Fee != e.Fee || quote.Loss != e.Loss || now.Before(quote.RequestedAt) || !now.Before(quote.ExpiresAt) {
				return "QUOTE_CHANGED_OR_EXPIRED", nil, available, nil
			}
		}
	}
	at, err := bankv2.Availability(r, position, now)
	if err != nil {
		return "ARRIVAL_OR_CONTRACT_CHANGED", nil, available, nil
	}
	available = at
	if available.Before(now) {
		var last *time.Time
		if err = tx.QueryRow(ctx, `SELECT max(occurred_at) FROM (SELECT occurred_at FROM bank.postings WHERE namespace=$1 AND operation_key IN(SELECT operation_key FROM bank.operations WHERE namespace=$1 AND user_id=$2) UNION ALL SELECT occurred_at FROM bank.v2_postings WHERE namespace=$1 AND operation_key IN(SELECT operation_key FROM bank.v2_operations WHERE namespace=$1 AND user_id=$2))p`, r.Namespace, r.UserID).Scan(&last); err != nil {
			return "", nil, available, err
		}
		if last != nil && last.After(available) {
			return "", nil, available, ErrReconciliation
		}
	}
	return "", position, available, nil
}

func (s *Store) SettleV2(ctx context.Context, namespace, user, key string, now time.Time) (bankv2.Result, error) {
	if !validIdentity(namespace, user, key) {
		return bankv2.Result{}, bankv2.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return bankv2.Result{}, err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return bankv2.Result{}, err
	}
	r, err := loadV2(ctx, tx, namespace, user, key)
	if err != nil {
		return r, err
	}
	if r.Status == bankv2.NotFound {
		return r, ErrNotFound
	}
	if r.Status == bankv2.Accepted {
		if r.AvailableAt.After(now) {
			return r, ErrNotDue
		}
		if err = requireV2Order(ctx, tx, namespace, user, *r.AvailableAt, key); err != nil {
			return r, err
		}
		if err = settleV2(ctx, tx, *r.Request, r.Position, *r.AvailableAt); err != nil {
			return r, err
		}
		r, err = loadV2(ctx, tx, namespace, user, key)
		if err != nil {
			return r, err
		}
	}
	return r, tx.Commit(ctx)
}
