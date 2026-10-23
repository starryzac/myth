package audit

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
)

const (
	SchemaVersion = "zhiyu-evidence-v1"
	HashAlgorithm = "zhiyu-evidence-sha256-v1"
)

type Reference struct {
	ID     string `json:"id"`
	Digest string `json:"digest"`
}

// Document is the new protocol. Every reference and version is inside the
// hashed original. Data is a frozen claim, never an execution authorization.
type Document struct {
	SchemaVersion    string         `json:"schema_version"`
	CanonicalVersion string         `json:"canonical_version"`
	HashAlgorithm    string         `json:"hash_algorithm"`
	Simulation       bool           `json:"simulation"`
	ID               string         `json:"id"`
	Kind             string         `json:"kind"`
	SemanticVersion  string         `json:"semantic_version"`
	Outcome          string         `json:"outcome"`
	Data             map[string]any `json:"data"`
	References       []Reference    `json:"references"`
}

type Original struct {
	Canonical []byte
	Digest    string
}

// Seal binds only protocol structure and bytes. VerifyBundle additionally
// requires an explicitly registered semantic validator for each document.
func Seal(doc Document) (Original, error) {
	if err := doc.validate(); err != nil {
		return Original{}, err
	}
	refs := make([]any, len(doc.References))
	for i, ref := range doc.References {
		refs[i] = map[string]any{"id": ref.ID, "digest": ref.Digest}
	}
	value := map[string]any{
		"schema_version": doc.SchemaVersion, "canonical_version": doc.CanonicalVersion,
		"hash_algorithm": doc.HashAlgorithm, "simulation": doc.Simulation,
		"id": doc.ID, "kind": doc.Kind, "semantic_version": doc.SemanticVersion,
		"outcome": doc.Outcome, "data": doc.Data, "references": refs,
	}
	raw, err := Canonical(doc.CanonicalVersion, value)
	if err != nil {
		return Original{}, err
	}
	return Original{Canonical: raw, Digest: digestBytes("zhiyu/evidence/v1\x00", raw)}, nil
}

func validDigest(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func label(s string) bool { return len(s) > 0 && len(s) <= 160 && !strings.ContainsAny(s, "\x00\r\n") }

func (d Document) validate() error {
	if d.SchemaVersion != SchemaVersion || d.CanonicalVersion != CanonicalVersion || d.HashAlgorithm != HashAlgorithm {
		return ErrUnsupportedVersion
	}
	if !d.Simulation || !label(d.ID) || !label(d.Kind) || !label(d.SemanticVersion) || d.Data == nil || d.References == nil {
		return fmt.Errorf("%w: document header", ErrInvalid)
	}
	for i, ref := range d.References {
		if !label(ref.ID) || !validDigest(ref.Digest) || (i > 0 && d.References[i-1].ID >= ref.ID) {
			return fmt.Errorf("%w: references must be uniquely sorted by id", ErrInvalid)
		}
	}
	return nil
}

func parseOriginal(original Original) (Document, error) {
	var doc Document
	value, err := ParseObject(original.Canonical)
	if err != nil {
		return doc, err
	}
	// Check declared protocols before trying any known-schema semantic decoder.
	if value["schema_version"] != SchemaVersion || value["canonical_version"] != CanonicalVersion || value["hash_algorithm"] != HashAlgorithm {
		return doc, ErrUnsupportedVersion
	}
	decoder := json.NewDecoder(bytes.NewReader(original.Canonical))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&doc); err != nil {
		return doc, fmt.Errorf("%w: document fields", ErrInvalid)
	}
	sealed, err := Seal(doc)
	if err != nil {
		return doc, err
	}
	if !bytes.Equal(sealed.Canonical, original.Canonical) || !validDigest(original.Digest) || sealed.Digest != original.Digest {
		return doc, fmt.Errorf("%w: original encoding or digest differs", ErrInvalid)
	}
	return doc, nil
}

// Validator must be deterministic and pure. It validates a frozen claim under
// one exact semantic version, without database, clocks or financial side effects.
type Validator func(Document) error

type semanticKey struct{ kind, version string }
type ReferenceValidator func(parent, child Document, reference Reference) error
type Registry struct {
	validators          map[semanticKey]Validator
	referenceValidators map[semanticKey]ReferenceValidator
	scopeValidators     map[semanticKey]ScopeValidator
	graphValidators     map[semanticKey]GraphValidator
}

func NewRegistry() *Registry { return &Registry{validators: map[semanticKey]Validator{}} }

// RegisterReferenceValidator allows a pure business plugin to bind referenced
// originals (for example tenant/environment) without making the generic kernel
// depend on business domains. Registered links are checked before traversal.
func (r *Registry) RegisterReferenceValidator(kind, version string, validator ReferenceValidator) error {
	if r == nil || !label(kind) || !label(version) || validator == nil {
		return ErrInvalid
	}
	if r.referenceValidators == nil {
		r.referenceValidators = map[semanticKey]ReferenceValidator{}
	}
	key := semanticKey{kind, version}
	if r.referenceValidators[key] != nil {
		return fmt.Errorf("%w: reference validator already registered", ErrInvalid)
	}
	r.referenceValidators[key] = validator
	return nil
}

func (r *Registry) Register(kind, version string, validator Validator) error {
	if r == nil || !label(kind) || !label(version) || validator == nil {
		return ErrInvalid
	}
	key := semanticKey{kind, version}
	if r.validators == nil {
		r.validators = map[semanticKey]Validator{}
	}
	if _, exists := r.validators[key]; exists {
		return fmt.Errorf("%w: validator already registered", ErrInvalid)
	}
	r.validators[key] = validator
	return nil
}

