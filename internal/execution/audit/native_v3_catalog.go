package executionaudit

import (
	"encoding/json"
	"sort"
)

const NativeAuditProtocolV3 = "native-financial-audit-v3"
const NativeRowKindV3 = "NATIVE_FINANCIAL_ROW"
const NativeSnapshotKindV3 = "NATIVE_FINANCIAL_SNAPSHOT"
const NativeRowVersionV3 = "native-financial-row-v3"
const NativeSnapshotVersionV3 = "native-financial-snapshot-v3"
const NativeCatalogRevisionV3 = 22

// NativeAuditTableV3 is an independent, versioned allowlist. Neither archives'
// read-set v1 nor the legacy business v1/v2 catalogs are extended in place.
// Keys omit environment/user only because the enclosing capture binds both.
type NativeAuditTableV3 struct {
	Name       string   `json:"name"`
	PrimaryKey []string `json:"primary_key"`
	Class      string   `json:"class"`
}

// NativeAuditTablesV3 covers all registered financial originals and current
// resource projections through migration 022. Credentials, sessions' tokens,
// mutable worker leases, caches and audit's own runs/artifacts are excluded.
// The non-credential audit_session_subjects view carries actual USER identity.
func NativeAuditTablesV3() []NativeAuditTableV3 {
	keys := map[string][]string{
		"environments": {"id"}, "users": {"id"}, "accounts": {"id"}, "goals": {"id"}, "audit_session_subjects": {"id"},
		"income_origins": {"id"}, "income_locations": {"id"}, "income_reservations": {"action_id"}, "income_claims": {"action_id", "fragment_id"},
		"policies": {"id", "version"}, "grants": {"id", "version"}, "confirmations": {"id"}, "plans": {"id"}, "operations": {"id"}, "submission_permits": {"operation_id"}, "journals": {"id"}, "postings": {"journal_id", "leg_ref"}, "receipts": {"id"}, "bank_observations": {"id"}, "evidence_blobs": {"id"}, "outbox": {"id"}, "protection_snapshots": {"version"},
		"template_inventory_contracts": {}, "template_policy_previews": {"id"}, "template_policies": {"id"}, "template_policy_confirmations": {"id"}, "template_policy_versions": {"policy_id", "version"}, "template_policy_status_events": {"id"}, "template_obligations": {"id"}, "template_obligation_payments": {"id"}, "template_protection_publications": {"source_version"}, "goal_configuration_events": {"goal_id", "goal_version"},
		"native_joint_plans": {"id"}, "native_previews": {"id"}, "native_action_previews": {"id"}, "native_confirmations": {"id"}, "native_operations": {"id"}, "native_submission_permits": {"operation_id"}, "native_income_reservations": {"action_id"}, "native_income_claims": {"action_id", "fragment_id"}, "native_goal_balances": {"goal_id"}, "native_goal_lots": {"goal_id", "account_id", "source_kind", "source_id"}, "native_holds": {"operation_id", "resource_kind", "resource_id", "resource_detail"}, "native_positions": {"id"}, "native_position_states": {"position_id"}, "native_principal_returns": {"bank_key"}, "native_return_locations": {"bank_key", "account_id"}, "native_return_claims": {"operation_id", "bank_key", "account_id"}, "native_bank_observations": {"id"}, "native_journals": {"id"}, "native_postings": {"journal_id", "leg_ref"}, "native_receipts": {"id"},
		"native_group_batches": {"id"}, "native_next_steps": {"group_id", "number"}, "native_priority_analyses": {"id"}, "native_priority_answers": {"id"}, "native_payment_sources": {"operation_id"}, "native_obligation_payments": {"id"},
		"native_grant_previews": {"id"}, "native_grants": {"id", "version"}, "native_grant_state_events": {"id"}, "native_grant_states": {"grant_id", "grant_version"}, "native_occurrences": {"id"}, "native_automation_event_receipts": {"grant_id", "grant_version", "event_id"}, "native_grant_budget_claims": {"occurrence_id"}, "native_standing_plans": {"id"}, "native_occurrence_operations": {"operation_id"},
		"native_bill_captures": {"id"}, "native_bill_originals": {"id"}, "native_bill_import_results": {"capture_id"}, "native_bill_capture_resolutions": {"capture_id"},
		"standing_event_receipts": {"consumer_name", "event_id"}, "standing_inbox": {"consumer_name", "event_id", "policy_id", "policy_version", "grant_id", "grant_version", "family"},
		"native_standing_bridge_groups": {"id"}, "native_standing_bridge_steps": {"group_id", "number"}, "native_standing_bridge_closures": {"group_id"},
	}
	current := map[string]bool{"environments": true, "users": true, "accounts": true, "goals": true, "income_locations": true, "income_reservations": true, "income_claims": true, "native_income_reservations": true, "native_income_claims": true, "native_goal_balances": true, "native_goal_lots": true, "native_holds": true, "native_position_states": true, "native_return_locations": true, "native_return_claims": true, "native_grant_states": true, "native_grant_budget_claims": true}
	out := make([]NativeAuditTableV3, 0, len(keys))
	for name, key := range keys {
		class := "FINANCIAL_ORIGINAL"
		if current[name] {
			class = "CURRENT_RESOURCE"
		}
		if name == "audit_session_subjects" {
			class = "NON_CREDENTIAL_SUBJECT"
		}
		out = append(out, NativeAuditTableV3{Name: name, PrimaryKey: append([]string{}, key...), Class: class})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func NativeAuditCatalogHashV3() string {
	raw, _ := json.Marshal(NativeAuditTablesV3())
	return nakedHash(raw)
}

func nativeAuditTableV3(name string) (NativeAuditTableV3, bool) {
	for _, table := range NativeAuditTablesV3Revision(NativeCurrentCatalogRevisionV3) {
		if table.Name == name {
			return table, true
		}
	}
	return NativeAuditTableV3{}, false
}
