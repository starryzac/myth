import { request } from '../api/http';
import { object } from '../features/policy-form';
import { parseFullPolicy } from '../api/full-policies';
import type { FullPolicy } from '../api/full-policies';
import { parseFullGoalModel } from '../api/full-goals';
import { parseReleaseIntent, parseReleaseLookup, parseReleasePreview, parseReleaseResponse, releaseHash, releaseRequestEnvelope, releaseUUID } from '../api/goal-release-authorizations';
import type { ReleaseIntent, ReleasePreview, ReleaseResponse, ReleaseScope, ReleaseSource } from '../api/goal-release-authorizations';
import { cashIntentEnvelope, createCashIntent, parseCashAction, parseCashAuthorization, parseCashCandidate, parseCashIntent, parseCashLookup } from '../api/goal-cash-releases';
import type { CashAction, CashCandidate, CashGoal, CashIntent } from '../api/goal-cash-releases';
import type { NextState, OperationResult } from './api';
import type { PendingOperation } from './operation';
import { context } from './policies';
import { goalReleaseCheck as check, goalReleaseExact as exact, goalReleaseKey, goalReleasePath, goalReleasePrefix, parseGoalReleaseRecovery } from './goal-release-recovery';
import type { GoalReleaseRecovery } from './goal-release-recovery';

export type GoalReleaseContext = { policy: FullPolicy; sources: ReleaseSource[]; accounts: { id: string; name: string }[]; authorizations: ReleaseResponse[]; actions: { intent: CashIntent; action: CashAction }[]; any_unresolved: boolean };
export type GoalReleaseUpdate = { environment_id: string; recovery: GoalReleaseRecovery; authorization: ReleaseResponse | null; action: CashAction | null; phase: 'AUTHORIZATION' | 'PREPARE' | 'EXECUTE'; resume_original: boolean };
export type GoalReleaseRead = OperationResult & { goal_release?: GoalReleaseUpdate };
export type GoalReleaseCommand = { path: string; body: Record<string, unknown>; recovery: GoalReleaseRecovery };
export type GoalReleasePerform = (path: string, body: Record<string, unknown>, recovery: GoalReleaseRecovery) => Promise<void>;

