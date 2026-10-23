package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

var ErrNativeOccurrenceClaimLost = errors.New("native occurrence claim no longer current")

type NativeFamilyCapability struct {
	Family    policy.NativeFamily `json:"family"`
	Qualified bool                `json:"qualified"`
	Reason    string              `json:"reason"`
}

// A grant is necessary but cannot open an unverified execution family.
// Qualification changes only with that family's real event/T1/T2/T3/recovery
// tests, never with a browser flag, grant payload or another family's success.
func NativeAutomationCapabilities() []NativeFamilyCapability {
	out := []NativeFamilyCapability{}
	for _, family := range []policy.NativeFamily{policy.NativeSingleGoal, policy.NativeJointGoals, policy.NativeAssetAllocation, policy.NativeMaturityReinvest, policy.NativePeriodicPayment} {
		c := NativeFamilyCapability{Family: family, Reason: "FAMILY_EVENT_BANK_RECOVERY_ACCEPTANCE_PENDING"}
		if family == policy.NativeSingleGoal {
			c.Qualified, c.Reason = true, "ACTUAL_PG_BANK_HTTP_T1_T2_T3_AND_CLI_PROCESS_RECOVERY_VERIFIED"
		}
		if family == policy.NativePeriodicPayment {
			c.Qualified, c.Reason = true, "ACTUAL_PG_BANK_HTTP_T1_T2_T3_AND_ORIGINAL_KEY_RECOVERY_VERIFIED"
		}
		if family == policy.NativeJointGoals {
			c.Qualified, c.Reason = true, "SAME_AND_CROSS_ACCOUNT_2_TO_8_BANK_GROUP_AND_ORIGINAL_PREFIX_RECOVERY_VERIFIED"
		}
		if family == policy.NativeAssetAllocation || family == policy.NativeMaturityReinvest {
			c.Qualified, c.Reason = true, "ACTUAL_PG_BANK_HTTP_T1_T2_T3_AND_ORIGINAL_KEY_RECOVERY_VERIFIED"
		}
		out = append(out, c)
	}
	return out
}

func qualifiedNativeFamilies() []string {
	out := []string{}
	for _, c := range NativeAutomationCapabilities() {
		if c.Qualified {
			out = append(out, string(c.Family))
		}
	}
	return out
}

