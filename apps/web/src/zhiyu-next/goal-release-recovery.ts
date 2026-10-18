import { object } from '../features/policy-form';
import { parseReleaseIntent, releaseUUID } from '../api/goal-release-authorizations';
import type { ReleaseIntent } from '../api/goal-release-authorizations';
import { parseCashExecute, parseCashPrepare } from '../api/goal-cash-releases';
import type { CashAction, CashIntent } from '../api/goal-cash-releases';

export type GoalReleaseRecovery = { protocol: 'zhiyu-next-goal-release-recovery-v1'; user_id: string; authorization: ReleaseIntent; cash: CashIntent | null; action: CashAction | null };
export const goalReleasePrefix = '/zhiyu-next/goal-releases';
export const goalReleaseCheck: (value: unknown) => asserts value = (value) => { if (!value) throw new Error('目标应急回拨原件、身份或来源未核实，请保留原请求。'); };
export const goalReleaseExact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
export function goalReleasePath(path: string): { phase: 'AUTHORIZATION' | 'PREPARE' | 'EXECUTE'; id?: string } | null {
  const authorization = /^\/zhiyu-next\/goal-releases\/authorizations\/([0-9a-f-]{36})\/confirm$/.exec(path);
  if (authorization && releaseUUID(authorization[1])) return { phase: 'AUTHORIZATION', id: authorization[1] };
  if (path === `${goalReleasePrefix}/prepare`) return { phase: 'PREPARE' };
  const execute = /^\/zhiyu-next\/goal-releases\/actions\/([0-9a-f-]{36})\/execute$/.exec(path);
  return execute && releaseUUID(execute[1]) ? { phase: 'EXECUTE', id: execute[1] } : null;
}
export const isGoalReleasePath = (path: string) => goalReleasePath(path) !== null;
export const goalReleaseKey = (epoch: string, phase: 'authorization' | 'prepare', key: string) => { goalReleaseCheck(releaseUUID(epoch) && releaseUUID(key)); return `zhiyu-next:${epoch}:goal-release-${phase}:${key}`; };
export function parseGoalReleaseRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, key: string): GoalReleaseRecovery {
  const phase = goalReleasePath(path); goalReleaseCheck(phase && releaseUUID(epoch) && releaseUUID(key) && body.client_request_id === key && body.expected_epoch_id === epoch);
  goalReleaseCheck(object(value) && goalReleaseExact(value, ['protocol', 'user_id', 'authorization', 'cash', 'action']) && value.protocol === 'zhiyu-next-goal-release-recovery-v1' && releaseUUID(value.user_id));
  const authorization = parseReleaseIntent(value.authorization);
  goalReleaseCheck(authorization.user_id === value.user_id && authorization.body.expected_epoch_id === epoch);
  const result = value as unknown as GoalReleaseRecovery;
  if (phase.phase === 'AUTHORIZATION') {
    goalReleaseCheck(goalReleaseExact(body, ['expected_epoch_id', 'expected_policy_version_id', 'reviewed_scope_hash', 'accepted', 'client_request_id']) && body.accepted === true && value.cash === null && value.action === null && authorization.policy_id === phase.id && authorization.body.idempotency_key === goalReleaseKey(epoch, 'authorization', key));
    const { idempotency_key: unused, ...native } = authorization.body; void unused;
    const { client_request_id: ignored, ...original } = body; void ignored;
    goalReleaseCheck(JSON.stringify(native) === JSON.stringify(original));
  } else {
    goalReleaseCheck(object(value.cash) && value.cash.protocol === 'goal-cash-release-browser-v1' && value.cash.user_id === value.user_id && object(value.cash.body) && value.cash.body.expected_epoch_id === epoch && object(value.cash.authorization));
    const cash = result.cash!; parseCashPrepare(cash.body);
    goalReleaseCheck(cash.body.authorization_idempotency_key === authorization.body.idempotency_key && cash.body.policy_id === authorization.policy_id);
    if (phase.phase === 'PREPARE') {
      const { idempotency_key: unused, ...native } = cash.body; void unused;
      const { client_request_id: ignored, ...original } = body; void ignored;
      goalReleaseCheck(value.action === null && cash.body.idempotency_key === goalReleaseKey(epoch, 'prepare', key) && JSON.stringify(native) === JSON.stringify(original));
    } else {
      goalReleaseCheck(goalReleaseExact(body, ['accepted', 'reviewed_effect_hash', 'expected_epoch_id', 'client_request_id']) && object(value.action) && value.action.action_id === phase.id && object(value.action.original_command) && body.reviewed_effect_hash === value.action.original_command.effect_hash);
      const { client_request_id: unused, ...native } = body; void unused; parseCashExecute(native);
    }
  }
  return result;
}
