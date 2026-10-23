package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativePriorityProtocol = "native-priority-analysis-v1"
const nativePriorityWorldLimit = 16

type NativePriorityDomain struct {
	GoalID              funds.ID      `json:"goal_id"`
	Priorities          []int         `json:"priorities,omitempty"`
	ContributionTargets []funds.Money `json:"contribution_targets_cents,omitempty"`
}
type NativePriorityRequest struct {
	PlanID  funds.ID               `json:"plan_id"`
	Mode    string                 `json:"mode"`
	Domains []NativePriorityDomain `json:"domains"`
}
type NativePriorityAnalysis struct {
	Protocol      string                        `json:"protocol"`
	ID            funds.ID                      `json:"id"`
	SessionID     funds.ID                      `json:"session_id"`
	Request       NativePriorityRequest         `json:"request"`
	PlanHash      string                        `json:"plan_hash"`
	Input         planning.FiniteQuestionInput  `json:"input"`
	Result        planning.FiniteQuestionResult `json:"result"`
	DomainScope   string                        `json:"domain_scope"`
	BankAuthority bool                          `json:"bank_authority"`
}
type NativePriorityAnswerRequest struct {
	AnalysisID         funds.ID `json:"analysis_id"`
	OriginalQuestionID funds.ID `json:"original_question_id"`
	QuestionHash       string   `json:"question_hash"`
	ChoiceKey          string   `json:"choice_key"`
}
type NativePriorityAnswer struct {
	ID            funds.ID                          `json:"id"`
	AnalysisID    funds.ID                          `json:"analysis_id"`
	SessionID     funds.ID                          `json:"session_id"`
	Request       NativePriorityAnswerRequest       `json:"request"`
	Preference    planning.AcceptedFinitePreference `json:"preference"`
	AnsweredAt    time.Time                         `json:"answered_at"`
	BankAuthority bool                              `json:"bank_authority"`
}

func priorityDependency(plan NativeJointPlan) string {
	raw, _ := encode(struct {
		Dependencies             planning.DependencyVector
		Source, Inventory, Input string
	}{plan.Dependencies, plan.SourceHash, plan.InventoryHash, plan.InputHash})
	return Hash(raw)
}

