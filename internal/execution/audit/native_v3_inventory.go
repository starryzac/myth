package executionaudit

import (
	"bytes"
	"fmt"
	"sort"
	"strconv"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

func nativeForecastProtectionV3(input planning.ForecastInput, result planning.ForecastResult) (planning.Protection, error) {
	unowned := input.Facts.CurrentCash
	var err error
	for _, goal := range input.Facts.Goals {
		unowned, err = unowned.Sub(goal.CashOwned)
		if err != nil {
			return planning.Protection{}, err
		}
	}
	unowned, err = unowned.Sub(input.UnassignedGoalCash)
	if err != nil {
		return planning.Protection{}, err
	}
	unowned, err = unowned.Sub(input.HeldCash)
	if err != nil {
		return planning.Protection{}, err
	}
	other := funds.Money{}
	if result.CurrentSafe.Cents() < unowned.Cents() {
		other, _ = unowned.Sub(result.CurrentSafe)
	}
	other, err = other.Add(result.MaximumDeficit)
	if err != nil {
		return planning.Protection{}, err
	}
	return planning.Protection{Complete: true, ObservedAt: input.AsOf, EvidenceHash: result.InputHash, Checkpoints: []planning.ProtectionCheckpoint{{ID: "native-template-protection-365-v1", At: input.AsOf, OtherProtected: other}}}, nil
}

func (g *nativeGraphV3) checkInventoryAndProtectionV3() error {
	seedHash := nakedHash([]byte("zhiyu-demo-seed-v1/synthetic-zero-obligations/" + g.manifest.Scope.EnvironmentID + "/" + g.manifest.Scope.UserID))
	publications := map[string]bool{}
	for _, row := range g.rows["template_protection_publications"] {
		var input planning.ForecastInput
		var result planning.ForecastResult
		nativeOriginalV3(row, "input_original", &input)
		nativeOriginalV3(row, "result_original", &result)
		source := strconv.FormatInt(row.positive("source_version"), 10)
		protectionRow := g.row("protection_snapshots", source)
		if protectionRow == nil {
			return fmt.Errorf("%w: native forecast protection original omitted", evidence.ErrIncomplete)
		}
		expected, err := nativeForecastProtectionV3(input, result)
		zone, e := policy.TemplateLocation(input.Timezone)
		if err != nil || e != nil {
			return businessInvalid("native protection conversion input")
		}
		date := input.AsOf.In(zone).Format("2006-01-02")
		basis := nakedHash(mustJSONV3(struct{ SourceHash, Date, Timezone string }{input.Facts.SourceHash, date, input.Timezone}))
		if row.text("basis_hash") != basis || row.text("local_date") != date || row.text("timezone") != input.Timezone || !row.instant("created_at").Equal(input.AsOf) || row.text("protection_hash") != protectionRow.text("original_hash") || !bytes.Equal(mustJSONV3(expected), nativeRawV3(protectionRow, "original")) {
			return businessInvalid("native forecast publication exact source/date/protection conversion")
		}
		publications[source] = true
	}
	versions := []int64{}
	for _, row := range g.rows["protection_snapshots"] {
		if nativeHashFieldV3(row, "original", "original_hash") != nil {
			return businessInvalid("native exact protection source bytes")
		}
		var original planning.Protection
		nativeOriginalV3(row, "original", &original)
		version := row.positive("version")
		versions = append(versions, version)
		if !original.Complete || !original.ObservedAt.Equal(row.instant("observed_at")) {
			return businessInvalid("native complete protection source observation")
		}
		if !publications[strconv.FormatInt(version, 10)] {
			if original.EvidenceHash != seedHash {
				return fmt.Errorf("%w: native protection source outside registered seed/publication protocols", evidence.ErrUnsupportedVersion)
			}
			if len(original.Checkpoints) != 1 || original.Checkpoints[0].ID != "explicit-synthetic-zero-obligations" || !original.Checkpoints[0].At.Equal(original.ObservedAt) || !original.Checkpoints[0].OtherProtected.IsZero() || !original.Checkpoints[0].CurrentGoalMinimum.IsZero() {
				return businessInvalid("native synthetic seed inventory is exact and never imported cash authority")
			}
		}
	}
	sort.Slice(versions, func(i, j int) bool { return versions[i] < versions[j] })
	for i, v := range versions {
		if v != int64(i+1) {
			return fmt.Errorf("%w: native complete historical protection version chain", evidence.ErrIncomplete)
		}
	}
	if int64(len(versions)) != g.snapshot.Watermarks["source_version"] {
		return fmt.Errorf("%w: native current protection source omitted", evidence.ErrIncomplete)
	}
	if len(g.rows["template_inventory_contracts"]) > 1 {
		return businessInvalid("native one scoped inventory original")
	}
	if len(g.rows["template_policies"])+len(g.rows["template_policy_previews"]) > 0 && len(g.rows["template_inventory_contracts"]) != 1 {
		return fmt.Errorf("%w: native template complete inventory original absent", evidence.ErrIncomplete)
	}
	for _, row := range g.rows["template_inventory_contracts"] {
		var inventory mirrortemplateInventoryV3
		nativeOriginalV3(row, "original", &inventory)
		seed := g.row("protection_snapshots", strconv.FormatUint(inventory.SeedProtectionVersion, 10))
		if seed == nil || nativeHashFieldV3(row, "original", "original_hash") != nil || inventory.Protocol != "synthetic-seed-inventory-v1" || inventory.Protocol != row.text("protocol_version") || inventory.Scope != g.ownerScopeV3() || inventory.SeedProtectionVersion != uint64(row.positive("seed_protection_version")) || inventory.SeedProtectionHash != row.text("seed_protection_hash") || inventory.SeedProtectionHash != seed.text("original_hash") || inventory.ClosedWorld != "explicit-synthetic-empty-external-obligations-assets-history-v1" {
			return businessInvalid("native inventory exact scoped seed binding")
		}
		var protection planning.Protection
		nativeOriginalV3(seed, "original", &protection)
		if protection.EvidenceHash != seedHash {
			return businessInvalid("native inventory cannot turn a later forecast into an empty seed")
		}
	}
	return g.checkedRowsV3()
}
