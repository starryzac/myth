package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// The family collector must validate its actual bank/source original before
// calling this private observation helper. It preserves the first capture and
// identity even if a later RR observation has a different captured_at/paid.
// It grants no execution permission and is not an HTTP input endpoint.
func registerNativeOccurrence(ctx context.Context, tx pgx.Tx, p Principal, grant NativeStandingGrant, source policy.NativeOccurrenceSource, eventID *funds.ID, sourceOriginal []byte, now time.Time) (NativeOccurrence, bool, error) {
	var zero NativeOccurrence
	if p.Kind != "SYSTEM" || p.SessionID != (funds.ID{}) || grant.Scope != nativeAutomationScope(p) || grant.Terms.Scope != grant.Scope || grant.Terms.Validate() != nil || source.Validate(grant.Terms.Family) != nil || len(sourceOriginal) == 0 || len(sourceOriginal) > 1<<20 || now.IsZero() || (source.Kind == "INCOME_EVENT") != (eventID != nil) {
		return zero, false, reject("INVALID_NATIVE_OCCURRENCE", nil)
	}
	source.OccurredAt = source.OccurredAt.UTC()
	source.AvailableAt = source.AvailableAt.UTC()
	now = now.UTC().Truncate(time.Microsecond)
	if eventID != nil && *eventID == (funds.ID{}) {
		return zero, false, reject("INVALID_NATIVE_OCCURRENCE", nil)
	}
	if source.Kind == "INCOME_EVENT" && (source.OccurredAt.Before(grant.Terms.EffectiveFrom) || !source.OccurredAt.Before(grant.Terms.ExpiresAt)) {
		return zero, false, reject("NATIVE_SOURCE_OUTSIDE_GRANT_WINDOW", nil)
	}
	key, err := policy.NativeOccurrenceKey(grant.Scope, grant.ID, grant.Terms.Family, source)
	if err != nil {
		return zero, false, reject("INVALID_NATIVE_OCCURRENCE", err)
	}
	var old string
	err = tx.QueryRow(ctx, `SELECT id::text FROM native_occurrences WHERE environment_id=$1 AND user_id=$2 AND grant_id=$3 AND grant_version=$4 AND family=$5 AND source_kind=$6 AND source_id=$7`, p.EnvironmentID.String(), p.UserID.String(), grant.ID.String(), grant.Version, string(grant.Terms.Family), source.Kind, source.ID).Scan(&old)
	if err == nil {
		id, e := funds.ParseID(old)
		if e != nil {
			return zero, false, e
		}
		original, e := loadNativeOccurrence(ctx, tx, p, id)
		if e != nil {
			return zero, false, e
		}
		if original.Key != key {
			return zero, false, reject("NATIVE_SOURCE_ORIGINAL_CONFLICT", nil)
		}
		return original, false, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, false, err
	}
	id, err := newID()
	if err != nil {
		return zero, false, err
	}
	out := NativeOccurrence{Protocol: NativeAutomationProtocol + "/occurrence", ID: id, Scope: grant.Scope, GrantID: grant.ID, GrantVersion: grant.Version, Family: grant.Terms.Family, Key: key, Source: source, CreatedAt: now}
	raw, err := encode(out)
	if err != nil {
		return zero, false, err
	}
	var event any
	if eventID != nil {
		event = eventID.String()
	}
	tag, err := tx.Exec(ctx, `INSERT INTO native_occurrences(environment_id,user_id,id,grant_id,grant_version,family,occurrence_key,source_kind,source_id,source_hash,event_id,original,original_hash,source_original,source_original_hash,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) ON CONFLICT(environment_id,user_id,grant_id,grant_version,family,source_kind,source_id) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), id.String(), grant.ID.String(), grant.Version, string(out.Family), key, source.Kind, source.ID, source.OriginalHash, event, raw, Hash(raw), sourceOriginal, Hash(sourceOriginal), now)
	if err != nil {
		return zero, false, err
	}
	if tag.RowsAffected() == 0 {
		return registerNativeOccurrence(ctx, tx, p, grant, source, eventID, sourceOriginal, now)
	}
	return out, true, nil
}

// Call after the family's real deterministic T1 validation and operation
// insertion, inside the common environment/user lock and current event claim.
// The deferred SQL guard requires at least one exact same-creation-T1 binding.
func reserveNativeOccurrenceBudget(ctx context.Context, tx pgx.Tx, p Principal, c NativeOccurrenceClaim, grant NativeStandingGrant, amount funds.Money, original []byte, now time.Time) error {
	if p.Kind != "SYSTEM" || p.SessionID != (funds.ID{}) || c.Occurrence.Scope != nativeAutomationScope(p) || grant.Scope != c.Occurrence.Scope || grant.ID != c.Occurrence.GrantID || grant.Version != c.Occurrence.GrantVersion || grant.Terms.Family != c.Occurrence.Family || amount.IsZero() || len(original) == 0 || len(original) > 1<<20 {
		return reject("INVALID_NATIVE_BUDGET", nil)
	}
	if err := nativeCurrentOccurrenceClaim(ctx, tx, c); err != nil {
		return err
	}
	current, err := loadNativeStandingGrant(ctx, tx, p, grant.ID)
	if err != nil {
		return err
	}
	want, _ := encode(grant)
	have, _ := encode(current.Grant)
	if string(want) != string(have) {
		return reject("EVIDENCE_INVALID", nil)
	}
	if current.State != "ACTIVE" || now.Before(grant.Terms.EffectiveFrom) || !now.Before(grant.Terms.ExpiresAt) {
		return reject("NATIVE_GRANT_NOT_ACTIVE", nil)
	}
	usage, err := nativeStandingUsage(ctx, tx, p, grant, now, nil)
	if err != nil {
		return err
	}
	life, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved, amount)
	month, e2 := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved, amount)
	if e != nil || e2 != nil || amount.Cents() > grant.Terms.SingleCap.Cents() || life.Cents() > grant.Terms.LifetimeCap.Cents() || month.Cents() > grant.Terms.MonthlyCap.Cents() {
		return reject("NATIVE_STANDING_CAP_EXCEEDED", nil)
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_grant_budget_claims(environment_id,user_id,occurrence_id,grant_id,grant_version,period,amount_cents,original,original_hash,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), c.Occurrence.ID.String(), grant.ID.String(), grant.Version, usage.Period, amount.Cents(), original, Hash(original), now)
	return err
}

func bindNativeOccurrenceOperation(ctx context.Context, tx pgx.Tx, p Principal, occurrence, operation funds.ID, economicAmount funds.Money, effectHash, bankRequestHash string, now time.Time) error {
	if p.Kind != "SYSTEM" || p.SessionID != (funds.ID{}) || occurrence == (funds.ID{}) || operation == (funds.ID{}) || !nativeActionHash(effectHash) || !nativeActionHash(bankRequestHash) || now.IsZero() {
		return reject("INVALID_NATIVE_BUDGET_BINDING", nil)
	}
	_, err := tx.Exec(ctx, `INSERT INTO native_occurrence_operations(environment_id,user_id,occurrence_id,operation_id,economic_amount_cents,effect_hash,bank_request_hash,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, p.EnvironmentID.String(), p.UserID.String(), occurrence.String(), operation.String(), economicAmount.Cents(), effectHash, bankRequestHash, now)
	return err
}
