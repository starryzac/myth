package app

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type Session struct {
	Token       string    `json:"token"`
	CSRFToken   string    `json:"csrf_token"`
	UserID      funds.ID  `json:"user_id"`
	Environment string    `json:"environment"`
	ExpiresAt   time.Time `json:"expires_at"`
}

func randomToken() (string, error) {
	var token [32]byte
	_, err := rand.Read(token[:])
	return hex.EncodeToString(token[:]), err
}

func (s *Service) Login(ctx context.Context, secret string) (Session, error) {
	if len(secret) < 32 || len(secret) > 128 {
		return Session{}, reject("UNAUTHENTICATED", nil)
	}
	env, err := s.environment(ctx, s.Pool, false)
	if err != nil {
		return Session{}, err
	}
	var uid string
	err = s.Pool.QueryRow(ctx, "SELECT user_id::text FROM login_credentials WHERE environment_id=$1 AND secret_hash=$2 AND NOT disabled", env.ID.String(), Hash([]byte(secret))).Scan(&uid)
	if err != nil {
		return Session{}, reject("UNAUTHENTICATED", nil)
	}
	user, err := funds.ParseID(uid)
	if err != nil {
		return Session{}, err
	}
	token, err := randomToken()
	if err != nil {
		return Session{}, err
	}
	csrf, err := randomToken()
	if err != nil {
		return Session{}, err
	}
	id, err := newID()
	if err != nil {
		return Session{}, err
	}
	end := s.now().Add(12 * time.Hour)
	_, err = s.Pool.Exec(ctx, "INSERT INTO sessions(id,environment_id,user_id,token_hash,csrf_hash,expires_at) VALUES($1,$2,$3,$4,$5,$6)", id.String(), env.ID.String(), uid, Hash([]byte(token)), Hash([]byte(csrf)), end)
	return Session{token, csrf, user, s.Namespace, end}, err
}

func (s *Service) Authenticate(ctx context.Context, token, csrf string, write bool) (Principal, error) {
	if len(token) != 64 || (write && len(csrf) != 64) {
		return Principal{}, reject("UNAUTHENTICATED", nil)
	}
	var eid, uid, sid, expectedCSRF string
	err := s.Pool.QueryRow(ctx, `SELECT s.environment_id::text,s.user_id::text,s.id::text,s.csrf_hash FROM sessions s JOIN environments e ON e.id=s.environment_id WHERE e.namespace=$1 AND s.token_hash=$2 AND s.expires_at>$3 AND NOT s.revoked`, s.Namespace, Hash([]byte(token)), s.now()).Scan(&eid, &uid, &sid, &expectedCSRF)
	if err != nil {
		return Principal{}, reject("UNAUTHENTICATED", nil)
	}
	if write && expectedCSRF != Hash([]byte(csrf)) {
		return Principal{}, reject("FORBIDDEN", nil)
	}
	e, err := funds.ParseID(eid)
	if err != nil {
		return Principal{}, err
	}
	u, err := funds.ParseID(uid)
	if err != nil {
		return Principal{}, err
	}
	sessionID, err := funds.ParseID(sid)
	if err != nil {
		return Principal{}, err
	}
	return Principal{e, u, "USER", sessionID}, nil
}
