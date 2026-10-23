package app

// RecoveryCodeManifest belongs to an operator-pinned executable artifact. It is
// not a client attestation or a replacement for checking that executable hash.
type RecoveryCodeManifest struct {
	Protocol            string   `json:"protocol"`
	ArtifactHash        string   `json:"artifact_hash"`
	MinimumSchema       uint64   `json:"minimum_schema"`
	MaximumSchema       uint64   `json:"maximum_schema"`
	WriterFenceProtocol string   `json:"writer_fence_protocol"`
	ReplayProtocols     []string `json:"replay_protocols"`
}
type RecoveryCodeDecision struct {
	Compatible              bool   `json:"compatible"`
	Reason                  string `json:"reason"`
	RecoveryRequired        bool   `json:"recovery_required"`
	DatabaseRollbackAllowed bool   `json:"database_rollback_allowed"`
	BankAuthority           bool   `json:"bank_authority"`
}

// CheckRecoveryCodeCompatibility permits compatible code replacement while
// retaining the newest database and immutable bank keys. It never restores an
// older data snapshot as a way to undo a previously observed bank effect.
func CheckRecoveryCodeCompatibility(m RecoveryCodeManifest, actualArtifactHash string, schema uint64, requiredReplay []string) RecoveryCodeDecision {
	d := RecoveryCodeDecision{Reason: "INCOMPATIBLE_CODE", RecoveryRequired: true}
	if m.Protocol != RecoveryProtocol+"/code" || !nativeActionHash(actualArtifactHash) || m.ArtifactHash != actualArtifactHash || m.MinimumSchema == 0 || m.MaximumSchema < m.MinimumSchema {
		return d
	}
	if schema < m.MinimumSchema || schema > m.MaximumSchema {
		d.Reason = "SCHEMA_NOT_SUPPORTED"
		return d
	}
	if schema >= 23 && m.WriterFenceProtocol != RecoveryProtocol {
		d.Reason = "WRITER_FENCE_NOT_SUPPORTED"
		return d
	}
	seen := map[string]bool{}
	for _, protocol := range m.ReplayProtocols {
		if seen[protocol] {
			return d
		}
		seen[protocol] = true
	}
	for _, protocol := range requiredReplay {
		if !seen[protocol] {
			d.Reason = "REPLAY_PROTOCOL_NOT_SUPPORTED"
			return d
		}
	}
	d.Compatible = true
	d.Reason = "COMPATIBLE_CODE_KEEP_CURRENT_DATA"
	return d
}
