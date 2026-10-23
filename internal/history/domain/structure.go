package domain

import (
	"bytes"
	"encoding/json"
)

// ValidateStructure admits a byte-consistent incomplete or semantically bad
// historical capture for preservation. Missing members stay missing. It never
// permits invented members, descriptor/hash changes or an ambiguous identity.
func ValidateStructure(b Bundle) error {
	m := b.Manifest
	if m.Protocol != Protocol || !sourceValid(m.Source) || !registeredCoverage(m) || len(m.Objects) > MaxObjects || len(b.Objects) > MaxObjects || len(m.Tables) > 128 || m.TotalBytes < 0 || m.TotalBytes > MaxTotalBytes {
		return ErrArchive
	}
	hash, e := manifestHash(m)
	if e != nil || hash != m.RootHash {
		return ErrArchive
	}
	tables := map[string]TableCount{}
	for i, t := range m.Tables {
		spec, ok := Table(t.Name)
		a, _ := json.Marshal(spec)
		raw, _ := json.Marshal(t.TableSpec)
		if !ok || !bytes.Equal(a, raw) || t.Count < 0 || i > 0 && m.Tables[i-1].Name >= t.Name {
			return ErrArchive
		}
		tables[t.Name] = t
	}
	refs := map[string]ObjectRef{}
	counts := map[string]int{}
	var total int64
	for i, r := range m.Objects {
		if validateRef(r) != nil || i > 0 && m.Objects[i-1].ID >= r.ID {
			return ErrArchive
		}
		if _, ok := tables[r.Table]; !ok {
			return ErrArchive
		}
		refs[r.ID] = r
		counts[r.Table]++
		total += int64(r.Bytes)
	}
	if total != m.TotalBytes {
		return ErrArchive
	}
	for name, t := range tables {
		if counts[name] != t.Count {
			return ErrArchive
		}
	}
	seen := map[string]bool{}
	for _, o := range b.Objects {
		ref, ok := refs[o.ID]
		a, _ := json.Marshal(ref)
		raw, _ := json.Marshal(o.ObjectRef)
		if !ok || seen[o.ID] || !bytes.Equal(a, raw) || len(o.Original) != ref.Bytes || RawHash(o.Original) != ref.RawHash {
			return ErrArchive
		}
		seen[o.ID] = true
	}
	return nil
}
