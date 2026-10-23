package audit

import (
	"encoding/json"
	"fmt"
	"sort"
	"time"
)

func (b Bounds) validate() error {
	hard := DefaultBounds()
	if b.MaxNodes < 1 || b.MaxNodes > hard.MaxNodes || b.MaxBytes < 1 || b.MaxBytes > hard.MaxBytes || b.MaxEdges < 1 || b.MaxEdges > hard.MaxEdges || b.MaxDepth < 1 || b.MaxDepth > hard.MaxDepth {
		return fmt.Errorf("%w: captured audit bounds", ErrInvalid)
	}
	return nil
}

func (s Scope) validate() error {
	if !label(s.EnvironmentID) || !label(s.Namespace) || !label(s.UserID) || !label(s.Epoch) {
		return fmt.Errorf("%w: captured scope", ErrInvalid)
	}
	return nil
}

func (m CaptureManifest) validate() error {
	if m.ProtocolVersion != CaptureProtocol {
		return ErrUnsupportedVersion
	}
	if err := m.Scope.validate(); err != nil {
		return err
	}
	if m.Source.CapturedAt.IsZero() || !m.Source.CapturedAt.Equal(m.Source.CapturedAt.Truncate(time.Microsecond)) || m.Source.CapturedAt.Year() < 1 || m.Source.CapturedAt.Year() > 9999 || len(m.Source.Watermarks) == 0 || len(m.Source.Watermarks) > 16 || !validDigest(m.Source.SourceManifestSHA256) || !validDigest(m.Source.ConfigSHA256) {
		return fmt.Errorf("%w: captured source metadata", ErrInvalid)
	}
	for key, value := range m.Source.Watermarks {
		if !label(key) || value < 0 {
			return fmt.Errorf("%w: captured watermark", ErrInvalid)
		}
	}
	if len(m.Roots) == 0 || len(m.Roots) > 1000 || len(m.Members) == 0 || len(m.Members) > 1000 {
		return fmt.Errorf("%w: captured membership capacity", ErrInvalid)
	}
	for i, ref := range m.Roots {
		if !label(ref.ID) || !validDigest(ref.Digest) || (i > 0 && m.Roots[i-1].ID >= ref.ID) {
			return fmt.Errorf("%w: captured roots", ErrInvalid)
		}
	}
	for i, member := range m.Members {
		if !label(member.ID) || !validDigest(member.Digest) || member.Bytes < 1 || member.Bytes > MaxCanonicalBytes || !label(member.SchemaVersion) || !label(member.CanonicalVersion) || !label(member.HashAlgorithm) || !label(member.Kind) || !label(member.SemanticVersion) || (i > 0 && m.Members[i-1].ID >= member.ID) {
			return fmt.Errorf("%w: captured member", ErrInvalid)
		}
	}
	return nil
}

func captureCanonical(value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, ErrInvalid
	}
	parsed, err := ParseObject(raw)
	if err != nil {
		return nil, err
	}
	return Canonical(CanonicalVersion, parsed)
}

func (m CaptureManifest) CanonicalBytes() ([]byte, error) {
	if err := m.validate(); err != nil {
		return nil, err
	}
	m.Source.CapturedAt = m.Source.CapturedAt.UTC()
	return captureCanonical(m)
}

func (m CaptureManifest) Digest() (string, error) {
	raw, err := m.CanonicalBytes()
	if err != nil {
		return "", err
	}
	return digestBytes("zhiyu/audit-capture/v1\x00", raw), nil
}

func memberOf(id string, original Original, doc Document) CapturedMember {
	return CapturedMember{id, original.Digest, int64(len(original.Canonical)), doc.SchemaVersion, doc.CanonicalVersion, doc.HashAlgorithm, doc.Kind, doc.SemanticVersion}
}

// BuildCapture freezes independent copies of original bytes and source maps.
// It checks raw protocol integrity, not business semantics or DB completeness.
func BuildCapture(scope Scope, source SourceMetadata, rootIDs []string, originals map[string]Original) (CapturedBundle, error) {
	var result CapturedBundle
	if len(originals) == 0 || len(originals) > DefaultBounds().MaxNodes || len(rootIDs) == 0 || len(rootIDs) > DefaultBounds().MaxNodes {
		return result, fmt.Errorf("%w: captured capacity", ErrInvalid)
	}
	ids := make([]string, 0, len(originals))
	for id := range originals {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	result.Originals = make(map[string]Original, len(originals))
	result.Manifest = CaptureManifest{ProtocolVersion: CaptureProtocol, Scope: scope, Source: source, Roots: []Reference{}, Members: []CapturedMember{}}
	result.Manifest.Source.CapturedAt = source.CapturedAt.UTC()
	result.Manifest.Source.Watermarks = make(map[string]int64, len(source.Watermarks))
	for key, value := range source.Watermarks {
		result.Manifest.Source.Watermarks[key] = value
	}
	total := 0
	for _, id := range ids {
		original := originals[id]
		if len(original.Canonical) > DefaultBounds().MaxBytes-total {
			return CapturedBundle{}, fmt.Errorf("%w: captured byte capacity", ErrInvalid)
		}
		total += len(original.Canonical)
		doc, err := parseOriginal(original)
		if err != nil {
			return CapturedBundle{}, err
		}
		if doc.ID != id {
			return CapturedBundle{}, fmt.Errorf("%w: captured identity differs", ErrInvalid)
		}
		result.Manifest.Members = append(result.Manifest.Members, memberOf(id, original, doc))
		result.Originals[id] = Original{append([]byte(nil), original.Canonical...), original.Digest}
	}
	roots := append([]string(nil), rootIDs...)
	sort.Strings(roots)
	for _, id := range roots {
		original, ok := result.Originals[id]
		if !ok {
			return CapturedBundle{}, fmt.Errorf("%w: captured root missing", ErrInvalid)
		}
		result.Manifest.Roots = append(result.Manifest.Roots, Reference{id, original.Digest})
	}
	if err := result.Manifest.validate(); err != nil {
		return CapturedBundle{}, err
	}
	return result, nil
}

func (r *Registry) RegisterScopeValidator(kind, version string, validator ScopeValidator) error {
	if r == nil || !label(kind) || !label(version) || validator == nil {
		return ErrInvalid
	}
	if r.scopeValidators == nil {
		r.scopeValidators = map[semanticKey]ScopeValidator{}
	}
	key := semanticKey{kind, version}
	if r.scopeValidators[key] != nil {
		return fmt.Errorf("%w: scope validator already registered", ErrInvalid)
	}
	r.scopeValidators[key] = validator
	return nil
}

func (r *Registry) RegisterGraphValidator(kind, version string, validator GraphValidator) error {
	if r == nil || !label(kind) || !label(version) || validator == nil {
		return ErrInvalid
	}
	if r.graphValidators == nil {
		r.graphValidators = map[semanticKey]GraphValidator{}
	}
	key := semanticKey{kind, version}
	if r.graphValidators[key] != nil {
		return fmt.Errorf("%w: graph validator already registered", ErrInvalid)
	}
	r.graphValidators[key] = validator
	return nil
}
