package domain

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"zhiyu.local/zhiyu-go/internal/audit"
)

const Protocol = "zhiyu-history-archive-v1"
const MaxObjects = 10000
const MaxObjectBytes = 16 << 20
const MaxTotalBytes = 64 << 20
const MaxManifestBytes = 4 << 20
const MaxWireBytes = 96 << 20
const MaxObjectMetadataBytes = 32768

var ErrArchive = errors.New("INVALID_HISTORY_ARCHIVE")
var ErrCapacity = errors.New("HISTORY_CAPACITY_EXCEEDED")

type Source struct {
	System            string            `json:"system"`
	Schema            string            `json:"schema"`
	EnvironmentID     string            `json:"environment_id"`
	UserID            string            `json:"user_id"`
	Namespace         string            `json:"namespace"`
	Scope             string            `json:"scope"`
	CapturedAt        time.Time         `json:"captured_at"`
	CatalogHash       string            `json:"catalog_hash,omitempty"`
	Watermarks        map[string]uint64 `json:"watermarks"`
	UnavailableTables []string          `json:"unavailable_tables"`
}
type Binding struct {
	Kind    string `json:"kind"`
	Pointer string `json:"pointer"`
	Value   string `json:"value"`
}
type ObjectRef struct {
	ID             string    `json:"id"`
	Table          string    `json:"table"`
	RowKey         string    `json:"row_key"`
	Representation string    `json:"representation"`
	Protocol       string    `json:"protocol"`
	RawHash        string    `json:"raw_hash"`
	Bytes          int       `json:"bytes"`
	Outcome        string    `json:"outcome,omitempty"`
	Bindings       []Binding `json:"bindings"`
}
type Object struct {
	ObjectRef
	Original []byte `json:"original"`
}
type TableCount struct {
	TableSpec
	Count int `json:"count"`
}
type Manifest struct {
	Protocol   string       `json:"protocol"`
	Source     Source       `json:"source"`
	Tables     []TableCount `json:"tables"`
	Objects    []ObjectRef  `json:"objects"`
	TotalBytes int64        `json:"total_bytes"`
	RootHash   string       `json:"root_hash"`
}
type Bundle struct {
	Manifest Manifest `json:"manifest"`
	Objects  []Object `json:"objects"`
}
type Member struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Reason  string `json:"reason"`
	Outcome string `json:"retained_outcome,omitempty"`
}
type Verification struct {
	Status         string   `json:"status"`
	Assurance      string   `json:"assurance"`
	Scope          string   `json:"scope"`
	RootHash       string   `json:"root_hash"`
	CheckedObjects int      `json:"checked_objects"`
	CheckedBytes   int64    `json:"checked_bytes"`
	Members        []Member `json:"members"`
	MissingIDs     []string `json:"missing_ids"`
	BankAuthority  bool     `json:"bank_authority"`
	Activated      bool     `json:"activated"`
}
type Validator func(Object) (string, string)
type Registry struct{ validators map[string]Validator }

