import { request } from '../api/http';
import { object } from '../features/policy-form';
import { spendingHash as hash } from '../api/spending-evidence';
import { jointCheck as check, jointCanonical } from '../api/full-joint-goal-execution';
import type { NextState, OperationResult } from './api';
import { context } from './policies';
import { jointPath, jointV4PreparePath, parseJointRecovery, type JointRecovery } from './joint-recovery';
import { getNextJoint, jointExecutionOpen, parseThinJointPlan, parseThinJointResponse, type ThinJointResponse } from './goals';
import { makePending, type PendingOperation } from './operation';
import { parseJointPrepare } from './goals';

export type JointUpdate = { phase: 'PREPARE' | 'CONFIRM' | 'EXECUTE'; execution: ThinJointResponse; recovery: JointRecovery; resume_original: boolean };
export async function validateJointOriginal(original: PendingOperation, environment: NextState) {
  const p = jointPath(original.path); check(p && original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id);
  const recovery = parseJointRecovery(original.joint_recovery, original.path, original.body, environment.epoch_id, original.client_request_id);
  check(recovery.user_id === environment.dashboard.user_id);
  if (recovery.reviewed_plan) await parseThinJointPlan(recovery.reviewed_plan, environment);
  return { info: p, recovery };
}
/** Every lookup binds the exact retained body/key; NOT_FOUND and HTTP errors are never terminal. */
export async function readJointOriginal(original: PendingOperation, environment: NextState): Promise<OperationResult & { joint?: JointUpdate }> {
  const { info, recovery } = await validateJointOriginal(original, environment);
  let execution: ThinJointResponse;
  if (info.kind === 'EXECUTE') execution = await getNextJoint(info.plan_id!, environment, recovery.reviewed_plan);
  else {
    const v = await request(`/zhiyu-next/goals/joint/by-key/${encodeURIComponent(original.client_request_id)}`, 'GET', undefined, (v) => { context(v, environment); return v; });
    check(v.user_id === recovery.user_id && v.idempotency_key === original.client_request_id && v.not_found_is_final === false && v.bank_authority === false && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(String(v.status)));
    if (v.status === 'NOT_FOUND_NOT_FINAL') { check(v.command_kind === null && v.original_request === null && v.original_request_hash === null && v.original === null); return { simulation: true, client_request_id: original.client_request_id, environment_id: environment.environment_id, epoch_id: environment.epoch_id, status: 'PENDING' }; }
    check(v.command_kind === info.kind && jointCanonical(v.original_request) === jointCanonical(original.body) && v.original_request_hash === await hash(original.body));
    execution = await parseThinJointResponse(v.original, environment, recovery.reviewed_plan);
    if (info.kind === 'PREPARE') check(jointCanonical(execution.original_plan.original_request) === jointCanonical(original.body) && (original.path !== jointV4PreparePath || execution.original_plan.protocol === 'registered-joint-goal-execution-source-dag-v4'));
    else check(execution.original_consent && jointCanonical(execution.original_consent.original_request) === jointCanonical(original.body) && execution.original_consent.request_hash === v.original_request_hash);
  }
  const plan = execution.original_plan;
  if (info.kind === 'EXECUTE') check(execution.original_consent?.original_request.idempotency_key === recovery.confirmation_key);
  const row = info.kind === 'EXECUTE' ? execution.children[Number(original.body.expected_child_number) - 1] : null;
  if (row) check(row.action_id === original.body.expected_action_id);
  const unresolved = ['UNRESOLVED', 'PARTIALLY_PREPARED'].includes(execution.state);
  const terminalChild = !!row && ['ORIGINAL_RECEIPT_VERIFIED', 'STOPPED'].includes(row.state);
  const completed = info.kind === 'EXECUTE' ? terminalChild && !unresolved : !unresolved;
  const resume_original = info.kind === 'EXECUTE' && !!row && ['AUTHORIZED', 'PLANNED_UNRESERVED'].includes(row.state) && execution.original_consent?.current_evidence_verified === true && Date.parse(execution.as_of) < Date.parse(plan.expires_at) && jointExecutionOpen(plan.current_execution_validation);
  return { simulation: true, client_request_id: original.client_request_id, environment_id: environment.environment_id, epoch_id: environment.epoch_id, status: completed ? 'COMPLETED' : 'PENDING', joint: { phase: info.kind, execution, recovery, resume_original } };
}
/** Only independently verified consent plus all earlier actual receipts permits the next fixed child. */
export function nextJointCommand(update: JointUpdate): { path: string; body: Record<string, unknown>; recovery: JointRecovery } | null {
  const e = update.execution, p = e.original_plan;
  if (update.phase === 'PREPARE' || !jointExecutionOpen(p.current_execution_validation) || ['UNRESOLVED', 'STOPPED', 'RETAINED_HISTORY', 'PARTIALLY_PREPARED'].includes(e.state) || !e.original_consent?.current_evidence_verified || e.original_consent.original_request.idempotency_key !== update.recovery.confirmation_key || Date.parse(e.as_of) >= Date.parse(p.expires_at)) return null;
  const index = e.children.findIndex((r) => r.state !== 'ORIGINAL_RECEIPT_VERIFIED'); if (index < 0) return null;
  const child = e.children[index]!, fixed = p.children[index]!;
  if (!['AUTHORIZED', 'PLANNED_UNRESERVED'].includes(child.state) || e.children.slice(0, index).some((r) => r.state !== 'ORIGINAL_RECEIPT_VERIFIED')) return null;
  return { path: `/zhiyu-next/goals/joint/${p.plan_id}/execute-child`, body: { accepted: true, reviewed_plan_hash: p.plan_hash, expected_epoch_id: p.epoch_id, expected_child_number: fixed.child_number, expected_action_id: fixed.action_id }, recovery: { ...update.recovery, reviewed_plan: p } };
}
export function sameJointUpdate(update: JointUpdate | null, original: PendingOperation): boolean { if (!update || !object(original.joint_recovery)) return false; return update.recovery.user_id === original.joint_recovery.user_id && update.execution.epoch_id === original.epoch_id && (jointPath(original.path)?.kind === 'PREPARE' ? update.execution.original_plan.original_request.idempotency_key === original.client_request_id : update.execution.original_plan.plan_id === original.joint_recovery.reviewed_plan?.plan_id); }

