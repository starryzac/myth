// Package audit implements bounded, pure evidence encoding and verification.
// It does not confer financial authority or replay business services.
package audit

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	LegacyCanonicalVersion = "audit-canonical-json-v1"
	CanonicalVersion       = "zhiyu-canonical-json-v1"
	MaxCanonicalBytes      = 1024 * 1024
	maxDepth               = 32
	maxNodes               = 250000
)

var (
	ErrUnsupportedVersion = errors.New("unsupported evidence version")
	ErrInvalid            = errors.New("invalid evidence")
)

// Canonical encodes the declared integer-only protocol, preserving bool versus
// integer, string time lexemes, null, empty collections and Unicode code points.
// time.Time inputs use the old protocol's UTC microsecond adapter. Raw legacy
// subjects (which allow floats and larger bounds) are deliberately unsupported.
func Canonical(version string, value map[string]any) ([]byte, error) {
	if version != CanonicalVersion && version != LegacyCanonicalVersion {
		return nil, ErrUnsupportedVersion
	}
	if value == nil {
		return nil, fmt.Errorf("%w: object required", ErrInvalid)
	}
	var out bytes.Buffer
	nodes := 0
	var encode func(any, int) error
	encode = func(v any, depth int) error {
		nodes++
		if depth > maxDepth || nodes > maxNodes || out.Len() > MaxCanonicalBytes {
			return fmt.Errorf("%w: canonical bounds", ErrInvalid)
		}
		switch x := v.(type) {
		case nil:
			out.WriteString("null")
		case bool:
			out.WriteString(strconv.FormatBool(x))
		case int:
			out.WriteString(strconv.FormatInt(int64(x), 10))
		case int64:
			out.WriteString(strconv.FormatInt(x, 10))
		case json.Number:
			n, err := integer(x)
			if err != nil {
				return err
			}
			out.WriteString(strconv.FormatInt(n, 10))
		case string:
			if !utf8.ValidString(x) {
				return fmt.Errorf("%w: invalid UTF-8", ErrInvalid)
			}
			writeString(&out, x)
		case time.Time:
			if x.UTC().Year() < 1 || x.UTC().Year() > 9999 {
				return fmt.Errorf("%w: datetime year", ErrInvalid)
			}
			writeString(&out, x.UTC().Format("2006-01-02T15:04:05.000000Z"))
		case []any:
			out.WriteByte('[')
			for i, item := range x {
				if i > 0 {
					out.WriteByte(',')
				}
				if err := encode(item, depth+1); err != nil {
					return err
				}
			}
			out.WriteByte(']')
		case map[string]any:
			keys := make([]string, 0, len(x))
			for k := range x {
				keys = append(keys, k)
			}
			sort.Strings(keys) // Valid UTF-8 lexical order matches Unicode code point order.
			out.WriteByte('{')
			for i, k := range keys {
				if !utf8.ValidString(k) {
					return fmt.Errorf("%w: invalid key UTF-8", ErrInvalid)
				}
				if strings.HasSuffix(k, "_cents") && x[k] != nil {
					switch n := x[k].(type) {
					case int, int64:
					case json.Number:
						if _, err := integer(n); err != nil {
							return err
						}
					default:
						return fmt.Errorf("%w: cents must be signed64 integer", ErrInvalid)
					}
				}
				if i > 0 {
					out.WriteByte(',')
				}
				writeString(&out, k)
				out.WriteByte(':')
				if err := encode(x[k], depth+1); err != nil {
					return err
				}
			}
			out.WriteByte('}')
		default:
			return fmt.Errorf("%w: unsupported value type %T", ErrInvalid, v)
		}
		if out.Len() > MaxCanonicalBytes {
			return fmt.Errorf("%w: byte limit", ErrInvalid)
		}
		return nil
	}
	if err := encode(value, 0); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func integer(n json.Number) (int64, error) {
	s := string(n)
	digits := s
	if strings.HasPrefix(digits, "-") {
		digits = digits[1:]
	}
	if len(digits) == 0 || (len(digits) > 1 && digits[0] == '0') {
		return 0, fmt.Errorf("%w: integer lexical form", ErrInvalid)
	}
	for _, c := range digits {
		if c < '0' || c > '9' {
			return 0, fmt.Errorf("%w: integer required", ErrInvalid)
		}
	}
	if strings.ContainsAny(s, ".eE") {
		return 0, fmt.Errorf("%w: integer required", ErrInvalid)
	}
	result, err := strconv.ParseInt(string(n), 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%w: signed64 integer required", ErrInvalid)
	}
	return result, nil
}

// writeString deliberately does not use encoding/json's HTML or U+2028 escapes.
func writeString(out *bytes.Buffer, s string) {
	out.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			out.WriteByte('\\')
			out.WriteRune(r)
		case '\b':
			out.WriteString(`\b`)
		case '\t':
			out.WriteString(`\t`)
		case '\n':
			out.WriteString(`\n`)
		case '\f':
			out.WriteString(`\f`)
		case '\r':
			out.WriteString(`\r`)
		default:
			if r < 0x20 {
				fmt.Fprintf(out, `\u%04x`, r)
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
}

// ParseObject rejects duplicate keys at every depth, nonintegers, bad UTF-8 and
// lone escaped surrogates. It never decodes numbers through float64.
func ParseObject(raw []byte) (map[string]any, error) {
	if len(raw) > MaxCanonicalBytes || !utf8.Valid(raw) || !validSurrogates(raw) {
		return nil, fmt.Errorf("%w: original bytes", ErrInvalid)
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	nodes := 0
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		nodes++
		if depth > maxDepth || nodes > maxNodes {
			return nil, fmt.Errorf("%w: JSON bounds", ErrInvalid)
		}
		token, err := d.Token()
		if err != nil {
			return nil, fmt.Errorf("%w: JSON syntax", ErrInvalid)
		}
		switch x := token.(type) {
		case json.Delim:
			switch x {
			case '{':
				object := map[string]any{}
				for d.More() {
					keyToken, err := d.Token()
					if err != nil {
						return nil, fmt.Errorf("%w: key", ErrInvalid)
					}
					key, ok := keyToken.(string)
					if !ok {
						return nil, fmt.Errorf("%w: key type", ErrInvalid)
					}
					if _, exists := object[key]; exists {
						return nil, fmt.Errorf("%w: duplicate key", ErrInvalid)
					}
					value, err := read(depth + 1)
					if err != nil {
						return nil, err
					}
					object[key] = value
				}
				if end, err := d.Token(); err != nil || end != json.Delim('}') {
					return nil, ErrInvalid
				}
				return object, nil
			case '[':
				array := []any{}
				for d.More() {
					value, err := read(depth + 1)
					if err != nil {
						return nil, err
					}
					array = append(array, value)
				}
				if end, err := d.Token(); err != nil || end != json.Delim(']') {
					return nil, ErrInvalid
				}
				return array, nil
			}
			return nil, ErrInvalid
		case json.Number:
			if _, err := integer(x); err != nil {
				return nil, err
			}
			return x, nil
		case nil, bool, string:
			return x, nil
		default:
			return nil, ErrInvalid
		}
	}
	value, err := read(0)
	if err != nil {
		return nil, err
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, fmt.Errorf("%w: trailing JSON", ErrInvalid)
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%w: object required", ErrInvalid)
	}
	if _, err = Canonical(CanonicalVersion, object); err != nil {
		return nil, err
	}
	return object, nil
}

// encoding/json replaces invalid UTF-16 escapes; reject them before decoding.
func validSurrogates(raw []byte) bool {
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return false
		}
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return false
		}
		n, err := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if err != nil {
			return false
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n < 0xd800 || n > 0xdbff {
			continue
		}
		if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
			return false
		}
		low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
		if err != nil || low < 0xdc00 || low > 0xdfff {
			return false
		}
		i += 6
	}
	return true
}

// LegacyEventDigest reproduces only the old integer-only audit-event namespace.
// It is not a validation of an old event schema, chain or business result.
func LegacyEventDigest(value map[string]any) (string, error) {
	raw, err := Canonical(LegacyCanonicalVersion, value)
	if err != nil {
		return "", err
	}
	return digestBytes("bounded-funds/audit-event-v1\x00", raw), nil
}

func digestBytes(prefix string, raw []byte) string {
	h := sha256.New()
	h.Write([]byte(prefix))
	h.Write(raw)
	return hex.EncodeToString(h.Sum(nil))
}
