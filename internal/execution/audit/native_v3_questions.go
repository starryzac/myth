package executionaudit

import (
	"bytes"
	"strings"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

func nativeEvaluatePriorityV3(plan wireNativeJointPlan, input planning.FiniteQuestionInput) (planning.FiniteQuestionResult, error) {
	result, err := planning.EvaluateFiniteQuestions(input, func(selections []planning.FiniteSelection) (planning.FiniteOutcome, error) {
		return nativePriorityWorldV3(plan, selections, input.Now)
	})
	if !input.Complete {
		result.Reasons = append(result.Reasons, "APPLICATION_WORLD_LIMIT_16")
	}
	return result, err
}

// Questions describe a bounded counterfactual. Neither a model, a question nor
// its answer becomes a policy confirmation, a grant or a bank permission.
func (g *nativeGraphV3) checkQuestionsV3() error {
	analyses := map[string]wireNativePriorityAnalysis{}
	questions := map[funds.ID]bool{}
	keys := map[string]bool{}
	for _, row := range g.rows["native_priority_analyses"] {
		var a wireNativePriorityAnalysis
		nativeOriginalV3(row, "original", &a)
		var command wireNativePriorityRequest
		nativeOriginalV3(row, "request", &command)
		plan, ok := g.jointPlans[a.Request.PlanID.String()]
		if !ok || nativeHashFieldV3(row, "original", "original_hash") != nil || nativeHashFieldV3(row, "request", "request_hash") != nil || !exactJSON(command, a.Request) || a.ID.String() != row.text("id") || a.SessionID.String() != row.text("session_id") || a.Request.PlanID.String() != row.text("plan_id") || a.Protocol != "native-priority-analysis-v1" || a.Input.Scope != g.ownerScopeV3() || a.BankAuthority || a.Result.BankAuthority || !a.Input.Now.Equal(row.instant("created_at")) || !a.Input.ExpiresAt.Equal(row.instant("expires_at")) || a.Input.Now.Before(plan.CreatedAt) || !a.Input.Now.Before(plan.ExpiresAt) || a.DomainScope != "ONLY_EXPLICIT_USER_DECLARED_AMOUNT_OR_PRIORITY_CHOICES; CONFIRMED_HARD_LIMITS_PRESERVED; COUNTERFACTUAL_NOT_POLICY_OR_FINANCIAL_CONFIRMATION" {
			return businessInvalid("native finite comparison original full binding")
		}
		hash := nakedHash(mustJSONV3(plan))
		if a.PlanHash != hash {
			return businessInvalid("native finite comparison exact original joint plan")
		}
		ids := map[funds.ID]funds.ID{}
		for _, v := range a.Input.Variables {
			parts := strings.Split(v.ID, ".")
			if len(parts) != 2 {
				return businessInvalid("native finite question variable identity")
			}
			goal, e := funds.ParseID(parts[1])
			if e != nil || v.OriginalQuestionID == (funds.ID{}) || ids[goal] != (funds.ID{}) || questions[v.OriginalQuestionID] {
				return businessInvalid("native original question ID reused")
			}
			ids[goal] = v.OriginalQuestionID
			questions[v.OriginalQuestionID] = true
		}
		expected, err := nativePriorityInputV3(plan, a.Request, a.ID, ids)
		expected.Now = a.Input.Now
		if err != nil || !exactJSON(expected, a.Input) {
			return businessInvalid("native declared finite domain/source/dependency reconstruction")
		}
		result, err := nativeEvaluatePriorityV3(plan, expected)
		if err != nil || !exactJSON(result, a.Result) {
			return businessInvalid("native all economic worlds and necessary question deterministic replay")
		}
		questionID := ""
		if result.Question != nil {
			questionID = result.Question.OriginalQuestionID.String()
		}
		if row.optional("question_id") != questionID {
			return businessInvalid("native selected original question projection")
		}
		if err = g.humanAtV3(a.SessionID, a.Input.Now); err != nil {
			return err
		}
		key := row.text("idempotency_key")
		if keys[key] {
			return businessInvalid("native analysis command key reused")
		}
		keys[key] = true
		analyses[a.ID.String()] = a
	}
	answered := map[funds.ID]bool{}
	keys = map[string]bool{}
	for _, row := range g.rows["native_priority_answers"] {
		var answer wireNativePriorityAnswer
		nativeOriginalV3(row, "original", &answer)
		var command wireNativePriorityAnswerRequest
		nativeOriginalV3(row, "request", &command)
		analysis, ok := analyses[answer.AnalysisID.String()]
		if !ok || analysis.Result.Question == nil || nativeHashFieldV3(row, "original", "original_hash") != nil || nativeHashFieldV3(row, "request", "request_hash") != nil || !bytes.Equal(mustJSONV3(answer.Request), mustJSONV3(command)) || answer.ID.String() != row.text("id") || answer.SessionID.String() != row.text("session_id") || answer.AnalysisID.String() != row.text("analysis_id") || answer.Request.AnalysisID != answer.AnalysisID || !answer.AnsweredAt.Equal(row.instant("answered_at")) || answer.BankAuthority {
			return businessInvalid("native finite USER answer original binding")
		}
		q := *analysis.Result.Question
		plan := g.jointPlans[analysis.Request.PlanID.String()]
		if answer.Request.OriginalQuestionID != q.OriginalQuestionID || answer.Request.QuestionHash != q.QuestionHash || row.text("question_id") != q.OriginalQuestionID.String() || answered[q.OriginalQuestionID] {
			return businessInvalid("native answer must consume one exact original question")
		}
		input := planning.FiniteAnswer{AnswerID: answer.ID, OriginalQuestionID: q.OriginalQuestionID, QuestionHash: answer.Request.QuestionHash, ChoiceKey: answer.Request.ChoiceKey, Scope: plan.Scope, PrincipalKind: "USER", AnsweredAt: answer.AnsweredAt, DependencyHash: analysis.Input.DependencyHash, ObjectRefsHash: q.ObjectRefsHash, Verified: true}
		preference, err := planning.AcceptFiniteAnswer(q, input, planning.FiniteAnswerContext{Scope: plan.Scope, Now: answer.AnsweredAt, DependencyHash: nativePriorityDependencyV3(plan), ObjectRefs: analysis.Input.ObjectRefs, QuestionCurrent: true, AlreadyAnswered: false})
		if err != nil || !exactJSON(preference, answer.Preference) {
			return businessInvalid("native original answer requires reevaluation and grants no authority")
		}
		if err = g.humanAtV3(answer.SessionID, answer.AnsweredAt); err != nil {
			return err
		}
		key := row.text("idempotency_key")
		if keys[key] {
			return businessInvalid("native answer command key reused")
		}
		keys[key] = true
		answered[q.OriginalQuestionID] = true
	}
	return g.checkedRowsV3()
}
