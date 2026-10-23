// Package bankrecovery defines bounded, read-only simulated-bank discovery.
// Discovery does not establish a USER, permission, or economic effect.
package bankrecovery

import (
	"errors"
	"regexp"
	"strings"
	"time"
)

const SubjectsProtocol = "zhiyu-bank-recovery-subjects-v1"
const MaxSubjects = 1000

var ErrInvalid = errors.New("invalid bank recovery subjects contract")
var namespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

func ValidNamespace(value string) bool { return namespace.MatchString(value) }

// SubjectSnapshot is a current complete list at one database snapshot. It is
// not an historical watermark; later account creation requires another read.
type SubjectSnapshot struct {
	Protocol   string    `json:"protocol"`
	Namespace  string    `json:"namespace"`
	CapturedAt time.Time `json:"captured_at"`
	Complete   bool      `json:"complete"`
	UserIDs    []string  `json:"user_ids"`
}

func (s SubjectSnapshot) Validate() error {
	if s.Protocol != SubjectsProtocol || !ValidNamespace(s.Namespace) || s.CapturedAt.IsZero() || !s.Complete || s.UserIDs == nil || len(s.UserIDs) > MaxSubjects {
		return ErrInvalid
	}
	previous := ""
	for _, id := range s.UserIDs {
		if id == "" || len(id) > 128 || strings.ContainsAny(id, "/\\\x00\r\n") || id <= previous {
			return ErrInvalid
		}
		previous = id
	}
	return nil
}
