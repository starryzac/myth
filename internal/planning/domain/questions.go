package domain

import (
	"errors"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const FiniteQuestionProtocol = "finite-economic-question-go-v1"
const MaxFiniteWorlds = 128

var ErrFiniteInput = errors.New("FINITE_INPUT_INVALID")
var ErrFiniteAnswer = errors.New("ORIGINAL_QUESTION_STALE_OR_INVALID")
var finiteKey = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,80}$`)

// FiniteObjectRef is built from a current scoped server read. These flags do
// not authenticate JSON from a model/browser; neither may supply this DTO.
type FiniteObjectRef struct {
	Scope    policy.TemplateOwnerScope `json:"scope"`
	Kind     string                    `json:"kind"`
	ID       funds.ID                  `json:"id"`
	Version  uint64                    `json:"version"`
	Hash     string                    `json:"hash"`
	Current  bool                      `json:"current"`
	Verified bool                      `json:"verified"`
}
type FiniteChoice struct {
	Key       string       `json:"key"`
	Label     string       `json:"label"`
	Kind      string       `json:"kind"`
	Amount    *funds.Money `json:"amount_cents,omitempty"`
	AccountID *funds.ID    `json:"account_id,omitempty"`
	Priority  *int         `json:"priority,omitempty"`
	Date      string       `json:"date,omitempty"`
	Intent    string       `json:"intent,omitempty"`
}
type FiniteVariable struct {
	ID                 string          `json:"id"`
	OriginalQuestionID funds.ID        `json:"original_question_id"`
	Field              string          `json:"field"`
	Prompt             string          `json:"prompt"`
	SourceRef          FiniteObjectRef `json:"source_ref"`
	Choices            []FiniteChoice  `json:"choices"`
	Complete           bool            `json:"complete"`
}
type FiniteSelection struct {
	VariableID string       `json:"variable_id"`
	Choice     FiniteChoice `json:"choice"`
}
type EconomicCashChange struct {
	AccountID funds.ID    `json:"account_id"`
	Direction string      `json:"direction"`
	Amount    funds.Money `json:"amount_cents"`
	At        time.Time   `json:"at"`
}
type EconomicPrincipalChange struct {
	PositionID  *funds.ID   `json:"position_id,omitempty"`
	ProductID   funds.ID    `json:"product_id"`
	TermsHash   string      `json:"terms_hash"`
	GoalID      *funds.ID   `json:"goal_id,omitempty"`
	Direction   string      `json:"direction"`
	Amount      funds.Money `json:"amount_cents"`
	AvailableAt time.Time   `json:"available_at"`
}
type EconomicGoalChange struct {
	GoalID    funds.ID    `json:"goal_id"`
	AccountID funds.ID    `json:"account_id"`
	Direction string      `json:"direction"`
	Amount    funds.Money `json:"amount_cents"`
	At        time.Time   `json:"at"`
}
type EconomicIncomeUse struct {
	GoalID           *funds.ID   `json:"goal_id,omitempty"`
	FragmentID       funds.ID    `json:"fragment_id"`
	OriginID         funds.ID    `json:"origin_id"`
	AccountID        funds.ID    `json:"account_id"`
	Amount           funds.Money `json:"amount_cents"`
	SourceOccurredAt time.Time   `json:"source_occurred_at"`
	EligibilityStart time.Time   `json:"eligibility_start"`
	SourceHash       string      `json:"source_hash"`
}
type EconomicOrderedChild struct {
	Number               int                 `json:"number"`
	GoalID               funds.ID            `json:"goal_id"`
	SourceAccountID      funds.ID            `json:"source_account_id"`
	DestinationAccountID funds.ID            `json:"destination_account_id"`
	Amount               funds.Money         `json:"amount_cents"`
	IncomeUses           []EconomicIncomeUse `json:"income_uses"`
	SettlementAt         time.Time           `json:"settlement_at"`
}

// EconomicEffect includes existing identities and every economic dimension;
// operation/key/quote IDs are intentionally absent. A new purchased position
// uses nil PositionID; an existing redeemed position must keep its identity.
type EconomicEffect struct {
	Scope           policy.TemplateOwnerScope `json:"scope"`
	Complete        bool                      `json:"complete"`
	Kind            string                    `json:"kind"`
	Currency        funds.Currency            `json:"currency"`
	Amount          funds.Money               `json:"amount_cents"`
	Cash            []EconomicCashChange      `json:"cash"`
	Principal       []EconomicPrincipalChange `json:"principal"`
	Ownership       []EconomicGoalChange      `json:"ownership"`
	IncomeUses      []EconomicIncomeUse       `json:"income_uses"`
	Fee             funds.Money               `json:"fee_cents"`
	Loss            funds.Money               `json:"loss_cents"`
	SettlementAt    time.Time                 `json:"settlement_at"`
	PayeeID         string                    `json:"payee_id"`
	ObligationID    string                    `json:"obligation_id"`
	PolicyRefs      []FiniteObjectRef         `json:"policy_refs"`
	OrderedChildren []EconomicOrderedChild    `json:"ordered_children"`
}
type FiniteOutcome struct {
	Status  string
	Effect  *EconomicEffect
	Reasons []string
}
type FiniteEvaluator func([]FiniteSelection) (FiniteOutcome, error)
type FiniteQuestionInput struct {
	Scope          policy.TemplateOwnerScope
	Now            time.Time
	ExpiresAt      time.Time
	DependencyHash string
	ObjectRefs     []FiniteObjectRef
	Variables      []FiniteVariable
	Complete       bool
}
type FiniteWorld struct {
	ID         string            `json:"id"`
	Selections []FiniteSelection `json:"selections"`
	Status     string            `json:"status"`
	Signature  string            `json:"signature,omitempty"`
	Reasons    []string          `json:"reasons"`
}
type FiniteQuestion struct {
	Protocol                string                    `json:"protocol"`
	OriginalQuestionID      funds.ID                  `json:"original_question_id"`
	VariableID              string                    `json:"variable_id"`
	Scope                   policy.TemplateOwnerScope `json:"scope"`
	Prompt                  string                    `json:"prompt"`
	SourceRef               FiniteObjectRef           `json:"source_ref"`
	Choices                 []FiniteChoice            `json:"choices"`
	AskedAt                 time.Time                 `json:"asked_at"`
	ExpiresAt               time.Time                 `json:"expires_at"`
	DependencyHash          string                    `json:"dependency_hash"`
	ObjectRefsHash          string                    `json:"object_refs_hash"`
	DomainHash              string                    `json:"domain_hash"`
	QuestionHash            string                    `json:"question_hash"`
	WorstResidualSignatures int                       `json:"worst_residual_signatures"`
	BankAuthority           bool                      `json:"bank_authority"`
}
type FiniteQuestionResult struct {
	Protocol             string          `json:"protocol"`
	InputHash            string          `json:"input_hash"`
	Status               string          `json:"status"`
	CompleteWithinDomain bool            `json:"complete_within_domain"`
	ExpectedWorlds       int             `json:"expected_worlds"`
	EvaluatedWorlds      int             `json:"evaluated_worlds"`
	Stable               *bool           `json:"stable"`
	ShouldAsk            *bool           `json:"should_ask"`
	Signatures           []string        `json:"signatures"`
	Worlds               []FiniteWorld   `json:"worlds"`
	Question             *FiniteQuestion `json:"question,omitempty"`
	Reasons              []string        `json:"reasons"`
	BankAuthority        bool            `json:"bank_authority"`
}

func finiteBool(value bool) *bool { return &value }
func cloneFiniteChoice(choice FiniteChoice) FiniteChoice {
	if choice.Amount != nil {
		value := *choice.Amount
		choice.Amount = &value
	}
	if choice.AccountID != nil {
		value := *choice.AccountID
		choice.AccountID = &value
	}
	if choice.Priority != nil {
		value := *choice.Priority
		choice.Priority = &value
	}
	return choice
}
func cloneFiniteSelections(selections []FiniteSelection) []FiniteSelection {
	copy := append([]FiniteSelection(nil), selections...)
	for i := range copy {
		copy[i].Choice = cloneFiniteChoice(copy[i].Choice)
	}
	return copy
}
func finiteContains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}
func normalizedFiniteRefs(refs []FiniteObjectRef, scope policy.TemplateOwnerScope) ([]FiniteObjectRef, error) {
	if len(refs) > 512 {
		return nil, ErrFiniteInput
	}
	out := append([]FiniteObjectRef(nil), refs...)
	seen := map[string]bool{}
	for _, ref := range out {
		key := ref.Kind + ":" + ref.ID.String()
		if !finiteContains([]string{"ACCOUNT", "GOAL", "POLICY", "INCOME_ORIGIN", "PRODUCT", "PRODUCT_TERMS", "POSITION", "LIABILITY", "QUESTION", "USER_REQUEST"}, ref.Kind) || ref.Scope != scope || ref.ID == (funds.ID{}) || ref.Version == 0 || !validHash(ref.Hash) || !ref.Current || !ref.Verified || seen[key] {
			return nil, ErrFiniteInput
		}
		seen[key] = true
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Kind != out[j].Kind {
			return out[i].Kind < out[j].Kind
		}
		return out[i].ID.String() < out[j].ID.String()
	})
	return out, nil
}
func finiteRefPresent(ref FiniteObjectRef, refs []FiniteObjectRef) bool {
	for _, r := range refs {
		if r == ref {
			return true
		}
	}
	return false
}
func finiteIDPresent(kind string, id funds.ID, refs []FiniteObjectRef) bool {
	for _, r := range refs {
		if r.Kind == kind && r.ID == id {
			return true
		}
	}
	return false
}
func finiteIDHashPresent(kind string, id funds.ID, contentHash string, refs []FiniteObjectRef) bool {
	for _, r := range refs {
		if r.Kind == kind && r.ID == id && r.Hash == contentHash {
			return true
		}
	}
	return false
}
func validateFiniteChoice(c FiniteChoice, field string, refs []FiniteObjectRef) error {
	if !finiteKey.MatchString(c.Key) || strings.TrimSpace(c.Label) == "" || !utf8.ValidString(c.Label) || utf8.RuneCountInString(c.Label) > 120 {
		return ErrFiniteInput
	}
	fields := 0
	if c.Amount != nil {
		fields++
	}
	if c.AccountID != nil {
		fields++
	}
	if c.Priority != nil {
		fields++
	}
	if c.Date != "" {
		fields++
	}
	if c.Intent != "" {
		fields++
	}
	if fields != 1 {
		return ErrFiniteInput
	}
	switch field {
	case "AMOUNT_PREFERENCE":
		if c.Kind != "money" || c.Amount == nil || c.Amount.IsZero() {
			return ErrFiniteInput
		}
	case "SOURCE_ACCOUNT", "DESTINATION_ACCOUNT":
		if c.Kind != "account" || c.AccountID == nil || !finiteIDPresent("ACCOUNT", *c.AccountID, refs) {
			return ErrFiniteInput
		}
	case "GOAL_PRIORITY":
		if c.Kind != "priority" || c.Priority == nil || *c.Priority < 0 || *c.Priority > 100 {
			return ErrFiniteInput
		}
	case "DEADLINE_PREFERENCE":
		if c.Kind != "date" {
			return ErrFiniteInput
		}
		if _, e := policy.TemplateDate(c.Date); e != nil {
			return ErrFiniteInput
		}
	case "INTENT_PREFERENCE":
		if c.Kind != "intent" || !finiteContains([]string{"TRANSFER_INTERNAL", "ALLOCATE_GOAL", "JOINT_ALLOCATION", "PURCHASE_ASSET", "REDEEM_ASSET", "PAYMENT", "PAIRED_GOAL_TRANSFER", "NO_ACTION"}, c.Intent) {
			return ErrFiniteInput
		}
	default:
		return ErrFiniteInput
	}
	return nil
}

// EconomicSignature compares full effects produced by a real deterministic
// engine. It is neither that engine's safety check nor financial authorization.
func EconomicSignature(effect EconomicEffect, scope policy.TemplateOwnerScope, now time.Time, refs []FiniteObjectRef) (string, error) {
	if !effect.Complete || effect.Scope != scope || effect.Currency != funds.CNY || !finiteContains([]string{"TRANSFER_INTERNAL", "ALLOCATE_GOAL", "JOINT_ALLOCATION", "PURCHASE_ASSET", "REDEEM_ASSET", "PAYMENT", "PAIRED_GOAL_TRANSFER", "NO_ACTION"}, effect.Kind) || len(effect.Cash) > 200 || len(effect.Principal) > 100 || len(effect.Ownership) > 200 || len(effect.IncomeUses) > 128 || len(effect.OrderedChildren) > 1024 || len(effect.PayeeID) > 160 || len(effect.ObligationID) > 160 || !utf8.ValidString(effect.PayeeID) || !utf8.ValidString(effect.ObligationID) {
		return "", ErrFiniteInput
	}
	if effect.Kind == "NO_ACTION" {
		if !effect.Amount.IsZero() || len(effect.Cash)+len(effect.Principal)+len(effect.Ownership)+len(effect.IncomeUses)+len(effect.OrderedChildren) > 0 || !effect.Fee.IsZero() || !effect.Loss.IsZero() {
			return "", ErrFiniteInput
		}
		effect.SettlementAt = time.Time{}
	} else if effect.Amount.IsZero() || effect.SettlementAt.Before(now) || effect.SettlementAt.IsZero() {
		return "", ErrFiniteInput
	}
	if effect.Kind == "PAYMENT" && (effect.PayeeID == "" || effect.ObligationID == "") {
		return "", ErrFiniteInput
	}
	refs, e := normalizedFiniteRefs(refs, scope)
	if e != nil {
		return "", e
	}
	policyRefs, e := normalizedFiniteRefs(effect.PolicyRefs, scope)
	if e != nil {
		return "", e
	}
	if effect.Kind != "NO_ACTION" && len(policyRefs) == 0 {
		return "", ErrFiniteInput
	}
	for _, r := range policyRefs {
		if r.Kind != "POLICY" || !finiteRefPresent(r, refs) {
			return "", ErrFiniteInput
		}
	}
	effect.PolicyRefs = policyRefs
	effect.SettlementAt = effect.SettlementAt.UTC()
	effect.Cash = append([]EconomicCashChange{}, effect.Cash...)
	effect.Ownership = append([]EconomicGoalChange{}, effect.Ownership...)
	effect.Principal = append([]EconomicPrincipalChange{}, effect.Principal...)
	effect.IncomeUses = append([]EconomicIncomeUse{}, effect.IncomeUses...)
	effect.OrderedChildren = append([]EconomicOrderedChild{}, effect.OrderedChildren...)
	for i, c := range effect.Cash {
		if c.Amount.IsZero() || !finiteContains([]string{"DEBIT", "CREDIT"}, c.Direction) || !finiteIDPresent("ACCOUNT", c.AccountID, refs) || c.At.IsZero() || c.At.Before(now) {
			return "", ErrFiniteInput
		}
		effect.Cash[i].At = c.At.UTC()
	}
	for i, g := range effect.Ownership {
		if g.Amount.IsZero() || !finiteContains([]string{"DEBIT", "CREDIT"}, g.Direction) || !finiteIDPresent("ACCOUNT", g.AccountID, refs) || !finiteIDPresent("GOAL", g.GoalID, refs) || g.At.IsZero() || g.At.Before(now) {
			return "", ErrFiniteInput
		}
		effect.Ownership[i].At = g.At.UTC()
	}
	for i, p := range effect.Principal {
		if p.Amount.IsZero() || !finiteContains([]string{"DEBIT", "CREDIT"}, p.Direction) || !finiteIDPresent("PRODUCT", p.ProductID, refs) || !finiteIDHashPresent("PRODUCT_TERMS", p.ProductID, p.TermsHash, refs) || p.AvailableAt.IsZero() || (p.GoalID != nil && !finiteIDPresent("GOAL", *p.GoalID, refs)) || (p.PositionID != nil && !finiteIDPresent("POSITION", *p.PositionID, refs)) || (p.PositionID == nil && effect.Kind != "PURCHASE_ASSET") {
			return "", ErrFiniteInput
		}
		effect.Principal[i].AvailableAt = p.AvailableAt.UTC()
	}
	for i, u := range effect.IncomeUses {
		if u.Amount.IsZero() || u.FragmentID != funds.LocationID(u.OriginID, u.AccountID) || !finiteIDHashPresent("INCOME_ORIGIN", u.OriginID, u.SourceHash, refs) || !finiteIDPresent("ACCOUNT", u.AccountID, refs) || u.SourceOccurredAt.IsZero() || u.EligibilityStart.IsZero() || u.SourceOccurredAt.After(now) || u.SourceOccurredAt.Before(u.EligibilityStart) || (u.GoalID != nil && !finiteIDPresent("GOAL", *u.GoalID, refs)) || ((effect.Kind == "ALLOCATE_GOAL" || effect.Kind == "JOINT_ALLOCATION") && u.GoalID == nil) {
			return "", ErrFiniteInput
		}
		effect.IncomeUses[i].SourceOccurredAt = u.SourceOccurredAt.UTC()
		effect.IncomeUses[i].EligibilityStart = u.EligibilityStart.UTC()
	}
	if effect.Kind == "JOINT_ALLOCATION" && len(effect.OrderedChildren) < 2 {
		return "", ErrFiniteInput
	}
	if effect.Kind != "JOINT_ALLOCATION" && len(effect.OrderedChildren) > 0 {
		return "", ErrFiniteInput
	}
	var childTotal funds.Money
	for i, child := range effect.OrderedChildren {
		if child.Number != i+1 || child.Amount.IsZero() || !finiteIDPresent("GOAL", child.GoalID, refs) || !finiteIDPresent("ACCOUNT", child.SourceAccountID, refs) || !finiteIDPresent("ACCOUNT", child.DestinationAccountID, refs) || child.SettlementAt.IsZero() || child.SettlementAt.Before(now) || len(child.IncomeUses) < 1 || len(child.IncomeUses) > 128 {
			return "", ErrFiniteInput
		}
		effect.OrderedChildren[i].SettlementAt = child.SettlementAt.UTC()
		effect.OrderedChildren[i].IncomeUses = append([]EconomicIncomeUse(nil), child.IncomeUses...)
		var used funds.Money
		for j, u := range child.IncomeUses {
			if u.GoalID == nil || *u.GoalID != child.GoalID || u.Amount.IsZero() || u.AccountID != child.SourceAccountID || u.FragmentID != funds.LocationID(u.OriginID, u.AccountID) || !finiteIDHashPresent("INCOME_ORIGIN", u.OriginID, u.SourceHash, refs) || u.SourceOccurredAt.IsZero() || u.EligibilityStart.IsZero() || u.SourceOccurredAt.After(now) || u.SourceOccurredAt.Before(u.EligibilityStart) {
				return "", ErrFiniteInput
			}
			effect.OrderedChildren[i].IncomeUses[j].SourceOccurredAt = u.SourceOccurredAt.UTC()
			effect.OrderedChildren[i].IncomeUses[j].EligibilityStart = u.EligibilityStart.UTC()
			used, e = used.Add(u.Amount)
			if e != nil {
				return "", ErrFiniteInput
			}
		}
		if used != child.Amount {
			return "", ErrFiniteInput
		}
		childTotal, e = childTotal.Add(child.Amount)
		if e != nil {
			return "", ErrFiniteInput
		}
		sort.Slice(effect.OrderedChildren[i].IncomeUses, func(a, b int) bool {
			return hash(effect.OrderedChildren[i].IncomeUses[a]) < hash(effect.OrderedChildren[i].IncomeUses[b])
		})
	}
	if effect.Kind == "JOINT_ALLOCATION" && childTotal != effect.Amount {
		return "", ErrFiniteInput
	}
	// Sort complete canonical rows. Native engine leg shapes remain explicit;
	// no fee, origin identity, liquidity date or ownership field is discarded.
	sort.Slice(effect.Cash, func(i, j int) bool { return hash(effect.Cash[i]) < hash(effect.Cash[j]) })
	sort.Slice(effect.Ownership, func(i, j int) bool { return hash(effect.Ownership[i]) < hash(effect.Ownership[j]) })
	sort.Slice(effect.Principal, func(i, j int) bool { return hash(effect.Principal[i]) < hash(effect.Principal[j]) })
	sort.Slice(effect.IncomeUses, func(i, j int) bool { return hash(effect.IncomeUses[i]) < hash(effect.IncomeUses[j]) })
	signature := hash(effect)
	if signature == "" {
		return "", ErrFiniteInput
	}
	return signature, nil
}

// EvaluateFiniteQuestions enumerates the complete declared small domain. The
// supplied callback must be the pure native planner, never an LLM or bank write.
func EvaluateFiniteQuestions(in FiniteQuestionInput, evaluate FiniteEvaluator) (FiniteQuestionResult, error) {
	r := FiniteQuestionResult{Protocol: FiniteQuestionProtocol, Status: "UNKNOWN", Signatures: []string{}, Worlds: []FiniteWorld{}, Reasons: []string{}}
	if in.Scope.EnvironmentID == (funds.ID{}) || in.Scope.UserID == (funds.ID{}) || in.Now.IsZero() || !in.ExpiresAt.After(in.Now) || !validHash(in.DependencyHash) || evaluate == nil {
		return r, ErrFiniteInput
	}
	refs, err := normalizedFiniteRefs(in.ObjectRefs, in.Scope)
	if err != nil {
		return r, err
	}
	in.ObjectRefs = refs
	in.Now = in.Now.UTC()
	in.ExpiresAt = in.ExpiresAt.UTC()
	if len(in.Variables) > 8 {
		r.Status = "CAPACITY_EXCEEDED"
		r.Reasons = append(r.Reasons, "VARIABLE_CAPACITY_EXCEEDED")
		return r, nil
	}
	in.Variables = append([]FiniteVariable(nil), in.Variables...)
	sort.Slice(in.Variables, func(i, j int) bool { return in.Variables[i].ID < in.Variables[j].ID })
	seenIDs := map[string]bool{}
	seenQuestions := map[funds.ID]bool{}
	seenFields := map[string]bool{}
	count := 1
	for i, variable := range in.Variables {
		fieldKey := variable.Field + ":" + variable.SourceRef.Kind + ":" + variable.SourceRef.ID.String()
		if !finiteKey.MatchString(variable.ID) || variable.OriginalQuestionID == (funds.ID{}) || seenIDs[variable.ID] || seenQuestions[variable.OriginalQuestionID] || !finiteRefPresent(variable.SourceRef, refs) || strings.TrimSpace(variable.Prompt) == "" || !utf8.ValidString(variable.Prompt) || utf8.RuneCountInString(variable.Prompt) > 1024 || len(variable.Choices) < 2 || len(variable.Choices) > 8 {
			return r, ErrFiniteInput
		}
		seenIDs[variable.ID] = true
		seenQuestions[variable.OriginalQuestionID] = true
		if seenFields[fieldKey] {
			return r, ErrFiniteInput
		}
		seenFields[fieldKey] = true
		choices := append([]FiniteChoice(nil), variable.Choices...)
		for index := range choices {
			choices[index] = cloneFiniteChoice(choices[index])
		}
		sort.Slice(choices, func(i, j int) bool { return choices[i].Key < choices[j].Key })
		keys := map[string]bool{}
		values := map[string]bool{}
		for _, choice := range choices {
			if e := validateFiniteChoice(choice, variable.Field, refs); e != nil {
				return r, e
			}
			value := choice
			value.Key = ""
			value.Label = ""
			signature := hash(value)
			if keys[choice.Key] || values[signature] {
				return r, ErrFiniteInput
			}
			keys[choice.Key] = true
			values[signature] = true
		}
		in.Variables[i].Choices = choices
		if !variable.Complete {
			r.Reasons = append(r.Reasons, "DECLARED_CANDIDATE_DOMAIN_INCOMPLETE")
			return r, nil
		}
		if count > MaxFiniteWorlds/len(choices) {
			r.Status = "CAPACITY_EXCEEDED"
			r.ExpectedWorlds = count * len(choices)
			r.Reasons = append(r.Reasons, "WORLD_CAPACITY_EXCEEDED_NO_STABILITY_CLAIM")
			return r, nil
		}
		count *= len(choices)
	}
	r.ExpectedWorlds = count
	r.InputHash = hash(in)
	if r.InputHash == "" {
		return r, ErrFiniteInput
	}
	if !in.Complete {
		r.Reasons = append(r.Reasons, "CURRENT_FACTS_INCOMPLETE")
		return r, nil
	}
	refsHash := hash(refs)
	domainHash := hash(in.Variables)
	selections := make([]FiniteSelection, len(in.Variables))
	unknown := false
	allBlocked := true
	signatures := map[string]bool{}
	var visit func(int)
	visit = func(index int) {
		if index < len(in.Variables) {
			v := in.Variables[index]
			for _, choice := range v.Choices {
				selections[index] = FiniteSelection{v.ID, choice}
				visit(index + 1)
			}
			return
		}
		selected := cloneFiniteSelections(selections)
		outcome, e := evaluate(cloneFiniteSelections(selected))
		world := FiniteWorld{ID: hash(selected), Selections: selected, Status: outcome.Status, Reasons: append([]string{}, outcome.Reasons...)}
		if e != nil || (outcome.Status != "KNOWN" && outcome.Status != "BLOCKED") {
			world.Status = "UNKNOWN"
			world.Reasons = []string{"NATIVE_ENGINE_UNKNOWN_OR_UNSUPPORTED"}
			unknown = true
		} else if outcome.Status == "BLOCKED" {
			if outcome.Effect != nil || len(outcome.Reasons) == 0 {
				world.Status = "UNKNOWN"
				unknown = true
			} else {
				reasons := append([]string(nil), outcome.Reasons...)
				sort.Strings(reasons)
				world.Signature = hash(struct {
					Status  string
					Reasons []string
				}{outcome.Status, reasons})
			}
		} else {
			allBlocked = false
			if outcome.Effect == nil {
				world.Status = "UNKNOWN"
				unknown = true
			} else {
				world.Signature, e = EconomicSignature(*outcome.Effect, in.Scope, in.Now, refs)
				if e != nil {
					world.Status = "UNKNOWN"
					world.Signature = ""
					world.Reasons = []string{"COMPLETE_CURRENT_ECONOMIC_EFFECT_REQUIRED"}
					unknown = true
				}
			}
		}
		if world.Signature != "" {
			signatures[world.Signature] = true
		}
		r.Worlds = append(r.Worlds, world)
	}
	visit(0)
	r.EvaluatedWorlds = len(r.Worlds)
	if unknown {
		r.Reasons = append(r.Reasons, "UNKNOWN_WORLD_PREVENTS_STABILITY_AND_QUESTION_CLAIMS")
		return r, nil
	}
	r.CompleteWithinDomain = true
	for signature := range signatures {
		r.Signatures = append(r.Signatures, signature)
	}
	sort.Strings(r.Signatures)
	if allBlocked {
		r.Status = "ALL_WORLDS_BLOCKED"
		r.Stable = finiteBool(false)
		r.ShouldAsk = finiteBool(false)
		return r, nil
	}
	if len(signatures) == 1 {
		r.Status = "STABLE"
		r.Stable = finiteBool(true)
		r.ShouldAsk = finiteBool(false)
		return r, nil
	}
	bestIndex := -1
	bestWorst := len(signatures)
	for index, v := range in.Variables {
		worst := 0
		for _, choice := range v.Choices {
			partition := map[string]bool{}
			for _, world := range r.Worlds {
				if world.Selections[index].Choice.Key == choice.Key {
					partition[world.Signature] = true
				}
			}
			if len(partition) > worst {
				worst = len(partition)
			}
		}
		if worst < bestWorst {
			bestWorst = worst
			bestIndex = index
		}
	}
	r.Stable = finiteBool(false)
	if bestIndex < 0 {
		r.Status = "DIVERGENT_NO_SINGLE_DISCRIMINATING_QUESTION"
		r.ShouldAsk = finiteBool(false)
		r.Reasons = append(r.Reasons, "NO_ORIGINAL_SINGLE_QUESTION_REDUCES_WORST_ECONOMIC_AMBIGUITY")
		return r, nil
	}
	v := in.Variables[bestIndex]
	q := FiniteQuestion{Protocol: FiniteQuestionProtocol, OriginalQuestionID: v.OriginalQuestionID, VariableID: v.ID, Scope: in.Scope, Prompt: v.Prompt, SourceRef: v.SourceRef, Choices: v.Choices, AskedAt: in.Now, ExpiresAt: in.ExpiresAt, DependencyHash: in.DependencyHash, ObjectRefsHash: refsHash, DomainHash: domainHash, WorstResidualSignatures: bestWorst}
	q.QuestionHash = hash(q)
	r.Question = &q
	r.Status = "QUESTION_REQUIRED"
	r.ShouldAsk = finiteBool(true)
	return r, nil
}

type FiniteAnswer struct {
	AnswerID           funds.ID
	OriginalQuestionID funds.ID
	QuestionHash       string
	ChoiceKey          string
	Scope              policy.TemplateOwnerScope
	PrincipalKind      string
	AnsweredAt         time.Time
	DependencyHash     string
	ObjectRefsHash     string
	Verified           bool
}
type FiniteAnswerContext struct {
	Scope           policy.TemplateOwnerScope
	Now             time.Time
	DependencyHash  string
	ObjectRefs      []FiniteObjectRef
	QuestionCurrent bool
	AlreadyAnswered bool
}
type AcceptedFinitePreference struct {
	AnswerID           funds.ID        `json:"answer_id"`
	OriginalQuestionID funds.ID        `json:"original_question_id"`
	VariableID         string          `json:"variable_id"`
	Choice             FiniteChoice    `json:"choice"`
	SourceRef          FiniteObjectRef `json:"source_ref"`
	DependencyHash     string          `json:"dependency_hash"`
	ReevaluateRequired bool            `json:"reevaluate_required"`
	BankAuthority      bool            `json:"bank_authority"`
}

// AcceptFiniteAnswer returns a preference, never financial confirmation. The
// application atomically records answer uniqueness/current-question ownership.
func AcceptFiniteAnswer(q FiniteQuestion, a FiniteAnswer, current FiniteAnswerContext) (AcceptedFinitePreference, error) {
	frozen := q
	frozen.QuestionHash = ""
	refs, err := normalizedFiniteRefs(current.ObjectRefs, current.Scope)
	if err != nil || q.Protocol != FiniteQuestionProtocol || !validHash(q.QuestionHash) || hash(frozen) != q.QuestionHash || q.BankAuthority || q.Scope != current.Scope || a.Scope != current.Scope || !current.QuestionCurrent || current.AlreadyAnswered || current.Now.IsZero() || !current.Now.Before(q.ExpiresAt) || current.Now.Before(q.AskedAt) || a.AnswerID == (funds.ID{}) || a.OriginalQuestionID != q.OriginalQuestionID || a.QuestionHash != q.QuestionHash || a.PrincipalKind != "USER" || !a.Verified || a.AnsweredAt.Before(q.AskedAt) || a.AnsweredAt.After(current.Now) || !a.AnsweredAt.Before(q.ExpiresAt) || a.DependencyHash != q.DependencyHash || current.DependencyHash != q.DependencyHash || a.ObjectRefsHash != q.ObjectRefsHash || hash(refs) != q.ObjectRefsHash || !finiteRefPresent(q.SourceRef, refs) {
		return AcceptedFinitePreference{}, ErrFiniteAnswer
	}
	for _, choice := range q.Choices {
		if choice.Key == a.ChoiceKey {
			return AcceptedFinitePreference{AnswerID: a.AnswerID, OriginalQuestionID: q.OriginalQuestionID, VariableID: q.VariableID, Choice: cloneFiniteChoice(choice), SourceRef: q.SourceRef, DependencyHash: q.DependencyHash, ReevaluateRequired: true}, nil
		}
	}
	return AcceptedFinitePreference{}, ErrFiniteAnswer
}
