package app

import "sync"

// Memoize only successful PURE verification of immutable bytes. Mutable
// permission, prefix receipts, funds/source versions, reservations, budget
// and PG claim generations are checked separately on every fresh T2.
const standingBridgeValidatorVersion = "standing-bridge-replay-v1"
const standingBridgeMemoCapacity = 32

type standingBridgeValidationMemo struct {
	mu    sync.Mutex
	keys  []string
	valid map[string]bool
}

var standingBridgeVerified = standingBridgeValidationMemo{valid: map[string]bool{}}

func (m *standingBridgeValidationMemo) contains(key string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.valid[key]
}

func (m *standingBridgeValidationMemo) record(key string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.valid == nil {
		m.valid = map[string]bool{}
	}
	if m.valid[key] {
		return
	}
	if len(m.keys) == standingBridgeMemoCapacity {
		delete(m.valid, m.keys[0])
		m.keys = m.keys[1:]
	}
	m.keys = append(m.keys, key)
	m.valid[key] = true
}

func (m *standingBridgeValidationMemo) verify(raw []byte, namespace string) error {
	key := standingBridgeValidatorVersion + "\x00" + namespace + "\x00" + Hash(raw)
	if m.contains(key) {
		return nil
	}
	var fresh nativeStandingBridgeGroup
	if auditJSON(raw, &fresh) != nil {
		return reject("EVIDENCE_INVALID", nil)
	}
	if e := replayStandingBridge(fresh, namespace); e != nil {
		return e
	}
	m.record(key)
	return nil
}
