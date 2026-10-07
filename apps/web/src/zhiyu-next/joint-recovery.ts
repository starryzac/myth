import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { jointCheck as check, parseJointPrepare, parseJointConfirm, parseJointExecute } from '../api/full-joint-goal-execution';
import type { ThinJointPlan } from './goals';

export type JointRecovery = { protocol: 'zhiyu-next-joint-recovery-v1'; user_id: string; reviewed_plan: ThinJointPlan | null; confirmation_key: string | null };
export const jointV4PreparePath = '/zhiyu-next/goals/joint/prepare?archive_protocol=V4';
export type JointPath = { kind: 'PREPARE' | 'CONFIRM' | 'EXECUTE'; plan_id: string | null };
export function jointPath(path: string): JointPath | null {
  if (path === '/zhiyu-next/goals/joint/prepare' || path === jointV4PreparePath) return { kind: 'PREPARE', plan_id: null };
  const m = /^\/zhiyu-next\/goals\/joint\/([0-9a-f-]{36})\/(confirm|execute-child)$/.exec(path);
  return m && uuid(m[1]) ? { kind: m[2] === 'confirm' ? 'CONFIRM' : 'EXECUTE', plan_id: m[1]! } : null;
}
export const isJointPath = (path: string) => jointPath(path) !== null;
export const jointUsesKey = (path: string) => { const p = jointPath(path); return !!p && p.kind !== 'EXECUTE'; };
/** Synchronous restore guard; async cryptographic child/effect checks run before every read or POST. */
export function parseJointRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): JointRecovery {
  const info = jointPath(path); check(info && object(value) && Object.keys(value).sort().join('|') === 'confirmation_key|protocol|reviewed_plan|user_id' && value.protocol === 'zhiyu-next-joint-recovery-v1' && uuid(value.user_id));
  if (info.kind === 'PREPARE') { const b = parseJointPrepare(body); check(b.expected_epoch_id === epoch && b.idempotency_key === client && value.reviewed_plan === null && value.confirmation_key === null); }
  else {
    const b = info.kind === 'CONFIRM' ? parseJointConfirm(body) : parseJointExecute(body); const p = value.reviewed_plan;
    check(object(p) && p.plan_id === info.plan_id && uuid(p.plan_id) && p.user_id === value.user_id && p.epoch_id === epoch && digest(p.plan_hash) && b.expected_epoch_id === epoch && b.reviewed_plan_hash === p.plan_hash && uuid(value.confirmation_key) && Array.isArray(p.children));
    if (info.kind === 'CONFIRM') check('idempotency_key' in b && b.idempotency_key === value.confirmation_key && b.idempotency_key === client);
    else { const x = parseJointExecute(b), c = p.children[x.expected_child_number - 1]; check(object(c) && c.action_id === x.expected_action_id && c.child_number === x.expected_child_number); }
  }
  return value as JointRecovery;
}