func loadNativeOccurrence(ctx context.Context, q queryRow, p Principal, id funds.ID) (NativeOccurrence, error) {
	var out NativeOccurrence
	var raw, source []byte
	var hash, key, sourceHash string
	err := q.QueryRow(ctx, `SELECT original,original_hash,occurrence_key,source_original,source_original_hash FROM native_occurrences WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash, &key, &source, &sourceHash)
	if err != nil {
		return out, reject("NOT_FOUND", err)
	}
	if Hash(raw) != hash || Hash(source) != sourceHash || auditJSON(raw, &out) != nil || out.Protocol != NativeAutomationProtocol+"/occurrence" || out.Scope != nativeAutomationScope(p) || out.ID != id || out.GrantVersion != 1 || out.Source.Validate(out.Family) != nil || out.Key != key {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	want, e := policy.NativeOccurrenceKey(out.Scope, out.GrantID, out.Family, out.Source)
	if e != nil || want != key {
		return out, reject("EVIDENCE_INVALID", e)
	}
	return out, nil
}

func (s *Service) ClaimNativeOccurrence(ctx context.Context, owner string, lease time.Duration) (*NativeOccurrenceClaim, error) {
	return s.claimNativeOccurrence(ctx, owner, lease, qualifiedNativeFamilies())
}

// families is a server code qualification input, never a public request field.
// Tests exercise the queue using this private method while all production
// family adapters remain closed. Claiming an event grants no funds permission.
func (s *Service) claimNativeOccurrence(ctx context.Context, owner string, lease time.Duration, families []string) (*NativeOccurrenceClaim, error) {
	if owner == "" || len(owner) > 128 || lease < time.Millisecond || lease > time.Minute {
		return nil, reject("INVALID_WORKER_CONFIGURATION", nil)
	}
	if len(families) == 0 {
		return nil, nil
	}
	if len(families) > 5 {
		return nil, reject("INVALID_WORKER_CONFIGURATION", nil)
	}
	seen := map[string]bool{}
	for _, f := range families {
		if !policy.NativeFamily(f).Valid() || seen[f] {
			return nil, reject("INVALID_WORKER_CONFIGURATION", nil)
		}
		seen[f] = true
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer rollback(ctx, tx)
	if _, err = s.environment(ctx, tx, true); err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE native_occurrences o SET state='EXHAUSTED',lease_owner=NULL,lease_until=NULL,last_error='RETRY_EXHAUSTED',finished_at=clock_timestamp() FROM environments e WHERE o.environment_id=e.id AND e.namespace=$1 AND o.family=ANY($2::text[]) AND o.state IN ('READY','RUNNING') AND o.attempts>=o.max_attempts AND (o.lease_until IS NULL OR o.lease_until<=clock_timestamp())`, s.Namespace, families)
	if err != nil {
		return nil, err
	}
	var env, user, id string
	var out NativeOccurrenceClaim
	err = tx.QueryRow(ctx, `WITH picked AS(SELECT o.environment_id,o.user_id,o.id FROM native_occurrences o JOIN environments e ON(e.id=o.environment_id) JOIN native_grants g ON(g.environment_id=o.environment_id AND g.user_id=o.user_id AND g.id=o.grant_id AND g.version=o.grant_version) JOIN native_grant_states st ON(st.environment_id=g.environment_id AND st.user_id=g.user_id AND st.grant_id=g.id AND st.grant_version=g.version) WHERE e.namespace=$1 AND o.family=ANY($2::text[]) AND st.state='ACTIVE' AND g.effective_from<=$5 AND g.expires_at>$5 AND o.state IN ('READY','RUNNING') AND o.attempts<o.max_attempts AND o.next_attempt_at<=clock_timestamp() AND (o.lease_until IS NULL OR o.lease_until<=clock_timestamp()) ORDER BY o.next_attempt_at,o.id FOR UPDATE OF o SKIP LOCKED LIMIT 1) UPDATE native_occurrences o SET state='RUNNING',claim_generation=o.claim_generation+1,lease_owner=$3,lease_until=clock_timestamp()+$4::bigint*interval '1 millisecond',attempts=o.attempts+1 FROM picked p WHERE o.environment_id=p.environment_id AND o.user_id=p.user_id AND o.id=p.id RETURNING o.environment_id::text,o.user_id::text,o.id::text,o.claim_generation,o.attempts,o.max_attempts`, s.Namespace, families, owner, lease.Milliseconds(), s.now()).Scan(&env, &user, &id, &out.Generation, &out.Attempts, &out.MaxAttempts)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	p := Principal{Kind: "SYSTEM"}
	p.EnvironmentID, err = funds.ParseID(env)
	if err != nil {
		return nil, err
	}
	p.UserID, err = funds.ParseID(user)
	if err != nil {
		return nil, err
	}
	occurrenceID, err := funds.ParseID(id)
	if err != nil {
		return nil, err
	}
	out.Occurrence, err = loadNativeOccurrence(ctx, tx, p, occurrenceID)
	if err != nil {
		return nil, err
	}
	out.Owner = owner
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &out, nil
}

func nativeCurrentOccurrenceClaim(ctx context.Context, tx pgx.Tx, c NativeOccurrenceClaim) error {
	var valid bool
	err := tx.QueryRow(ctx, `SELECT claim_generation=$4 AND lease_owner=$5 AND lease_until>clock_timestamp() AND state='RUNNING' FROM native_occurrences WHERE environment_id=$1 AND user_id=$2 AND id=$3 FOR UPDATE`, c.Occurrence.Scope.EnvironmentID.String(), c.Occurrence.Scope.UserID.String(), c.Occurrence.ID.String(), c.Generation, c.Owner).Scan(&valid)
	if err != nil {
		return err
	}
	if !valid {
		return ErrNativeOccurrenceClaimLost
	}
	p := Principal{EnvironmentID: c.Occurrence.Scope.EnvironmentID, UserID: c.Occurrence.Scope.UserID, Kind: "SYSTEM"}
	original, err := loadNativeOccurrence(ctx, tx, p, c.Occurrence.ID)
	if err != nil {
		return err
	}
	want, _ := encode(original)
	got, _ := encode(c.Occurrence)
	if string(want) != string(got) {
		return ErrNativeOccurrenceClaimLost
	}
	return nil
}

