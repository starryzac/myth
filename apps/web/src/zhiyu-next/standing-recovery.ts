import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assetExact as exact } from './asset-recovery';

export const STANDING_PREFIX = '/zhiyu-next/standing';
export const standingFamilies = ['SINGLE_GOAL', 'JOINT_GOALS', 'ASSET_PORTFOLIO', 'MATURITY_REINVESTMENT', 'PERIODIC_PAYMENT'] as const;
export type StandingFamily = typeof standingFamilies[number];
export type StandingGoalMode = 'CUMULATIVE_EVENT_TARGET';
export type StandingCandidateRequest = { client_request_id: string; expected_epoch_id: string; family: StandingFamily; policy_version_ids: string[]; source_account_ids: string[]; goal_ids: string[]; product_ids: string[]; validity_preset: 'ONE_DAY' | 'SEVEN_DAYS' | 'THIRTY_DAYS'; goal_mode?: StandingGoalMode };
export type StandingRecovery = { protocol: 'zhiyu-next-standing-recovery-v1'; user_id: string; candidate_request: StandingCandidateRequest | null; candidate_id: string | null; reviewed_candidate_hash: string | null; reviewed_scope_hash: string | null; grant_id: string | null; previous_control_hash: string | null };
export function standingCheck(value: unknown): asserts value { if (!value) throw new Error('持续范围、本人签署或原控制未匹配，请保留同一原请求。'); }
export function standingPath(path: string) { return path === `${STANDING_PREFIX}/candidates` ? 'CANDIDATE' as const : path === `${STANDING_PREFIX}/confirm` ? 'CONFIRM' as const : path === `${STANDING_PREFIX}/control` ? 'CONTROL' as const : null; }
export const isStandingPath = (path: string) => !!standingPath(path);
export function standingIds(value: unknown, min: number, max: number): value is string[] { return Array.isArray(value) && value.length >= min && value.length <= max && value.every(uuid) && new Set(value).size === value.length; }
export function parseStandingCandidateRequest(v: unknown): StandingCandidateRequest {
  standingCheck(object(v) && exact(v, ['client_request_id', 'expected_epoch_id', 'family', 'policy_version_ids', 'source_account_ids', 'goal_ids', 'product_ids', 'validity_preset', ...('goal_mode' in v ? ['goal_mode'] : [])]) && uuid(v.client_request_id) && uuid(v.expected_epoch_id) && standingFamilies.includes(v.family as StandingFamily) && standingIds(v.policy_version_ids, 1, 100) && standingIds(v.source_account_ids, 1, 64) && standingIds(v.goal_ids, 0, 8) && standingIds(v.product_ids, 0, 64) && ['ONE_DAY', 'SEVEN_DAYS', 'THIRTY_DAYS'].includes(String(v.validity_preset)));
  if ('goal_mode' in v) standingCheck(v.goal_mode === 'CUMULATIVE_EVENT_TARGET' && v.family === 'SINGLE_GOAL');
  if (['SINGLE_GOAL', 'JOINT_GOALS'].includes(String(v.family))) standingCheck(v.product_ids.length === 0 && (v.family === 'SINGLE_GOAL' ? v.goal_ids.length === 1 : v.goal_ids.length >= 2));
  else if (v.family === 'PERIODIC_PAYMENT') standingCheck(v.goal_ids.length === 0 && v.product_ids.length === 0);
  else standingCheck(v.product_ids.length > 0);
  return v as StandingCandidateRequest;
}
export function parseStandingRecovery(v: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): StandingRecovery {
  const kind = standingPath(path); standingCheck(kind && object(v) && exact(v, ['protocol', 'user_id', 'candidate_request', 'candidate_id', 'reviewed_candidate_hash', 'reviewed_scope_hash', 'grant_id', 'previous_control_hash']) && v.protocol === 'zhiyu-next-standing-recovery-v1' && uuid(v.user_id) && body.expected_epoch_id === epoch && body.client_request_id === client && uuid(client));
  if (kind === 'CONTROL') standingCheck(v.candidate_request === null && v.candidate_id === null && v.reviewed_candidate_hash === null && v.reviewed_scope_hash === null && uuid(v.grant_id) && digest(v.previous_control_hash) && exact(body, ['client_request_id', 'expected_epoch_id', 'accepted', 'grant_id', 'expected_revision', 'command']) && body.grant_id === v.grant_id && body.accepted === true && Number.isSafeInteger(body.expected_revision) && Number(body.expected_revision) >= 1 && Number(body.expected_revision) <= 127 && ['PAUSE', 'RESUME', 'REVOKE'].includes(String(body.command)));
  else { const request = parseStandingCandidateRequest(v.candidate_request); standingCheck(request.expected_epoch_id === epoch && v.grant_id === null && v.previous_control_hash === null); if (kind === 'CANDIDATE') standingCheck(v.candidate_id === null && v.reviewed_candidate_hash === null && v.reviewed_scope_hash === null && JSON.stringify(body) === JSON.stringify(request)); else standingCheck(uuid(v.candidate_id) && digest(v.reviewed_candidate_hash) && digest(v.reviewed_scope_hash) && exact(body, ['client_request_id', 'expected_epoch_id', 'accepted', 'candidate_id', 'reviewed_scope_hash', 'reviewed_candidate_hash']) && body.accepted === true && body.candidate_id === v.candidate_id && body.reviewed_scope_hash === v.reviewed_scope_hash && body.reviewed_candidate_hash === v.reviewed_candidate_hash); }
  return v as StandingRecovery;
}
