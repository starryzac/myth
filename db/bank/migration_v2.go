package bank

import _ "embed"

// MultiAssetSchema is an explicit additive migration. Existing callers of
// Schema/Migrate do not silently install a new bank business protocol.
//
//go:embed 002_multi_asset.sql
var MultiAssetSchema string
