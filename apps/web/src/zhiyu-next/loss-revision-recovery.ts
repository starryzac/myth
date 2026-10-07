import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest, spendingCanonicalJson as canonical } from '../api/spending-evidence';
import { assetCheck as check, assetExact as exact } from './asset-recovery';

export const revisionBase = '/zhiyu-next/assets/lossy-revisions';
export type RenewalRequest = { expected_epoch_id: string; position_id: string; previous_quote_id: string; reviewed_previous_quote_hash: string; client_request_id: string };
export type RevisionPrepare = { expected_epoch_id: string; position_id: string; quote_id: string; client_request_id: string };
export type LossRevisionRecovery = { protocol: 'zhiyu-next-loss-revision-recovery-v2'; user_id: string; renewal_request: RenewalRequest; prepare_request: RevisionPrepare | null; action_id: string | null; reviewed_quote_hash: string | null; reviewed_effect_hash: string | null };
export function revisionPath(path: string) {
  if (path === `${revisionBase}/renew`) return { kind: 'RENEW' as const, action_id: null };
  if (path === `${revisionBase}/prepare`) return { kind: 'PREPARE' as const, action_id: null };
  const match = /^\/zhiyu-next\/assets\/lossy-revisions\/actions\/([0-9a-f-]{36})\/(confirm-and-execute|execute-original)$/.exec(path);
  return match && uuid(match[1]) ? { kind: match[2] === 'confirm-and-execute' ? 'CONFIRM' as const : 'EXECUTE' as const, action_id: match[1]! } : null;
}
export const isLossRevisionPath = (path: string) => !!revisionPath(path);
export const revisionUsesClientId = (path: string) => ['RENEW', 'PREPARE', 'CONFIRM'].includes(revisionPath(path)?.kind ?? '');
export function parseRenewalRequest(value: unknown): RenewalRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'position_id', 'previous_quote_id', 'reviewed_previous_quote_hash', 'client_request_id']) && ['expected_epoch_id', 'position_id', 'previous_quote_id', 'client_request_id'].every((key) => uuid(value[key])) && digest(value.reviewed_previous_quote_hash));
  return value as RenewalRequest;
}
export function parseRevisionPrepare(value: unknown): RevisionPrepare {
  check(object(value) && exact(value, ['expected_epoch_id', 'position_id', 'quote_id', 'client_request_id']) && Object.values(value).every(uuid)); return value as RevisionPrepare;
}
export function parseLossRevisionRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): LossRevisionRecovery {
  const info = revisionPath(path);
  check(info && object(value) && exact(value, ['protocol', 'user_id', 'renewal_request', 'prepare_request', 'action_id', 'reviewed_quote_hash', 'reviewed_effect_hash']) && value.protocol === 'zhiyu-next-loss-revision-recovery-v2' && uuid(value.user_id));
  const renewal = parseRenewalRequest(value.renewal_request); check(renewal.expected_epoch_id === epoch && body.expected_epoch_id === epoch);
  if (info.kind === 'RENEW') check(value.prepare_request === null && value.action_id === null && value.reviewed_quote_hash === null && value.reviewed_effect_hash === null && canonical(body) === canonical(renewal) && client === renewal.client_request_id);
  else {
    const prepare = parseRevisionPrepare(value.prepare_request); check(prepare.expected_epoch_id === epoch && prepare.position_id === renewal.position_id && prepare.quote_id !== renewal.previous_quote_id);
    if (info.kind === 'PREPARE') check(value.action_id === null && value.reviewed_quote_hash === null && value.reviewed_effect_hash === null && canonical(body) === canonical(prepare) && client === prepare.client_request_id);
    else check(value.action_id === info.action_id && digest(value.reviewed_quote_hash) && digest(value.reviewed_effect_hash) && body.reviewed_quote_hash === value.reviewed_quote_hash && body.reviewed_effect_hash === value.reviewed_effect_hash && exact(body, info.kind === 'CONFIRM' ? ['expected_epoch_id', 'reviewed_quote_hash', 'reviewed_effect_hash', 'accepted', 'client_request_id'] : ['expected_epoch_id', 'reviewed_quote_hash', 'reviewed_effect_hash']) && (info.kind !== 'CONFIRM' || body.accepted === true && body.client_request_id === client));
  }
  return value as LossRevisionRecovery;
}
