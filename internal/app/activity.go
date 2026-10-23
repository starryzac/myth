package app

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const activityCursorVersion = "activity-snapshot-v1"

// Activity is a bounded display record. It contains neither retained request
// bytes nor a permission decision. Authoritative recovery uses Operation.
type Activity struct {
	ID               funds.ID  `json:"id"`
	Kind             string    `json:"kind"`
	AggregateID      funds.ID  `json:"aggregate_id"`
	AggregateVersion uint64    `json:"aggregate_version"`
	CreatedAt        time.Time `json:"created_at"`
	OperationFamily  string    `json:"operation_family,omitempty"`
	OperationKind    string    `json:"operation_kind,omitempty"`
	ReceiptID        *funds.ID `json:"receipt_id,omitempty"`
}

type ActivityPage struct {
	Items             []Activity `json:"items"`
	NextCursor        string     `json:"next_cursor,omitempty"`
	AsOf              time.Time  `json:"as_of"`
	Total             int64      `json:"total"`
	Returned          int        `json:"returned"`
	HasMore           bool       `json:"has_more"`
	VerificationScope string     `json:"verification_scope"`
}

type activityCursor struct {
	Version       string    `json:"version"`
	Environment   funds.ID  `json:"environment"`
	User          funds.ID  `json:"user"`
	AsOf          time.Time `json:"as_of"`
	UpperSequence int64     `json:"upper_sequence"`
	UpperCreated  time.Time `json:"upper_created"`
	UpperID       funds.ID  `json:"upper_id"`
	AfterCreated  time.Time `json:"after_created"`
	AfterID       funds.ID  `json:"after_id"`
	Total         int64     `json:"total"`
	Limit         int       `json:"limit"`
}

func (s *Service) encodeActivityCursor(c activityCursor) (string, error) {
	raw, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	m := hmac.New(sha256.New, s.PermitSecret)
	_, _ = m.Write([]byte(activityCursorVersion + "\x00"))
	_, _ = m.Write(raw)
	return base64.RawURLEncoding.EncodeToString(raw) + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil)), nil
}

