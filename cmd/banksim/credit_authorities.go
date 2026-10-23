package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"zhiyu.local/zhiyu-go/internal/adapters/banksim"
)

// This private bank-only file is never loaded from an application runtime.
// Errors intentionally omit values, paths, request bodies and credentials.
func loadExternalAuthorities(path string) ([]banksim.CreditAuthority, error) {
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 1<<20 {
		return nil, fmt.Errorf("external credit authority file unavailable")
	}
	var cfg struct {
		Protocol    string `json:"protocol"`
		Simulation  bool   `json:"simulation"`
		Authorities []struct {
			ID        string `json:"id"`
			Namespace string `json:"namespace"`
			UserID    string `json:"user_id"`
			AccountID string `json:"account_id"`
			Secret    string `json:"secret"`
		} `json:"authorities"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	var extra any
	if d.Decode(&cfg) != nil || d.Decode(&extra) != io.EOF || cfg.Protocol != "zhiyu-external-credit-authorities-v1" || !cfg.Simulation || len(cfg.Authorities) < 1 || len(cfg.Authorities) > 1000 {
		return nil, fmt.Errorf("invalid external credit authority file")
	}
	out := make([]banksim.CreditAuthority, len(cfg.Authorities))
	for i, a := range cfg.Authorities {
		if len(a.Secret) < 32 || len(a.Secret) > 4096 {
			return nil, fmt.Errorf("invalid external credit authority file")
		}
		out[i] = banksim.CreditAuthority{ID: a.ID, Namespace: a.Namespace, UserID: a.UserID, AccountID: a.AccountID, Secret: []byte(a.Secret)}
	}
	return out, nil
}
