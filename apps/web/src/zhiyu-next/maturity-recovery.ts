import type { MaturityPrepare } from '../api/full-maturity-execution';
import { parseMaturityPrepare } from '../api/full-maturity-execution';
import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assetCheck as check, assetExact as exact } from './asset-recovery';

/** Original prepare locator only. It contains no browser-created bank facts or consent. */
export type MaturityRecovery = { protocol: 'zhiyu-next-maturity-recovery-v1'; user_id: string; prepare_request: MaturityPrepare; action_id: string | null; reviewed_command_hash: string | null };
export function maturityPath(path: string) {
  if (path === '/zhiyu-next/assets/maturity/prepare') return { kind: 'PREPARE' as const, action_id: null };
  const match = /^\/zhiyu-next\/assets\/maturity\/actions\/([0-9a-f-]{36})\/(confirm-and-execute|execute-original)$/.exec(path);
  return match && uuid(match[1]) ? { kind: match[2] === 'confirm-and-execute' ? 'CONFIRM' as const : 'EXECUTE' as const, action_id: match[1]! } : null;
}
export const isMaturityPath = (path: string) => !!maturityPath(path);
export const maturityUsesKey = (path: string) => maturityPath(path)?.kind === 'PREPARE';
export function parseMaturityRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): MaturityRecovery {
  const info = maturityPath(path);
  check(info && object(value) && exact(value, ['protocol', 'user_id', 'prepare_request', 'action_id', 'reviewed_command_hash']) && value.protocol === 'zhiyu-next-maturity-recovery-v1' && uuid(value.user_id));
  const prepare = parseMaturityPrepare(value.prepare_request);
  check(uuid(prepare.idempotency_key) && prepare.expected_epoch_id === epoch && body.expected_epoch_id === epoch);
  if (info.kind === 'PREPARE') check(value.action_id === null && value.reviewed_command_hash === null && JSON.stringify(body) === JSON.stringify(prepare) && prepare.idempotency_key === client);
  else check(value.action_id === info.action_id && digest(value.reviewed_command_hash) && body.reviewed_command_hash === value.reviewed_command_hash && exact(body, info.kind === 'CONFIRM' ? ['expected_epoch_id', 'reviewed_command_hash', 'accepted'] : ['expected_epoch_id', 'reviewed_command_hash']) && (info.kind !== 'CONFIRM' || body.accepted === true));
  return value as MaturityRecovery;
}
