package app

import (
	"context"
	"encoding/hex"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankcredit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativeOccurrenceDiscoveryBatch = 64

type nativeIncomeOccurrencePayload struct {
	Protocol          string `json:"protocol"`
	OriginID          string `json:"origin_id"`
	EvidenceID        string `json:"evidence_id"`
	RecordHash        string `json:"record_hash"`
	ExternalAuthority string `json:"external_authority"`
	ExternalEventID   string `json:"external_event_id"`
	CreditSequence    int64  `json:"credit_sequence"`
	SourceVersion     uint64 `json:"source_version"`
	BankCaptureHash   string `json:"bank_capture_hash"`
}

type nativeAutomationEvent struct {
	Protocol     string                    `json:"protocol"`
	Scope        policy.TemplateOwnerScope `json:"scope"`
	GrantID      funds.ID                  `json:"grant_id"`
	GrantVersion uint64                    `json:"grant_version"`
	ID           funds.ID                  `json:"id"`
	AggregateID  funds.ID                  `json:"aggregate_id"`
	Kind         string                    `json:"kind"`
	PayloadHex   string                    `json:"payload_hex"`
	CreatedAt    time.Time                 `json:"created_at"`
}

// DiscoverNativeOccurrences owns an independent inbox. It never changes
// outbox.processed_at, replaces a source key, reserves funds or calls a bank.
// A persistent receipt also retains bad/unsupported events without poisoning
// all subsequent events. Paused grants may observe facts but cannot admit T1.
func (s *Service) DiscoverNativeOccurrences(ctx context.Context) (int, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return 0, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, false)
	if err != nil {
		return 0, err
	}
	rows, err := tx.Query(ctx, `SELECT o.user_id::text,g.id::text,g.version,o.id::text,o.aggregate_id::text,o.kind,o.payload,o.created_at FROM outbox o JOIN native_grants g ON(g.environment_id=o.environment_id AND g.user_id=o.user_id) WHERE o.environment_id=$1 AND o.kind='INCOME_IMPORTED' AND g.family IN ('SINGLE_GOAL','JOINT_GOALS','ASSET_ALLOCATION') AND o.created_at>=g.confirmed_at AND o.created_at<g.expires_at AND NOT EXISTS(SELECT 1 FROM native_automation_event_receipts r WHERE r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.grant_id=g.id AND r.grant_version=g.version AND r.event_id=o.id) ORDER BY o.created_at,o.id,g.id LIMIT $2`, env.ID.String(), nativeOccurrenceDiscoveryBatch)
	if err != nil {
		return 0, err
	}
	events := []nativeAutomationEvent{}
	for rows.Next() {
		var user, grant, id, aggregate string
		var payload []byte
		event := nativeAutomationEvent{Protocol: NativeAutomationProtocol + "/event", Scope: policy.TemplateOwnerScope{EnvironmentID: env.ID}}
		if err = rows.Scan(&user, &grant, &event.GrantVersion, &id, &aggregate, &event.Kind, &payload, &event.CreatedAt); err != nil {
			rows.Close()
			return 0, err
		}
		for _, item := range []struct {
			raw    string
			target *funds.ID
		}{{user, &event.Scope.UserID}, {grant, &event.GrantID}, {id, &event.ID}, {aggregate, &event.AggregateID}} {
			*item.target, err = funds.ParseID(item.raw)
			if err != nil {
				rows.Close()
				return 0, err
			}
		}
		event.CreatedAt = event.CreatedAt.UTC()
		event.PayloadHex = hex.EncodeToString(payload)
		events = append(events, event)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return 0, err
	}
	registered := 0
	for _, event := range events {
		p := Principal{EnvironmentID: event.Scope.EnvironmentID, UserID: event.Scope.UserID, Kind: "SYSTEM"}
		grant, err := loadNativeStandingGrant(ctx, tx, p, event.GrantID)
		if err != nil {
			return 0, err
		}
		source, sourceRaw, outcome, reason, err := s.nativeIncomeOccurrenceSource(ctx, tx, event, grant.Grant)
		if err != nil {
			return 0, err
		}
		eventRaw, err := encode(event)
		if err != nil {
			return 0, err
		}
		tag, err := tx.Exec(ctx, `INSERT INTO native_automation_event_receipts(environment_id,user_id,grant_id,grant_version,event_id,outcome,reason,original,original_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), event.GrantID.String(), event.GrantVersion, event.ID.String(), outcome, reason, eventRaw, Hash(eventRaw))
		if err != nil {
			return 0, err
		}
		if tag.RowsAffected() == 0 || outcome != "REGISTERED" {
			continue
		}
		_, created, err := registerNativeOccurrence(ctx, tx, p, grant.Grant, source, &event.ID, sourceRaw, s.now())
		if err != nil {
			return 0, err
		}
		if created {
			registered++
		}
	}
	return registered, tx.Commit(ctx)
}

func (s *Service) nativeIncomeOccurrenceSource(ctx context.Context, q queryRow, event nativeAutomationEvent, grant NativeStandingGrant) (policy.NativeOccurrenceSource, []byte, string, string, error) {
	var source policy.NativeOccurrenceSource
	payloadRaw, err := hex.DecodeString(event.PayloadHex)
	if err != nil {
		return source, nil, "INVALID", "EVENT_HEX_INVALID", nil
	}
	var payload nativeIncomeOccurrencePayload
	if auditJSON(payloadRaw, &payload) != nil || payload.Protocol != nativeIncomeProtocol {
		return source, nil, "UNSUPPORTED", "INCOME_EVENT_PROTOCOL_UNSUPPORTED", nil
	}
	if payload.OriginID != event.AggregateID.String() || payload.EvidenceID != event.ID.String() || !nativeActionHash(payload.RecordHash) || !nativeActionHash(payload.BankCaptureHash) || payload.SourceVersion == 0 {
		return source, nil, "INVALID", "INCOME_EVENT_IDENTITY_DIFFERS", nil
	}
	var raw []byte
	var kind, digest string
	err = q.QueryRow(ctx, `SELECT kind,canonical,digest FROM evidence_blobs WHERE environment_id=$1 AND user_id=$2 AND id=$3`, event.Scope.EnvironmentID.String(), event.Scope.UserID.String(), payload.EvidenceID).Scan(&kind, &raw, &digest)
	if errors.Is(err, pgx.ErrNoRows) {
		return source, nil, "INVALID", "INCOME_ORIGINAL_MISSING", nil
	}
	if err != nil {
		return source, nil, "", "", err
	}
	var record bankcredit.Record
	if kind != "BANK_EXTERNAL_CREDIT_V1" || Hash(raw) != digest || digest != payload.RecordHash || auditJSON(raw, &record) != nil || record.Validate() != nil {
		return source, nil, "INVALID", "INCOME_ORIGINAL_INVALID", nil
	}
	e := record.Event
	if e.Namespace != s.Namespace || e.UserID != event.Scope.UserID.String() || e.OriginID != payload.OriginID || e.EvidenceID != payload.EvidenceID || e.AuthorityID != payload.ExternalAuthority || e.EventID != payload.ExternalEventID || record.Sequence != payload.CreditSequence || record.CreditedAt.After(event.CreatedAt) {
		return source, nil, "INVALID", "INCOME_EVENT_SCOPE_OR_TIME_DIFFERS", nil
	}
	var amount int64
	var originalAccount, evidence, hash string
	var occurred time.Time
	err = q.QueryRow(ctx, `SELECT original_account_id::text,amount_cents,occurred_at,evidence_id::text,evidence_hash::text FROM income_origins WHERE environment_id=$1 AND user_id=$2 AND id=$3`, event.Scope.EnvironmentID.String(), event.Scope.UserID.String(), payload.OriginID).Scan(&originalAccount, &amount, &occurred, &evidence, &hash)
	if errors.Is(err, pgx.ErrNoRows) {
		return source, nil, "INVALID", "INCOME_ORIGIN_MISSING", nil
	}
	if err != nil {
		return source, nil, "", "", err
	}
	if originalAccount != e.AccountID || amount != e.Amount.Cents() || !occurred.Equal(e.OccurredAt) || evidence != e.EvidenceID || hash != digest {
		return source, nil, "INVALID", "INCOME_ORIGIN_ORIGINAL_DIFFERS", nil
	}
	if e.OccurredAt.Before(grant.Terms.EffectiveFrom) || !e.OccurredAt.Before(grant.Terms.ExpiresAt) {
		return source, nil, "NOT_ELIGIBLE", "ORIGINAL_INCOME_OUTSIDE_GRANT_WINDOW", nil
	}
	source = policy.NativeOccurrenceSource{Kind: "INCOME_EVENT", ID: e.OriginID, OriginalHash: digest, OccurredAt: e.OccurredAt, AvailableAt: record.CreditedAt}
	if source.Validate(grant.Terms.Family) != nil {
		return source, nil, "INVALID", "INCOME_SOURCE_INVALID", nil
	}
	capture, err := encode(struct {
		Protocol      string                `json:"protocol"`
		Event         nativeAutomationEvent `json:"event"`
		BankRecordHex string                `json:"bank_record_hex"`
	}{NativeAutomationProtocol + "/income-source", event, hex.EncodeToString(raw)})
	return source, capture, "REGISTERED", "", err
}
