package domain

import (
	"crypto/sha1" // UUIDv5 identity compatibility, not an evidence digest.
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
)

// ID is a UUID value, independent of storage and transport adapters.
type ID [16]byte

// IncomeOriginID names the immutable original bank transaction identity.
type IncomeOriginID = ID

func ParseID(s string) (ID, error) {
	var id ID
	if len(s) != 36 || s[8] != '-' || s[13] != '-' || s[18] != '-' || s[23] != '-' {
		return id, errors.New("invalid UUID")
	}
	compact := s[:8] + s[9:13] + s[14:18] + s[19:23] + s[24:]
	decoded, err := hex.DecodeString(compact)
	if err != nil {
		return id, fmt.Errorf("invalid UUID: %w", err)
	}
	copy(id[:], decoded)
	return id, nil
}

func (id ID) String() string {
	s := hex.EncodeToString(id[:])
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}

func (id ID) MarshalJSON() ([]byte, error) { return json.Marshal(id.String()) }
func (id *ID) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err != nil {
		return err
	}
	parsed, err := ParseID(s)
	if err != nil {
		return err
	}
	*id = parsed
	return nil
}

// LocationID matches Python uuid5(origin_transaction_id,
// "income-location:" + canonical_account_uuid), including repeat transfers.
func LocationID(origin IncomeOriginID, account ID) ID {
	h := sha1.New()
	_, _ = h.Write(origin[:])
	_, _ = h.Write([]byte("income-location:" + account.String()))
	var id ID
	copy(id[:], h.Sum(nil)[:16])
	id[6] = (id[6] & 0x0f) | 0x50
	id[8] = (id[8] & 0x3f) | 0x80
	return id
}

// EvidenceHash is the original SHA-256 reference, not a newly recomputed hash.
type EvidenceHash [32]byte

func ParseEvidenceHash(s string) (EvidenceHash, error) {
	var hash EvidenceHash
	if len(s) != 64 {
		return hash, errors.New("invalid evidence hash")
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return hash, errors.New("evidence hash must be lowercase hex")
		}
	}
	decoded, err := hex.DecodeString(s)
	if err != nil {
		return hash, err
	}
	copy(hash[:], decoded)
	return hash, nil
}

func (hash EvidenceHash) String() string { return hex.EncodeToString(hash[:]) }
