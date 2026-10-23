package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"

	"zhiyu.local/zhiyu-go/internal/adapters/banksim"
	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	"zhiyu.local/zhiyu-go/internal/platform"
)

type demoBankFacts struct {
	Protocol    string                      `json:"protocol"`
	Simulation  bool                        `json:"simulation"`
	Namespace   string                      `json:"namespace"`
	UserID      funds.ID                    `json:"user_id"`
	Products    []bankv2.ProductSnapshot    `json:"products"`
	Liabilities []bankv2.LiabilityReference `json:"liabilities"`
}

// Explicit seed-role-only simulated independent bank originals. Neither the
// public API nor Agent/worker configuration receives this bank DB capability.
func seedDemoBankFacts(ctx context.Context, s *app.Service, c platform.Runtime, path string, out io.Writer) error {
	if c.Role != "seed" || c.BankDSN == "" {
		return errors.New("controlled bank facts require the separate seed role")
	}
	seed, e := readSeed(c.SeedState)
	if e != nil {
		return e
	}
	raw, e := os.ReadFile(path)
	if e != nil || len(raw) == 0 || len(raw) > 1<<20 {
		return errors.New("bounded explicit bank-facts file unavailable")
	}
	var in demoBankFacts
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var extra any
	if decoder.Decode(&in) != nil || decoder.Decode(&extra) != io.EOF || in.Protocol != "zhiyu-demo-bank-facts-v1" || !in.Simulation || in.Namespace != c.Namespace || in.Namespace != seed.Namespace || in.UserID != seed.UserID || in.Products == nil || in.Liabilities == nil || len(in.Products)+len(in.Liabilities) < 1 || len(in.Products) > 20 || len(in.Liabilities) > 20 {
		return errors.New("controlled bank-facts identity or protocol rejected")
	}
	var owned bool
	if e = s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM environments e JOIN users u ON u.environment_id=e.id WHERE e.namespace=$1 AND e.id=$2 AND u.id=$3)`, seed.Namespace, seed.EnvironmentID.String(), seed.UserID.String()).Scan(&owned); e != nil || !owned {
		return errors.New("controlled bank-facts target is not the configured seeded user")
	}
	products, liabilities := map[string]bool{}, map[string]bool{}
	for _, p := range in.Products {
		if p.Validate() != nil || products[p.Terms.ProductID] {
			return errors.New("controlled product original invalid or duplicated")
		}
		products[p.Terms.ProductID] = true
	}
	for _, l := range in.Liabilities {
		if l.Validate() != nil || liabilities[l.ID] {
			return errors.New("controlled liability original invalid or duplicated")
		}
		liabilities[l.ID] = true
	}
	bank, e := banksim.New(ctx, c.BankDSN, []byte(c.BankPermitSecret))
	if e != nil {
		return errors.New("controlled independent bank unavailable")
	}
	defer bank.Close()
	if bank.CheckV2(ctx) != nil {
		return errors.New("controlled bank v2 schema is not ready")
	}
	for _, p := range in.Products {
		if bank.PublishProductV2(ctx, in.Namespace, p) != nil {
			return errors.New("controlled product publication failed; original bank records retained for exact replay")
		}
	}
	for _, l := range in.Liabilities {
		if bank.OpenLiabilityV2(ctx, in.Namespace, in.UserID.String(), l) != nil {
			return errors.New("controlled liability registration failed; original bank records retained for exact replay")
		}
	}
	return json.NewEncoder(out).Encode(map[string]any{"status": "CONTROLLED_BANK_ORIGINALS_RECORDED", "namespace": in.Namespace, "products": len(in.Products), "liabilities": len(in.Liabilities), "fixture_sha256": app.Hash(raw), "simulation": true, "financial_effects_created": false, "credentials_printed": false})
}