func nativePriorityInput(plan NativeJointPlan, request NativePriorityRequest, id funds.ID) (planning.FiniteQuestionInput, error) {
	input, err := plan.Input.DomainInput()
	if err != nil {
		return planning.FiniteQuestionInput{}, err
	}
	if request.Mode != "DECLARED_FINITE_CHOICES" || request.PlanID != plan.ID || len(request.Domains) > 8 {
		return planning.FiniteQuestionInput{}, reject("INVALID_QUESTION_DOMAIN", nil)
	}
	refs := []planning.FiniteObjectRef{}
	add := func(kind string, id funds.ID, version uint64, value any) {
		raw, _ := encode(value)
		refs = append(refs, planning.FiniteObjectRef{Scope: plan.Scope, Kind: kind, ID: id, Version: version, Hash: Hash(raw), Current: true, Verified: true})
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
	out := planning.FiniteQuestionInput{Scope: plan.Scope, Now: plan.CreatedAt, ExpiresAt: plan.ExpiresAt, DependencyHash: priorityDependency(plan), ObjectRefs: refs, Complete: true, Variables: []planning.FiniteVariable{}}
	seen := map[funds.ID]bool{}
	worlds := 1
	for _, domain := range request.Domains {
		count := len(domain.Priorities) + len(domain.ContributionTargets)
		if seen[domain.GoalID] || count < 2 || count > 8 || (len(domain.Priorities) > 0 && len(domain.ContributionTargets) > 0) {
			return out, reject("INVALID_QUESTION_DOMAIN", nil)
		}
		seen[domain.GoalID] = true
		var source planning.FiniteObjectRef
		for _, ref := range refs {
			if ref.Kind == "GOAL" && ref.ID == domain.GoalID {
				source = ref
			}
		}
		if source.ID == (funds.ID{}) {
			return out, reject("NOT_FOUND", nil)
		}
		question, err := newID()
		if err != nil {
			return out, err
		}
		variable := planning.FiniteVariable{ID: "priority." + domain.GoalID.String(), OriginalQuestionID: question, Field: "GOAL_PRIORITY", Prompt: "在这次声明的比较范围内，这个目标选择哪一档优先级？", SourceRef: source, Complete: true, Choices: []planning.FiniteChoice{}}
		choices := map[int]bool{}
		for _, value := range domain.Priorities {
			if value < 0 || value > 100 || choices[value] {
				return out, reject("INVALID_QUESTION_DOMAIN", nil)
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
				return out, reject("INVALID_QUESTION_DOMAIN", nil)
			}
			amounts := map[int64]bool{}
			for _, value := range domain.ContributionTargets {
				if value.IsZero() || amounts[value.Cents()] || value.Cents() < config.Monthly.Min.Cents() || value.Cents() > config.Monthly.Max.Cents() {
					return out, reject("INVALID_QUESTION_DOMAIN", nil)
				}
				amounts[value.Cents()] = true
				amount := value
				variable.Choices = append(variable.Choices, planning.FiniteChoice{Key: fmt.Sprintf("target-%d", value.Cents()), Label: fmt.Sprintf("本月期望 %s 分", value.String()), Kind: "money", Amount: &amount})
			}
		}
		out.Variables = append(out.Variables, variable)
		worlds *= count
	}
	if worlds > nativePriorityWorldLimit {
		out.Complete = false
	}
	return out, nil
}

// These are explicit IF_USER_CHOOSES hypotheses, never replacements for the
// actual confirmed policy. All copies required by the pure protection proof
// change together; the original frozen plan and USER policy bytes stay intact.
func nativePriorityWorld(plan NativeJointPlan, selections []planning.FiniteSelection, now time.Time) (planning.FiniteOutcome, error) {
	in, err := plan.Input.DomainInput()
	if err != nil {
		return planning.FiniteOutcome{}, err
	}
	if now.Before(in.Now) || !now.Before(plan.ExpiresAt) {
		return planning.FiniteOutcome{}, reject("ORIGINAL_QUESTION_STALE", nil)
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
			return planning.FiniteOutcome{}, reject("INVALID_QUESTION_DOMAIN", nil)
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
					return planning.FiniteOutcome{}, reject("JOINT_PLAN_INVALID", nil)
				}
				if selection.Choice.Priority != nil {
					config["importance"], _ = encode(*selection.Choice.Priority)
				} else {
					var monthly map[string]json.RawMessage
					if json.Unmarshal(config["monthly_contribution"], &monthly) != nil {
						return planning.FiniteOutcome{}, reject("JOINT_PLAN_INVALID", nil)
					}
					monthly["target_cents"], _ = encode(*selection.Choice.Amount)
					config["monthly_contribution"], _ = encode(monthly)
				}
				raw, _ := encode(config)
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
			raw, _ := encode(fact)
			goal := child.GoalID
			economic := planning.EconomicIncomeUse{GoalID: &goal, FragmentID: use.FragmentID, OriginID: use.OriginID, AccountID: use.AccountID, Amount: use.Amount, SourceOccurredAt: fact.OccurredAt, EligibilityStart: start, SourceHash: Hash(raw)}
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

func evaluateNativePriority(ctx context.Context, plan NativeJointPlan, input planning.FiniteQuestionInput) (planning.FiniteQuestionResult, error) {
	result, err := planning.EvaluateFiniteQuestions(input, func(selections []planning.FiniteSelection) (planning.FiniteOutcome, error) {
		if err := ctx.Err(); err != nil {
			return planning.FiniteOutcome{}, err
		}
		return nativePriorityWorld(plan, selections, input.Now)
	})
	if !input.Complete {
		result.Reasons = append(result.Reasons, "APPLICATION_WORLD_LIMIT_16")
	}
	return result, err
}

func loadNativePriority(ctx context.Context, q queryRow, p Principal, id funds.ID) (NativePriorityAnalysis, error) {
	var result NativePriorityAnalysis
	var raw []byte
	var digest string
	if err := q.QueryRow(ctx, `SELECT original,original_hash FROM native_priority_analyses WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &digest); err != nil {
		return result, reject("NOT_FOUND", err)
	}
	if Hash(raw) != digest || decode(raw, &result) != nil || result.ID != id || result.Protocol != nativePriorityProtocol || result.Input.Scope.EnvironmentID != p.EnvironmentID || result.Input.Scope.UserID != p.UserID || result.BankAuthority || result.Result.BankAuthority {
		return result, reject("EVIDENCE_INVALID", nil)
	}
	plan, err := loadNativeJointPlan(ctx, q, p, result.Request.PlanID)
	if err != nil {
		return result, err
	}
	if err = ReplayNativePriorityAnalysis(ctx, plan, result); err != nil {
		return result, err
	}
	return result, nil
}

// Historical reads replay at the actually captured question time. Current
// dependency/session/expiry checks are a separate prerequisite for answering.
func ReplayNativePriorityAnalysis(ctx context.Context, plan NativeJointPlan, analysis NativePriorityAnalysis) error {
	planHash, err := NativeJointReviewHash(plan)
	if err != nil || analysis.PlanHash != planHash || analysis.DomainScope != "ONLY_EXPLICIT_USER_DECLARED_AMOUNT_OR_PRIORITY_CHOICES; CONFIRMED_HARD_LIMITS_PRESERVED; COUNTERFACTUAL_NOT_POLICY_OR_FINANCIAL_CONFIRMATION" {
		return reject("EVIDENCE_INVALID", err)
	}
	if _, err = ReplayNativeJointPlan(plan); err != nil {
		return err
	}
	expected, err := nativePriorityInput(plan, analysis.Request, analysis.ID)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	if len(expected.Variables) != len(analysis.Input.Variables) {
		return reject("EVIDENCE_INVALID", nil)
	}
	for i := range expected.Variables {
		if analysis.Input.Variables[i].OriginalQuestionID == (funds.ID{}) {
			return reject("EVIDENCE_INVALID", nil)
		}
		expected.Variables[i].OriginalQuestionID = analysis.Input.Variables[i].OriginalQuestionID
	}
	expected.Now = analysis.Input.Now
	if expected.Now.Before(plan.CreatedAt) || !expected.Now.Before(plan.ExpiresAt) {
		return reject("EVIDENCE_INVALID", nil)
	}
	a, err := encode(expected)
	if err != nil {
		return err
	}
	b, err := encode(analysis.Input)
	if err != nil {
		return err
	}
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	replayed, err := evaluateNativePriority(ctx, plan, expected)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	a, _ = encode(replayed)
	b, _ = encode(analysis.Result)
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	return nil
}

func (s *Service) AnalyzeNativePriorities(ctx context.Context, p Principal, key string, in NativePriorityRequest) (NativePriorityAnalysis, error) {
	var zero NativePriorityAnalysis
	if err := validateKey(key); err != nil {
		return zero, err
	}
	raw, err := encode(in)
	if err != nil {
		return zero, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return zero, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return zero, err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return zero, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return zero, err
	}
	var prior, priorHash string
	err = tx.QueryRow(ctx, `SELECT id::text,request_hash::text FROM native_priority_analyses WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&prior, &priorHash)
	if err == nil {
		if priorHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, e := funds.ParseID(prior)
		if e != nil {
			return zero, e
		}
		return loadNativePriority(ctx, tx, p, id)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	plan, err := loadNativeJointPlan(ctx, tx, p, in.PlanID)
	if err != nil {
		return zero, err
	}
	if !s.now().Before(plan.ExpiresAt) || fv != plan.Dependencies.FundsVersion || sv != plan.Dependencies.SourceEvidenceVersion || env.WriterGeneration != plan.Dependencies.WriterGeneration || env.ControlVersion != plan.Dependencies.ControlVersion {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, s.now(), plan.Input.ProtectionForecast.Timezone)
	if err != nil {
		return zero, err
	}
	if snapshot.Facts.SourceHash != plan.SourceHash {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	if _, err = ReplayNativeJointPlan(plan); err != nil {
		return zero, err
	}
	id, err := newID()
	if err != nil {
		return zero, err
	}
	input, err := nativePriorityInput(plan, in, id)
	if err != nil {
		return zero, err
	}
	input.Now = s.now()
	if !input.Now.Before(plan.ExpiresAt) {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	result, err := evaluateNativePriority(ctx, plan, input)
	if err != nil {
		return zero, reject("QUESTION_COMPUTATION_INCOMPLETE", err)
	}
	planHash, err := NativeJointReviewHash(plan)
	if err != nil {
		return zero, err
	}
	out := NativePriorityAnalysis{Protocol: nativePriorityProtocol, ID: id, SessionID: p.SessionID, Request: in, PlanHash: planHash, Input: input, Result: result, DomainScope: "ONLY_EXPLICIT_USER_DECLARED_AMOUNT_OR_PRIORITY_CHOICES; CONFIRMED_HARD_LIMITS_PRESERVED; COUNTERFACTUAL_NOT_POLICY_OR_FINANCIAL_CONFIRMATION"}
	original, err := encode(out)
	if err != nil {
		return zero, err
	}
	var question any
	if result.Question != nil {
		question = result.Question.OriginalQuestionID.String()
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_priority_analyses(environment_id,user_id,id,session_id,plan_id,idempotency_key,request,request_hash,original,original_hash,question_id,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`, env.ID.String(), p.UserID.String(), id.String(), p.SessionID.String(), in.PlanID.String(), key, raw, Hash(raw), original, Hash(original), question, input.Now, plan.ExpiresAt)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return out, nil
}

func (s *Service) NativePriorityAnalysis(ctx context.Context, p Principal, id funds.ID) (NativePriorityAnalysis, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativePriorityAnalysis{}, err
	}
	return loadNativePriority(ctx, s.Pool, p, id)
}

func (s *Service) AnswerNativePriority(ctx context.Context, p Principal, key string, in NativePriorityAnswerRequest) (NativePriorityAnswer, error) {
	var zero NativePriorityAnswer
	if err := validateKey(key); err != nil {
		return zero, err
	}
	requestRaw, err := encode(in)
	if err != nil {
		return zero, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return zero, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return zero, err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return zero, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return zero, err
	}
	var oldRaw []byte
	var oldHash, oldRequest string
	err = tx.QueryRow(ctx, `SELECT original,original_hash::text,request_hash::text FROM native_priority_answers WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&oldRaw, &oldHash, &oldRequest)
	if err == nil {
		if oldRequest != Hash(requestRaw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		if Hash(oldRaw) != oldHash || decode(oldRaw, &zero) != nil {
			return zero, reject("EVIDENCE_INVALID", nil)
		}
		return zero, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	analysis, err := loadNativePriority(ctx, tx, p, in.AnalysisID)
	if err != nil {
		return zero, err
	}
	if analysis.Result.Question == nil {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	q := *analysis.Result.Question
	if q.OriginalQuestionID != in.OriginalQuestionID || q.QuestionHash != in.QuestionHash {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	plan, err := loadNativeJointPlan(ctx, tx, p, analysis.Request.PlanID)
	if err != nil {
		return zero, err
	}
	planHash, _ := NativeJointReviewHash(plan)
	if planHash != analysis.PlanHash || fv != plan.Dependencies.FundsVersion || sv != plan.Dependencies.SourceEvidenceVersion || env.WriterGeneration != plan.Dependencies.WriterGeneration || env.ControlVersion != plan.Dependencies.ControlVersion {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, s.now(), plan.Input.ProtectionForecast.Timezone)
	if err != nil {
		return zero, err
	}
	if snapshot.Facts.SourceHash != plan.SourceHash {
		return zero, reject("ORIGINAL_QUESTION_STALE", nil)
	}
	var answered bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_priority_answers WHERE environment_id=$1 AND user_id=$2 AND question_id=$3)`, env.ID.String(), p.UserID.String(), q.OriginalQuestionID.String()).Scan(&answered); err != nil {
		return zero, err
	}
	id, err := newID()
	if err != nil {
		return zero, err
	}
	now := s.now()
	answer := planning.FiniteAnswer{AnswerID: id, OriginalQuestionID: q.OriginalQuestionID, QuestionHash: in.QuestionHash, ChoiceKey: in.ChoiceKey, Scope: plan.Scope, PrincipalKind: "USER", AnsweredAt: now, DependencyHash: analysis.Input.DependencyHash, ObjectRefsHash: q.ObjectRefsHash, Verified: true}
	preference, err := planning.AcceptFiniteAnswer(q, answer, planning.FiniteAnswerContext{Scope: plan.Scope, Now: now, DependencyHash: priorityDependency(plan), ObjectRefs: analysis.Input.ObjectRefs, QuestionCurrent: true, AlreadyAnswered: answered})
	if err != nil {
		return zero, reject("ORIGINAL_QUESTION_STALE", err)
	}
	out := NativePriorityAnswer{ID: id, AnalysisID: analysis.ID, SessionID: p.SessionID, Request: in, Preference: preference, AnsweredAt: now}
	original, err := encode(out)
	if err != nil {
		return zero, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_priority_answers(environment_id,user_id,id,session_id,analysis_id,question_id,idempotency_key,request,request_hash,original,original_hash,answered_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, env.ID.String(), p.UserID.String(), id.String(), p.SessionID.String(), analysis.ID.String(), q.OriginalQuestionID.String(), key, requestRaw, Hash(requestRaw), original, Hash(original), now)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return out, nil
}