func NewRegistry() *Registry { return &Registry{validators: map[string]Validator{}} }
func (r *Registry) Register(protocol string, v Validator) error {
	if r == nil || protocol == "" || v == nil {
		return ErrArchive
	}
	if _, ok := r.validators[protocol]; ok {
		return ErrArchive
	}
	r.validators[protocol] = v
	return nil
}
func RawHash(raw []byte) string { h := sha256.Sum256(raw); return hex.EncodeToString(h[:]) }
func validHash(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			if c < 'a' || c > 'f' {
				return false
			}
		}
	}
	return true
}
func bounded(s string, n int) bool {
	return len(s) <= n && utf8.ValidString(s) && !strings.ContainsRune(s, 0)
}
func identity(s string) bool { return s != "" && bounded(s, 2048) }
func ObjectID(table, key, representation string) string {
	return RawHash([]byte("history-object-id-v1\x00" + table + "\x00" + key + "\x00" + representation))
}
func RowKey(values []string) (string, error) {
	if len(values) < 1 || len(values) > 12 {
		return "", ErrArchive
	}
	for _, s := range values {
		if !bounded(s, 512) {
			return "", ErrArchive
		}
	}
	raw, e := json.Marshal(values)
	if e != nil || len(raw) > 4096 {
		return "", ErrArchive
	}
	return string(raw), nil
}
func keyValues(key string) ([]string, error) {
	var values []string
	d := json.NewDecoder(strings.NewReader(key))
	d.DisallowUnknownFields()
	if d.Decode(&values) != nil {
		return nil, ErrArchive
	}
	raw, e := RowKey(values)
	if e != nil || raw != key {
		return nil, ErrArchive
	}
	return values, nil
}
func NewObject(meta ObjectRef, raw []byte) (Object, error) {
	meta.RawHash = RawHash(raw)
	meta.Bytes = len(raw)
	meta.ID = ObjectID(meta.Table, meta.RowKey, meta.Representation)
	meta.Bindings = append([]Binding{}, meta.Bindings...)
	o := Object{meta, append([]byte(nil), raw...)}
	if e := validateRef(meta); e != nil {
		return Object{}, e
	}
	return o, nil
}
func validateRef(ref ObjectRef) error {
	if raw, e := json.Marshal(ref); e != nil || len(raw) > MaxObjectMetadataBytes {
		return ErrCapacity
	}
	spec, ok := Table(ref.Table)
	key, e := keyValues(ref.RowKey)
	if !ok || e != nil || len(key) != len(spec.PrimaryKey) || ref.ID != ObjectID(ref.Table, ref.RowKey, ref.Representation) || !validHash(ref.RawHash) || ref.Bytes < 1 || ref.Bytes > MaxObjectBytes || !identity(ref.Protocol) || !bounded(ref.Protocol, 160) || !bounded(ref.Outcome, 64) || len(ref.Bindings) > 32 {
		return ErrArchive
	}
	switch ref.Representation {
	case "captured_wire", "legacy_canonical", "db_export", "stored_original":
	default:
		return ErrArchive
	}
	seen := map[string]bool{}
	for _, b := range ref.Bindings {
		if !bounded(b.Pointer, 512) || !strings.HasPrefix(b.Pointer, "/") || !bounded(b.Value, 2048) || seen[b.Pointer] {
			return ErrArchive
		}
		seen[b.Pointer] = true
		switch b.Kind {
		case "OBJECT_ID", "ORIGIN_ID", "ACTION_ID", "BANK_NAMESPACE", "BANK_KEY", "EFFECT_HASH", "OUTCOME", "OCCURRED_AT", "ORIGINAL_DIGEST":
		default:
			return ErrArchive
		}
	}
	return nil
}
func sourceValid(s Source) bool {
	if !identity(s.System) || !bounded(s.System, 128) || !identity(s.Schema) || !bounded(s.Schema, 128) || !identity(s.EnvironmentID) || !bounded(s.EnvironmentID, 128) || !identity(s.UserID) || !bounded(s.UserID, 128) || !identity(s.Namespace) || !bounded(s.Namespace, 128) || s.CapturedAt.IsZero() || len(s.Watermarks) > 4 || s.CatalogHash != "" && !validHash(s.CatalogHash) {
		return false
	}
	if s.Scope != "SUPPLIED_ORIGINALS" && s.Scope != "REGISTERED_TABLE_SET" {
		return false
	}
	for k := range s.Watermarks {
		switch k {
		case "funds_version", "source_version", "writer_generation", "control_version":
		default:
			return false
		}
	}
	for i, name := range s.UnavailableTables {
		if _, ok := Table(name); !ok || name == "legacy_originals" || i > 0 && s.UnavailableTables[i-1] >= name {
			return false
		}
	}
	if s.Scope == "REGISTERED_TABLE_SET" && (s.System != "ZHIYU_GO" || s.Schema != CurrentSchema || s.CatalogHash != CatalogHash() || len(s.Watermarks) != 4) {
		return false
	}
	return true
}
func manifestHash(m Manifest) (string, error) {
	m.RootHash = ""
	raw, e := json.Marshal(m)
	if e != nil || len(raw) > MaxManifestBytes {
		return "", ErrCapacity
	}
	return RawHash(append([]byte(Protocol+"\x00"), raw...)), nil
}
func Seal(source Source, tables []TableSpec, objects []Object) (Bundle, error) {
	var zero Bundle
	if !sourceValid(source) || len(objects) > MaxObjects || len(tables) > 128 {
		return zero, ErrArchive
	}
	source.CapturedAt = source.CapturedAt.UTC()
	source.Watermarks = cloneWatermarks(source.Watermarks)
	source.UnavailableTables = append([]string{}, source.UnavailableTables...)
	m := Manifest{Protocol: Protocol, Source: source, Tables: []TableCount{}, Objects: []ObjectRef{}}
	counts := map[string]int{}
	seen := map[string]bool{}
	for _, o := range objects {
		if validateRef(o.ObjectRef) != nil || RawHash(o.Original) != o.RawHash || len(o.Original) != o.Bytes || seen[o.ID] {
			return zero, ErrArchive
		}
		seen[o.ID] = true
		counts[o.Table]++
		m.TotalBytes += int64(o.Bytes)
		if m.TotalBytes > MaxTotalBytes {
			return zero, ErrCapacity
		}
		ref := o.ObjectRef
		ref.Bindings = append([]Binding{}, ref.Bindings...)
		m.Objects = append(m.Objects, ref)
	}
	sort.Slice(m.Objects, func(i, j int) bool { return m.Objects[i].ID < m.Objects[j].ID })
	tableSeen := map[string]bool{}
	for _, s := range tables {
		expected, ok := Table(s.Name)
		a, _ := json.Marshal(s)
		b, _ := json.Marshal(expected)
		if !ok || tableSeen[s.Name] || !bytes.Equal(a, b) {
			return zero, ErrArchive
		}
		tableSeen[s.Name] = true
		s.PrimaryKey = append([]string{}, s.PrimaryKey...)
		m.Tables = append(m.Tables, TableCount{s, counts[s.Name]})
	}
	for name := range counts {
		if !tableSeen[name] {
			return zero, ErrArchive
		}
	}
	sort.Slice(m.Tables, func(i, j int) bool { return m.Tables[i].Name < m.Tables[j].Name })
	if !registeredCoverage(m) {
		return zero, ErrArchive
	}
	var e error
	m.RootHash, e = manifestHash(m)
	if e != nil {
		return zero, e
	}
	out := Bundle{Manifest: m, Objects: append([]Object{}, objects...)}
	for i := range out.Objects {
		out.Objects[i].Original = append([]byte{}, out.Objects[i].Original...)
		out.Objects[i].Bindings = append([]Binding{}, out.Objects[i].Bindings...)
	}
	sort.Slice(out.Objects, func(i, j int) bool { return out.Objects[i].ID < out.Objects[j].ID })
	return out, nil
}

