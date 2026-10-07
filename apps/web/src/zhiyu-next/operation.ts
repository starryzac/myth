import { isRunId } from '../api/decisions';
import { object } from '../features/policy-form';
import type { NextState } from './api';
import { templateNames } from './policy-schema';
import { safeConfiguration } from './policies';
import { isPaymentPath, paymentPath, paymentUsesKey, parsePaymentRecovery, type PaymentRecovery } from './payment-recovery';
import { isJointPath, jointUsesKey, parseJointRecovery, type JointRecovery } from './joint-recovery';
import { isAssetPath, assetUsesClientId, parseAssetRecovery, type AssetRecovery } from './asset-recovery';
import { isMaturityPath, maturityUsesKey, parseMaturityRecovery, type MaturityRecovery } from './maturity-recovery';
import { isPermissionPath, validatePermissionBody } from './asset-permissions';
import { isReinvestmentPrepare, parseReinvestmentRecovery, type ReinvestmentRecovery } from './reinvestment-recovery';
import { isLossPath, lossUsesClientId, parseLossRecovery, type LossRecovery } from './loss-recovery';
import { isQuestionPath, parseQuestionRecovery, type QuestionRecovery } from './question-recovery';
import { isStandingPath, parseStandingRecovery, type StandingRecovery } from './standing-recovery';
import { isGoalModelPath, validateGoalModelBody } from './goal-repairs';
import { isLossRevisionPath, revisionUsesClientId, parseLossRevisionRecovery, type LossRevisionRecovery } from './loss-revision-recovery';
const changePath = /^\/zhiyu-next\/policies\/(MVP_POLICY|FULL_POLICY)\/([0-9a-f-]{36})\/change-(review|confirm)$/;
export function isPolicyChangePath(path: string): boolean { const match = changePath.exec(path); return !!match && isRunId(match[2]); }