function envelope(value: unknown, environment: NextState, kind: string): unknown {
  context(value, environment);
  check(exact(value, ['simulation', 'variant', 'environment_id', 'epoch_id', 'protocol', 'user_id', 'kind', 'native', 'bank_authority', 'receipt_is_current_authority']) && value.protocol === 'zhiyu-next-goal-release-read-v1' && value.user_id === environment.dashboard.user_id && value.kind === kind && value.bank_authority === false && value.receipt_is_current_authority === false);
  return value.native;
}
export function releaseAcknowledgment(value: unknown, environment: NextState) { context(value, environment); check(value.protocol === 'zhiyu-next-goal-release-read-v1' && value.bank_authority === false && value.receipt_is_current_authority === false && value.user_id === environment.dashboard.user_id); return value; }
async function authorizationIntent(user: string, owner: string, scope: ReleaseScope, body: ReleaseIntent['body']): Promise<ReleaseIntent> {
  return parseReleaseIntent({ protocol: 'goal-release-browser-command-v1', user_id: user, owner_goal_id: owner, policy_id: scope.policy_id, path: `/goal-release-authorizations/policies/${scope.policy_id}/confirm`, body, body_json: JSON.stringify(body), reviewed_scope: scope, request_hash: await releaseHash(releaseRequestEnvelope(user, scope.policy_id, body)) });
}
async function originalAuthorizationIntent(value: ReleaseResponse, owner: string, user: string) {
  const original = value.original_authorization;
  return authorizationIntent(user, owner, original.scope, original.original_request as ReleaseIntent['body']);
}
async function actionIntent(value: CashAction, authorizations: ReleaseResponse[], name: string, environment: NextState) {
  check(object(value) && object(value.original_command) && object(value.original_command.effect));
  const effect = value.original_command.effect;
  const authorization = authorizations.find((row) => row.original_authorization.authorization_id === effect.authorization_id);
  check(authorization && effect.user_id === environment.dashboard.user_id && effect.epoch_id === environment.epoch_id);
  const owner_goal = { id: effect.source_goal_id, name, policy_id: effect.original_goal_policy_id, policy_version_id: effect.original_goal_policy_version_id };
  const body = value.original_prepare_request;
  return parseCashIntent({ protocol: 'goal-cash-release-browser-v1', user_id: environment.dashboard.user_id, owner_goal, body, body_json: JSON.stringify(body), client_intent_hash: await releaseHash(cashIntentEnvelope(environment.dashboard.user_id, body)), authorization });
}
export async function parseGoalReleaseContext(value: unknown, policyId: string, environment: NextState): Promise<GoalReleaseContext> {
  const native = envelope(value, environment, 'CONTEXT');
  check(object(native) && exact(native, ['policy', 'sources', 'accounts', 'authorizations', 'actions', 'any_unresolved']) && Array.isArray(native.sources) && native.sources.length > 0 && native.sources.length <= 32 && Array.isArray(native.accounts) && native.accounts.length <= 1000 && Array.isArray(native.authorizations) && native.authorizations.length <= 1000 && Array.isArray(native.actions) && native.actions.length <= 1000 && typeof native.any_unresolved === 'boolean');
  const policy = parseFullPolicy(native.policy);
  check(policy.policy_id === policyId && policy.epoch_id === environment.epoch_id && policy.template_name === 'CrossGoalReallocationPolicy' && policy.current_version.confirmation.user_id === environment.dashboard.user_id);
  const sources = native.sources as ReleaseSource[];
  for (const source of sources) {
    check(object(source) && exact(source, ['goal', 'model']) && object(source.goal) && releaseUUID(source.goal.id));
    const model = parseFullGoalModel(source.model, source.goal);
    check(model.epoch_id === environment.epoch_id);
  }
  check(new Set(sources.map((row) => row.goal.id)).size === sources.length);
  const accounts = native.accounts as GoalReleaseContext['accounts'];
  check(accounts.every((row) => object(row) && exact(row, ['id', 'name']) && releaseUUID(row.id) && typeof row.name === 'string') && new Set(accounts.map((row) => row.id)).size === accounts.length);
  const authorizations: ReleaseResponse[] = [];
  for (const row of native.authorizations) {
    check(object(row) && object(row.original_authorization) && object(row.original_authorization.scope) && Array.isArray(row.original_authorization.scope.source_goals));
    const binding = row.original_authorization.scope.source_goals[0]; check(object(binding));
    const goal = { id: String(binding.goal_id), name: '原授权目标', policy_id: String(binding.original_policy_id), policy_version_id: String(binding.original_policy_version_id) };
    const parsed = await parseCashAuthorization(row, goal, environment.dashboard.user_id);
    check(parsed.original_authorization.epoch_id === environment.epoch_id && parsed.original_authorization.policy_id === policyId); authorizations.push(parsed);
  }
  check(new Set(authorizations.map((row) => row.original_authorization.authorization_id)).size === authorizations.length);
  const actions: GoalReleaseContext['actions'] = [];
  for (const row of native.actions as CashAction[]) {
    const intent = await actionIntent(row, authorizations, sources.find((source) => source.goal.id === row.original_prepare_request.source_goal_id)?.goal.name ?? '原目标', environment);
    actions.push({ intent, action: await parseCashAction(row, intent) });
  }
  check(new Set(actions.map((row) => row.action.action_id)).size === actions.length && (native.any_unresolved || !actions.some((row) => row.action.unresolved)));
  return { policy, sources, accounts, authorizations, actions, any_unresolved: native.any_unresolved };
}
export async function getGoalReleaseContext(policyId: string, environment: NextState): Promise<GoalReleaseContext> { check(releaseUUID(policyId)); return request(`${goalReleasePrefix}/context/${policyId}`, 'GET', undefined, (value) => parseGoalReleaseContext(value, policyId, environment)); }
export async function getGoalReleasePreview(entry: GoalReleaseContext, goal: ReleaseSource['goal'], environment: NextState): Promise<ReleasePreview> {
  return request(`${goalReleasePrefix}/authorizations/${entry.policy.policy_id}/preview`, 'POST', { expected_epoch_id: environment.epoch_id, expected_policy_version_id: entry.policy.current_version.version_id }, async (value) => parseReleasePreview(envelope(value, environment, 'AUTHORIZATION_PREVIEW'), { goal, userId: environment.dashboard.user_id, epochId: environment.epoch_id }, entry.policy, entry.sources));
}
export async function authorizationCommand(preview: ReleasePreview, ownerGoalId: string, environment: NextState, key = crypto.randomUUID()): Promise<GoalReleaseCommand> {
  check(preview.scope.user_id === environment.dashboard.user_id && preview.scope.epoch_id === environment.epoch_id);
  const native = { expected_epoch_id: environment.epoch_id, expected_policy_version_id: preview.scope.policy_version_id, reviewed_scope_hash: preview.scope_hash, accepted: true as const, idempotency_key: goalReleaseKey(environment.epoch_id, 'authorization', key) };
  const authorization = await authorizationIntent(environment.dashboard.user_id, ownerGoalId, preview.scope, native);
  const { idempotency_key: unused, ...body } = native; void unused;
  return { path: `${goalReleasePrefix}/authorizations/${preview.scope.policy_id}/confirm`, body: { ...body, client_request_id: key }, recovery: { protocol: 'zhiyu-next-goal-release-recovery-v1', user_id: environment.dashboard.user_id, authorization, cash: null, action: null } };
}
export async function prepareReleaseCommand(goal: CashGoal, authorization: ReleaseResponse, destination: string, environment: NextState, key = crypto.randomUUID()): Promise<GoalReleaseCommand> {
  check(authorization.current_scope_status === 'CURRENT' && authorization.original_authorization.epoch_id === environment.epoch_id && authorization.original_authorization.user_id === environment.dashboard.user_id);
  const cash = await createCashIntent({ goal, userId: environment.dashboard.user_id, epochId: environment.epoch_id }, authorization, destination, goalReleaseKey(environment.epoch_id, 'prepare', key));
  const intent = await originalAuthorizationIntent(authorization, goal.id, environment.dashboard.user_id);
  const { idempotency_key: unused, ...body } = cash.body; void unused;
  return { path: `${goalReleasePrefix}/prepare`, body: { ...body, client_request_id: key }, recovery: { protocol: 'zhiyu-next-goal-release-recovery-v1', user_id: environment.dashboard.user_id, authorization: intent, cash, action: null } };
}
export async function getGoalCashPreview(command: GoalReleaseCommand, environment: NextState): Promise<CashCandidate> {
  check(command.path === `${goalReleasePrefix}/prepare` && command.recovery.cash);
  return request(`${goalReleasePrefix}/preview`, 'POST', command.body, (value) => parseCashCandidate(envelope(value, environment, 'CANDIDATE'), command.recovery.cash!));
}
export async function executeReleaseCommand(intent: CashIntent, action: CashAction, environment: NextState, key = crypto.randomUUID()): Promise<GoalReleaseCommand> {
  await parseCashIntent(intent); await parseCashAction(action, intent);
  check(intent.user_id === environment.dashboard.user_id && intent.body.expected_epoch_id === environment.epoch_id && !action.unresolved && !action.service_receipt_verified && ['PLANNED', 'AUTHORIZED'].includes(action.original_action_status));
  return { path: `${goalReleasePrefix}/actions/${action.action_id}/execute`, body: { accepted: true, reviewed_effect_hash: action.original_command.effect_hash, expected_epoch_id: environment.epoch_id, client_request_id: key }, recovery: { protocol: 'zhiyu-next-goal-release-recovery-v1', user_id: environment.dashboard.user_id, authorization: await originalAuthorizationIntent(intent.authorization, intent.owner_goal.id, environment.dashboard.user_id), cash: intent, action } };
}
export async function validateGoalReleaseOriginal(original: PendingOperation, environment: NextState): Promise<GoalReleaseRecovery> {
  check(original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id && original.body_json === JSON.stringify(original.body));
  const recovery = parseGoalReleaseRecovery(original.goal_release_recovery, original.path, original.body, original.epoch_id, original.client_request_id);
  check(recovery.user_id === environment.dashboard.user_id);
  check(await releaseHash(releaseRequestEnvelope(recovery.user_id, recovery.authorization.policy_id, recovery.authorization.body)) === recovery.authorization.request_hash && await releaseHash(recovery.authorization.reviewed_scope) === recovery.authorization.body.reviewed_scope_hash);
  if (recovery.cash) {
    await parseCashIntent(recovery.cash); await parseReleaseResponse(recovery.cash.authorization, recovery.authorization);
    check(recovery.cash.owner_goal.id === recovery.authorization.owner_goal_id);
    if (recovery.action) await parseCashAction(recovery.action, recovery.cash);
  }
  return recovery;
}
export async function readGoalReleaseOriginal(original: PendingOperation, environment: NextState): Promise<GoalReleaseRead> {
  const recovery = await validateGoalReleaseOriginal(original, environment), phase = goalReleasePath(original.path)!;
  const base = { simulation: true as const, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id };
  if (phase.phase === 'AUTHORIZATION') {
    const lookup = await request(`${goalReleasePrefix}/authorizations/${phase.id}/by-key/${original.client_request_id}`, 'GET', undefined, (value) => parseReleaseLookup(envelope(value, environment, 'AUTHORIZATION_LOOKUP'), recovery.authorization));
    if (lookup.status === 'NOT_FOUND_NOT_FINAL') return { ...base, status: 'PENDING' };
    check(lookup.original); return { ...base, status: 'COMPLETED', goal_release: { environment_id: environment.environment_id, recovery, phase: phase.phase, authorization: lookup.original, action: null, resume_original: false } };
  }
  check(recovery.cash);
  let action: CashAction;
  if (phase.phase === 'PREPARE') {
    const lookup = await request(`${goalReleasePrefix}/by-key/${original.client_request_id}`, 'GET', undefined, (value) => parseCashLookup(envelope(value, environment, 'ACTION_LOOKUP'), recovery.cash!, recovery.action));
    if (lookup.status === 'NOT_FOUND_NOT_FINAL') return { ...base, status: 'PENDING' };
    check(lookup.original); action = lookup.original;
  } else action = await request(`${goalReleasePrefix}/actions/${phase.id}`, 'GET', undefined, (value) => parseCashAction(envelope(value, environment, 'ACTION'), recovery.cash!, recovery.action));
  const terminal = phase.phase === 'PREPARE' ? !action.unresolved : action.service_receipt_verified || !action.unresolved && ['INVALIDATED', 'CANCELLED', 'FAILED'].includes(action.original_action_status);
  return { ...base, status: terminal ? 'COMPLETED' : 'PENDING', goal_release: { environment_id: environment.environment_id, recovery, phase: phase.phase, authorization: recovery.cash.authorization, action, resume_original: !terminal && phase.phase === 'EXECUTE' } };
}
