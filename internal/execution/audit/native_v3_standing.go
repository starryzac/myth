package executionaudit

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"strconv"
	"strings"
	"time"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankcredit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type nativeOccurrenceV3 struct {
	Protocol     string                        `json:"protocol"`
	ID           funds.ID                      `json:"id"`
	Scope        policy.TemplateOwnerScope     `json:"scope"`
	GrantID      funds.ID                      `json:"grant_id"`
	GrantVersion uint64                        `json:"grant_version"`
	Family       policy.NativeFamily           `json:"family"`
	Key          string                        `json:"key"`
	Source       policy.NativeOccurrenceSource `json:"source"`
	CreatedAt    time.Time                     `json:"created_at"`
}
type nativeAutomationEventV3 struct {
	Protocol     string                    `json:"protocol"`
	Scope        policy.TemplateOwnerScope `json:"scope"`
	GrantID      funds.ID                  `json:"grant_id"`
	GrantVersion uint64                    `json:"grant_version"`
	ID           funds.ID                  `json:"id"`
	AggregateID  funds.ID                  `json:"aggregate_id"`
	Kind         string                    `json:"kind"`
	PayloadHex   string                    `json:"payload_hex"`
	CreatedAt    time.Time                 `json:"created_at"`
}

func (g *nativeGraphV3) checkOccurrencesV3() error {
	g.occurrences = map[string]nativeOccurrenceV3{}
	for _, row := range g.rows["native_occurrences"] {
		var occurrence nativeOccurrenceV3
		nativeOriginalV3(row, "original", &occurrence)
		grant, ok := g.nativeGrants[occurrence.GrantID.String()]
		key, err := policy.NativeOccurrenceKey(occurrence.Scope, occurrence.GrantID, occurrence.Family, occurrence.Source)
		if !ok || nativeHashFieldV3(row, "original", "original_hash") != nil || nativeHashFieldV3(row, "source_original", "source_original_hash") != nil || err != nil || occurrence.Protocol != "native-automation-v1/occurrence" || occurrence.Scope != g.ownerScopeV3() || occurrence.ID.String() != row.text("id") || occurrence.GrantID.String() != row.text("grant_id") || occurrence.GrantVersion != uint64(row.positive("grant_version")) || occurrence.GrantVersion != grant.Version || occurrence.Family != grant.Terms.Family || string(occurrence.Family) != row.text("family") || occurrence.Key != key || occurrence.Key != row.text("occurrence_key") || occurrence.Source.Kind != row.text("source_kind") || occurrence.Source.ID != row.text("source_id") || occurrence.Source.OriginalHash != row.text("source_hash") || !occurrence.CreatedAt.Equal(row.instant("created_at")) {
			return businessInvalid("native stable original occurrence identity")
		}
		switch occurrence.Source.Kind {
		case "INCOME_EVENT":
			var source struct {
				Protocol      string                  `json:"protocol"`
				Event         nativeAutomationEventV3 `json:"event"`
				BankRecordHex string                  `json:"bank_record_hex"`
			}
			nativeOriginalV3(row, "source_original", &source)
			event := source.Event
			outbox := g.row("outbox", row.text("event_id"))
			raw, err := hex.DecodeString(source.BankRecordHex)
			var record bankcredit.Record
			if source.Protocol != "native-automation-v1/income-source" || err != nil || nativeCompactOriginalV3(raw, &record) != nil || record.Validate() != nil || nakedHash(raw) != occurrence.Source.OriginalHash || event.Protocol != "native-automation-v1/event" || event.Scope != occurrence.Scope || event.GrantID != grant.ID || event.GrantVersion != grant.Version || event.ID.String() != row.text("event_id") || event.Kind != "INCOME_IMPORTED" || event.AggregateID.String() != occurrence.Source.ID || record.Event.OriginID != occurrence.Source.ID || record.Event.Namespace != g.manifest.Scope.Namespace || record.Event.UserID != g.manifest.Scope.UserID || !record.Event.OccurredAt.Equal(occurrence.Source.OccurredAt) || !record.CreditedAt.Equal(occurrence.Source.AvailableAt) || occurrence.Source.OccurredAt.Before(grant.Terms.EffectiveFrom) || !occurrence.Source.OccurredAt.Before(grant.Terms.ExpiresAt) || outbox == nil || outbox.text("kind") != event.Kind || outbox.text("aggregate_id") != event.AggregateID.String() || !outbox.instant("created_at").Equal(event.CreatedAt) || event.PayloadHex != hex.EncodeToString(nativeRawV3(outbox, "payload")) {
				return businessInvalid("native exact independently confirmed income occurrence source")
			}
			origin := g.row("income_origins", record.Event.OriginID)
			if origin == nil || origin.text("evidence_hash") != occurrence.Source.OriginalHash || origin.text("evidence_id") != record.Event.EvidenceID || origin.text("original_account_id") != record.Event.AccountID || origin.money("amount_cents") != record.Event.Amount {
				return businessInvalid("native occurrence income not same immutable origin")
			}
		case "PRINCIPAL_RETURN":
			var source struct {
				Protocol        string                 `json:"protocol"`
				Request         bankv2.Request         `json:"request"`
				Receipt         nativeReceiptV3        `json:"receipt"`
				PrincipalReturn bankv2.PrincipalReturn `json:"principal_return"`
			}
			nativeOriginalV3(row, "source_original", &source)
			returned := g.row("native_principal_returns", occurrence.Source.ID)
			if source.Protocol != "native-standing-asset-v1/settled-maturity-source" || returned == nil || source.Request.Effect.Kind != bankv2.Mature || source.Request.Key != occurrence.Source.ID || source.Receipt.BankResult.Status != bankv2.Settled || bankv2.VerifyResult(source.Request, source.Receipt.BankResult) != nil || source.Receipt.BankResult.PrincipalReturn == nil || !exactJSON(*source.Receipt.BankResult.PrincipalReturn, source.PrincipalReturn) || !exactJSON(g.nativeReceipts[source.Request.OperationID], source.Receipt) || returned.text("original_hash") != occurrence.Source.OriginalHash || !bytes.Equal(nativeRawV3(returned, "original"), mustJSONV3(source.PrincipalReturn)) || !source.PrincipalReturn.OccurredAt.Equal(occurrence.Source.OccurredAt) || !occurrence.Source.OccurredAt.Equal(occurrence.Source.AvailableAt) || occurrence.Source.AvailableAt.After(occurrence.CreatedAt) {
				return businessInvalid("native actual MATURE receipt/arrived PR distinct from income")
			}
		case "PAYMENT_OCCURRENCE":
			var source struct {
				Protocol   string                      `json:"protocol"`
				Debt       planning.ForecastObligation `json:"debt"`
				Bank       bankv2.LiabilityState       `json:"bank"`
				CapturedAt time.Time                   `json:"captured_at"`
			}
			nativeOriginalV3(row, "source_original", &source)
			if source.Protocol != "native-automation-v1/payment-source" || source.Debt.ID != occurrence.Source.ID || source.Bank.Validate() != nil || source.Bank.Original.ID != occurrence.Source.ID || source.Bank.OriginalHash != occurrence.Source.OriginalHash || len(source.Debt.DueDate) < 7 || occurrence.Source.Period != source.Debt.DueDate[:7] {
				return businessInvalid("native registered actual payment occurrence liability")
			}
		default:
			return fmt.Errorf("%w: native occurrence source protocol", evidence.ErrUnsupportedVersion)
		}
		g.occurrences[occurrence.ID.String()] = occurrence
	}
	return g.checkedRowsV3()
}