export type PendingOperation = { protocol: 'zhiyu-next-original-v1'; environment_id: string; epoch_id: string; client_request_id: string; path: string; body: Record<string, unknown>; body_json: string; payment_recovery?: PaymentRecovery; joint_recovery?: JointRecovery; asset_recovery?: AssetRecovery; maturity_recovery?: MaturityRecovery; reinvestment_recovery?: ReinvestmentRecovery; loss_recovery?: LossRecovery; question_recovery?: QuestionRecovery; standing_recovery?: StandingRecovery; loss_revision_recovery?: LossRevisionRecovery };
const prefix = 'zhiyu-next-original-v1:';
export const operationKey = (environment: string) => `${prefix}${environment}`;
export function parsePending(value: unknown): PendingOperation {
  if (!object(value) || !['body|body_json|client_request_id|environment_id|epoch_id|path|protocol', 'body|body_json|client_request_id|environment_id|epoch_id|path|payment_recovery|protocol', 'body|body_json|client_request_id|environment_id|epoch_id|joint_recovery|path|protocol', 'asset_recovery|body|body_json|client_request_id|environment_id|epoch_id|path|protocol', 'body|body_json|client_request_id|environment_id|epoch_id|maturity_recovery|path|protocol', 'body|body_json|client_request_id|environment_id|epoch_id|path|protocol|reinvestment_recovery', 'body|body_json|client_request_id|environment_id|epoch_id|loss_recovery|path|protocol', 'body|body_json|client_request_id|environment_id|epoch_id|path|protocol|question_recovery', 'body|body_json|client_request_id|environment_id|epoch_id|path|protocol|standing_recovery', 'body|body_json|client_request_id|environment_id|epoch_id|loss_revision_recovery|path|protocol'].includes(Object.keys(value).sort().join('|')) || value.protocol !== 'zhiyu-next-original-v1' || typeof value.environment_id !== 'string' || !value.environment_id || !isRunId(value.epoch_id) || !isRunId(value.client_request_id) || typeof value.path !== 'string' || !object(value.body) || value.body_json !== JSON.stringify(value.body) || (!isPaymentPath(value.path) && !isJointPath(value.path) && !isAssetPath(value.path) && !isMaturityPath(value.path) && !isLossPath(value.path) && !isLossRevisionPath(value.path) && (isPolicyChangePath(value.path) || isGoalModelPath(value.path) ? value.body.idempotency_key : value.body.client_request_id) !== value.client_request_id) || (assetUsesClientId(value.path) && value.body.client_request_id !== value.client_request_id) || ((paymentUsesKey(value.path) || jointUsesKey(value.path) || maturityUsesKey(value.path)) && value.body.idempotency_key !== value.client_request_id) || (!isPaymentPath(value.path) && value.payment_recovery !== undefined) || (!isJointPath(value.path) && value.joint_recovery !== undefined) || (!isAssetPath(value.path) && value.asset_recovery !== undefined) || (!isMaturityPath(value.path) && value.maturity_recovery !== undefined) || (!isReinvestmentPrepare(value.path) && !isAssetPath(value.path) && value.reinvestment_recovery !== undefined) || (!isLossPath(value.path) && value.loss_recovery !== undefined) || (revisionUsesClientId(value.path) && value.body.client_request_id !== value.client_request_id) || (!isLossRevisionPath(value.path) && value.loss_revision_recovery !== undefined) || (lossUsesClientId(value.path) && value.body.client_request_id !== value.client_request_id) || (!isQuestionPath(value.path) && value.question_recovery !== undefined) || (!isStandingPath(value.path) && value.standing_recovery !== undefined)) throw new Error('原请求记录无法核实，请保留记录并返回原环境。');
  const body = value.body;
  const exact = (keys: string[]) => Object.keys(body).sort().join('|') === keys.sort().join('|');
  const digest = (raw: unknown) => typeof raw === 'string' && /^[0-9a-f]{64}$/.test(raw);
  let allowed = false;
  if (value.path === '/zhiyu-next/authorizations/confirm') allowed = exact(['proposal_id', 'reviewed_hash', 'accepted', 'client_request_id', 'expected_epoch_id']) && isRunId(body.proposal_id) && digest(body.reviewed_hash) && body.accepted === true && body.expected_epoch_id === value.epoch_id;
  else if (value.path === '/zhiyu-next/autonomy/pause') allowed = exact(['client_request_id']);
  else if (value.path === '/zhiyu-next/autonomy/resume') allowed = exact(['authorization_id', 'accepted', 'client_request_id']) && isRunId(body.authorization_id) && body.accepted === true;
  else if (value.path === '/zhiyu-next/demo/income') allowed = exact(['expected_epoch_id', 'event', 'client_request_id']) && body.expected_epoch_id === value.epoch_id && ['PAYROLL_A', 'PAYROLL_B'].includes(String(body.event));
  else if (/^\/zhiyu-next\/actions\/[0-9a-f-]{36}\/confirm-and-execute$/.test(value.path)) allowed = exact(['accepted', 'effect_hash', 'client_request_id', 'expected_epoch_id']) && body.accepted === true && digest(body.effect_hash) && body.expected_epoch_id === value.epoch_id;
  else if (value.path === '/zhiyu-next/policy-candidates') allowed = exact(['template_name', 'dsl_version', 'configuration', 'goal_id', 'expected_version_id', 'goal_account_id', 'expected_epoch_id', 'client_request_id']) && templateNames.includes(body.template_name as typeof templateNames[number]) && body.dsl_version === 'FULL_V1' && body.expected_epoch_id === value.epoch_id && safeConfiguration(body.configuration) && (body.template_name === 'LongTermGoalPolicy' ? isRunId(body.goal_id) && isRunId(body.expected_version_id) && body.goal_account_id === null : body.goal_id === null && body.expected_version_id === null && body.goal_account_id === null);
  else if (value.path === '/zhiyu-next/policy-commands/confirm') allowed = exact(['candidate_id', 'reviewed_hash', 'accepted', 'expected_epoch_id', 'client_request_id']) && isRunId(body.candidate_id) && digest(body.reviewed_hash) && body.accepted === true && body.expected_epoch_id === value.epoch_id;
  else if (value.path === '/zhiyu-next/policy-discovery') allowed = exact(['expected_epoch_id', 'client_request_id']) && body.expected_epoch_id === value.epoch_id;
  else if (value.path === '/zhiyu-next/policy-discovery/confirm') allowed = exact(['proposal_id', 'reviewed_hash', 'accepted', 'expected_epoch_id', 'client_request_id']) && isRunId(body.proposal_id) && digest(body.reviewed_hash) && body.accepted === true && body.expected_epoch_id === value.epoch_id;
  else if (value.path === '/zhiyu-next/policy-commands/lifecycle') allowed = exact(['source_kind', 'policy_id', 'goal_id', 'expected_version_id', 'reviewed_hash', 'command', 'reason', 'accepted', 'expected_epoch_id', 'client_request_id']) && ['MVP_POLICY', 'FULL_POLICY', 'GOAL_BRIDGE'].includes(String(body.source_kind)) && isRunId(body.policy_id) && isRunId(body.expected_version_id) && digest(body.reviewed_hash) && ['SUSPEND', 'RESUME', 'REVOKE'].includes(String(body.command)) && typeof body.reason === 'string' && !!body.reason.trim() && body.reason.length <= 1000 && body.accepted === true && body.expected_epoch_id === value.epoch_id && (body.source_kind === 'GOAL_BRIDGE' ? isRunId(body.goal_id) : body.goal_id === null);
  else if (isPolicyChangePath(value.path)) {
    const core = ['expected_version_id', 'expected_epoch_id', 'configuration', 'idempotency_key'];
    allowed = isRunId(body.expected_version_id) && body.expected_epoch_id === value.epoch_id && safeConfiguration(body.configuration) && (value.path.endsWith('/change-review') ? exact(core) : exact([...core, 'review_id', 'accepted', 'reviewed_configuration_hash', 'reviewed_review_hash', 'reviewed_scope', 'reason']) && isRunId(body.review_id) && body.accepted === true && digest(body.reviewed_configuration_hash) && digest(body.reviewed_review_hash) && ['MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS', 'INDIVIDUAL_PRODUCT_CAPACITY', 'WHOLE_POSITION_RECOVERY_CANDIDATES', 'CURRENT_JOINT_GOAL_ALLOCATION'].includes(String(body.reviewed_scope)) && typeof body.reason === 'string' && !!body.reason.trim() && body.reason.length <= 1000);
  }
  else if (isPermissionPath(value.path)) { validatePermissionBody(body, value.path, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isGoalModelPath(value.path)) { validateGoalModelBody(body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isStandingPath(value.path)) { parseStandingRecovery(value.standing_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isQuestionPath(value.path)) { parseQuestionRecovery(value.question_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isMaturityPath(value.path)) { parseMaturityRecovery(value.maturity_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isLossRevisionPath(value.path)) { parseLossRevisionRecovery(value.loss_revision_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isLossPath(value.path)) { parseLossRecovery(value.loss_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isReinvestmentPrepare(value.path) || value.reinvestment_recovery !== undefined) { parseReinvestmentRecovery(value.reinvestment_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isAssetPath(value.path)) { parseAssetRecovery(value.asset_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isJointPath(value.path)) { parseJointRecovery(value.joint_recovery, value.path, body, value.epoch_id, value.client_request_id); allowed = true; }
  else if (isPaymentPath(value.path)) {
    const info = paymentPath(value.path)!; const recovery = parsePaymentRecovery(value.payment_recovery, value.path, value.epoch_id);
    const scope = recovery.scope;
    if (info.kind === 'OBSERVE') allowed = exact(['expected_epoch_id', 'original_policy_id', 'client_request_id']) && body.original_policy_id === scope.original_policy_id && body.client_request_id === value.client_request_id;
    else if (info.kind === 'START') allowed = exact(['expected_epoch_id', 'full_policy_id', 'expected_full_version_id', 'original_policy_id', 'expected_original_version_id', 'idempotency_key']) && body.full_policy_id === scope.full_policy_id && body.expected_full_version_id === scope.full_version_id && body.original_policy_id === scope.original_policy_id && body.expected_original_version_id === scope.original_version_id;
    else if (info.kind === 'CONFIRM') allowed = exact(['expected_epoch_id', 'reviewed_scope_hash', 'accepted', 'reason', 'idempotency_key']) && digest(body.reviewed_scope_hash) && body.accepted === true && typeof body.reason === 'string' && !!body.reason.trim() && body.reason.length <= 1000;
    else if (info.kind === 'PREPARE') allowed = exact(['expected_epoch_id', 'idempotency_key']);
    else if (info.kind === 'ACTION_CONFIRM') allowed = exact(['expected_epoch_id', 'reviewed_effect_hash', 'accepted']) && body.reviewed_effect_hash === recovery.action?.effect_hash && body.accepted === true;
    else allowed = exact(['expected_epoch_id']);
    allowed = allowed && body.expected_epoch_id === value.epoch_id;
  }
  if (!allowed) throw new Error('原请求内容无法核实，不会从浏览器记录添加金额或执行事实。');
  return value as PendingOperation;
}
export function makePending(environment: NextState, path: string, body: Record<string, unknown>, payment_recovery?: PaymentRecovery, joint_recovery?: JointRecovery, asset_recovery?: AssetRecovery, maturity_recovery?: MaturityRecovery, reinvestment_recovery?: ReinvestmentRecovery, loss_recovery?: LossRecovery, question_recovery?: QuestionRecovery, standing_recovery?: StandingRecovery, loss_revision_recovery?: LossRevisionRecovery): PendingOperation {
  const client_request_id = jointUsesKey(path) || maturityUsesKey(path) || isGoalModelPath(path) ? String(body.idempotency_key) : assetUsesClientId(path) || isReinvestmentPrepare(path) || lossUsesClientId(path) || revisionUsesClientId(path) || isQuestionPath(path) || isStandingPath(path) ? String(body.client_request_id) : crypto.randomUUID();
  const content = { ...(path === '/zhiyu-next/policy-candidates' ? { goal_id: null, expected_version_id: null, goal_account_id: null } : path === '/zhiyu-next/policy-commands/lifecycle' ? { goal_id: null } : {}), ...body, ...(isJointPath(path) || isAssetPath(path) || isMaturityPath(path) || isLossPath(path) || isLossRevisionPath(path) || isGoalModelPath(path) ? {} : isPolicyChangePath(path) || paymentUsesKey(path) ? { idempotency_key: client_request_id } : isPaymentPath(path) && paymentPath(path)?.kind !== 'OBSERVE' ? {} : { client_request_id }) };
  return parsePending({ protocol: 'zhiyu-next-original-v1', environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id, path, body: content, body_json: JSON.stringify(content), ...(payment_recovery ? { payment_recovery: structuredClone(payment_recovery) } : {}), ...(joint_recovery ? { joint_recovery: structuredClone(joint_recovery) } : {}), ...(asset_recovery ? { asset_recovery: structuredClone(asset_recovery) } : {}), ...(maturity_recovery ? { maturity_recovery: structuredClone(maturity_recovery) } : {}), ...(reinvestment_recovery ? { reinvestment_recovery: structuredClone(reinvestment_recovery) } : {}), ...(loss_recovery ? { loss_recovery: structuredClone(loss_recovery) } : {}), ...(question_recovery ? { question_recovery: structuredClone(question_recovery) } : {}), ...(standing_recovery ? { standing_recovery: structuredClone(standing_recovery) } : {}), ...(loss_revision_recovery ? { loss_revision_recovery: structuredClone(loss_revision_recovery) } : {}) });
}
export function retainPending(value: PendingOperation): void {
  parsePending(value);
  try { localStorage.setItem(operationKey(value.environment_id), JSON.stringify(value)); }
  catch { throw new Error('浏览器无法保留原请求，本次请求尚未发送。'); }
}
export function restorePending(environment: NextState): PendingOperation | null {
  let found: PendingOperation | null = null;
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index); if (!key?.startsWith(prefix)) continue;
    const raw = localStorage.getItem(key); if (raw === null) continue;
    const value = parsePending(JSON.parse(raw));
    if (key !== operationKey(value.environment_id) || value.environment_id !== environment.environment_id || value.epoch_id !== environment.epoch_id || found) throw new Error('发现另一个扩展环境的未决原请求，请返回原环境核对。');
    found = value;
  }
  return found;
}
/** Clear only after independently reading the operation terminal status. */
export function completePending(value: PendingOperation): void {
  const retained = localStorage.getItem(operationKey(value.environment_id));
  if (retained !== JSON.stringify(value)) throw new Error('原请求已变化，请先核对原状态。');
  localStorage.removeItem(operationKey(value.environment_id));
}
/** Transition only after an independent terminal read; never create an empty write-gate gap. */
export function replacePending(previous: PendingOperation, next: PendingOperation): void {
  parsePending(next);
  if (previous.environment_id !== next.environment_id || previous.epoch_id !== next.epoch_id || localStorage.getItem(operationKey(previous.environment_id)) !== JSON.stringify(previous)) throw new Error('原请求已变化，请保留原定位。');
  try { localStorage.setItem(operationKey(next.environment_id), JSON.stringify(next)); }
  catch { throw new Error('浏览器未能保留下一个固定步骤，原请求仍保留。'); }
}

