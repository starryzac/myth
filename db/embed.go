// Package db owns embedded Goose migrations for the isolated Go application DB.
package db

import "embed"

// Migrations is embedded in the binary; migration ownership uses a separate DSN.
//
//go:embed migrations/*.sql
var Migrations embed.FS
