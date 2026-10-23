package executionaudit

import (
	"bytes"
	"encoding/hex"
	"fmt"
	"time"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

type nativeReceiptV3 struct {
	Protocol    string        `json:"protocol"`
	ID          funds.ID      `json:"id"`
	OperationID funds.ID      `json:"operation_id"`
	GroupID     funds.ID      `json:"group_id"`
	JournalID   funds.ID      `json:"journal_id"`
	BankResult  bankv2.Result `json:"bank_result"`
	RecordedAt  time.Time     `json:"recorded_at"`
}

// Bank v2 signs canonical UTC instants. Retained USER previews may preserve
// their original timezone offsets; equality must use the exact bank contract
// digest, while preview/review hashes still bind the original preview bytes.
func nativeSameBankEffectV3(left, right bankv2.Effect) bool {
	l, le := left.Digest()
	r, re := right.Digest()
	return le == nil && re == nil && l == r
}

func nativeCommandKindV3(command string) bankv2.Kind {
	switch command {
	case "JOINT_ALLOCATE", "STANDING_ALLOCATE", "BRIDGE_ALLOCATE", "STANDING_BRIDGE_ALLOCATE":
		return bankv2.Allocate
	case "BRIDGE_TRANSFER", "STANDING_BRIDGE_TRANSFER":
		return bankv2.Transfer
	case "TRANSFER_GOAL":
		return bankv2.MoveGoal
	case "PAY_RECURRING":
		return bankv2.Payment
	case "PURCHASE_ASSET":
		return bankv2.Purchase
	case "REDEEM_ASSET":
		return bankv2.Redeem
	case "MATURE_ASSET":
		return bankv2.Mature
	}
	return ""
}

func nativeJournalKindV3(command string, kind bankv2.Kind) string {
	switch command {
	case "JOINT_ALLOCATE":
		return "ALLOCATION"
	case "STANDING_ALLOCATE":
		return "STANDING_ALLOCATION"
	case "BRIDGE_TRANSFER", "BRIDGE_ALLOCATE":
		return "BRIDGE_" + string(kind)
	case "STANDING_BRIDGE_TRANSFER", "STANDING_BRIDGE_ALLOCATE":
		return "STANDING_BRIDGE_" + string(kind)
	}
	return string(kind)
}

func (g *nativeGraphV3) checkNativeOperationsV3() error {
	g.nativeRequests = map[string]bankv2.Request{}
	g.nativeReceipts = map[string]nativeReceiptV3{}
	keys, permits := map[string]bool{}, map[string]bool{}
	for _, row := range g.rows["native_operations"] {
		id := row.id("id").String()
		var request bankv2.Request
		raw := nativeOriginalV3(row, "bank_request", &request)
		canonical, err := request.CanonicalBytes()
		rh, re := request.Digest()
		eh, ee := request.Effect.Digest()
		kind := nativeCommandKindV3(row.text("command_kind"))
		bridge := row.text("command_kind") == "BRIDGE_TRANSFER" || row.text("command_kind") == "BRIDGE_ALLOCATE" || row.text("command_kind") == "STANDING_BRIDGE_TRANSFER" || row.text("command_kind") == "STANDING_BRIDGE_ALLOCATE"
		if kind == "" {
			return fmt.Errorf("%w: native command family", evidence.ErrUnsupportedVersion)
		}
		var dependencies planning.DependencyVector
		row.object("dependencies", &dependencies)
		if err != nil || re != nil || ee != nil || !bytes.Equal(canonical, raw) || rh != row.text("bank_request_hash") || eh != row.text("effect_hash") || request.Effect.Kind != kind || request.Namespace != g.manifest.Scope.Namespace || request.Namespace != row.text("bank_namespace") || request.UserID != g.manifest.Scope.UserID || request.OperationID != id || request.Key != row.text("bank_key") || request.PermitID != row.id("permit_id").String() || !bridge && !request.RequestedAt.Equal(row.instant("created_at")) || bridge && request.RequestedAt.After(row.instant("created_at")) || row.instant("updated_at").Before(request.RequestedAt) || uint64(row.positive("reserved_funds_version")) > uint64(g.snapshot.Watermarks["funds_version"]) || nativeHashFieldV3(row, "request", "request_hash") != nil {
			return businessInvalid("native operation immutable full request/effect/scope")
		}
		if dependencies.FundsVersion < 1 || dependencies.SourceEvidenceVersion < 1 || dependencies.WriterGeneration < 1 || dependencies.ControlVersion < 1 || dependencies.FundsVersion >= uint64(row.positive("reserved_funds_version")) {
			return businessInvalid("native operation reservation dependency")
		}
		preview := g.row("native_previews", row.text("preview_id"))
		sharedBridge := bridge && row.text("mode") == "ONCE"
		if preview == nil || !sharedBridge && (preview.text("bank_request_hash") != rh || preview.text("effect_hash") != eh || !bytes.Equal(nativeRawV3(preview, "bank_request"), raw)) || sharedBridge && (preview.text("kind") != "BRIDGE_GROUP" || preview.text("id") != row.optional("group_id")) || !exactJSON(preview.values["dependencies"], row.values["dependencies"]) || nativeHashFieldV3(preview, "original", "original_hash") != nil {
			return businessInvalid("native operation frozen execution preview")
		}
		key := row.text("command_kind") + "\x00" + row.text("idempotency_key")
		if keys[key] || permits[request.PermitID] {
			return businessInvalid("native duplicate command key/permit")
		}
		keys[key], permits[request.PermitID] = true, true
		state := row.text("status")
		if nativeExpectedReservationStateV3(state) == "" {
			return businessInvalid("native operation state")
		}
		signature := row.optional("permit_signature")
		if signature != "" {
			b, e := hex.DecodeString(signature)
			if e != nil || len(b) != 32 {
				return businessInvalid("native signature encoding")
			}
		}
		permit := g.row("native_submission_permits", id)
		if state == "QUEUED" || state == "CANCELLED" {
			if signature != "" || permit != nil {
				return businessInvalid("native unsigned cancellation/queued permit")
			}
		} else if signature == "" || permit == nil {
			return businessInvalid("native signed state lacks retained T2")
		}
		if row.text("mode") == "ONCE" {
			if row.optional("grant_id") != "" || row.values["grant_version"] != nil || g.row("native_confirmations", row.text("confirmation_id")) == nil {
				return businessInvalid("native ONCE USER confirmation")
			}
		} else if row.text("mode") == "STANDING" {
			if row.optional("confirmation_id") != "" || g.row("native_grants", row.text("grant_id"), fmt.Sprint(row.positive("grant_version"))) == nil {
				return businessInvalid("native STANDING grant original")
			}
		} else {
			return businessInvalid("native permission mode")
		}
		if (state == "SETTLED") != (row.optional("receipt_id") != "") {
			return businessInvalid("native receipt terminal state")
		}
		if previous := row.optional("previous_operation_id"); previous != "" {
			p := g.row("native_operations", previous)
			if p == nil || p.optional("group_id") != row.optional("group_id") || p.positive("child_number")+1 != row.positive("child_number") {
				return businessInvalid("native original group predecessor")
			}
			if signature != "" && p.text("status") != "SETTLED" {
				return businessInvalid("native signed successor before predecessor settlement")
			}
		}
		row.positive("version")
		g.nativeRequests[id] = request
	}
	for _, row := range g.rows["native_bank_observations"] {
		request, ok := g.nativeRequests[row.text("operation_id")]
		var result bankv2.Result
		nativeOriginalV3(row, "original", &result)
		if !ok || nativeHashFieldV3(row, "original", "original_hash") != nil || bankv2.VerifyResult(request, result) != nil || string(result.Status) != row.text("status") || row.instant("observed_at").Before(request.RequestedAt) {
			return businessInvalid("native retained bank observation")
		}
	}
	for _, row := range g.rows["native_receipts"] {
		var receipt nativeReceiptV3
		nativeOriginalV3(row, "original", &receipt)
		id, operation := row.id("id").String(), row.id("operation_id").String()
		op := g.row("native_operations", operation)
		request, ok := g.nativeRequests[operation]
		if !ok || op == nil || nativeHashFieldV3(row, "original", "original_hash") != nil || receipt.Protocol != "native-receipt-v1" || receipt.ID.String() != id || receipt.OperationID.String() != operation || receipt.JournalID.String() != row.text("journal_id") || op.text("status") != "SETTLED" || op.optional("receipt_id") != id || receipt.BankResult.Status != bankv2.Settled || bankv2.VerifyResult(request, receipt.BankResult) != nil || receipt.BankResult.SettledAt == nil || receipt.RecordedAt.Before(*receipt.BankResult.SettledAt) || !receipt.RecordedAt.Equal(row.instant("created_at")) || receipt.RecordedAt.After(g.manifest.Source.CapturedAt) {
			return businessInvalid("native settled receipt exact original")
		}
		if group := op.optional("group_id"); group != "" && receipt.GroupID.String() != group {
			return businessInvalid("native receipt group")
		}
		journal := g.row("native_journals", receipt.JournalID.String())
		if journal == nil || journal.text("operation_id") != operation || journal.text("status") != "POSTED" || journal.text("kind") != nativeJournalKindV3(op.text("command_kind"), request.Effect.Kind) || !journal.instant("posted_at").Equal(receipt.RecordedAt) {
			return businessInvalid("native one posted journal per receipt")
		}
		legs := map[string]bankv2.Posting{}
		for i, leg := range receipt.BankResult.Postings {
			legs[fmt.Sprintf("bank:%d:%s", i+1, leg.ID)] = leg
		}
		for _, posting := range g.rows["native_postings"] {
			if posting.text("journal_id") != receipt.JournalID.String() {
				continue
			}
			leg, found := legs[posting.text("leg_ref")]
			if !found || posting.text("bank_posting_id") != leg.ID || posting.text("dimension") != leg.Dimension || posting.text("ledger_id") != leg.LedgerID || posting.text("direction") != leg.Direction || posting.text("currency") != "CNY" || posting.money("amount_cents") != leg.Amount || !posting.instant("occurred_at").Equal(leg.OccurredAt) {
				return businessInvalid("native exact complete bank posting legs")
			}
			delete(legs, posting.text("leg_ref"))
		}
		if len(legs) != 0 {
			return businessInvalid("native omitted bank posting leg")
		}
		if _, exists := g.nativeReceipts[operation]; exists {
			return businessInvalid("native duplicate operation receipt")
		}
		g.nativeReceipts[operation] = receipt
	}
	for _, op := range g.rows["native_operations"] {
		if (op.text("status") == "SETTLED") != (g.nativeReceipts[op.text("id")].Protocol != "") {
			return businessInvalid("native complete receipt inventory")
		}
	}
	for _, journal := range g.rows["native_journals"] {
		receipt := g.nativeReceipts[journal.text("operation_id")]
		if receipt.JournalID.String() != journal.text("id") {
			return businessInvalid("native orphan/duplicate journal")
		}
	}
	for _, posting := range g.rows["native_postings"] {
		if g.row("native_journals", posting.text("journal_id")) == nil {
			return businessInvalid("native orphan posting")
		}
	}
	return g.checkedRowsV3()
}
