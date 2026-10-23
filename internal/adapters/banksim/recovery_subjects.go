package banksim

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankrecovery"
)

// Subjects discovers accounts for a namespace without relying on the restored
// application's user list. It reads no credentials and mutates no bank state.
func (s *Store) Subjects(ctx context.Context, namespace string) (bankrecovery.SubjectSnapshot, error) {
	var out bankrecovery.SubjectSnapshot
	if !bankrecovery.ValidNamespace(namespace) {
		return out, bankrecovery.ErrInvalid
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	out.Protocol, out.Namespace, out.UserIDs = bankrecovery.SubjectsProtocol, namespace, []string{}
	if err = tx.QueryRow(ctx, "SELECT transaction_timestamp()").Scan(&out.CapturedAt); err != nil {
		return bankrecovery.SubjectSnapshot{}, err
	}
	rows, err := tx.Query(ctx, `SELECT DISTINCT user_id COLLATE "C" AS user_id FROM bank.accounts WHERE namespace=$1 ORDER BY user_id LIMIT 1001`, namespace)
	if err != nil {
		return bankrecovery.SubjectSnapshot{}, err
	}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return bankrecovery.SubjectSnapshot{}, err
		}
		out.UserIDs = append(out.UserIDs, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return bankrecovery.SubjectSnapshot{}, err
	}
	if len(out.UserIDs) > bankrecovery.MaxSubjects {
		return bankrecovery.SubjectSnapshot{}, ErrV2Capacity
	}
	out.Complete = true
	if err = out.Validate(); err != nil {
		return bankrecovery.SubjectSnapshot{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return bankrecovery.SubjectSnapshot{}, err
	}
	return out, nil
}

func registerRecoverySubjects(mux *http.ServeMux, store *Store) {
	mux.HandleFunc("GET /v2/recovery/subjects", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if len(q) != 1 || len(q["namespace"]) != 1 || !bankrecovery.ValidNamespace(q.Get("namespace")) {
			writeError(w, http.StatusBadRequest, "INVALID_BANK_RECOVERY_SCOPE")
			return
		}
		out, err := store.Subjects(r.Context(), q.Get("namespace"))
		if err != nil {
			v2HTTPError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, out)
	})
}

func (c *ClientV2) Subjects(ctx context.Context, namespace string) (bankrecovery.SubjectSnapshot, error) {
	var out bankrecovery.SubjectSnapshot
	if !bankrecovery.ValidNamespace(namespace) {
		return out, bankrecovery.ErrInvalid
	}
	var wire json.RawMessage
	q := url.Values{"namespace": {namespace}}
	if err := c.call(ctx, http.MethodGet, "/v2/recovery/subjects?"+q.Encode(), nil, "", "", &wire); err != nil {
		return out, err
	}
	if len(wire) > 256<<10 || !uniqueV2JSON(wire) {
		return out, bankrecovery.ErrInvalid
	}
	d := json.NewDecoder(bytes.NewReader(wire))
	d.DisallowUnknownFields()
	if d.Decode(&out) != nil || out.Namespace != namespace || out.Validate() != nil {
		return bankrecovery.SubjectSnapshot{}, bankrecovery.ErrInvalid
	}
	if _, err := d.Token(); err != io.EOF {
		return bankrecovery.SubjectSnapshot{}, bankrecovery.ErrInvalid
	}
	return out, nil
}
