package app

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

func (s *Service) loadTemplateHistory(ctx context.Context, tx pgx.Tx, p Principal, snapshot *templateSnapshot) error {
	if len(snapshot.Raw["template_policy_versions"]) > 1000 {
		return reject("TEMPLATE_HISTORY_CAPACITY", nil)
	}
	for _, raw := range snapshot.Raw["template_policy_versions"] {
		var row struct {
			ID funds.ID `json:"id"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return reject("TEMPLATE_HISTORY_INVALID", nil)
		}
		views, err := s.loadTemplatePoliciesAt(ctx, tx, p, &row.ID)
		if err != nil || len(views) != 1 || views[0].Timezone != snapshot.Timezone {
			return reject("TEMPLATE_HISTORY_INVALID", err)
		}
		snapshot.HistoricalVersions = append(snapshot.HistoricalVersions, views[0].Version)
	}
	for _, raw := range snapshot.Raw["template_policy_status_events"] {
		var row struct {
			ID        funds.ID  `json:"id"`
			VersionID funds.ID  `json:"version_id"`
			Sequence  uint64    `json:"sequence"`
			Action    string    `json:"action"`
			OldState  *string   `json:"old_state"`
			NewState  string    `json:"new_state"`
			ActorKind string    `json:"actor_kind"`
			At        time.Time `json:"occurred_at"`
			Original  string    `json:"original"`
			Hash      string    `json:"original_hash"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return reject("TEMPLATE_HISTORY_INVALID", nil)
		}
		original, err := decodePGBytea(row.Original)
		var event templateStatusOriginal
		oldState := ""
		if row.OldState != nil {
			oldState = *row.OldState
		}
		if err != nil || Hash(original) != row.Hash || auditJSON(original, &event) != nil || event.Protocol != "template-lifecycle-v1" || event.ID != row.ID || event.VersionID != row.VersionID || event.Sequence != row.Sequence || event.Action != row.Action || event.OldState != oldState || event.NewState != row.NewState || event.ActorKind != row.ActorKind || !event.At.Equal(row.At) {
			return reject("TEMPLATE_HISTORY_INVALID", err)
		}
		snapshot.HistoricalEvents = append(snapshot.HistoricalEvents, planning.HistoricalPolicyEvent{ID: event.ID, VersionID: event.VersionID, Sequence: event.Sequence, Action: event.Action, OldState: event.OldState, NewState: event.NewState, ActorKind: event.ActorKind, At: event.At, Verified: true, SourceHash: row.Hash})
	}
	return nil
}

func expandTemplateHistory(snapshot *templateSnapshot) error {
	input := planning.HistoricalObligationInput{Scope: snapshot.Facts.Scope, AsOf: snapshot.AsOf, Timezone: snapshot.Timezone, Complete: snapshot.Facts.Complete, InventoryHash: Hash(snapshot.InventoryRaw), Versions: snapshot.HistoricalVersions, Events: snapshot.HistoricalEvents, Registered: snapshot.Retained, Settlements: snapshot.Facts.Settlements, Bills: snapshot.HistoricalBills}
	// First-observed proof comes from the immutable trusted capture repository.
	if len(snapshot.Facts.Bills) != len(snapshot.HistoricalBills) {
		return reject("TEMPLATE_BILL_HISTORY_INCOMPLETE", nil)
	}
	result, err := planning.ExpandHistoricalObligations(input)
	if err != nil {
		return reject("TEMPLATE_HISTORY_INCOMPLETE", err)
	}
	snapshot.History = result
	snapshot.Retained = result.Obligations
	return nil
}

func (s *Service) persistTemplateHistory(ctx context.Context, tx pgx.Tx, p Principal, snapshot templateSnapshot) (bool, error) {
	changed := false
	for _, adoption := range snapshot.History.Adoptions {
		original := adoption.Original
		if original.Amount.IsZero() {
			// No positive obligation was declared. There is no erased debt or
			// payment to infer from this exact zero-total statement.
			continue
		}
		raw, err := encode(original)
		if err != nil {
			return false, err
		}
		tag, err := tx.Exec(ctx, `INSERT INTO template_obligations(environment_id,user_id,id,policy_id,version_id,period_key,kind,due_date,amount_cents,original,original_hash,adopted_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8::date,$9,$10,$11,$12) ON CONFLICT(environment_id,user_id,id) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), original.ID, original.PolicyID.String(), original.VersionID.String(), adoption.Period, adoption.Kind, original.DueDate, original.Amount.Cents(), raw, Hash(raw), snapshot.AsOf)
		if err != nil {
			return false, err
		}
		changed = changed || tag.RowsAffected() != 0
	}
	return changed, nil
}