const viewKey = (environment: NextState) => `zhiyu-next-joint-read-locator-v1:${environment.environment_id}:${environment.epoch_id}`;
/** Read locator only, separate from the one pending write gate; it grants no consent or current authority. */
export function rememberJointView(update: JointUpdate, environment: NextState): void {
  const p = update.execution.original_plan; check(p.user_id === environment.dashboard.user_id && p.epoch_id === environment.epoch_id);
  localStorage.setItem(viewKey(environment), JSON.stringify({ protocol: 'zhiyu-next-joint-read-locator-v1', environment_id: environment.environment_id, epoch_id: p.epoch_id, user_id: p.user_id, prepare_request: p.original_request, ...(p.protocol === 'registered-joint-goal-execution-source-dag-v4' ? { prepare_path: jointV4PreparePath } : {}) }));
}
function restoreJointLocator(environment: NextState) {
  const raw = localStorage.getItem(viewKey(environment)); if (raw === null) return null;
  const v: unknown = JSON.parse(raw); check(object(v) && ['environment_id|epoch_id|prepare_request|protocol|user_id', 'environment_id|epoch_id|prepare_path|prepare_request|protocol|user_id'].includes(Object.keys(v).sort().join('|')) && (v.prepare_path === undefined || v.prepare_path === jointV4PreparePath) && v.protocol === 'zhiyu-next-joint-read-locator-v1' && v.environment_id === environment.environment_id && v.epoch_id === environment.epoch_id && v.user_id === environment.dashboard.user_id);
  const b = parseJointPrepare(v.prepare_request); check(b.expected_epoch_id === environment.epoch_id);
  return { body: b, path: v.prepare_path === jointV4PreparePath ? jointV4PreparePath : '/zhiyu-next/goals/joint/prepare' };
}
export function restoreJointView(environment: NextState) { return restoreJointLocator(environment)?.body ?? null; }
export async function readJointView(environment: NextState): Promise<JointUpdate | null> {
  const locator = restoreJointLocator(environment); if (!locator) return null;
  const { body, path } = locator;
  const read = await readJointOriginal(makePending(environment, path, body as unknown as Record<string, unknown>, undefined, { protocol: 'zhiyu-next-joint-recovery-v1', user_id: environment.dashboard.user_id, reviewed_plan: null, confirmation_key: null }), environment);
  return read.joint ?? null;
}