func (s *Service) decodeActivityCursor(raw string, p Principal, limit int) (activityCursor, error) {
	var c activityCursor
	if len(raw) > 2048 {
		return c, reject("INVALID_CURSOR", nil)
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 2 {
		return c, reject("INVALID_CURSOR", nil)
	}
	b, e := base64.RawURLEncoding.DecodeString(parts[0])
	if e != nil {
		return c, reject("INVALID_CURSOR", e)
	}
	sig, e := base64.RawURLEncoding.DecodeString(parts[1])
	if e != nil {
		return c, reject("INVALID_CURSOR", e)
	}
	m := hmac.New(sha256.New, s.PermitSecret)
	_, _ = m.Write([]byte(activityCursorVersion + "\x00"))
	_, _ = m.Write(b)
	if !hmac.Equal(sig, m.Sum(nil)) || json.Unmarshal(b, &c) != nil {
		return c, reject("INVALID_CURSOR", nil)
	}
	if c.Version != activityCursorVersion || c.Environment != p.EnvironmentID || c.User != p.UserID || c.Limit != limit || c.Total < 0 || c.UpperSequence < 1 || c.AsOf.After(s.now()) || !s.now().Before(c.AsOf.Add(15*time.Minute)) || c.AfterID == (funds.ID{}) || c.UpperID == (funds.ID{}) {
		return c, reject("INVALID_CURSOR", nil)
	}
	return c, nil
}

// Activities binds membership to the first page's immutable event sequence and
// count while holding the same user lock as all outbox writers. Its cursor is
// scoped, signed and short lived; later inserts cannot appear in this walk.
// No retained payload is selected or decoded, even for a 20-item page.
func (s *Service) Activities(ctx context.Context, p Principal, limit int, cursor string) (ActivityPage, error) {
	v := ActivityPage{Items: []Activity{}, VerificationScope: "ACTIVITY_MEMBERSHIP_SNAPSHOT"}
	if limit == 0 {
		limit = 20
	}
	if limit < 1 || limit > 100 {
		return v, reject("CAPACITY_EXCEEDED", nil)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return v, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, false)
	if err != nil {
		return v, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return v, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return v, err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return v, err
	}
	c := activityCursor{Version: activityCursorVersion, Environment: env.ID, User: p.UserID, AsOf: s.now(), Limit: limit}
	if cursor != "" {
		c, err = s.decodeActivityCursor(cursor, p, limit)
		if err != nil {
			return v, err
		}
	} else {
		var id string
		err = tx.QueryRow(ctx, `SELECT event_sequence,created_at,id::text FROM outbox WHERE environment_id=$1 AND user_id=$2 ORDER BY event_sequence DESC LIMIT 1`, env.ID.String(), p.UserID.String()).Scan(&c.UpperSequence, &c.UpperCreated, &id)
		if errors.Is(err, pgx.ErrNoRows) {
			v.AsOf = c.AsOf
			return v, tx.Commit(ctx)
		}
		if err != nil {
			return v, err
		}
		c.UpperID, err = funds.ParseID(id)
		if err != nil {
			return v, err
		}
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM outbox WHERE environment_id=$1 AND user_id=$2 AND event_sequence<=$3`, env.ID.String(), p.UserID.String(), c.UpperSequence).Scan(&c.Total); err != nil {
			return v, err
		}
	}
	v.AsOf, v.Total = c.AsOf, c.Total
	query := `SELECT e.id::text,e.kind,e.aggregate_id::text,e.aggregate_version,e.created_at,n.id::text,l.id::text,CASE n.command_kind WHEN 'BRIDGE_TRANSFER' THEN 'TRANSFER_INTERNAL' WHEN 'BRIDGE_ALLOCATE' THEN 'ALLOCATE_GOAL' WHEN 'STANDING_BRIDGE_TRANSFER' THEN 'TRANSFER_INTERNAL' WHEN 'STANDING_BRIDGE_ALLOCATE' THEN 'ALLOCATE_GOAL' WHEN 'STANDING_ALLOCATE' THEN 'ALLOCATE_GOAL' WHEN 'JOINT_ALLOCATE' THEN 'ALLOCATE_GOAL' ELSE n.command_kind END,CASE WHEN e.kind='NATIVE_OPERATION_SETTLED' THEN r.id::text END FROM outbox e LEFT JOIN native_operations n ON(n.environment_id=e.environment_id AND n.user_id=e.user_id AND n.id=e.aggregate_id) LEFT JOIN operations l ON(l.environment_id=e.environment_id AND l.user_id=e.user_id AND l.id=e.aggregate_id) LEFT JOIN native_receipts r ON(r.environment_id=n.environment_id AND r.user_id=n.user_id AND r.operation_id=n.id) WHERE e.environment_id=$1 AND e.user_id=$2 AND e.event_sequence<=$3`
	args := []any{env.ID.String(), p.UserID.String(), c.UpperSequence}
	if cursor != "" {
		query += ` AND (e.created_at,e.id)<($4,$5::uuid)`
		args = append(args, c.AfterCreated, c.AfterID.String())
	}
	query += ` ORDER BY e.created_at DESC,e.id DESC LIMIT `
	// The numeric limit is fully range checked; all identity/cursor data remain
	// parameters. Avoid ever constructing an unbounded detail query.
	if cursor != "" {
		query += `$6`
		args = append(args, limit+1)
	} else {
		query += `$4`
		args = append(args, limit+1)
	}
	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return v, err
	}
	for rows.Next() {
		var a Activity
		var id, aggregate string
		var nativeID, legacyID, kind, receipt *string
		if err = rows.Scan(&id, &a.Kind, &aggregate, &a.AggregateVersion, &a.CreatedAt, &nativeID, &legacyID, &kind, &receipt); err != nil {
			rows.Close()
			return v, err
		}
		a.ID, err = funds.ParseID(id)
		if err != nil {
			rows.Close()
			return v, err
		}
		a.AggregateID, err = funds.ParseID(aggregate)
		if err != nil {
			rows.Close()
			return v, err
		}
		if nativeID != nil && legacyID != nil {
			rows.Close()
			return v, reject("EVIDENCE_INVALID", nil)
		}
		if nativeID != nil {
			a.OperationFamily = "NATIVE"
			if kind == nil {
				rows.Close()
				return v, reject("EVIDENCE_INVALID", nil)
			}
			a.OperationKind = *kind
		} else if legacyID != nil {
			a.OperationFamily = "LEGACY"
		}
		if receipt != nil {
			parsed, e := funds.ParseID(*receipt)
			if e != nil {
				rows.Close()
				return v, e
			}
			a.ReceiptID = &parsed
		}
		v.Items = append(v.Items, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return v, err
	}
	if len(v.Items) > limit {
		v.HasMore = true
		v.Items = v.Items[:limit]
		last := v.Items[len(v.Items)-1]
		c.AfterCreated, c.AfterID = last.CreatedAt, last.ID
		v.NextCursor, err = s.encodeActivityCursor(c)
		if err != nil {
			return v, err
		}
	}
	v.Returned = len(v.Items)
	return v, tx.Commit(ctx)
}
