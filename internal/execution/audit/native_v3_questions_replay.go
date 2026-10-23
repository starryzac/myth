// Frozen pure comparison consumer from internal/app/native_questions.go.
// Only question ID creation is replaced by the exact retained original IDs.
package executionaudit

import (
	"encoding/json"
	"fmt"
	"time"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativePriorityWorldLimitV3 = 16

func nativePriorityDependencyV3(plan wireNativeJointPlan) string {
	raw, _ := nativeMirrorEncodeV3(struct {
		Dependencies             planning.DependencyVector
		Source, Inventory, Input string
	}{plan.Dependencies, plan.SourceHash, plan.InventoryHash, plan.InputHash})
	return nakedHash(raw)
}

func nativePriorityInputV3(plan wireNativeJointPlan, request wireNativePriorityRequest, id funds.ID, questionIDs map[funds.ID]funds.ID) (planning.FiniteQuestionInput, error) {
	input, err := plan.Input.DomainInput()
	if err != nil {
		return planning.FiniteQuestionInput{}, err
	}
	if request.Mode != "DECLARED_FINITE_CHOICES" || request.PlanID != plan.ID || len(request.Domains) > 8 {
		return planning.FiniteQuestionInput{}, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
	}
	refs := []planning.FiniteObjectRef{}
	add := func(kind string, id funds.ID, version uint64, value any) {
		raw, _ := nativeMirrorEncodeV3(value)
		refs = append(refs, planning.FiniteObjectRef{Scope: plan.Scope, Kind: kind, ID: id, Version: version, Hash: nakedHash(raw), Current: true, Verified: true})
	}
	for _, account := range input.Cash {
		add("ACCOUNT", account.AccountID, plan.Dependencies.FundsVersion, account)
	}
	for _, goal := range input.Goals {
		var version uint64
		for _, ref := range plan.PolicyReferences {
			if ref.GoalID == goal.Goal.ID {
				version = ref.GoalVersion
			}
		}
		add("GOAL", goal.Goal.ID, version, goal.Goal)
	}
	for _, ref := range plan.PolicyReferences {
		refs = append(refs, planning.FiniteObjectRef{Scope: plan.Scope, Kind: "POLICY", ID: ref.PolicyID, Version: ref.Version, Hash: ref.ConfigurationHash, Current: true, Verified: true})
	}
	for _, fact := range plan.Input.Ledger.OriginFacts {
		add("INCOME_ORIGIN", fact.TransactionID, 1, fact)
	}
	add("USER_REQUEST", id, 1, request)
	out := planning.FiniteQuestionInput{Scope: plan.Scope, Now: plan.CreatedAt, ExpiresAt: plan.ExpiresAt, DependencyHash: nativePriorityDependencyV3(plan), ObjectRefs: refs, Complete: true, Variables: []planning.FiniteVariable{}}
	seen := map[funds.ID]bool{}
	worlds := 1
	for _, domain := range request.Domains {
		count := len(domain.Priorities) + len(domain.ContributionTargets)
		if seen[domain.GoalID] || count < 2 || count > 8 || (len(domain.Priorities) > 0 && len(domain.ContributionTargets) > 0) {
			return out, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
		}
		seen[domain.GoalID] = true
		var source planning.FiniteObjectRef
		for _, ref := range refs {
			if ref.Kind == "GOAL" && ref.ID == domain.GoalID {
				source = ref
			}
		}
		if source.ID == (funds.ID{}) {
			return out, nativeMirrorRejectV3("NOT_FOUND", nil)
		}
		question, ok := questionIDs[domain.GoalID]
		if !ok || question == (funds.ID{}) {
			return out, businessInvalid("native original question identity missing")
		}
		variable := planning.FiniteVariable{ID: "priority." + domain.GoalID.String(), OriginalQuestionID: question, Field: "GOAL_PRIORITY", Prompt: "在这次声明的比较范围内，这个目标选择哪一档优先级？", SourceRef: source, Complete: true, Choices: []planning.FiniteChoice{}}
		choices := map[int]bool{}
		for _, value := range domain.Priorities {
			if value < 0 || value > 100 || choices[value] {
				return out, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
			}
			choices[value] = true
			priority := value
			variable.Choices = append(variable.Choices, planning.FiniteChoice{Key: fmt.Sprintf("priority-%d", value), Label: fmt.Sprintf("优先级 %d", value), Kind: "priority", Priority: &priority})
		}
		if len(domain.ContributionTargets) > 0 {
			variable.ID = "target." + domain.GoalID.String()
			variable.Field = "AMOUNT_PREFERENCE"
			variable.Prompt = "在这次声明的比较范围内，这个目标的本月期望安排选择多少？"
			var config *policy.LongTermGoalConfig
			for _, goal := range input.Goals {
				if goal.Goal.ID == domain.GoalID {
					value, e := goal.Version.Compiled.Decode()
					if e != nil {
						return out, e
					}
					config = value.(*policy.LongTermGoalConfig)
				}
			}
			if config == nil {
				return out, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
			}
			amounts := map[int64]bool{}
			for _, value := range domain.ContributionTargets {
				if value.IsZero() || amounts[value.Cents()] || value.Cents() < config.Monthly.Min.Cents() || value.Cents() > config.Monthly.Max.Cents() {
					return out, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
				}
				amounts[value.Cents()] = true
				amount := value
				variable.Choices = append(variable.Choices, planning.FiniteChoice{Key: fmt.Sprintf("target-%d", value.Cents()), Label: fmt.Sprintf("本月期望 %s 分", value.String()), Kind: "money", Amount: &amount})
			}
		}
		out.Variables = append(out.Variables, variable)
		worlds *= count
	}
	if worlds > nativePriorityWorldLimitV3 {
		out.Complete = false
	}
	return out, nil
}

func nativePriorityWorldV3(plan wireNativeJointPlan, selections []planning.FiniteSelection, now time.Time) (planning.FiniteOutcome, error) {
	in, err := plan.Input.DomainInput()
	if err != nil {
		return planning.FiniteOutcome{}, err
	}
	if now.Before(in.Now) || !now.Before(plan.ExpiresAt) {
		return planning.FiniteOutcome{}, nativeMirrorRejectV3("ORIGINAL_QUESTION_STALE", nil)
	}
	in.Now = now
	ledger := in.Ledger.Snapshot()
	ledger.AsOf = now
	in.Ledger, err = funds.NewIncomeLedger(ledger)
	if err != nil {
		return planning.FiniteOutcome{}, err
	}
	in.ProtectionInput.AsOf = now
	for _, selection := range selections {
		var goalID funds.ID
		text := selection.VariableID
		prefix := "priority."
		if selection.Choice.Kind == "money" {
			prefix = "target."
		}
		if len(text) != len(prefix)+36 || text[:len(prefix)] != prefix || (selection.Choice.Priority == nil) == (selection.Choice.Amount == nil) {
			return planning.FiniteOutcome{}, nativeMirrorRejectV3("INVALID_QUESTION_DOMAIN", nil)
		}
		goalID, err = funds.ParseID(text[len(prefix):])
		if err != nil {
			return planning.FiniteOutcome{}, err
		}
		for i := range in.Goals {
			if in.Goals[i].Goal.ID == goalID {
				version := in.Goals[i].Version
				var config map[string]json.RawMessage
				if json.Unmarshal(version.Compiled.Configuration, &config) != nil {
					return planning.FiniteOutcome{}, nativeMirrorRejectV3("JOINT_PLAN_INVALID", nil)
				}
				if selection.Choice.Priority != nil {
					config["importance"], _ = nativeMirrorEncodeV3(*selection.Choice.Priority)
				} else {
					var monthly map[string]json.RawMessage
					if json.Unmarshal(config["monthly_contribution"], &monthly) != nil {
						return planning.FiniteOutcome{}, nativeMirrorRejectV3("JOINT_PLAN_INVALID", nil)
					}
					monthly["target_cents"], _ = nativeMirrorEncodeV3(*selection.Choice.Amount)
					config["monthly_contribution"], _ = nativeMirrorEncodeV3(monthly)
				}
				raw, _ := nativeMirrorEncodeV3(config)
				compiled, e := policy.CompileTemplate(policy.LongTermGoalTemplate, raw)
				if e != nil {
					return planning.FiniteOutcome{}, e
				}
				version.Compiled = compiled
				version.ConfirmedConfigurationHash = compiled.Hash
				in.Goals[i].Version = version
				copy := version
				in.Goals[i].Goal.GoalVersion = &copy
				for j := range in.ProtectionInput.Versions {
					if in.ProtectionInput.Versions[j].PolicyID == version.PolicyID {
						in.ProtectionInput.Versions[j] = version
					}
				}
				for j := range in.ProtectionInput.Facts.Goals {
					if in.ProtectionInput.Facts.Goals[j].ID == goalID {
						copy := version
						in.ProtectionInput.Facts.Goals[j].GoalVersion = &copy
					}
				}
				for j := range in.ProtectionInput.Facts.ProtectedReferences {
					if in.ProtectionInput.Facts.ProtectedReferences[j].PolicyID == version.PolicyID {
						in.ProtectionInput.Facts.ProtectedReferences[j].ConfigurationHash = compiled.Hash
					}
				}
			}
		}
	}
	result, err := planning.SolveJoint(in)
	if err != nil {
		return planning.FiniteOutcome{}, err
	}
	if result.Status == "UNKNOWN" {
		return planning.FiniteOutcome{Status: "UNKNOWN", Reasons: result.Reasons}, nil
	}
	if result.Status == "INFEASIBLE" {
		reasons := result.Reasons
		if len(reasons) == 0 {
			reasons = []string{"JOINT_INFEASIBLE"}
		}
		return planning.FiniteOutcome{Status: "BLOCKED", Reasons: reasons}, nil
	}
	effect := planning.EconomicEffect{Scope: plan.Scope, Complete: true, Kind: "JOINT_ALLOCATION", Currency: funds.CNY, SettlementAt: in.Now, PolicyRefs: []planning.FiniteObjectRef{}}
	for _, ref := range plan.PolicyReferences {
		effect.PolicyRefs = append(effect.PolicyRefs, planning.FiniteObjectRef{Scope: plan.Scope, Kind: "POLICY", ID: ref.PolicyID, Version: ref.Version, Hash: ref.ConfigurationHash, Current: true, Verified: true})
	}
	for _, child := range result.Children {
		ordered := planning.EconomicOrderedChild{Number: child.Number, GoalID: child.GoalID, SourceAccountID: child.SourceAccountID, DestinationAccountID: child.DestinationAccountID, Amount: child.Amount, SettlementAt: in.Now}
		for _, use := range child.Uses {
			var fact funds.BankCreditFact
			for _, f := range plan.Input.Ledger.OriginFacts {
				if f.TransactionID == use.OriginID {
					fact = f
				}
			}
			var version policy.TemplateVersion
			for _, g := range in.Goals {
				if g.Goal.ID == child.GoalID {
					version = g.Version
				}
			}
			start, _, e := policy.TemplateValidity(version.Compiled, version.ConfirmedAt, in.Timezone)
			if e != nil {
				return planning.FiniteOutcome{}, e
			}
			if start.Before(version.ConfirmedAt) {
				start = version.ConfirmedAt
			}
			raw, _ := nativeMirrorEncodeV3(fact)
			goal := child.GoalID
			economic := planning.EconomicIncomeUse{GoalID: &goal, FragmentID: use.FragmentID, OriginID: use.OriginID, AccountID: use.AccountID, Amount: use.Amount, SourceOccurredAt: fact.OccurredAt, EligibilityStart: start, SourceHash: nakedHash(raw)}
			ordered.IncomeUses = append(ordered.IncomeUses, economic)
			effect.IncomeUses = append(effect.IncomeUses, economic)
		}
		if child.SourceAccountID != child.DestinationAccountID {
			effect.Cash = append(effect.Cash, planning.EconomicCashChange{AccountID: child.SourceAccountID, Direction: "DEBIT", Amount: child.Amount, At: in.Now}, planning.EconomicCashChange{AccountID: child.DestinationAccountID, Direction: "CREDIT", Amount: child.Amount, At: in.Now})
		}
		effect.Ownership = append(effect.Ownership, planning.EconomicGoalChange{GoalID: child.GoalID, AccountID: child.DestinationAccountID, Direction: "CREDIT", Amount: child.Amount, At: in.Now})
		effect.OrderedChildren = append(effect.OrderedChildren, ordered)
		effect.Amount, err = effect.Amount.Add(child.Amount)
		if err != nil {
			return planning.FiniteOutcome{}, err
		}
	}
	if len(result.Children) < 2 {
		effect.OrderedChildren = nil
		effect.Kind = "ALLOCATE_GOAL"
	}
	if len(result.Children) == 0 {
		effect.Kind = "NO_ACTION"
		effect.PolicyRefs = nil
	}
	return planning.FiniteOutcome{Status: "KNOWN", Effect: &effect}, nil
}
