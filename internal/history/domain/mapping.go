package domain

import "encoding/json"

const MappingProtocol = "zhiyu-passive-history-mapping-v1"

// A destination is an inert archive alias. It is never an account, operation,
// bank namespace, bank key, replacement origin or execution authorization.
type MappingEntry struct {
	ObjectID      string `json:"object_id"`
	SourceRawHash string `json:"source_raw_hash"`
	DestinationID string `json:"destination_id"`
}
type Mapping struct {
	Protocol                 string         `json:"protocol"`
	ArchiveRootHash          string         `json:"archive_root_hash"`
	SourceEnvironmentID      string         `json:"source_environment_id"`
	SourceUserID             string         `json:"source_user_id"`
	SourceNamespace          string         `json:"source_namespace"`
	DestinationEnvironmentID string         `json:"destination_environment_id"`
	DestinationUserID        string         `json:"destination_user_id"`
	Entries                  []MappingEntry `json:"entries"`
	Activated                bool           `json:"activated"`
	BankAuthority            bool           `json:"bank_authority"`
}

func ValidateMapping(bundle Bundle, m Mapping) error {
	source := bundle.Manifest.Source
	if m.Protocol != MappingProtocol || m.ArchiveRootHash != bundle.Manifest.RootHash || m.SourceEnvironmentID != source.EnvironmentID || m.SourceUserID != source.UserID || m.SourceNamespace != source.Namespace || !identity(m.DestinationEnvironmentID) || !identity(m.DestinationUserID) || m.Activated || m.BankAuthority || len(m.Entries) != len(bundle.Manifest.Objects) || len(m.Entries) > MaxObjects {
		return ErrArchive
	}
	destinations := map[string]bool{}
	for i, entry := range m.Entries {
		ref := bundle.Manifest.Objects[i]
		if entry.ObjectID != ref.ID || entry.SourceRawHash != ref.RawHash || !identity(entry.DestinationID) || !bounded(entry.DestinationID, 128) || destinations[entry.DestinationID] {
			return ErrArchive
		}
		destinations[entry.DestinationID] = true
	}
	return nil
}
func MappingHash(m Mapping) (string, error) {
	raw, e := json.Marshal(m)
	if e != nil || len(raw) > MaxManifestBytes {
		return "", ErrCapacity
	}
	return RawHash(append([]byte(MappingProtocol+"\x00"), raw...)), nil
}
