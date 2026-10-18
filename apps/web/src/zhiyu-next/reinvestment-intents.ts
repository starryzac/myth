import { request } from '../api/http';
import { multiConfigurationHash as hash } from '../api/full-policy-change-multi';
import { spendingCanonicalJson as canonical, spendingDigest as digest, spendingUUID as uuid } from '../api/spending-evidence';
import { object } from '../features/policy-form';
import type { MaturityAction } from '../api/full-maturity-execution';
import type { NextState } from './api';
import { context } from './policies';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
import { parsePending, operationKey, type PendingOperation } from './operation';
import { parseReinvestmentRequest, parseReinvestmentRecovery, isReinvestmentPrepare, type ReinvestmentRequest, type ReinvestmentRecovery } from './reinvestment-recovery';
import { parseReinvestmentIntent, readReinvestmentSource, type ReinvestmentIntent } from './reinvestments';

export const INTENT_PROTOCOL = 'maturity-settled-reinvestment-intent-v2';
export const INTENT_REFRESH_PATH = '/zhiyu-next/assets/reinvestments/intents/refresh';
const base = '/zhiyu-next/assets/reinvestments/intents';
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const identity = ['simulation', 'variant', 'environment_id', 'epoch_id'];
export type IntentRefreshRequest = { expected_epoch_id: string; maturity_action_id: string; expected_intent_hash: string; replacement_request: ReinvestmentRequest; client_request_id: string };
export type IntentRevision = { protocol: typeof INTENT_PROTOCOL; user_id: string; epoch_id: string; revision: number; base_intent_hash: string; previous_intent_hash: string; original_request: IntentRefreshRequest; current_intent: ReinvestmentIntent; revision_hash: string; bank_authority: false; financial_plan_created: false; financial_confirmation_created: false; original_submission_absent_at_refresh: true };
export type IntentHistory = { status: 'INTENT_UNPREPARED' | 'ORIGINAL_SUBMISSION_PRESENT' | 'NOT_FOUND_NOT_FINAL'; base_intent: ReinvestmentIntent | null; current_intent: ReinvestmentIntent | null; intent_hash: string | null; revisions: IntentRevision[]; source: MaturityAction };
export type IntentRefreshCommand = { protocol: 'zhiyu-next-reinvestment-intent-command-v2'; environment_id: string; epoch_id: string; body: IntentRefreshRequest; source_recovery: ReinvestmentRecovery };
export type IntentRefreshRead = { status: 'PENDING' | 'COMPLETED' | 'REJECTED'; history: IntentHistory | null; rejection: string | null };
const pendingPrefix = 'zhiyu-next-reinvestment-intent-command-v2:';
const commandKey = (environment: NextState) => `${pendingPrefix}${environment.environment_id}`;