// RetainedResultRegistry verifies only that the recorded outcome is recognized.
// FAILED and UNKNOWN remain those outcomes even when the evidence is VALID.
// No bank settlement, funds permission or business replay is implied.
func RetainedResultRegistry() *Registry {
	r := NewRegistry()
	_ = r.Register("RETAINED_RESULT", "retained-result-v1", func(d Document) error {
		switch d.Outcome {
		case "SUCCEEDED", "FAILED", "UNKNOWN":
			return nil
		default:
			return fmt.Errorf("%w: retained result outcome", ErrInvalid)
		}
	})
	return r
}

type Status string

const (
	Valid              Status = "VALID"
	Invalid            Status = "INVALID"
	Incomplete         Status = "INCOMPLETE"
	UnsupportedVersion Status = "UNSUPPORTED_VERSION"
)

// Verification describes only the requested reachable closure, not full history.
// ClosureDigest is an internal consistency value, not an external trusted anchor.
type Verification struct {
	Status        Status
	RootIDs       []string
	CheckedIDs    []string
	ByteCount     int
	ClosureDigest string
	FailedID      string
	Reason        string
}

// VerifyBundle validates at most 1000 reachable originals / 16 MiB. Missing
// originals and exhausted bounds are INCOMPLETE; unknown versions never fall back.
// Unreferenced originals are outside the explicitly returned scope.
func VerifyBundle(rootIDs []string, originals map[string]Original, registry *Registry) Verification {
	result := Verification{Status: Valid, RootIDs: append([]string(nil), rootIDs...)}
	fail := func(status Status, id, reason string) Verification {
		result.Status, result.FailedID, result.Reason = status, id, reason
		return result
	}
	if len(rootIDs) == 0 || len(rootIDs) > 1000 {
		return fail(Incomplete, "", "nonempty bounded roots required")
	}
	queue := append([]string(nil), rootIDs...)
	sort.Strings(queue)
	for i, id := range queue {
		if !label(id) || (i > 0 && queue[i-1] == id) {
			return fail(Invalid, id, "duplicate or invalid root")
		}
	}
	sort.Strings(result.RootIDs)
	seen := map[string]bool{}
	digests := map[string]string{}
	// The cache is local to this verification call and contains only checked
	// protocol decoding. Link checks cannot repeatedly parse large children or
	// exceed the closure's aggregate original-byte budget before traversal.
	decoded := map[string]Document{}
	decodedBytes := 0
	boundError := errors.New("verification bound exceeded")
	decode := func(id string, original Original) (Document, error) {
		if doc, ok := decoded[id]; ok {
			return doc, nil
		}
		if len(decoded) >= 1000 || decodedBytes+len(original.Canonical) > 16*1024*1024 {
			return Document{}, boundError
		}
		doc, err := parseOriginal(original)
		if err != nil {
			return doc, err
		}
		decoded[id] = doc
		decodedBytes += len(original.Canonical)
		return doc, nil
	}
	for len(queue) > 0 {
		id := queue[0]
		queue = queue[1:]
		if seen[id] {
			continue
		}
		original, exists := originals[id]
		if !exists {
			return fail(Incomplete, id, "referenced original missing")
		}
		if len(seen) >= 1000 || result.ByteCount+len(original.Canonical) > 16*1024*1024 {
			return fail(Incomplete, id, "verification bound exceeded")
		}
		doc, err := decode(id, original)
		if err != nil {
			if errors.Is(err, boundError) {
				return fail(Incomplete, id, err.Error())
			}
			if errors.Is(err, ErrUnsupportedVersion) {
				return fail(UnsupportedVersion, id, err.Error())
			}
			return fail(Invalid, id, err.Error())
		}
		if doc.ID != id {
			return fail(Invalid, id, "original identity differs from lookup")
		}
		if registry == nil {
			return fail(UnsupportedVersion, id, "no semantic registry")
		}
		validator := registry.validators[semanticKey{doc.Kind, doc.SemanticVersion}]
		if validator == nil {
			return fail(UnsupportedVersion, id, "unregistered kind or semantic version")
		}
		if err := validator(doc); err != nil {
			if errors.Is(err, ErrUnsupportedVersion) {
				return fail(UnsupportedVersion, id, err.Error())
			}
			return fail(Invalid, id, err.Error())
		}
		for _, ref := range doc.References {
			child, exists := originals[ref.ID]
			if !exists {
				return fail(Incomplete, ref.ID, "referenced original missing")
			}
			if child.Digest != ref.Digest {
				return fail(Invalid, ref.ID, "reference digest differs")
			}
			if link := registry.referenceValidators[semanticKey{doc.Kind, doc.SemanticVersion}]; link != nil {
				childDoc, err := decode(ref.ID, child)
				if err != nil {
					if errors.Is(err, boundError) {
						return fail(Incomplete, ref.ID, err.Error())
					}
					if errors.Is(err, ErrUnsupportedVersion) {
						return fail(UnsupportedVersion, ref.ID, err.Error())
					}
					return fail(Invalid, ref.ID, err.Error())
				}
				if err = link(doc, childDoc, ref); err != nil {
					if errors.Is(err, ErrUnsupportedVersion) {
						return fail(UnsupportedVersion, ref.ID, err.Error())
					}
					return fail(Invalid, ref.ID, err.Error())
				}
			}
			queue = append(queue, ref.ID)
		}
		seen[id] = true
		digests[id] = original.Digest
		result.CheckedIDs = append(result.CheckedIDs, id)
		result.ByteCount += len(original.Canonical)
	}
	sort.Strings(result.CheckedIDs)
	var closure strings.Builder
	for _, id := range result.CheckedIDs {
		fmt.Fprintf(&closure, "%d:%s:%s\n", len(id), id, digests[id])
	}
	result.ClosureDigest = digestBytes("zhiyu/evidence-closure/v1\x00", []byte(closure.String()))
	return result
}
