import { object } from '../features/policy-form';
import { releaseUUID } from '../api/goal-release-authorizations';
import type { TransferAction, TransferPreview } from './goal-transfers';

export const transferPrefix = '/zhiyu-next/goal-transfers';
export const transferCheck: (value: unknown) => asserts value = (value) => { if (!value) throw new Error('目标间转移原件、双方范围或当前身份未核实，请保留原请求。'); };
export const transferExact = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
export type TransferPreviewRequest = { expected_epoch_id: string; policy_id: string; expected_policy_version_id: string; source_goal_id: string; expected_source_policy_version_id: string; target_goal_id: string; expected_target_policy_version_id: string; amount_cents: number; idempotency_key: string };
export type TransferRecovery = { protocol: 'zhiyu-next-goal-transfer-recovery-v1'; user_id: string; preview_request: TransferPreviewRequest; preview: TransferPreview | null; action: TransferAction | null };
export function transferPath(path: string): { phase: 'PREVIEW' | 'PREPARE' | 'EXECUTE'; id?: string } | null {
  if (path === `${transferPrefix}/preview`) return { phase: 'PREVIEW' };
  if (path === `${transferPrefix}/prepare`) return { phase: 'PREPARE' };
  const match = /^\/zhiyu-next\/goal-transfers\/actions\/([0-9a-f-]{36})\/execute$/.exec(path);
  return match && releaseUUID(match[1]) ? { phase: 'EXECUTE', id: match[1] } : null;
}
export const isTransferPath = (path: string) => transferPath(path) !== null;
export const transferKey = (epoch: string, phase: 'preview' | 'prepare', key: string) => { transferCheck(releaseUUID(epoch) && releaseUUID(key)); return `zhiyu-next:${epoch}:goal-transfer-${phase}:${key}`; };
export function parseTransferRequest(value: unknown): TransferPreviewRequest {
  transferCheck(object(value) && transferExact(value, ['expected_epoch_id', 'policy_id', 'expected_policy_version_id', 'source_goal_id', 'expected_source_policy_version_id', 'target_goal_id', 'expected_target_policy_version_id', 'amount_cents', 'idempotency_key']));
  transferCheck(['expected_epoch_id', 'policy_id', 'expected_policy_version_id', 'source_goal_id', 'expected_source_policy_version_id', 'target_goal_id', 'expected_target_policy_version_id'].every((key) => releaseUUID(value[key])) && value.source_goal_id !== value.target_goal_id && Number.isSafeInteger(value.amount_cents) && Number(value.amount_cents) > 0 && typeof value.idempotency_key === 'string');
  const match = new RegExp(`^zhiyu-next:${String(value.expected_epoch_id)}:goal-transfer-preview:([0-9a-f-]{36})$`).exec(value.idempotency_key); transferCheck(match && releaseUUID(match[1]));
  return value as unknown as TransferPreviewRequest;
}
export function parseTransferRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, key: string): TransferRecovery {
  const phase = transferPath(path); transferCheck(phase && releaseUUID(epoch) && releaseUUID(key) && body.client_request_id === key && body.expected_epoch_id === epoch);
  transferCheck(object(value) && transferExact(value, ['protocol', 'user_id', 'preview_request', 'preview', 'action']) && value.protocol === 'zhiyu-next-goal-transfer-recovery-v1' && releaseUUID(value.user_id));
  const request = parseTransferRequest(value.preview_request); transferCheck(request.expected_epoch_id === epoch);
  const { client_request_id: ignored, ...native } = body; void ignored;
  if (phase.phase === 'PREVIEW') {
    const { idempotency_key: unused, ...original } = request; void unused;
    transferCheck(value.preview === null && value.action === null && request.idempotency_key === transferKey(epoch, 'preview', key) && JSON.stringify(native) === JSON.stringify(original));
  } else {
    transferCheck(object(value.preview) && value.preview.user_id === value.user_id && value.preview.epoch_id === epoch && object(value.preview.scope));
    if (phase.phase === 'PREPARE') transferCheck(transferExact(body, ['expected_epoch_id', 'preview_id', 'reviewed_scope_hash', 'accepted', 'client_request_id']) && value.action === null && body.accepted === true && body.preview_id === value.preview.preview_id && body.reviewed_scope_hash === value.preview.scope_hash);
    else transferCheck(transferExact(body, ['expected_epoch_id', 'reviewed_effect_hash', 'accepted', 'client_request_id']) && body.accepted === true && object(value.action) && value.action.action_id === phase.id && object(value.action.original_command) && body.reviewed_effect_hash === value.action.original_command.effect_hash);
  }
  return value as unknown as TransferRecovery;
}