func mustJSONV3(value any) []byte { raw, _ := json.Marshal(value); return raw }

func (g *nativeGraphV3) checkStandingBudgetV3() error {
	lifetime, months := map[string]*big.Int{}, map[string]*big.Int{}
	seenOps := map[string]bool{}
	for _, claim := range g.rows["native_grant_budget_claims"] {
		id := claim.text("occurrence_id")
		occurrence, ok := g.occurrences[id]
		grant, grantOK := g.nativeGrants[claim.text("grant_id")]
		if !ok || !grantOK || occurrence.GrantID != grant.ID || occurrence.GrantVersion != uint64(claim.positive("grant_version")) || nativeHashFieldV3(claim, "original", "original_hash") != nil || claim.money("amount_cents").IsZero() || claim.money("amount_cents").Cents() > grant.Terms.SingleCap.Cents() || claim.money("spent_cents").Cents() > claim.money("amount_cents").Cents() {
			return businessInvalid("native grant budget original/single cap")
		}
		zone, _ := policy.TemplateLocation(grant.Terms.Timezone)
		if claim.text("period") != claim.instant("created_at").In(zone).Format("2006-01") {
			return businessInvalid("native grant budget immutable natural month")
		}
		var bound, spent funds.Money
		allTerminal, count := true, 0
		for _, binding := range g.rows["native_occurrence_operations"] {
			if binding.text("occurrence_id") != id {
				continue
			}
			op := g.row("native_operations", binding.text("operation_id"))
			request, exists := g.nativeRequests[binding.text("operation_id")]
			if op == nil || !exists || seenOps[binding.text("operation_id")] || op.text("mode") != "STANDING" || op.text("grant_id") != grant.ID.String() || uint64(op.positive("grant_version")) != grant.Version || binding.text("bank_request_hash") != op.text("bank_request_hash") || binding.text("effect_hash") != op.text("effect_hash") || !binding.instant("created_at").Equal(op.instant("created_at")) {
				return businessInvalid("native budget bound exact STANDING original operation")
			}
			economic := request.Effect.Amount
			if op.text("command_kind") == "STANDING_BRIDGE_ALLOCATE" {
				var step *ledgerRow
				for _, s := range g.rows["native_standing_bridge_steps"] {
					if s.text("operation_id") == request.OperationID {
						step = s
					}
				}
				if step == nil {
					return businessInvalid("native staged budget step original")
				}
				if !step.boolean("initial_source") {
					economic = funds.Money{}
				}
			}
			if binding.money("economic_amount_cents") != economic {
				return businessInvalid("native economic budget must count each child initial source exactly once")
			}
			var err error
			bound, err = bound.Add(economic)
			if err != nil {
				return businessInvalid("native bound budget overflow")
			}
			if op.text("status") == "SETTLED" {
				spent, err = spent.Add(economic)
				if err != nil {
					return businessInvalid("native spent budget overflow")
				}
			}
			if nativeExpectedReservationStateV3(op.text("status")) == "RESERVED" {
				allTerminal = false
			}
			seenOps[binding.text("operation_id")] = true
			count++
		}
		bridge := g.row("native_standing_bridge_groups", id)
		if count == 0 || bound.Cents() > claim.money("amount_cents").Cents() || bridge == nil && bound != claim.money("amount_cents") {
			return businessInvalid("native budget complete operation bindings")
		}
		if bridge != nil && g.row("native_standing_bridge_closures", id) == nil {
			for _, step := range g.rows["native_standing_bridge_steps"] {
				if step.text("group_id") == id && g.row("native_operations", step.text("operation_id")) == nil {
					allTerminal = false
				}
			}
		}
		usage := claim.money("amount_cents")
		if claim.text("state") == "FINAL" {
			if !allTerminal || claim.optional("closed_at") == "" || spent != claim.money("spent_cents") {
				return businessInvalid("native final budget actual terminal economic effects")
			}
			usage = spent
		} else if claim.text("state") != "RESERVED" || !claim.money("spent_cents").IsZero() || claim.optional("closed_at") != "" {
			return businessInvalid("native UNKNOWN budget remains fully reserved")
		}
		key := grant.ID.String() + "/" + strconv.FormatUint(grant.Version, 10)
		addBusinessSum(lifetime, key, usage.Cents())
		addBusinessSum(months, key+"/"+claim.text("period"), usage.Cents())
	}
	for _, op := range g.rows["native_operations"] {
		if op.text("mode") == "STANDING" && !seenOps[op.text("id")] {
			return businessInvalid("native standing operation omitted occurrence/budget binding")
		}
	}
	for _, binding := range g.rows["native_occurrence_operations"] {
		if g.row("native_grant_budget_claims", binding.text("occurrence_id")) == nil {
			return businessInvalid("native orphan budget binding")
		}
	}
	for key, total := range lifetime {
		id := strings.Split(key, "/")[0]
		if total.Cmp(big.NewInt(g.nativeGrants[id].Terms.LifetimeCap.Cents())) > 0 {
			return businessInvalid("native aggregate lifetime budget cap")
		}
	}
	for key, total := range months {
		id := strings.Split(key, "/")[0]
		if total.Cmp(big.NewInt(g.nativeGrants[id].Terms.MonthlyCap.Cents())) > 0 {
			return businessInvalid("native aggregate natural month budget cap")
		}
	}
	return g.checkedRowsV3()
}
