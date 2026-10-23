// Package domain preserves bounded historical originals. It has no database,
// filesystem, bank or authorization capability. VALID never means activation.
package domain

import (
	"encoding/json"
	"sort"
)

const CurrentSchema = "zhiyu-current-readset-v1"
const LegacySchema = "legacy-originals-v1"
const RowProtocol = "zhiyu-db-export-row-v1"

type TableSpec struct {
	Name       string   `json:"name"`
	PrimaryKey []string `json:"primary_key"`
}

// This v1 read set is frozen through the explicitly listed 018 contracts. A
// future table needs a new read-set version, not a change to old archive hashes.
// Credentials, tokens, projections and archives are excluded.
func CurrentTables() []TableSpec {
	names := []string{"accounts", "income_origins", "income_locations", "confirmations", "goals", "plans", "operations", "jobs", "journals", "receipts", "bank_observations", "outbox", "evidence_blobs", "native_joint_plans", "template_inventory_contracts", "template_policy_previews", "template_policies", "template_policy_confirmations", "template_policy_status_events", "template_obligations", "template_obligation_payments", "native_previews", "native_action_previews", "native_confirmations", "native_operations", "native_jobs", "native_positions", "native_bank_observations", "native_journals", "native_receipts", "native_group_batches", "native_priority_analyses", "native_priority_answers", "native_obligation_payments"}
	keys := map[string][]string{
		"environments": {"id"}, "users": {"environment_id", "id"}, "audit_session_subjects": {"id"},
		"policies": {"environment_id", "user_id", "id", "version"}, "grants": {"environment_id", "user_id", "id", "version"},
		"income_reservations": {"environment_id", "user_id", "action_id"}, "income_claims": {"environment_id", "user_id", "action_id", "fragment_id"}, "postings": {"environment_id", "user_id", "journal_id", "leg_ref"},
		"protection_snapshots": {"environment_id", "user_id", "version"}, "submission_permits": {"environment_id", "user_id", "operation_id"},
		"template_inventory_contracts": {"environment_id", "user_id"}, "template_policy_versions": {"environment_id", "user_id", "policy_id", "version"}, "template_protection_publications": {"environment_id", "user_id", "source_version"}, "goal_configuration_events": {"environment_id", "user_id", "goal_id", "goal_version"},
		"native_submission_permits": {"environment_id", "user_id", "operation_id"}, "native_income_reservations": {"environment_id", "user_id", "action_id"}, "native_income_claims": {"environment_id", "user_id", "action_id", "fragment_id"}, "native_goal_balances": {"environment_id", "user_id", "goal_id"}, "native_goal_lots": {"environment_id", "user_id", "goal_id", "account_id", "source_kind", "source_id"}, "native_holds": {"environment_id", "user_id", "operation_id", "resource_kind", "resource_id", "resource_detail"}, "native_position_states": {"environment_id", "user_id", "position_id"}, "native_principal_returns": {"environment_id", "user_id", "bank_key"}, "native_return_locations": {"environment_id", "user_id", "bank_key", "account_id"}, "native_return_claims": {"environment_id", "user_id", "operation_id", "bank_key", "account_id"}, "native_postings": {"environment_id", "user_id", "journal_id", "leg_ref"}, "native_next_steps": {"environment_id", "user_id", "group_id", "number"}, "native_payment_sources": {"environment_id", "user_id", "operation_id"},
		"standing_event_receipts": {"environment_id", "user_id", "consumer_name", "event_id"}, "standing_inbox": {"environment_id", "user_id", "consumer_name", "event_id", "policy_id", "policy_version", "grant_id", "grant_version", "family"},
		"standing_consumers":    {"environment_id", "consumer_name"},
		"native_grant_previews": {"environment_id", "user_id", "id"}, "native_grants": {"environment_id", "user_id", "id", "version"}, "native_grant_state_events": {"environment_id", "user_id", "id"}, "native_grant_states": {"environment_id", "user_id", "grant_id", "grant_version"}, "native_occurrences": {"environment_id", "user_id", "id"}, "native_automation_event_receipts": {"environment_id", "user_id", "grant_id", "grant_version", "event_id"}, "native_grant_budget_claims": {"environment_id", "user_id", "occurrence_id"}, "native_standing_plans": {"environment_id", "user_id", "id"}, "native_occurrence_operations": {"environment_id", "user_id", "operation_id"},
	}
	for _, name := range names {
		if _, ok := keys[name]; !ok {
			keys[name] = []string{"environment_id", "user_id", "id"}
		}
	}
	out := make([]TableSpec, 0, len(keys))
	for name, key := range keys {
		out = append(out, TableSpec{name, append([]string(nil), key...)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func Table(name string) (TableSpec, bool) {
	if name == "legacy_originals" {
		return TableSpec{name, []string{"id"}}, true
	}
	for _, spec := range CurrentTables() {
		if spec.Name == name {
			return spec, true
		}
	}
	return TableSpec{}, false
}

func CatalogHash() string { raw, _ := json.Marshal(CurrentTables()); return RawHash(raw) }
