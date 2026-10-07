import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assetExact as exact } from './asset-recovery';

export const QUESTION_PREFIX = '/zhiyu-next/questions/full-assets';
export type Mode = 'PORTFOLIO' | 'FIXED_LADDER';
export type QuestionStart = { expected_epoch_id: string; client_request_id: string; full_policy_id: string; expected_full_policy_version_id: string };
export type QuestionRecovery = { protocol: 'zhiyu-next-question-recovery-v1'; user_id: string; start_request: QuestionStart; session_id: string | null; previous_receipt_hash: string | null };
export function questionCheck(value: unknown): asserts value { if (!value) throw new Error('原问题、版本、来源或偏好命令未匹配，请保留同一会话。'); }
export function questionPath(path: string) { if (path === `${QUESTION_PREFIX}/sessions`) return { kind: 'START' as const, session_id: null }; const m = /^\/zhiyu-next\/questions\/full-assets\/sessions\/([0-9a-f-]{36})\/(answers|refresh|close)$/.exec(path); return m && uuid(m[1]) ? { kind: m[2] === 'answers' ? 'ANSWER' as const : m[2] === 'refresh' ? 'REFRESH' as const : 'CLOSE' as const, session_id: m[1]! } : null; }
export const isQuestionPath = (path: string) => !!questionPath(path);
export function parseQuestionStart(value: unknown): QuestionStart { questionCheck(object(value) && exact(value, ['expected_epoch_id', 'client_request_id', 'full_policy_id', 'expected_full_policy_version_id']) && Object.values(value).every(uuid)); return value as QuestionStart; }
export function parseQuestionRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): QuestionRecovery {
  const info = questionPath(path); questionCheck(info && object(value) && exact(value, ['protocol', 'user_id', 'start_request', 'session_id', 'previous_receipt_hash']) && value.protocol === 'zhiyu-next-question-recovery-v1' && uuid(value.user_id));
  const start = parseQuestionStart(value.start_request); questionCheck(start.expected_epoch_id === epoch && body.expected_epoch_id === epoch && body.client_request_id === client && uuid(client));
  if (info.kind === 'START') questionCheck(value.session_id === null && value.previous_receipt_hash === null && JSON.stringify(body) === JSON.stringify(start));
  else { questionCheck(value.session_id === info.session_id && digest(value.previous_receipt_hash) && exact(body, ['expected_epoch_id', 'client_request_id', 'expected_revision', ...(info.kind === 'ANSWER' ? ['question_id', 'mode'] : [])]) && Number.isSafeInteger(body.expected_revision) && Number(body.expected_revision) >= 1 && Number(body.expected_revision) <= 16); if (info.kind === 'ANSWER') questionCheck(uuid(body.question_id) && ['PORTFOLIO', 'FIXED_LADDER'].includes(String(body.mode))); }
  return value as QuestionRecovery;
}
