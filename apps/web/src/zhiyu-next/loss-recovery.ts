import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
export type LossPrepare = { expected_epoch_id: string; position_id: string; client_request_id: string };
export type LossRecovery = { protocol: 'zhiyu-next-loss-recovery-v1'; user_id: string; prepare_request: LossPrepare; action_id: string | null; reviewed_quote_hash: string | null; reviewed_effect_hash: string | null };
export function lossPath(path: string) { if (path === '/zhiyu-next/assets/lossy/prepare') return { kind: 'PREPARE' as const, action_id: null }; const match = /^\/zhiyu-next\/assets\/lossy\/actions\/([0-9a-f-]{36})\/(confirm-and-execute|execute-original)$/.exec(path); return match && uuid(match[1]) ? { kind: match[2] === 'confirm-and-execute' ? 'CONFIRM' as const : 'EXECUTE' as const, action_id: match[1]! } : null; }
export const isLossPath = (path: string) => !!lossPath(path);
export const lossUsesClientId = (path: string) => ['PREPARE', 'CONFIRM'].includes(lossPath(path)?.kind ?? '');
export function parseLossPrepare(value: unknown): LossPrepare { check(object(value) && exact(value, ['expected_epoch_id', 'position_id', 'client_request_id']) && Object.values(value).every(uuid)); return value as LossPrepare; }
export function parseLossRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): LossRecovery {
  const info = lossPath(path); check(info && object(value) && exact(value, ['protocol', 'user_id', 'prepare_request', 'action_id', 'reviewed_quote_hash', 'reviewed_effect_hash']) && value.protocol === 'zhiyu-next-loss-recovery-v1' && uuid(value.user_id));
  const prepare = parseLossPrepare(value.prepare_request); check(prepare.expected_epoch_id === epoch && body.expected_epoch_id === epoch);
  if (info.kind === 'PREPARE') check(value.action_id === null && value.reviewed_quote_hash === null && value.reviewed_effect_hash === null && JSON.stringify(body) === JSON.stringify(prepare) && prepare.client_request_id === client);
  else check(value.action_id === info.action_id && digest(value.reviewed_quote_hash) && digest(value.reviewed_effect_hash) && body.reviewed_quote_hash === value.reviewed_quote_hash && body.reviewed_effect_hash === value.reviewed_effect_hash && exact(body, info.kind === 'CONFIRM' ? ['expected_epoch_id', 'reviewed_quote_hash', 'reviewed_effect_hash', 'accepted', 'client_request_id'] : ['expected_epoch_id', 'reviewed_quote_hash', 'reviewed_effect_hash']) && (info.kind !== 'CONFIRM' || body.accepted === true && body.client_request_id === client));
  return value as LossRecovery;
}
