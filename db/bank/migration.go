// Package bank contains the isolated simulated-bank schema, separate from all
// application migrations and application database privileges.
package bank

import _ "embed"

//go:embed 001_bank.sql
var Schema string