func cloneWatermarks(in map[string]uint64) map[string]uint64 {
	out := map[string]uint64{}
	for k, v := range in {
		out[k] = v
	}
	return out
}
func registeredCoverage(m Manifest) bool {
	if m.Source.Scope != "REGISTERED_TABLE_SET" {
		return len(m.Source.UnavailableTables) == 0
	}
	names := map[string]bool{}
	for _, t := range m.Tables {
		if t.Name == "legacy_originals" || names[t.Name] {
			return false
		}
		names[t.Name] = true
	}
	for _, name := range m.Source.UnavailableTables {
		if names[name] {
			return false
		}
		names[name] = true
	}
	expected := CurrentTables()
	if len(names) != len(expected) {
		return false
	}
	for _, t := range expected {
		if !names[t.Name] {
			return false
		}
	}
	return true
}

func scalarAt(root any, pointer string) (string, bool) {
	if !strings.HasPrefix(pointer, "/") {
		return "", false
	}
	current := root
	parts := strings.Split(pointer[1:], "/")
	if len(parts) > 32 {
		return "", false
	}
	for _, part := range parts {
		if strings.Contains(strings.ReplaceAll(strings.ReplaceAll(part, "~0", ""), "~1", ""), "~") {
			return "", false
		}
		part = strings.ReplaceAll(strings.ReplaceAll(part, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return "", false
		}
		current, ok = object[part]
		if !ok {
			return "", false
		}
	}
	value, ok := current.(string)
	return value, ok
}
func Verify(bundle Bundle, registry *Registry) Verification {
	m := bundle.Manifest
	result := Verification{Status: "VALID", Assurance: "BYTES_AND_REGISTERED_ROW_SHAPE_ONLY", Scope: m.Source.Scope, RootHash: m.RootHash, Members: []Member{}, MissingIDs: []string{}}
	invalid := func(reason string) Verification {
		result.Status = "INVALID"
		result.Members = append(result.Members, Member{Status: "INVALID", Reason: reason})
		return result
	}
	if m.Protocol != Protocol || !sourceValid(m.Source) || len(m.Objects) > MaxObjects || len(bundle.Objects) > MaxObjects || len(m.Tables) > 128 || m.TotalBytes > MaxTotalBytes {
		return invalid("MANIFEST_ENVELOPE")
	}
	if !registeredCoverage(m) {
		return invalid("REGISTERED_READSET_SCOPE")
	}
	hash, e := manifestHash(m)
	if e != nil || hash != m.RootHash {
		return invalid("MANIFEST_HASH")
	}
	tables := map[string]TableCount{}
	for i, t := range m.Tables {
		spec, ok := Table(t.Name)
		a, _ := json.Marshal(spec)
		b, _ := json.Marshal(t.TableSpec)
		if !ok || !bytes.Equal(a, b) || t.Count < 0 || i > 0 && m.Tables[i-1].Name >= t.Name {
			return invalid("TABLE_ALLOWLIST")
		}
		tables[t.Name] = t
	}
	actual := map[string]Object{}
	for _, o := range bundle.Objects {
		if _, exists := actual[o.ID]; exists {
			return invalid("DUPLICATE_OBJECT")
		}
		actual[o.ID] = o
	}
	var total int64
	counts := map[string]int{}
	for i, ref := range m.Objects {
		if validateRef(ref) != nil || i > 0 && m.Objects[i-1].ID >= ref.ID {
			return invalid("OBJECT_IDENTITY")
		}
		if _, ok := tables[ref.Table]; !ok {
			return invalid("TABLE_ALLOWLIST")
		}
		counts[ref.Table]++
		total += int64(ref.Bytes)
		o, ok := actual[ref.ID]
		if !ok {
			result.MissingIDs = append(result.MissingIDs, ref.ID)
			continue
		}
		delete(actual, ref.ID)
		a, _ := json.Marshal(o.ObjectRef)
		b, _ := json.Marshal(ref)
		if !bytes.Equal(a, b) || len(o.Original) != ref.Bytes || RawHash(o.Original) != ref.RawHash {
			return invalid("ORIGINAL_HASH_OR_DESCRIPTOR")
		}
		result.CheckedObjects++
		result.CheckedBytes += int64(o.Bytes)
		status, reason := "UNSUPPORTED_VERSION", "PROTOCOL_NOT_REGISTERED"
		var parsed map[string]any
		if len(o.Bindings) > 0 || o.Protocol == RowProtocol {
			parsed, e = audit.ParseRawObject(o.Original)
			if e != nil {
				status, reason = "INVALID", "ORIGINAL_JSON"
			} else {
				status, reason = "VALID", "IDENTITY_BINDINGS_ONLY"
				for _, binding := range o.Bindings {
					value, ok := scalarAt(parsed, binding.Pointer)
					if !ok || value != binding.Value {
						status, reason = "INVALID", "ORIGINAL_IDENTITY_BINDING"
						break
					}
				}
			}
		}
		if status != "INVALID" && o.Protocol == RowProtocol {
			if o.Representation != "db_export" || m.Source.Schema != CurrentSchema || m.Source.System != "ZHIYU_GO" || m.Source.Scope != "REGISTERED_TABLE_SET" {
				status, reason = "UNSUPPORTED_VERSION", "ROW_SCHEMA_NOT_REGISTERED"
			} else {
				spec := tables[o.Table].TableSpec
				key, _ := keyValues(o.RowKey)
				for j, column := range spec.PrimaryKey {
					if value, ok := parsed[column].(string); ok {
						if value != key[j] {
							status, reason = "INVALID", "ROW_PRIMARY_KEY"
						}
					} else if number, ok := parsed[column].(json.Number); ok {
						if number.String() != key[j] {
							status, reason = "INVALID", "ROW_PRIMARY_KEY"
						}
					} else {
						status, reason = "INVALID", "ROW_PRIMARY_KEY"
					}
				}
				if status != "INVALID" {
					switch o.Table {
					case "environments":
						if parsed["id"] != m.Source.EnvironmentID || parsed["namespace"] != m.Source.Namespace {
							status, reason = "INVALID", "ROW_SOURCE_SCOPE"
						}
					case "users":
						if parsed["environment_id"] != m.Source.EnvironmentID || parsed["id"] != m.Source.UserID {
							status, reason = "INVALID", "ROW_SOURCE_SCOPE"
						}
					case "standing_consumers":
						if parsed["environment_id"] != m.Source.EnvironmentID {
							status, reason = "INVALID", "ROW_SOURCE_SCOPE"
						}
					default:
						if parsed["environment_id"] != m.Source.EnvironmentID || parsed["user_id"] != m.Source.UserID {
							status, reason = "INVALID", "ROW_SOURCE_SCOPE"
						}
					}
				}
				if status != "INVALID" {
					status, reason = "VALID", "EXPLICIT_REGISTERED_ROW_SHAPE"
				}
			}
		} else if status != "INVALID" {
			if registry != nil {
				if validator, ok := registry.validators[o.Protocol]; ok {
					status, reason = validator(o)
				} else {
					status, reason = "UNSUPPORTED_VERSION", "PROTOCOL_NOT_REGISTERED"
				}
			} else {
				status, reason = "UNSUPPORTED_VERSION", "PROTOCOL_NOT_REGISTERED"
			}
		}
		if status != "VALID" && status != "INVALID" && status != "INCOMPLETE" && status != "UNSUPPORTED_VERSION" {
			status, reason = "INVALID", "VALIDATOR_STATUS"
		}
		result.Members = append(result.Members, Member{o.ID, status, reason, o.Outcome})
		if status == "INVALID" {
			result.Status = "INVALID"
		} else if status == "INCOMPLETE" && result.Status != "INVALID" {
			result.Status = "INCOMPLETE"
		} else if status == "UNSUPPORTED_VERSION" && result.Status == "VALID" {
			result.Status = "UNSUPPORTED_VERSION"
		}
	}
	if len(actual) > 0 || total != m.TotalBytes {
		return invalid("MANIFEST_MEMBERS_OR_BYTES")
	}
	for name, table := range tables {
		if counts[name] != table.Count {
			return invalid("TABLE_COUNT")
		}
	}
	if len(result.MissingIDs) > 0 && result.Status != "INVALID" {
		result.Status = "INCOMPLETE"
	}
	if len(m.Source.UnavailableTables) > 0 && result.Status != "INVALID" {
		result.Status = "INCOMPLETE"
	}
	if result.Status == "VALID" && (m.Source.Schema != CurrentSchema && m.Source.Schema != LegacySchema) {
		result.Status = "UNSUPPORTED_VERSION"
	}
	return result
}
