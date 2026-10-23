package executionaudit

import (
	"bytes"
	"fmt"
	"time"
	evidence "zhiyu.local/zhiyu-go/internal/audit"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

func (g *nativeGraphV3) confirmationV3(request bankv2.Request, preview string, a bankv2.Authority, role string, session funds.ID, at, expires time.Time, childEffects []string) (*ledgerRow, error) {
	row := g.row("native_confirmations", a.SourceID)
	effect, _ := request.Effect.Digest()
	if row == nil || nativeHashFieldV3(row, "original", "original_hash") != nil || row.text("preview_id") != preview || row.text("session_id") != session.String() || row.text("role") != role || row.text("policy_id") != a.PolicyID || row.positive("policy_version") != a.PolicyVersion || row.text("effect_hash") != effect || !row.instant("created_at").Equal(at) || !row.instant("expires_at").Equal(expires) || at.After(request.RequestedAt) && request.Effect.Kind != bankv2.Transfer && request.Effect.Kind != bankv2.Allocate || !expires.Equal(request.ExpiresAt) {
		return row, businessInvalid("native actual USER confirmation row/role/exact effect")
	}
	var stored []string
	row.object("child_effect_hashes", &stored)
	if !exactJSON(stored, childEffects) {
		return row, businessInvalid("native full reviewed child effects")
	}
	if err := g.humanAtV3(session, at); err != nil {
		return row, err
	}
	g.usedNativeConfirmations[a.SourceID] = true
	return row, nil
}

func jointBankEffectV3(plan wireNativeJointPlan, child planning.JointChild) bankv2.Effect {
	uses := make([]bankv2.OriginUse, len(child.Uses))
	for i, use := range child.Uses {
		uses[i] = bankv2.OriginUse{OriginID: use.OriginID.String(), AccountID: use.AccountID.String(), Amount: use.Amount, Bucket: "AVAILABLE"}
	}
	return bankv2.Effect{Kind: bankv2.Allocate, BusinessKey: fmt.Sprintf("%s:%d", plan.ID.String(), child.Number), Amount: child.Amount, CashUses: []bankv2.CashUse{{AccountID: child.SourceAccountID.String(), Amount: child.Amount}}, OriginUses: uses, ReturnUses: []bankv2.ReturnUse{}, DestinationGoalID: child.GoalID.String(), LatestArrivalAt: plan.ExpiresAt}
}

func (g *nativeGraphV3) checkOnceV3() error {
	g.onceApprovals = map[string]any{}
	if g.usedNativeConfirmations == nil {
		g.usedNativeConfirmations = map[string]bool{}
	}
	for id, request := range g.nativeRequests {
		op := g.row("native_operations", id)
		if op.text("mode") != "ONCE" {
			continue
		}
		confirmation := g.row("native_confirmations", op.text("confirmation_id"))
		if confirmation == nil {
			return businessInvalid("native ONCE real USER original missing")
		}
		if len(request.Authorities) < 1 || request.Authorities[0].SourceID != confirmation.text("id") || request.Authorities[0].Role != "EXECUTE" {
			return businessInvalid("native ONCE execute authority source")
		}
		switch op.text("command_kind") {
		case "JOINT_ALLOCATE":
			plan, ok := g.jointPlans[op.text("plan_id")]
			number := int(op.positive("child_number"))
			if !ok || number < 1 || number > len(plan.Result.Children) || plan.Result.Status != "OPTIMAL" {
				return businessInvalid("native USER reviewed full optimal joint plan")
			}
			child := plan.Result.Children[number-1]
			expectedEffect := jointBankEffectV3(plan, child)
			effects := make([]string, len(plan.Result.Children))
			for i, c := range plan.Result.Children {
				effects[i], _ = jointBankEffectV3(plan, c).Digest()
			}
			var approval wirenativeJointApproval
			nativeOriginalV3(confirmation, "original", &approval)
			review := nakedHash(mustJSONV3(plan))
			authority := request.Authorities[0]
			var version uint64
			for _, ref := range plan.PolicyReferences {
				if ref.VersionID == child.VersionID && ref.PolicyID == child.PolicyID {
					version = ref.Version
				}
			}
			if approval.Protocol != "native-joint-user-confirmation-v1" || approval.PrincipalKind != "USER" || approval.EnvironmentID.String() != g.manifest.Scope.EnvironmentID || approval.UserID.String() != g.manifest.Scope.UserID || approval.PlanID != plan.ID || approval.ReviewedBundleHash != review || request.ReferenceHash != review || !exactJSON(approval.ChildEffectHashes, effects) || !exactJSON(request.Effect, expectedEffect) || len(request.Authorities) != 1 || authority.PolicyID != child.PolicyID.String() || authority.PolicyVersion != int64(version) || authority.Version != 1 || authority.GoalID != child.GoalID.String() || authority.AllowsFee || authority.AllowsLoss || !approval.ExpiresAt.Equal(plan.ExpiresAt) || approval.ConfirmedAt.Before(plan.CreatedAt) || !approval.ConfirmedAt.Before(plan.ExpiresAt) {
				return businessInvalid("native joint complete USER bundle/exact child authority")
			}
			if _, err := g.confirmationV3(request, op.text("preview_id"), authority, "EXECUTE", approval.SessionID, approval.ConfirmedAt, approval.ExpiresAt, effects); err != nil {
				return err
			}
			g.onceApprovals[id] = approval
		case "PURCHASE_ASSET", "REDEEM_ASSET", "MATURE_ASSET":
			preview, ok := g.nativeActions[op.text("preview_id")].(wireNativeAssetPreview)
			var approval wirenativeAssetApproval
			nativeOriginalV3(confirmation, "original", &approval)
			for _, check := range []struct {
				name  string
				valid bool
			}{
				{"identity", ok && approval.Protocol == "native-asset-action-v1/USER" && approval.PrincipalKind == "USER" && approval.ID.String() == confirmation.text("id") && approval.Scope == g.ownerScopeV3() && approval.PreviewID == preview.ID},
				{"configuration", approval.PolicyVersionID == preview.Policy.Version.VersionID && approval.ConfigurationHash == preview.Policy.Version.Compiled.Hash},
				{"command", approval.Command.PreviewID == preview.ID && approval.Command.ReviewedHash == preview.ReviewedHash && approval.CommandHash == nakedHash(mustJSONV3(approval.Command)) && bytes.Equal(mustJSONV3(approval.Command), nativeRawV3(op, "request"))},
				{"time", approval.ExpiresAt.Equal(preview.ExpiresAt) && !approval.ConfirmedAt.Before(preview.CreatedAt) && approval.ConfirmedAt.Before(preview.ExpiresAt)},
				{"effect", request.ReferenceHash == preview.ReviewedHash && nativeSameBankEffectV3(request.Effect, preview.BankEffect) && approval.EffectHash == preview.EffectHash},
			} {
				if !check.valid {
					return businessInvalid("native asset actual USER " + check.name)
				}
			}
			authorities, err := mirrorassetRequestAuthoritiesV3(preview, approval.ID)
			if err != nil || !exactJSON(request.Authorities, authorities) {
				return businessInvalid("native asset execute/position-original authority")
			}
			cost := !request.Effect.Fee.IsZero() || !request.Effect.Loss.IsZero()
			if cost != approval.Command.CostReviewed {
				return businessInvalid("native asset fee/loss requires independent USER cost consent")
			}
			if cost {
				c := request.CostConsent
				if c == nil || c.ConfirmationID != approval.ID.String() || c.PrincipalKind != "USER" || c.ReviewedEffectHash != preview.EffectHash || !c.ConfirmedAt.Equal(approval.ConfirmedAt) || !c.ExpiresAt.Equal(approval.ExpiresAt) {
					return businessInvalid("native asset exact USER fee/loss original")
				}
			} else if request.CostConsent != nil {
				return businessInvalid("native cost consent cannot be fabricated for zero cost")
			}
			if _, err := g.confirmationV3(request, preview.ID.String(), request.Authorities[0], "EXECUTE", approval.SessionID, approval.ConfirmedAt, approval.ExpiresAt, []string{preview.EffectHash}); err != nil {
				return err
			}
			g.onceApprovals[id] = approval
		case "PAY_RECURRING":
			preview, ok := g.nativeActions[op.text("preview_id")].(wireNativePaymentPreview)
			var approval wirenativePaymentApproval
			nativeOriginalV3(confirmation, "original", &approval)
			a := request.Authorities[0]
			if !ok || approval.Protocol != "native-payment-USER-v1" || approval.PrincipalKind != "USER" || approval.ID.String() != confirmation.text("id") || approval.Scope != g.ownerScopeV3() || approval.Command.PreviewID != preview.ID || approval.Command.ReviewedHash != preview.ReviewedHash || approval.CommandHash != nakedHash(mustJSONV3(approval.Command)) || approval.EffectHash != preview.EffectHash || request.ReferenceHash != preview.ReviewedHash || !exactJSON(request.Effect, preview.BankEffect) || len(request.Authorities) != 1 || a.PolicyID != preview.Policy.Version.PolicyID.String() || a.PolicyVersion != int64(preview.Policy.Version.Version) || a.Version != 1 || a.GoalID != "" || a.AllowsFee || a.AllowsLoss || approval.ConfirmedAt.Before(preview.CreatedAt) || !approval.ConfirmedAt.Before(preview.ExpiresAt) || !bytes.Equal(mustJSONV3(approval.Command), nativeRawV3(op, "request")) {
				return businessInvalid("native payment actual USER exact liability review")
			}
			if _, err := g.confirmationV3(request, preview.ID.String(), a, "EXECUTE", approval.SessionID, approval.ConfirmedAt, approval.ExpiresAt, []string{preview.EffectHash}); err != nil {
				return err
			}
			g.onceApprovals[id] = approval
		case "TRANSFER_GOAL":
			preview, ok := g.nativeActions[op.text("preview_id")].(wireNativeGoalTransferPreview)
			if !ok || len(request.Authorities) != 3 || request.ReferenceHash != preview.ReviewedHash || !exactJSON(request.Effect, preview.BankEffect) {
				return businessInvalid("paired transfer original full effect")
			}
			var execute wirenativeGoalTransferApproval
			for i, a := range request.Authorities {
				role := []string{"EXECUTE", "RELEASE_SOURCE", "RECEIVE_TARGET"}[i]
				bankRole := []string{"EXECUTE", "GOAL_RELEASE", "GOAL_RECEIVE"}[i]
				row := g.row("native_confirmations", a.SourceID)
				if row == nil {
					return businessInvalid("paired transfer distinct USER source missing")
				}
				var approval wirenativeGoalTransferApproval
				nativeOriginalV3(row, "original", &approval)
				if a.Role != bankRole || a.Version != 1 || approval.Protocol != "native-paired-goal-transfer-v1/USER" || approval.PrincipalKind != "USER" || approval.ID.String() != a.SourceID || approval.Role != role || approval.Scope != preview.Scope || approval.PreviewID != preview.ID || !approval.Command.ReleaseSource || !approval.Command.ReceiveTarget || approval.Command.PreviewID != preview.ID || approval.Command.ReviewedHash != preview.ReviewedHash || approval.CommandHash != nakedHash(mustJSONV3(approval.Command)) || approval.BankEffectHash != preview.EffectHash || approval.ConfirmedAt.Before(preview.CreatedAt) || !approval.ConfirmedAt.Before(preview.ExpiresAt) {
					return businessInvalid("paired transfer three distinct actual USER originals")
				}
				if _, err := g.confirmationV3(request, preview.ID.String(), a, role, approval.SessionID, approval.ConfirmedAt, approval.ExpiresAt, []string{preview.EffectHash}); err != nil {
					return err
				}
				if i == 0 {
					execute = approval
				} else {
					left, right := approval, execute
					left.ID, right.ID = funds.ID{}, funds.ID{}
					left.Role, right.Role = "", ""
					if !exactJSON(left, right) {
						return businessInvalid("paired independent USER consents disagree")
					}
				}
				wanted := []funds.ID{approval.ExecuteID, approval.Release.ID, approval.Receive.ID}[i]
				if wanted != approval.ID {
					return businessInvalid("paired USER role identity")
				}
			}
			if execute.ExecuteID == execute.Release.ID || execute.ExecuteID == execute.Receive.ID || execute.Release.ID == execute.Receive.ID || execute.ExecuteID == (funds.ID{}) {
				return businessInvalid("paired USER authorities must be independent IDs")
			}
			in := preview.PairedInput
			in.Now = execute.ConfirmedAt
			if effect, err := planning.CheckPairedGoalTransfer(in, execute.Release, execute.Receive); err != nil || !exactJSON(effect, preview.PairedEffect) {
				return businessInvalid("paired source release and target receive pure authorization")
			}
			expectedRelease, expectedReceive := mirrorgoalTransferUserConsentsV3(preview, execute.Release.ID, execute.Receive.ID, execute.ConfirmedAt)
			if !exactJSON(expectedRelease, execute.Release) || !exactJSON(expectedReceive, execute.Receive) || request.Authorities[0].PolicyID != preview.PairedInput.Policy.PolicyID.String() || request.Authorities[0].PolicyVersion != int64(preview.PairedInput.Policy.Version) || request.Authorities[1].PolicyID != preview.PairedInput.Source.PolicyID.String() || request.Authorities[1].PolicyVersion != int64(preview.PairedInput.Source.GoalVersion.Version) || request.Authorities[2].PolicyID != preview.PairedInput.Target.PolicyID.String() || request.Authorities[2].PolicyVersion != int64(preview.PairedInput.Target.GoalVersion.Version) || !bytes.Equal(mustJSONV3(execute.Command), nativeRawV3(op, "request")) {
				return businessInvalid("paired distinct policies and original command")
			}
			g.onceApprovals[id] = execute
		case "BRIDGE_TRANSFER", "BRIDGE_ALLOCATE":
			_, _, approval, err := g.onceBridgeOriginalsV3(op.text("group_id"))
			if err != nil {
				return err
			}
			g.onceApprovals[id] = approval
		default:
			return fmt.Errorf("%w: native ONCE financial family", evidence.ErrUnsupportedVersion)
		}
	}
	for _, row := range g.rows["native_confirmations"] {
		if !g.usedNativeConfirmations[row.text("id")] {
			return businessInvalid("native orphan USER confirmation outside complete original authority graph")
		}
	}
	return g.checkedRowsV3()
}
