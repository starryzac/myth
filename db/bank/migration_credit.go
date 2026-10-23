package bank

import _ "embed"

// ExternalCreditSchema is an explicit additive external-source protocol. No
// v1/v2 migration installs it or changes the meaning of its old originals.
//
//go:embed 003_external_credit.sql
var ExternalCreditSchema string