func nativeFinishOccurrence(ctx context.Context, tx pgx.Tx, c NativeOccurrenceClaim, state, reason string) error {
	if state != "ACCEPTED" && state != "NO_ACTION" && state != "BLOCKED" && state != "EXHAUSTED" {
		return reject("INVALID_OCCURRENCE_OUTCOME", nil)
	}
	tag, err := tx.Exec(ctx, `UPDATE native_occurrences SET state=$6,last_error=$7,lease_owner=NULL,lease_until=NULL,finished_at=clock_timestamp() WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND claim_generation=$4 AND lease_owner=$5 AND state='RUNNING' AND lease_until>clock_timestamp()`, c.Occurrence.Scope.EnvironmentID.String(), c.Occurrence.Scope.UserID.String(), c.Occurrence.ID.String(), c.Generation, c.Owner, state, reason)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return ErrNativeOccurrenceClaimLost
	}
	return nil
}

func (s *Service) ProcessNativeOccurrence(ctx context.Context, c NativeOccurrenceClaim) error {
	if c.Occurrence.Family == policy.NativeSingleGoal {
		return s.admitStandingAllocation(ctx, c)
	}
	if c.Occurrence.Family == policy.NativeJointGoals {
		return s.admitStandingJoint(ctx, c)
	}
	if c.Occurrence.Family == policy.NativePeriodicPayment {
		return s.admitStandingPayment(ctx, c)
	}
	if c.Occurrence.Family == policy.NativeAssetAllocation || c.Occurrence.Family == policy.NativeMaturityReinvest {
		return s.admitStandingAsset(ctx, c)
	}
	// No family may fall back to a current USER session or the old v1 grant.
	// Each dedicated adapter will enter its own locked STANDING T1 and record
	// its budget/operation binding before this queue can become ACCEPTED.
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return err
	}
	p := Principal{EnvironmentID: c.Occurrence.Scope.EnvironmentID, UserID: c.Occurrence.Scope.UserID, Kind: "SYSTEM"}
	if err = sameUser(p, env.ID); err != nil {
		return err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return err
	}
	if err = nativeCurrentOccurrenceClaim(ctx, tx, c); err != nil {
		return err
	}
	if err = nativeFinishOccurrence(ctx, tx, c, "BLOCKED", "FAMILY_NOT_QUALIFIED"); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Service) NativeStandingStep(ctx context.Context, owner string, lease time.Duration) (bool, error) {
	intakeWorked, intakeErr := s.NativeIncomeIntakeStep(ctx, owner, lease)
	if intakeErr != nil && Code(intakeErr) == "INTERNAL_ERROR" && !errors.Is(intakeErr, ErrNativeIncomeIntakeClaimLost) {
		return intakeWorked, intakeErr
	}
	discovered, err := s.DiscoverNativeOccurrences(ctx)
	if err != nil {
		return intakeWorked, err
	}
	payments, err := s.discoverNativePaymentOccurrences(ctx)
	if err != nil {
		return intakeWorked || discovered > 0, err
	}
	discovered += payments
	returns, err := s.DiscoverStandingMaturityReturns(ctx)
	if err != nil {
		return intakeWorked || discovered > 0, err
	}
	discovered += returns
	c, err := s.ClaimNativeOccurrence(ctx, owner, lease)
	if err != nil || c == nil {
		if err == nil {
			err = intakeErr
		}
		return intakeWorked || discovered > 0, err
	}
	err = s.ProcessNativeOccurrence(ctx, *c)
	if err != nil {
		return true, s.retryNativeOccurrence(ctx, *c, err)
	}
	return true, nil
}
