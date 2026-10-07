import { request } from '../api/http';
import { object, parseMoneyInput } from '../features/policy-form';
import { parseFullGoalConflicts, parseFullGoalRepairs, parseGoalRepairSelection, validateGoalConflictGoals, type FullGoalConflicts, type FullGoalRepairs, type GoalRepairSelection } from '../api/full-goal-conflicts';
import { parseFullGoalConfiguration, parseFullGoalCommandLookup, parseFullGoalPreview, type FullGoalReceipt } from '../api/full-goals';
import { prepareFullGoalIntent } from '../features/full-goal-operation';
import { jointCanonical, jointCheck as check } from '../api/full-joint-goal-execution';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import type { NextState, OperationResult } from './api';
import type { PendingOperation } from './operation';
import { context } from './policies';
import { readGoalLinkModel } from './goal-asset-link';

export type GoalRepairVersion = FullGoalRepairs['version_previews'][number];
export type GoalModelUpdate = { goal_id: string; receipt: FullGoalReceipt; client_request_id: string };
export function goalModelId(path: string): string | null { const m = /^\/zhiyu-next\/goals\/models\/([0-9a-f-]{36})\/confirm$/.exec(path); return m && uuid(m[1]) ? m[1]! : null; }
export const isGoalModelPath = (path: string) => goalModelId(path) !== null;
export function validateGoalModelBody(body: Record<string, unknown>, epoch: string, client: string): void {
  check(Object.keys(body).sort().join('|') === 'accepted|configuration|expected_epoch_id|expected_version_id|idempotency_key|reason|reviewed_base_hash|reviewed_full_hash' && body.expected_epoch_id === epoch && body.idempotency_key === client && uuid(client) && uuid(body.expected_version_id) && digest(body.reviewed_full_hash) && digest(body.reviewed_base_hash) && body.accepted === true && typeof body.reason === 'string' && body.reason.trim().length > 0 && body.reason.length <= 1000);
  parseFullGoalConfiguration(body.configuration);
}
export async function goalModelIntent(original: PendingOperation, environment: NextState) {
  const id = goalModelId(original.path), goal = environment.goals.find((g) => g.id === id);
  check(id && goal && original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id);
  validateGoalModelBody(original.body, original.epoch_id, original.client_request_id);
  return prepareFullGoalIntent({ user_id: environment.dashboard.user_id, goal_id: id, policy_id: goal.policy_id, body: original.body as Parameters<typeof prepareFullGoalIntent>[0]['body'] });
}
export async function readGoalModelOriginal(original: PendingOperation, environment: NextState): Promise<OperationResult & { goal_model?: GoalModelUpdate }> {
  const intent = await goalModelIntent(original, environment);
  const lookup = await request(`/zhiyu-next/goals/models/${intent.goal_id}/commands/by-key/${encodeURIComponent(original.client_request_id)}`, 'GET', undefined, (v) => { context(v, environment); return parseFullGoalCommandLookup(v, intent); });
  const base = { simulation: true as const, environment_id: original.environment_id, epoch_id: original.epoch_id, client_request_id: original.client_request_id };
  // Native NOT_FOUND is explicitly nonfinal. HTTP rejection or a missing original never releases this locator.
  return lookup.status === 'NOT_FOUND' ? { ...base, status: 'PENDING' } : { ...base, status: 'COMPLETED', goal_model: { goal_id: intent.goal_id, receipt: lookup.record!.receipt, client_request_id: original.client_request_id } };
}
export function parseNextGoalConflicts(value: unknown, environment: NextState): FullGoalConflicts {
  context(value, environment); const result = parseFullGoalConflicts(value.conflicts);
  check(result.user_id === environment.dashboard.user_id && (result.epoch_id === environment.epoch_id || result.state === 'UNKNOWN' && result.epoch_id === null));
  validateGoalConflictGoals(result, environment.goals); return result;
}
export const getNextGoalConflicts = (environment: NextState) => request('/zhiyu-next/goals/conflicts', 'GET', undefined, (v) => parseNextGoalConflicts(v, environment));
/** A deletion-minimal set is only one conflict. Keep the complete registered denominator selectable. */
export function goalRepairRanges(original: FullGoalConflicts, environment: NextState) {
  validateGoalConflictGoals(original, environment.goals);
  return original.included_goal_ids.map((id) => {
    const goal = environment.goals.find((g) => g.id === id); check(goal);
    const constraint = original.explanation?.constraints.find((c) => c.goal_id === id && c.kind === 'MONTHLY_MAX');
    return { goal_id: id, current_version_id: goal.policy_version_id, original_parameter_cents: constraint?.original_parameter_cents ?? goal.monthly_max_cents, current_month_contributed_cents: constraint?.current_month_contributed_cents ?? null, in_minimal_set: !!constraint };
  });
}
export async function prepareGoalRepairSelection(original: FullGoalConflicts, ranges: Record<string, string>, environment: NextState): Promise<GoalRepairSelection> {
  check(original.state === 'COMPUTED' && original.epoch_id === environment.epoch_id && original.user_id === environment.dashboard.user_id);
  const adjustments: GoalRepairSelection['adjustments'] = [];
  for (const row of goalRepairRanges(original, environment)) {
    if (!ranges[row.goal_id]?.trim()) continue;
    let current = row.original_parameter_cents;
    // Other conflicts can lie outside the displayed minimal set. Verify their native FULL source before search.
    if (!row.in_minimal_set) {
      const goal = environment.goals.find((g) => g.id === row.goal_id)!;
      const model = await readGoalLinkModel(goal, environment); current = parseFullGoalConfiguration(model.full_configuration).monthly_contribution.max_cents;
      check(current === row.original_parameter_cents && ['ACTIVE', 'CONFIRMED'].includes(String(model.policy_effective_status)));
    }
    const maximum = parseMoneyInput(ranges[row.goal_id]!); check(maximum > current);
    adjustments.push({ goal_id: row.goal_id, expected_version_id: row.current_version_id, minimum_new_monthly_max_cents: current + 1, maximum_new_monthly_max_cents: maximum });
  }
  return parseGoalRepairSelection({ expected_epoch_id: environment.epoch_id, reviewed_state_hash: original.review_state_hash, adjustments }, original);
}
export function parseNextGoalRepairs(value: unknown, original: FullGoalConflicts, body: GoalRepairSelection, environment: NextState): FullGoalRepairs {
  context(value, environment); check(object(value.repair) && Array.isArray(value.repair.version_previews));
  const native = structuredClone(value.repair);
  check(Array.isArray(native.version_previews));
  for (const row of native.version_previews) {
    check(object(row) && uuid(row.goal_id) && row.preview_endpoint === `/api/v1/zhiyu-next/goals/models/${row.goal_id}/preview` && row.confirmation_endpoint === `/api/v1/zhiyu-next/goals/models/${row.goal_id}/confirm`);
    // Only these two closed, verified transport paths differ from the native contract.
    row.preview_endpoint = `/api/v1/goals/${row.goal_id}/full-model/preview`; row.confirmation_endpoint = `/api/v1/goals/${row.goal_id}/full-model/confirm`;
  }
  parseFullGoalRepairs(native, original, body); return value.repair as FullGoalRepairs;
}
export function previewNextGoalRepairs(original: FullGoalConflicts, value: unknown, environment: NextState): Promise<FullGoalRepairs> {
  check(original.user_id === environment.dashboard.user_id && original.epoch_id === environment.epoch_id); validateGoalConflictGoals(original, environment.goals);
  const body = parseGoalRepairSelection(value, original);
  return request('/zhiyu-next/goals/repairs/preview', 'POST', body, (v) => parseNextGoalRepairs(v, original, body, environment));
}
/** Re-read the exact displayed baseline and native model preview; never substitute fresh hashes after consent. */
export async function checkReviewedGoalRepair(original: FullGoalConflicts, version: GoalRepairVersion, environment: NextState): Promise<void> {
  const fresh = await getNextGoalConflicts(environment); check(fresh.state === 'COMPUTED' && fresh.review_state_hash === original.review_state_hash);
  const goal = environment.goals.find((g) => g.id === version.goal_id); check(goal && goal.policy_version_id === version.current_version_id);
  const preview = await request(`/zhiyu-next/goals/models/${version.goal_id}/preview`, 'POST', version.preview_request, (v) => { context(v, environment); return parseFullGoalPreview(v.preview, { id: goal.id, policy_id: goal.policy_id, policy_version_id: version.current_version_id }); });
  check(preview.epoch_id === environment.epoch_id && preview.full_configuration_hash === version.confirmation_bindings.reviewed_full_hash && preview.base_configuration_hash === version.confirmation_bindings.reviewed_base_hash && jointCanonical(preview.full_configuration) === jointCanonical(version.confirmation_bindings.configuration));
}
