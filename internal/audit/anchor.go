package audit

import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
)

var ErrAnchorTrustMissing = errors.New("trusted audit anchor key missing")

func anchorMessage(keyID, manifestDigest string) []byte {
	return []byte("zhiyu/audit-anchor/v1\x00" + keyID + "\x00" + manifestDigest)
}

// SignAnchor has no key discovery or I/O. Keeping a signing/trust root outside
// the audited DB is the caller's responsibility; signatures confer no authority.
func SignAnchor(manifest CaptureManifest, keyID string, privateKey ed25519.PrivateKey) (Anchor, error) {
	if !label(keyID) || len(privateKey) != ed25519.PrivateKeySize {
		return Anchor{}, ErrInvalid
	}
	digest, err := manifest.Digest()
	if err != nil {
		return Anchor{}, err
	}
	signature := ed25519.Sign(privateKey, anchorMessage(keyID, digest))
	return Anchor{AnchorProtocol, AnchorAlgorithm, keyID, digest, base64.RawStdEncoding.EncodeToString(signature)}, nil
}

func VerifyAnchor(manifest CaptureManifest, anchor Anchor, trustedKeys map[string]ed25519.PublicKey) error {
	if anchor.ProtocolVersion != AnchorProtocol || anchor.Algorithm != AnchorAlgorithm {
		return ErrUnsupportedVersion
	}
	if !label(anchor.KeyID) || !validDigest(anchor.ManifestDigest) {
		return ErrInvalid
	}
	key, ok := trustedKeys[anchor.KeyID]
	if !ok {
		return ErrAnchorTrustMissing
	}
	if len(key) != ed25519.PublicKeySize {
		return ErrInvalid
	}
	digest, err := manifest.Digest()
	if err != nil {
		return err
	}
	signature, err := base64.RawStdEncoding.DecodeString(anchor.Signature)
	if err != nil || len(signature) != ed25519.SignatureSize || digest != anchor.ManifestDigest || !ed25519.Verify(key, anchorMessage(anchor.KeyID, digest), signature) {
		return ErrInvalid
	}
	return nil
}
