import { isRunId } from '../api/decisions';
import { parsePaymentScope, type PaymentScope, type PaymentAction, type PaymentIntent } from '../api/full-payment-relations';
import { object } from '../features/policy-form';

export type PaymentRecovery = { protocol: 'zhiyu-next-payment-recovery-v1'; scope: PaymentScope; start_command_id: string | null; authorization_id: string | null; action: PaymentAction | null };
export function paymentPath(path: string): { kind: PaymentIntent['kind'] | 'OBSERVE'; id: string | null } | null {
  if (path === '/zhiyu-next/payments/observe-current-period') return { kind: 'OBSERVE', id: null };
  if (path === '/zhiyu-next/payments/relation/start') return { kind: 'START', id: null };
  for (const [expression, kind] of [[/^\/zhiyu-next\/payments\/relation\/([0-9a-f-]{36})\/confirm$/, 'CONFIRM'], [/^\/zhiyu-next\/payments\/authorizations\/([0-9a-f-]{36})\/prepare$/, 'PREPARE'], [/^\/zhiyu-next\/payments\/actions\/([0-9a-f-]{36})\/confirm-and-execute$/, 'ACTION_CONFIRM'], [/^\/zhiyu-next\/payments\/actions\/([0-9a-f-]{36})\/execute-original$/, 'EXECUTE']] as const) {
    const found = expression.exec(path); if (found && isRunId(found[1])) return { kind, id: found[1] };
  }
  return null;
}
export const isPaymentPath = (path: string) => paymentPath(path) !== null;
export const paymentUsesKey = (path: string) => ['START', 'CONFIRM', 'PREPARE'].includes(paymentPath(path)?.kind ?? '');
export function parsePaymentRecovery(value: unknown, path: string, userEpoch: string): PaymentRecovery {
  const info = paymentPath(path);
  if (!info || !object(value) || Object.keys(value).sort().join('|') !== 'action|authorization_id|protocol|scope|start_command_id' || value.protocol !== 'zhiyu-next-payment-recovery-v1') throw new Error('原付款关联无法恢复，请保留记录。');
  const scope = parsePaymentScope(value.scope);
  if (scope.epoch_id !== userEpoch) throw new Error('原付款属于其他审计轮，请返回原环境。');
  const start = value.start_command_id, authorization = value.authorization_id, action = value.action;
  const valid = info.kind === 'START' ? start === null && authorization === null && action === null : info.kind === 'CONFIRM' ? start === info.id && authorization === null && action === null : info.kind === 'PREPARE' ? start === null && authorization === info.id && action === null : info.kind === 'OBSERVE' ? start === null && isRunId(authorization) && action === null : start === null && isRunId(authorization) && object(action) && action.action_id === info.id && typeof action.effect_hash === 'string' && /^[0-9a-f]{64}$/.test(action.effect_hash) && action.user_id === scope.user_id;
  if (!valid) throw new Error('原付款、关系或动作身份不一致，请保留原件。');
  return value as PaymentRecovery;
}