export function parseIntentRefreshRequest(value: unknown): IntentRefreshRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'maturity_action_id', 'expected_intent_hash', 'replacement_request', 'client_request_id']) && uuid(value.expected_epoch_id) && uuid(value.maturity_action_id) && digest(value.expected_intent_hash) && uuid(value.client_request_id));
  const replacement = parseReinvestmentRequest(value.replacement_request);
  check(replacement.expected_epoch_id === value.expected_epoch_id && replacement.maturity_action_id === value.maturity_action_id && replacement.client_request_id !== value.client_request_id);
  return value as IntentRefreshRequest;
}
function parseCommand(value: unknown, environment: NextState): IntentRefreshCommand {
  check(object(value) && exact(value, ['protocol', 'environment_id', 'epoch_id', 'body', 'source_recovery']) && value.protocol === 'zhiyu-next-reinvestment-intent-command-v2' && value.environment_id === environment.environment_id && value.epoch_id === environment.epoch_id);
  const body = parseIntentRefreshRequest(value.body); check(body.expected_epoch_id === environment.epoch_id);
  const recovery = parseReinvestmentRecovery(value.source_recovery, '/zhiyu-next/assets/reinvestments/prepare', body.replacement_request, environment.epoch_id, body.replacement_request.client_request_id);
  check(recovery.user_id === environment.dashboard.user_id && recovery.portfolio_id === null && recovery.reviewed_portfolio_hash === null);
  return value as IntentRefreshCommand;
}
export function restoreIntentRefreshCommand(environment: NextState): IntentRefreshCommand | null {
  let found: IntentRefreshCommand | null = null;
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i); if (!key?.startsWith(pendingPrefix)) continue;
    check(key === commandKey(environment) && found === null);
    found = parseCommand(JSON.parse(localStorage.getItem(key)!), environment);
  }
  return found;
}
export function retainIntentRefreshCommand(command: IntentRefreshCommand, environment: NextState): void {
  parseCommand(command, environment); const old = restoreIntentRefreshCommand(environment);
  check(old === null || same(old, command));
  try { localStorage.setItem(commandKey(environment), JSON.stringify(command)); }
  catch { throw new Error('未能保留意图更新原命令，本次请求尚未发送。'); }
}
export function clearIntentRefreshCommand(command: IntentRefreshCommand, environment: NextState): void {
  check(localStorage.getItem(commandKey(environment)) === JSON.stringify(command));
  localStorage.removeItem(commandKey(environment));
}
async function parseRevision(value: unknown, source: MaturityAction, environment: NextState): Promise<IntentRevision> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'epoch_id', 'revision', 'base_intent_hash', 'previous_intent_hash', 'original_request', 'current_intent', 'revision_hash', 'bank_authority', 'financial_plan_created', 'financial_confirmation_created', 'original_submission_absent_at_refresh']) && value.protocol === INTENT_PROTOCOL && value.user_id === environment.dashboard.user_id && value.epoch_id === environment.epoch_id && Number.isSafeInteger(value.revision) && Number(value.revision) >= 1 && Number(value.revision) <= 16 && digest(value.base_intent_hash) && digest(value.previous_intent_hash) && digest(value.revision_hash) && value.bank_authority === false && value.financial_plan_created === false && value.financial_confirmation_created === false && value.original_submission_absent_at_refresh === true);
  const original = parseIntentRefreshRequest(value.original_request); const current = parseReinvestmentIntent(value.current_intent, source, environment);
  check(original.expected_epoch_id === environment.epoch_id && original.maturity_action_id === source.action_id && original.expected_intent_hash === value.previous_intent_hash && same(original.replacement_request, current.original_request));
  const { revision_hash: _hash, ...unhashed } = value; void _hash; check(await hash(unhashed) === value.revision_hash);
  return value as IntentRevision;
}
export async function parseIntentHistory(raw: unknown, source: MaturityAction, environment: NextState): Promise<IntentHistory> {
  check(object(raw)); context(raw, environment);
  check(exact(raw, [...identity, 'protocol', 'status', 'base_intent', 'current_intent', 'intent_hash', 'revisions', 'bank_authority', 'absence_is_final']) && raw.protocol === INTENT_PROTOCOL && raw.bank_authority === false && raw.absence_is_final === false && Array.isArray(raw.revisions) && raw.revisions.length <= 16 && ['INTENT_UNPREPARED', 'ORIGINAL_SUBMISSION_PRESENT', 'NOT_FOUND_NOT_FINAL'].includes(String(raw.status)));
  if (raw.status === 'NOT_FOUND_NOT_FINAL') { check(raw.base_intent === null && raw.current_intent === null && raw.intent_hash === null && raw.revisions.length === 0); return { status: raw.status, base_intent: null, current_intent: null, intent_hash: null, revisions: [], source }; }
  const first = parseReinvestmentIntent(raw.base_intent, source, environment); const baseHash = await hash(first);
  let prior = first; let priorHash = baseHash; const revisions: IntentRevision[] = [];
  const commandIds = new Set<string>();
  for (const [i, value] of raw.revisions.entries()) {
    const revision = await parseRevision(value, source, environment); const next = revision.current_intent;
    check(revision.revision === i + 1 && revision.base_intent_hash === baseHash && revision.previous_intent_hash === priorHash && !commandIds.has(revision.original_request.client_request_id));
    check(next.original_request.full_policy_id === first.original_request.full_policy_id && next.original_request.mvp_asset_policy_id === first.original_request.mvp_asset_policy_id && same(next.original_maturity_event, first.original_maturity_event) && next.original_native_request.idempotency_key === first.original_native_request.idempotency_key && Date.parse(next.captured_at) >= Date.parse(prior.captured_at));
    commandIds.add(revision.original_request.client_request_id); revisions.push(revision); prior = next; priorHash = revision.revision_hash;
  }
  const current = parseReinvestmentIntent(raw.current_intent, source, environment);
  check(same(current, prior) && raw.intent_hash === priorHash);
  return { status: raw.status as IntentHistory['status'], base_intent: first, current_intent: current, intent_hash: priorHash, revisions, source };
}
export async function readIntentHistory(recovery: ReinvestmentRecovery, environment: NextState): Promise<IntentHistory> {
  const source = await readReinvestmentSource(recovery, environment);
  const raw = await request(`${base}/by-maturity-action/${source.action_id}`, 'GET');
  return parseIntentHistory(raw, source, environment);
}
export async function readIntentRefreshOriginal(command: IntentRefreshCommand, environment: NextState): Promise<IntentRefreshRead> {
  parseCommand(command, environment);
  const source = await readReinvestmentSource(command.source_recovery, environment);
  const raw = await request(`${base}/by-command/${command.body.client_request_id}`, 'GET'); context(raw, environment);
  check(exact(raw, [...identity, 'protocol', 'status', 'original_request', 'original_result', 'superseded', 'bank_authority', 'absence_is_final']) && raw.protocol === INTENT_PROTOCOL && raw.bank_authority === false && raw.absence_is_final === false);
  if (raw.status === 'NOT_FOUND_NOT_FINAL') { check(raw.original_request === null && raw.original_result === null && raw.superseded === null); return { status: 'PENDING', history: null, rejection: null }; }
  check(same(parseIntentRefreshRequest(raw.original_request), command.body) && object(raw.original_result)); const result = raw.original_result;
  if (raw.status === 'REJECTED') {
    check(exact(result, ['error', 'bank_authority', 'financial_plan_created', 'financial_confirmation_created', 'absence_is_final']) && result.bank_authority === false && result.financial_plan_created === false && result.financial_confirmation_created === false && result.absence_is_final === false && raw.superseded === null && object(result.error) && exact(result.error, ['code', 'message', 'status_code']) && typeof result.error.code === 'string' && typeof result.error.message === 'string' && Number.isSafeInteger(result.error.status_code) && Number(result.error.status_code) >= 400 && Number(result.error.status_code) < 600);
    return { status: 'REJECTED', history: null, rejection: result.error.message };
  }
  check(raw.status === 'RECORDED' && exact(result, ['protocol', 'status', 'client_request_id', 'revision', 'bank_authority', 'financial_plan_created', 'financial_confirmation_created']) && result.protocol === INTENT_PROTOCOL && result.status === 'RECORDED' && result.client_request_id === command.body.client_request_id && result.bank_authority === false && result.financial_plan_created === false && result.financial_confirmation_created === false && typeof raw.superseded === 'boolean');
  const revision = await parseRevision(result.revision, source, environment); check(same(revision.original_request, command.body));
  const history = await readIntentHistory(command.source_recovery, environment);
  check(history.revisions.length >= revision.revision && same(history.revisions[revision.revision - 1], revision) && raw.superseded === (history.revisions.length > revision.revision));
  return { status: 'COMPLETED', history, rejection: null };
}
export function matchingUnsubmittedPending(pending: PendingOperation | null, action: string): boolean {
  return !!pending && isReinvestmentPrepare(pending.path) && pending.reinvestment_recovery?.prepare_request.maturity_action_id === action && pending.reinvestment_recovery.portfolio_id === null && pending.reinvestment_recovery.reviewed_portfolio_hash === null;
}
export function verifyIntentSelection(history: IntentHistory, body: ReinvestmentRequest): void {
  check(history.status === 'INTENT_UNPREPARED' && history.current_intent && same(body, history.current_intent.original_request));
}
export function isHistoricalIntentRequest(history: IntentHistory, body: Record<string, unknown>): boolean {
  return [history.base_intent, ...history.revisions.map(row => row.current_intent)].some(intent => intent && same(intent.original_request, body));
}
export function adoptSupersededIntentPending(previous: PendingOperation, next: PendingOperation, history: IntentHistory): void {
  parsePending(previous); parsePending(next); verifyIntentSelection(history, parseReinvestmentRequest(next.body));
  check(matchingUnsubmittedPending(previous, history.source.action_id) && matchingUnsubmittedPending(next, history.source.action_id) && previous.environment_id === next.environment_id && previous.epoch_id === next.epoch_id && history.revisions.length > 0);
  const prior = [history.base_intent, ...history.revisions.slice(0, -1).map(row => row.current_intent)];
  check(prior.some(intent => intent && same(intent.original_request, previous.body)) && !same(previous.body, next.body) && same(previous.reinvestment_recovery!.maturity_prepare_request, next.reinvestment_recovery!.maturity_prepare_request));
  const retained = localStorage.getItem(operationKey(previous.environment_id)); check(retained === JSON.stringify(previous));
  const archiveKey = `zhiyu-next-superseded-unsubmitted-intent-v2:${previous.environment_id}:${previous.epoch_id}:${previous.client_request_id}`;
  const archive = JSON.stringify({ status: 'SUPERSEDED_CHOICE_NOT_FINANCIAL_RESULT', original_pending_json: retained, source_action_id: history.source.action_id, observed_supersession_hash: history.intent_hash });
  const oldArchive = localStorage.getItem(archiveKey);
  if (oldArchive !== null) { const priorArchive: unknown = JSON.parse(oldArchive); check(object(priorArchive) && exact(priorArchive, ['status', 'original_pending_json', 'source_action_id', 'observed_supersession_hash']) && priorArchive.status === 'SUPERSEDED_CHOICE_NOT_FINANCIAL_RESULT' && priorArchive.original_pending_json === retained && priorArchive.source_action_id === history.source.action_id && history.revisions.some(row => row.revision_hash === priorArchive.observed_supersession_hash)); }
  try { if (oldArchive === null) localStorage.setItem(archiveKey, archive); localStorage.setItem(operationKey(previous.environment_id), JSON.stringify(next)); }
  catch { throw new Error('旧选择原件或新版请求未能保留；不会发送新版准备。'); }
}
