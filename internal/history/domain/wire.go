package domain

import (
	"bytes"
	"encoding/json"
	"io"
	"strconv"
	"unicode/utf8"
)

// DecodeBundle preserves original byte arrays. Strictness applies to the new
// archive envelope; an unrecognised historic body remains an opaque original.
func DecodeBundle(raw []byte) (Bundle, error) {
	var out Bundle
	if len(raw) == 0 || len(raw) > MaxWireBytes {
		return out, ErrCapacity
	}
	if !utf8.Valid(raw) || !json.Valid(raw) || !pairedEscapes(raw) {
		return out, ErrArchive
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	nodes := 0
	var walk func(int) error
	walk = func(depth int) error {
		nodes++
		if depth > 64 || nodes > 2000000 {
			return ErrCapacity
		}
		token, e := d.Token()
		if e != nil {
			return ErrArchive
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, e := d.Token()
				if e != nil {
					return ErrArchive
				}
				s, ok := key.(string)
				if !ok || seen[s] {
					return ErrArchive
				}
				seen[s] = true
				if e = walk(depth + 1); e != nil {
					return e
				}
			}
			close, e := d.Token()
			if e != nil || close != json.Delim('}') {
				return ErrArchive
			}
		case '[':
			for d.More() {
				if e = walk(depth + 1); e != nil {
					return e
				}
			}
			close, e := d.Token()
			if e != nil || close != json.Delim(']') {
				return ErrArchive
			}
		default:
			return ErrArchive
		}
		return nil
	}
	if e := walk(0); e != nil {
		return out, e
	}
	if _, e := d.Token(); e != io.EOF {
		return out, ErrArchive
	}
	d = json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&out) != nil {
		return Bundle{}, ErrArchive
	}
	if d.Decode(new(any)) != io.EOF {
		return Bundle{}, ErrArchive
	}
	if len(out.Objects) > MaxObjects || len(out.Manifest.Objects) > MaxObjects {
		return Bundle{}, ErrCapacity
	}
	for _, o := range out.Objects {
		if len(o.Original) > MaxObjectBytes {
			return Bundle{}, ErrCapacity
		}
	}
	return out, nil
}

// encoding/json replaces unpaired surrogate escapes. Reject them before that
// lossy step so two byte-distinct identities cannot silently become U+FFFD.
func pairedEscapes(raw []byte) bool {
	in := false
	for i := 0; i < len(raw); i++ {
		if raw[i] == '"' {
			in = !in
			continue
		}
		if !in || raw[i] != '\\' {
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
		v, e := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if e != nil {
			return false
		}
		i += 4
		if v >= 0xDC00 && v <= 0xDFFF {
			return false
		}
		if v < 0xD800 || v > 0xDBFF {
			continue
		}
		if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
			return false
		}
		low, e := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
		if e != nil || low < 0xDC00 || low > 0xDFFF {
			return false
		}
		i += 6
	}
	return !in
}
