package executionaudit

import "sort"

const NativeCurrentCatalogRevisionV3 = 23

// Revision 22 remains byte-for-byte reproducible. The forward revision adds
// recovery originals with explicit environment-versus-user scope semantics.
func NativeAuditTablesV3Revision(revision int) []NativeAuditTableV3 {
	if revision == NativeCatalogRevisionV3 {
		return NativeAuditTablesV3()
	}
	if revision != NativeCurrentCatalogRevisionV3 {
		return nil
	}
	out := NativeAuditTablesV3()
	out = append(out,
		NativeAuditTableV3{Name: "writer_controls", PrimaryKey: []string{}, Class: "ENVIRONMENT_AUTHORITY"},
		NativeAuditTableV3{Name: "writer_control_events", PrimaryKey: []string{"id"}, Class: "ENVIRONMENT_AUTHORITY"},
		NativeAuditTableV3{Name: "recovery_runs", PrimaryKey: []string{"id"}, Class: "SINGLE_USER_ENVIRONMENT_RECOVERY_ORIGINAL"},
		NativeAuditTableV3{Name: "recovery_bank_scans", PrimaryKey: []string{"run_id", "protocol"}, Class: "FINANCIAL_ORIGINAL"},
		NativeAuditTableV3{Name: "recovery_bank_observations", PrimaryKey: []string{"run_id", "protocol", "bank_key", "version"}, Class: "FINANCIAL_ORIGINAL"})
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func NativeAuditCatalogHashV3Revision(revision int) string {
	tables := NativeAuditTablesV3Revision(revision)
	if len(tables) == 0 {
		return ""
	}
	return nakedHash(mustJSONV3(tables))
}

func nativeEnvironmentTableV3(table string) bool {
	return table == "writer_controls" || table == "writer_control_events" || table == "recovery_runs"
}
