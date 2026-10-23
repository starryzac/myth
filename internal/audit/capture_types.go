package audit

import (
	"crypto/ed25519"
	"errors"
	"time"
)

// ErrIncomplete lets a business plugin distinguish absent historical proof
// from a present original that contradicts the claimed business semantics.
var ErrIncomplete = errors.New("incomplete captured evidence")

// ReferenceError carries a concrete failing member or expected reference
// without coupling the generic verifier to business table identities.
type ReferenceError struct {
	ID    string
	Cause error
}

func (e *ReferenceError) Error() string { return "captured reference validation failed" }
func (e *ReferenceError) Unwrap() error { return e.Cause }

const CaptureProtocol = "zhiyu-audit-capture-v1"
const AnchorProtocol = "zhiyu-audit-anchor-v1"
const AnchorAlgorithm = "ED25519"

// Scope is supplied by the trusted, bounded snapshot collector. A verification
// of this scope does not claim coverage of other epochs or other users.
type Scope struct {
	EnvironmentID string `json:"environment_id"`
	Namespace     string `json:"namespace"`
	UserID        string `json:"user_id"`
	Epoch         string `json:"epoch"`
}

type SourceMetadata struct {
	CapturedAt           time.Time        `json:"captured_at"`
	Watermarks           map[string]int64 `json:"watermarks"`
	SourceManifestSHA256 string           `json:"source_manifest_sha256"`
	ConfigSHA256         string           `json:"config_sha256"`
}

type CapturedMember struct {
	ID               string `json:"id"`
	Digest           string `json:"digest"`
	Bytes            int64  `json:"bytes"`
	SchemaVersion    string `json:"schema_version"`
	CanonicalVersion string `json:"canonical_version"`
	HashAlgorithm    string `json:"hash_algorithm"`
	Kind             string `json:"kind"`
	SemanticVersion  string `json:"semantic_version"`
}

// CaptureManifest binds the entire captured membership, not just a convenient
// reachable root. The collector must establish completeness of its DB snapshot;
// the pure kernel checks exact membership and closure under that stated scope.
type CaptureManifest struct {
	ProtocolVersion string           `json:"protocol_version"`
	Scope           Scope            `json:"scope"`
	Source          SourceMetadata   `json:"source"`
	Roots           []Reference      `json:"roots"`
	Members         []CapturedMember `json:"members"`
}

type CapturedBundle struct {
	Manifest  CaptureManifest     `json:"manifest"`
	Originals map[string]Original `json:"originals"`
}

type Bounds struct {
	MaxNodes int `json:"max_nodes"`
	MaxBytes int `json:"max_bytes"`
	MaxEdges int `json:"max_edges"`
	MaxDepth int `json:"max_depth"`
}

func DefaultBounds() Bounds { return Bounds{1000, 16 * 1024 * 1024, 16000, 128} }

type Anchor struct {
	ProtocolVersion string `json:"protocol_version"`
	Algorithm       string `json:"algorithm"`
	KeyID           string `json:"key_id"`
	ManifestDigest  string `json:"manifest_digest"`
	Signature       string `json:"signature"`
}

// TrustedKeys must come from an independently trusted caller configuration.
// A key read from the same audited database is not a new trust root.
type CaptureOptions struct {
	Bounds        Bounds
	RequireAnchor bool
	Anchor        *Anchor
	TrustedKeys   map[string]ed25519.PublicKey
}

type CaptureVerification struct {
	Status               Status            `json:"status"`
	Scope                Scope             `json:"scope"`
	ManifestDigest       string            `json:"manifest_digest"`
	Assurance            string            `json:"assurance"`
	AnchorStatus         string            `json:"anchor_status"`
	CapturedAt           time.Time         `json:"captured_at"`
	Watermarks           map[string]int64  `json:"watermarks"`
	SourceManifestSHA256 string            `json:"source_manifest_sha256"`
	ConfigSHA256         string            `json:"config_sha256"`
	RootIDs              []string          `json:"root_ids"`
	CheckedIDs           []string          `json:"checked_ids"`
	OriginalBytes        int               `json:"original_bytes"`
	EdgeCount            int               `json:"edge_count"`
	ClosureDigest        string            `json:"closure_digest"`
	Versions             []CapturedVersion `json:"versions"`
	FailedID             string            `json:"failed_id,omitempty"`
	Reason               string            `json:"reason,omitempty"`
}

type CapturedVersion struct {
	SchemaVersion    string `json:"schema_version"`
	CanonicalVersion string `json:"canonical_version"`
	HashAlgorithm    string `json:"hash_algorithm"`
	Kind             string `json:"kind"`
	SemanticVersion  string `json:"semantic_version"`
}

// ScopeValidator and GraphValidator are deterministic business-owned plugins.
// The generic kernel never imports funds, execution, app, storage or transport.
type ScopeValidator func(Scope, Document) error
type GraphValidator func(CaptureManifest, map[string]Document) error
