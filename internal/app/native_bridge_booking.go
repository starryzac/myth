package app

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

func (s *Service) bridgeBookTransfer(ctx context.Context, c Claim, request bankv2.Request, result bankv2.Result) error {
	return s.bridgeBook(ctx, c, request, result, bankv2.Transfer)
}
func (s *Service) bridgeBookAllocate(ctx context.Context, c Claim, request bankv2.Request, result bankv2.Result) error {
	return s.bridgeBook(ctx, c, request, result, bankv2.Allocate)
}

func (s *Service) bridgeBook(ctx context.Context, c Claim, request bankv2.Request, result bankv2.Result, kind bankv2.Kind) error {
	if result.Status != bankv2.Settled || bankv2.VerifyResult(request, result) != nil || request.Effect.Kind != kind {
		return reject("EVIDENCE_INVALID", nil)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	if err = s.lockNativeClaim(ctx, tx, c); err != nil {
		return err
	}
	p := c.Principal
	var state, group, storedHash string
	var number int
	var signature *string
	if err = tx.QueryRow(ctx, `SELECT status,group_id::text,child_number,bank_request_hash,permit_signature FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), c.OperationID.String()).Scan(&state, &group, &number, &storedHash, &signature); err != nil {
		return err
	}
	if state == "SETTLED" {
		return nil
	}
	if state != "RECONCILING" || signature == nil {
		return reject("OPERATION_UNKNOWN", nil)
	}
	rh, err := request.Digest()
	if err != nil || rh != storedHash {
		return reject("EVIDENCE_INVALID", err)
	}
	if err = bridgeStoredPermit(ctx, tx, p, c.OperationID, request, *signature); err != nil {
		return err
	}
	groupID, err := funds.ParseID(group)
	if err != nil {
		return err
	}
	batch, err := s.bridgeBatch(ctx, tx, p, groupID)
	if err != nil {
		return err
	}
	if number < 1 || number > len(batch.Preview.Steps) || batch.Preview.Steps[number-1].RequestHash != rh {
		return reject("EVIDENCE_INVALID", nil)
	}
	ledger, err := s.Store.LoadNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, s.now())
	if err != nil {
		return err
	}
	ledger.Income, err = ledger.Income.Commit(c.OperationID, s.now())
	if err != nil {
		return err
	}
	if kind == bankv2.Transfer {
		source, err := funds.ParseID(request.Effect.CashUses[0].AccountID)
		if err != nil {
			return err
		}
		destination, err := funds.ParseID(request.Effect.DestinationAccountID)
		if err != nil {
			return err
		}
		sourceFound, destFound := false, false
		for i := range ledger.Accounts {
			a := &ledger.Accounts[i]
			if a.ID == source {
				a.Balance, err = a.Balance.Sub(request.Effect.Amount)
				sourceFound = true
			} else if a.ID == destination {
				a.Balance, err = a.Balance.Add(request.Effect.Amount)
				destFound = true
			}
			if err != nil {
				return err
			}
			if a.ID == source || a.ID == destination {
				a.ObservedAt = s.now()
			}
		}
		if !sourceFound || !destFound {
			return reject("EVIDENCE_INVALID", nil)
		}
	} else {
		if err = bridgeAssign(&ledger, request); err != nil {
			return err
		}
	}
	for i := range ledger.Holds {
		if ledger.Holds[i].OperationID == c.OperationID {
			ledger.Holds[i].State = funds.Committed
		}
	}
	journal, err := newID()
	if err != nil {
		return err
	}
	receiptID, err := newID()
	if err != nil {
		return err
	}
	now := s.now()
	_, err = tx.Exec(ctx, `INSERT INTO native_journals(environment_id,user_id,id,operation_id,kind,status,created_at) VALUES($1,$2,$3,$4,$5,'DRAFT',$6)`, p.EnvironmentID.String(), p.UserID.String(), journal.String(), c.OperationID.String(), "BRIDGE_"+string(kind), now)
	if err != nil {
		return err
	}
	for i, leg := range result.Postings {
		_, err = tx.Exec(ctx, `INSERT INTO native_postings(environment_id,user_id,journal_id,leg_ref,bank_posting_id,dimension,ledger_id,direction,amount_cents,occurred_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), journal.String(), fmt.Sprintf("bank:%d:%s", i+1, leg.ID), leg.ID, leg.Dimension, leg.LedgerID, leg.Direction, leg.Amount.Cents(), leg.OccurredAt)
		if err != nil {
			return err
		}
	}
	if _, err = tx.Exec(ctx, `UPDATE native_journals SET status='POSTED',posted_at=$4 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), journal.String(), now); err != nil {
		return err
	}
	receipt := NativeReceipt{"native-receipt-v1", receiptID, c.OperationID, groupID, journal, result, now}
	receiptRaw, err := encode(receipt)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_receipts(environment_id,user_id,id,operation_id,journal_id,original,original_hash,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, p.EnvironmentID.String(), p.UserID.String(), receiptID.String(), c.OperationID.String(), journal.String(), receiptRaw, Hash(receiptRaw), now)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE native_operations SET status='SETTLED',receipt_id=$4,processing_health='HEALTHY',version=version+1,updated_at=$5 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), c.OperationID.String(), receiptID.String(), now); err != nil {
		return err
	}
	if kind == bankv2.Allocate {
		zone, e := policy.TemplateLocation(batch.Plan.Input.ProtectionForecast.Timezone)
		if e != nil {
			return e
		}
		_, err = tx.Exec(ctx, `UPDATE goals SET month_contributed_cents=CASE WHEN month_period=$4 THEN month_contributed_cents+$5 ELSE $5 END,month_period=$4,version=version+1 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), request.Effect.DestinationGoalID, result.SettledAt.In(zone).Format("2006-01"), request.Effect.Amount.Cents())
		if err != nil {
			return err
		}
	}
	if err = s.activateNativeBridgeNext(ctx, tx, c, batch, number, &ledger); err != nil {
		return err
	}
	if err = s.Store.PersistNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, ledger); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()); err != nil {
		return err
	}
	if _, _, err = s.refreshTemplateProtectionAfterNativeEffect(ctx, tx, p, batch.Plan.Input.ProtectionForecast.Timezone); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO evidence_blobs(environment_id,user_id,id,kind,canonical,digest,created_at) VALUES($1,$2,$3,'NATIVE_BRIDGE_SETTLEMENT',$4,$5,$6)`, p.EnvironmentID.String(), p.UserID.String(), "native-bridge-settlement:"+c.OperationID.String(), receiptRaw, Hash(receiptRaw), now)
	if err != nil {
		return err
	}
	event, err := newID()
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO outbox(environment_id,user_id,id,aggregate_id,aggregate_version,kind,payload,created_at) SELECT $1,$2,$3,id,version,'NATIVE_OPERATION_SETTLED',$5,$6 FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$4`, p.EnvironmentID.String(), p.UserID.String(), event.String(), c.OperationID.String(), receiptRaw, now); err != nil {
		return err
	}
	if err = finishNativeJob(ctx, tx, c); err != nil {
		return err
	}
	if s.Failpoint != nil {
		if err = s.Failpoint("native_before_local_commit"); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func bridgeAssign(ledger *postgres.NativeLedger, request bankv2.Request) error {
	goal, err := funds.ParseID(request.Effect.DestinationGoalID)
	if err != nil {
		return err
	}
	account, err := funds.ParseID(request.Effect.CashUses[0].AccountID)
	if err != nil {
		return err
	}
	foundGoal, foundAccount := false, false
	for i := range ledger.Accounts {
		if ledger.Accounts[i].ID == account {
			ledger.Accounts[i].GoalOwned, err = ledger.Accounts[i].GoalOwned.Add(request.Effect.Amount)
			if err != nil {
				return err
			}
			foundAccount = true
		}
	}
	for i := range ledger.Goals {
		if ledger.Goals[i].GoalID == goal {
			if ledger.Goals[i].AccountID != account {
				return reject("EVIDENCE_INVALID", nil)
			}
			ledger.Goals[i].Cash, err = ledger.Goals[i].Cash.Add(request.Effect.Amount)
			if err != nil {
				return err
			}
			foundGoal = true
		}
	}
	if !foundGoal || !foundAccount {
		return reject("EVIDENCE_INVALID", nil)
	}
	for _, use := range request.Effect.OriginUses {
		found := false
		for i := range ledger.GoalLots {
			lot := &ledger.GoalLots[i]
			if lot.GoalID == goal && lot.AccountID == account && lot.SourceKind == "INCOME" && lot.SourceID == use.OriginID {
				lot.Available, err = lot.Available.Add(use.Amount)
				if err != nil {
					return err
				}
				found = true
				break
			}
		}
		if !found {
			ledger.GoalLots = append(ledger.GoalLots, postgres.NativeGoalLot{GoalID: goal, AccountID: account, SourceKind: "INCOME", SourceID: use.OriginID, Available: use.Amount})
		}
	}
	return nil
}

// The only creation path for a future allocation: its preceding transfer and
// receipt have already been sealed in this transaction, and Income.Commit has
// moved the ORIGINAL origin to the actual destination location. No synthetic
// future origin, post-T1 extra claim or key replacement is used.
func (s *Service) activateNativeBridgeNext(ctx context.Context, tx pgx.Tx, c Claim, batch nativeBridgeBatch, completed int, ledger *postgres.NativeLedger) error {
	if completed == len(batch.Preview.Steps) {
		return nil
	}
	p := c.Principal
	if err := s.bridgeContinuationAllowed(ctx, tx, p, batch, completed); err != nil {
		if Code(err) == "INTERNAL_ERROR" {
			return err
		}
		return s.bridgeCancelSuffix(ctx, tx, c, ledger, Code(err))
	}
	next := batch.Preview.Steps[completed]
	if !next.InitialSource {
		previous := batch.Preview.Steps[completed-1]
		if previous.Request.Effect.Kind != bankv2.Transfer || next.Request.Effect.Kind != bankv2.Allocate || previous.ChildNumber != next.ChildNumber || previous.Request.Effect.DestinationAccountID != next.Resources.CashAccountID.String() {
			return reject("EVIDENCE_INVALID", nil)
		}
		if err := s.bridgeInsertOperation(ctx, tx, p, batch.Preview, batch.Plan, next, batch.Key, batch.Command, batch.ReservedFV, s.now()); err != nil {
			return err
		}
		updated, err := bridgeReserve(*ledger, next, s.now())
		if err != nil {
			return err
		}
		*ledger = updated
	}
	return bridgeCreateJob(ctx, tx, p, next.OperationID)
}

func (s *Service) bridgeContinuationAllowed(ctx context.Context, tx pgx.Tx, p Principal, batch nativeBridgeBatch, completed int) error {
	now := s.now()
	if !now.Before(batch.Preview.ExpiresAt) {
		return reject("AUTHORIZATION_EXPIRED", nil)
	}
	human := p
	human.Kind = "USER"
	human.SessionID = batch.Approval.SessionID
	if err := s.human(ctx, tx, human); err != nil {
		return err
	}
	var fv, sv, wg, cv uint64
	if err := tx.QueryRow(ctx, `SELECT u.funds_version,u.source_version,e.writer_generation,e.control_version FROM users u JOIN environments e ON e.id=u.environment_id WHERE u.environment_id=$1 AND u.id=$2`, p.EnvironmentID.String(), p.UserID.String()).Scan(&fv, &sv, &wg, &cv); err != nil {
		return err
	}
	// The current T3 has not published its single FV/SV increment yet.
	if fv != batch.ReservedFV+uint64(completed-1) || sv != batch.SourceVersion+uint64(completed-1) || wg != batch.Writer || cv != batch.Control {
		return reject("SOURCE_CHANGED", nil)
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, batch.Plan.Input.ProtectionForecast.Timezone)
	if err != nil {
		return err
	}
	_, refs, err := nativeJointGoals(snapshot)
	if err != nil {
		return err
	}
	if len(refs) != len(batch.Plan.PolicyReferences) {
		return reject("SOURCE_CHANGED", nil)
	}
	allocated := map[funds.ID]uint64{}
	for i := 0; i < completed; i++ {
		step := batch.Preview.Steps[i]
		if step.Request.Effect.Kind == bankv2.Allocate {
			allocated[step.GoalID]++
		}
	}
	for i, ref := range refs {
		expected := batch.Plan.PolicyReferences[i]
		expected.GoalVersion += allocated[ref.GoalID]
		if ref != expected {
			return reject("SOURCE_CHANGED", nil)
		}
	}
	return nil
}
