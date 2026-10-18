import { request } from '../api/http';
import { parseLocalActorSession, type LocalActorSession } from '../api/local-actor';
import { releaseUUID5 } from '../api/goal-release-authorizations';
import { spendingCanonicalJson as canonical, spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { assetExact as exact } from './asset-recovery';
import { context } from './policies';
import { parseJointPrepare, parseThinJointPlan, type ThinJointPlan } from './goals';
import type { NextState } from './api';

export const JOINT_QUESTION_PREFIX = '/zhiyu-next/questions/full-joint';
export const JOINT_QUESTION_PROTOCOL = 'zhiyu-full-joint-fixed-question-v1';
const namespace = 'f0528a7d-e513-4bcf-988f-3bcba8a4aee4';
const numberToken = Symbol('original-json-number');
const check: (value: unknown) => asserts value = (value) => { if (!value) throw new Error('联合观察原件、当前来源或原请求未匹配，请保留原定位。'); };
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === 'string');
const ids = (v: unknown): v is string[] => strings(v) && v.every(uuid) && new Set(v).size === v.length;
const revision = (v: unknown, max = 16): v is number => Number.isSafeInteger(v) && Number(v) >= 1 && Number(v) <= max;
type RecordValue = Record<string, unknown>;

/** Use the HTTP helper's original response.text(), never JSON.stringify(parsed DTO).
 * Native reviver source support is required; unsupported browsers fail closed. */
export function jointQuestionRawTree(text: string): RecordValue {
  check(new TextEncoder().encode(text).length <= 64 * 1024 * 1024);
  const parse = JSON.parse as (text: string, reviver: (key: string, value: unknown, ctx?: { source?: string }) => unknown) => unknown;
  const tree = parse(text, (_key, value, ctx) => {
    if (typeof value !== 'number') return value;
    if (!ctx?.source) throw new Error('当前浏览器无法独立核验数字原文，请保留原定位并更新浏览器。');
    check(Number.isFinite(value)); return { [numberToken]: ctx.source };
  });
  check(object(tree)); return tree;
}
function rawCanonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(rawCanonical).join(',')}]`;
  if (object(v)) {
    if (numberToken in v) return String(Reflect.get(v, numberToken));
    const ordered = Object.keys(v).sort((a, b) => {
      const x = Array.from(a, (s) => s.codePointAt(0)!), y = Array.from(b, (s) => s.codePointAt(0)!);
      for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i]! - y[i]!;
      return x.length - y.length;
    });
    return `{${ordered.map((key) => `${JSON.stringify(key)}:${rawCanonical(v[key])}`).join(',')}}`;
  }
  if (v === null || typeof v === 'string' || typeof v === 'boolean') return JSON.stringify(v);
  throw new Error('原件缺少数字原文，无法独立核验。');
}
async function rawHash(v: unknown): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(rawCanonical(v))))].map((b) => b.toString(16).padStart(2, '0')).join('');
}
function rawObject(v: unknown): RecordValue { check(object(v)); return v; }
async function sourceFingerprint(raw: RecordValue): Promise<string> {
  check(raw.source_fingerprint_version === 'full-joint-fixed-source-clock-v1' && Array.isArray(raw.sources) && Array.isArray(raw.policies));
  const basis = { ...rawObject(raw.complete_native_basis) }, snapshot = { ...rawObject(basis.snapshot) };
  delete basis.as_of; delete snapshot.as_of;
  const living = snapshot.living_reserves ?? []; check(Array.isArray(living));
  snapshot.living_reserves = living.map((row) => { const value = { ...rawObject(row) }; delete value.estimation_input_digest; return value; });
  // Preserve absence of an optional living list, just as the original helper does.
  if (!Object.hasOwn(rawObject(basis.snapshot), 'living_reserves')) delete snapshot.living_reserves;
  basis.snapshot = snapshot;
  for (const name of ['exposure', 'income']) if (object(basis[name])) { const value = { ...basis[name] }; delete value.as_of; basis[name] = value; }
  const ordered = (rows: unknown[]) => rows.map(rawObject).sort((a, b) => String(a.id).localeCompare(String(b.id)));
  const native = await rawHash({ basis: { autonomy_basis: basis }, sources: ordered(raw.sources), policies: ordered(raw.policies) });
  const compared: RecordValue = { ...raw, complete_native_basis: { normalized_native_source_fingerprint: native } };
  if (object(compared.allocation_input)) { const input = { ...compared.allocation_input }; delete input.as_of; compared.allocation_input = input; }
  return rawHash(compared);
}

export type JointQuestionStart = { expected_epoch_id: string; client_request_id: string; full_policy_id: string; expected_full_policy_version_id: string; supported_input: 'REGISTERED_CONFIGURATION' };
export type JointQuestionCommand = { expected_epoch_id: string; client_request_id: string; expected_revision: number };
export type JointQuestionPending = { protocol: 'zhiyu-joint-question-pending-v1'; environment_id: string; user_id: string; epoch_id: string; policy_id: string; path: string; body: JointQuestionStart | JointQuestionCommand; body_json: string; session_id: string; previous_receipt_hash: string | null; previous_revision: number | null };
export type JointQuestionLocator = { protocol: 'zhiyu-joint-question-locator-v1'; environment_id: string; user_id: string; epoch_id: string; session_id: string; policy_id: string; revision: number; receipt_hash: string };
export type JointQuestionEvaluation = { status: 'STABLE' | 'UNKNOWN'; plan: ThinJointPlan | null; as_of: string; reasons: string[]; vector_signature: string | null };
export type JointQuestionReceipt = { session_id: string; revision: number; receipt_hash: string; previous_receipt_hash: string | null; command_id: string; command_kind: 'START' | 'REFRESH' | 'REBASE' | 'CLOSE'; original_request: { kind: string; path: string; body: RecordValue }; as_of: string; valid_until: string; source_fingerprint: string; state: 'READY_FOR_REVIEW' | 'UNKNOWN' | 'CLOSED'; authenticated_actor: LocalActorSession['principal']; evaluation: JointQuestionEvaluation };
export type JointQuestionView = { receipt: JointQuestionReceipt; effective_state: JointQuestionReceipt['state'] | 'STALE_RECOMPUTATION_REQUIRED' | 'ARCHIVED'; evaluation: JointQuestionEvaluation | null; current_source_fingerprint: string | null };
const identityFields = ['simulation', 'variant', 'environment_id', 'epoch_id'];
const rawReceipts = new WeakMap<object, string>();
const freshOriginals = new WeakSet<JointQuestionReceipt>();
export const originalJointQuestionResponse = (receipt: JointQuestionReceipt) => rawReceipts.get(receipt) ?? null;

function startBody(v: unknown): asserts v is JointQuestionStart {
  check(object(v) && exact(v, ['expected_epoch_id', 'client_request_id', 'full_policy_id', 'expected_full_policy_version_id', 'supported_input']) && ['expected_epoch_id', 'client_request_id', 'full_policy_id', 'expected_full_policy_version_id'].every((key) => uuid(v[key])) && v.supported_input === 'REGISTERED_CONFIGURATION');
}
function commandPath(path: string) { if (path === `${JOINT_QUESTION_PREFIX}/sessions`) return 'START'; const match = /^\/zhiyu-next\/questions\/full-joint\/sessions\/([0-9a-f-]{36})\/(refresh|close)$/.exec(path); check(match && uuid(match[1])); return match[2] === 'close' ? 'CLOSE' : 'REFRESH'; }
export function parseJointQuestionPending(v: unknown, env: NextState): JointQuestionPending {
  check(object(v) && exact(v, ['protocol', 'environment_id', 'user_id', 'epoch_id', 'policy_id', 'path', 'body', 'body_json', 'session_id', 'previous_receipt_hash', 'previous_revision']) && v.protocol === 'zhiyu-joint-question-pending-v1' && v.environment_id === env.environment_id && v.user_id === env.dashboard.user_id && v.epoch_id === env.epoch_id && uuid(v.policy_id) && uuid(v.session_id) && typeof v.path === 'string' && object(v.body) && v.body.expected_epoch_id === env.epoch_id && uuid(v.body.client_request_id) && v.body_json === JSON.stringify(v.body));
  const kind = commandPath(v.path);
  if (kind === 'START') { startBody(v.body); check(v.body.full_policy_id === v.policy_id && v.previous_receipt_hash === null && v.previous_revision === null); }
  else check(v.path.includes(`/sessions/${v.session_id}/`) && exact(v.body, ['expected_epoch_id', 'client_request_id', 'expected_revision']) && revision(v.body.expected_revision) && v.previous_revision === v.body.expected_revision && digest(v.previous_receipt_hash));
  return v as JointQuestionPending;
}

async function evaluation(v: unknown, raw: RecordValue, env: NextState, session: string): Promise<JointQuestionEvaluation> {
  check(object(v) && exact(v, ['protocol', 'supported_input', 'candidate_count', 'native_preview', 'status', 'stable', 'should_ask', 'vector_signature', 'pending_question', 'preference_question_count', 'divergent_candidate_production', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'requires_separate_whole_financial_confirmation']) && v.protocol === JOINT_QUESTION_PROTOCOL && v.supported_input === 'REGISTERED_CONFIGURATION' && v.candidate_count === 1 && v.pending_question === null && v.preference_question_count === 0 && v.divergent_candidate_production === 'NOT_IMPLEMENTED' && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.requires_separate_whole_financial_confirmation === true);
  const p = v.native_preview; check(object(p) && exact(p, ['simulation', 'bank_authority', 'grants_authority', 'preview_only', 'user_id', 'epoch_id', 'as_of', 'request', 'status', 'registered_goal_ids', 'unresolved_original_action_ids', 'plan', 'input_hash', 'reasons', 'limitations']) && p.simulation === true && p.bank_authority === false && p.grants_authority === false && p.preview_only === true && p.user_id === env.dashboard.user_id && p.epoch_id === env.epoch_id && time(p.as_of) && object(p.request) && p.request.expected_epoch_id === env.epoch_id && p.request.idempotency_key === `joint-question-native:${session.replaceAll('-', '')}` && ids(p.registered_goal_ids) && ids(p.unresolved_original_action_ids) && digest(p.input_hash) && strings(p.reasons) && strings(p.limitations));
  parseJointPrepare(p.request);
  check(['READY_TO_REVIEW', 'BLOCKED', 'UNKNOWN'].includes(String(p.status)));
  if (v.status === 'UNKNOWN') { check(p.status !== 'READY_TO_REVIEW' && p.plan === null && v.vector_signature === null && v.stable === null && v.should_ask === null); return { status: 'UNKNOWN', plan: null, as_of: p.as_of, reasons: p.reasons, vector_signature: null }; }
  check(v.status === 'STABLE' && v.stable === true && v.should_ask === false && digest(v.vector_signature) && p.status === 'READY_TO_REVIEW' && p.reasons.length === 0 && p.unresolved_original_action_ids.length === 0 && object(p.plan));
  const plan = p.plan, rawPlan = rawObject(rawObject(raw.native_preview).plan);
  check(exact(plan, ['protocol', 'simulation', 'bank_authority', 'funds_reserved', 'execution_mode', 'cross_operation_atomicity', 'plan_id', 'user_id', 'epoch_id', 'prepared_at', 'expires_at', 'inputs', 'allocation_input', 'allocation', 'full_binding_hash', 'total_allocation_cents', 'children', 'plan_hash']) && object(plan.inputs) && object(plan.allocation_input) && Array.isArray(plan.allocation_input.hard_protection_points) && plan.allocation_input.hard_protection_points.length === 1098 && Array.isArray(plan.allocation_input.goals) && object(plan.inputs.scope) && canonical(plan.inputs.request) === canonical(p.request));
  const { plan_hash: _hash, ...unhashed } = rawPlan; void _hash; check(await rawHash(unhashed) === plan.plan_hash && await rawHash(rawPlan.inputs) === p.input_hash);
  const { inputs: _inputs, allocation_input: _input, ...summary } = plan; void _inputs; void _input;
  const thin = await parseThinJointPlan({ ...summary, original_request: plan.inputs.request, complete_original_retained: true, current_execution_goal_limit: 8, current_execution_validation: 'QUESTION_OBSERVATION_NOT_FINANCIAL_ACCEPTANCE' }, env);
  check(thin.protocol === 'registered-joint-goal-execution-source-dag-v4' && thin.prepared_at === p.as_of && canonical([...p.registered_goal_ids].sort()) === canonical(thin.allocation.goals.map((g) => g.goal_id).sort()) && canonical(plan.allocation_input.goals.map((g) => rawObject(g).goal_id).sort()) === canonical([...p.registered_goal_ids].sort()));
  const rawChildren = rawPlan.children; check(Array.isArray(rawChildren));
  const signature = await rawHash({ protocol: JOINT_QUESTION_PROTOCOL, registered_goal_ids: rawObject(raw.native_preview).registered_goal_ids, allocation: (() => { const a = { ...rawObject(rawPlan.allocation) }; delete a.input_hash; return a; })(), total_cents: rawPlan.total_allocation_cents, children: rawChildren.map((child, i) => { const c = rawObject(child), effect = { ...rawObject(rawObject(c.command).effect) }; effect.operation_id = `derived_joint_child:${i + 1}`; delete effect.valid_from; delete effect.expires_at; return { number: c.child_number, goal_id: c.goal_id, effect, model_evidence_id: c.original_model_evidence_id, model_evidence_hash: c.original_model_evidence_hash }; }), scope: rawObject(rawPlan.inputs).scope, execution_mode: rawPlan.execution_mode, cross_operation_atomicity: rawPlan.cross_operation_atomicity, requires_separate_whole_financial_confirmation: true });
  check(signature === v.vector_signature); return { status: 'STABLE', plan: thin, as_of: p.as_of, reasons: [], vector_signature: signature };
}
export async function parseJointQuestionReceipt(v: unknown, raw: RecordValue, env: NextState, originalText: string, expected?: JointQuestionPending): Promise<JointQuestionReceipt> {
  check(object(v) && exact(v, ['protocol', 'user_id', 'epoch_id', 'session_id', 'revision', 'previous_receipt_hash', 'command_id', 'command_kind', 'authenticated_actor', 'original_request', 'as_of', 'valid_until', 'source_manifest', 'source_fingerprint', 'evaluation', 'state', 'receipt_hash', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited']) && v.protocol === JOINT_QUESTION_PROTOCOL && v.user_id === env.dashboard.user_id && v.epoch_id === env.epoch_id && uuid(v.session_id) && uuid(v.command_id) && revision(v.revision, 17) && time(v.as_of) && time(v.valid_until) && digest(v.receipt_hash) && digest(v.source_fingerprint) && object(v.source_manifest) && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false);
  const { receipt_hash: _hash, ...unhashed } = raw; void _hash; check(await rawHash(unhashed) === v.receipt_hash && await sourceFingerprint(rawObject(raw.source_manifest)) === v.source_fingerprint);
  const principal = parseLocalActorSession({ simulation: true, principal: v.authenticated_actor, bank_authority: false, confirms_financial_action: false }).principal;
  check(principal.role === 'USER' && principal.user_id === v.user_id && Date.parse(principal.issued_at) <= Date.parse(v.as_of) && Date.parse(v.as_of) < Date.parse(principal.expires_at) && object(v.original_request) && exact(v.original_request, ['kind', 'path', 'body']) && typeof v.original_request.path === 'string' && object(v.original_request.body));
  const path = v.original_request.path.replace(/^\/api\/v1/, ''), body = v.original_request.body, kind = commandPath(path);
  check(v.original_request.kind === `FULL_JOINT_FIXED_${kind}` && body.client_request_id === v.command_id && body.expected_epoch_id === env.epoch_id);
  if (kind === 'START') { startBody(body); check(v.command_kind === 'START' && v.revision === 1 && v.previous_receipt_hash === null && v.session_id === await releaseUUID5(namespace, `${v.user_id}:${v.epoch_id}:${v.command_id}`)); }
  else check(path.includes(`/sessions/${v.session_id}/`) && exact(body, ['expected_epoch_id', 'client_request_id', 'expected_revision']) && revision(body.expected_revision) && v.revision === body.expected_revision + 1 && digest(v.previous_receipt_hash) && (v.command_kind === kind || kind === 'REFRESH' && v.command_kind === 'REBASE'));
  if (expected) check(v.session_id === expected.session_id && v.command_id === expected.body.client_request_id && canonical(body) === canonical(expected.body) && path === expected.path && v.previous_receipt_hash === expected.previous_receipt_hash);
  const current = await evaluation(v.evaluation, rawObject(raw.evaluation), env, v.session_id);
  check(['READY_FOR_REVIEW', 'UNKNOWN', 'CLOSED'].includes(String(v.state)) && v.source_manifest.user_id === v.user_id && v.source_manifest.epoch_id === v.epoch_id && v.source_manifest.vector_signature === current.vector_signature);
  if (current.plan) {
    const preview = rawObject(rawObject(raw.evaluation).native_preview), plan = rawObject(preview.plan);
    const manifest = rawObject(raw.source_manifest);
    check(rawCanonical(manifest.full_scope) === rawCanonical(rawObject(plan.inputs).scope) && rawCanonical(manifest.allocation_input) === rawCanonical(plan.allocation_input) && rawCanonical(manifest.registered_goal_ids) === rawCanonical(preview.registered_goal_ids));
  }
  if (v.state === 'CLOSED') check(v.command_kind === 'CLOSE');
  else check(v.command_kind !== 'CLOSE' && v.revision <= 16 && Date.parse(v.as_of) < Date.parse(v.valid_until) && Date.parse(v.valid_until) <= Date.parse(v.as_of) + 900000 && current.as_of === v.as_of && v.state === (current.status === 'STABLE' ? 'READY_FOR_REVIEW' : 'UNKNOWN'));
  if (kind === 'START' && current.plan) check(current.plan.original_request.full_policy_id === body.full_policy_id && current.plan.original_request.expected_full_policy_version_id === body.expected_full_policy_version_id);
  const receipt = { ...v, evaluation: current } as JointQuestionReceipt; rawReceipts.set(receipt, originalText); assertMoneyFields(v); return receipt;
}
async function wire(path: string, env: NextState, method = 'GET', body?: unknown) {
  return request<{ value: RecordValue; raw: RecordValue; text: string }>(path, method, body, (v, text) => { context(v, env); return { value: v, raw: jointQuestionRawTree(text), text }; });
}
export async function readJointQuestion(locator: JointQuestionLocator, env: NextState): Promise<JointQuestionView> {
  check(locator.environment_id === env.environment_id && locator.user_id === env.dashboard.user_id && locator.epoch_id === env.epoch_id && uuid(locator.session_id));
  const { value: v, raw, text } = await wire(`${JOINT_QUESTION_PREFIX}/sessions/${locator.session_id}`, env);
  check(exact(v, [...identityFields, 'protocol', 'current_revision', 'effective_state', 'current_source_fingerprint', 'current_evaluation', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'current_read_required', 'receipt_is_current_authority', 'requires_separate_whole_financial_confirmation', ...(v.current_read_error === undefined ? [] : ['current_read_error'])]) && v.protocol === JOINT_QUESTION_PROTOCOL && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.current_read_required === true && v.receipt_is_current_authority === false && v.requires_separate_whole_financial_confirmation === true);
  const receipt = await parseJointQuestionReceipt(v.current_revision, rawObject(raw.current_revision), env, text);
  check(receipt.session_id === locator.session_id && (receipt.revision === locator.revision && receipt.receipt_hash === locator.receipt_hash || receipt.revision === locator.revision + 1 && receipt.previous_receipt_hash === locator.receipt_hash));
  check(['READY_FOR_REVIEW', 'UNKNOWN', 'CLOSED', 'ARCHIVED', 'STALE_RECOMPUTATION_REQUIRED'].includes(String(v.effective_state)));
  check(v.current_source_fingerprint === null || digest(v.current_source_fingerprint));
  const current = v.current_evaluation === null ? null : await evaluation(v.current_evaluation, rawObject(raw.current_evaluation), env, locator.session_id);
  if (current?.plan) check(current.plan.original_request.full_policy_id === locator.policy_id);
  if (['READY_FOR_REVIEW', 'UNKNOWN'].includes(String(v.effective_state))) check(v.effective_state === receipt.state && v.current_source_fingerprint === receipt.source_fingerprint && current && current.status === receipt.evaluation.status && Date.parse(current.as_of) < Date.parse(receipt.valid_until));
  if (['CLOSED', 'ARCHIVED'].includes(String(v.effective_state))) check(current === null && v.current_source_fingerprint === null);
  if (v.current_read_error !== undefined) check(v.effective_state === 'STALE_RECOMPUTATION_REQUIRED' && object(v.current_read_error) && exact(v.current_read_error, ['code', 'message']) && typeof v.current_read_error.code === 'string' && typeof v.current_read_error.message === 'string');
  return { receipt, evaluation: current, effective_state: v.effective_state as JointQuestionView['effective_state'], current_source_fingerprint: v.current_source_fingerprint as string | null };
}
export async function lookupJointQuestion(pending: JointQuestionPending, env: NextState): Promise<JointQuestionReceipt | null> {
  parseJointQuestionPending(pending, env);
  const { value: v, raw, text } = await wire(`${JOINT_QUESTION_PREFIX}/commands/${env.epoch_id}/by-key/${pending.body.client_request_id}`, env);
  check(exact(v, [...identityFields, 'protocol', 'status', 'client_request_id', 'original_request', 'original_receipt', 'not_found_is_final', 'replacement_allowed', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'current_read_required', 'receipt_is_current_authority']) && v.protocol === JOINT_QUESTION_PROTOCOL && v.client_request_id === pending.body.client_request_id && v.not_found_is_final === false && v.replacement_allowed === false && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.current_read_required === true && v.receipt_is_current_authority === false);
  if (v.status === 'NOT_FOUND_NOT_FINAL') { check(v.original_request === null && v.original_receipt === null); return null; }
  check(v.status === 'RECORDED'); const receipt = await parseJointQuestionReceipt(v.original_receipt, rawObject(raw.original_receipt), env, text, pending); check(canonical(v.original_request) === canonical(receipt.original_request)); freshOriginals.add(receipt); return receipt;
}
export async function postJointQuestion(pending: JointQuestionPending, env: NextState): Promise<void> {
  parseJointQuestionPending(pending, env); const { value: v, raw, text } = await wire(pending.path, env, 'POST', pending.body);
  check(exact(v, [...identityFields, 'current_read_required', 'receipt_is_current_authority', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'original_receipt']) && v.current_read_required === true && v.receipt_is_current_authority === false && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false);
  await parseJointQuestionReceipt(v.original_receipt, rawObject(raw.original_receipt), env, text, pending);
}
const pendingKey = (env: NextState) => `zhiyu-joint-question-pending-v1:${env.environment_id}:${env.epoch_id}`;
const locatorKey = (env: NextState) => `zhiyu-joint-question-locator-v1:${env.environment_id}:${env.epoch_id}`;
export function retainJointQuestion(p: JointQuestionPending, env: NextState) { parseJointQuestionPending(p, env); check(localStorage.getItem(pendingKey(env)) === null); localStorage.setItem(pendingKey(env), JSON.stringify(p)); }
export function restoreJointQuestion(env: NextState): JointQuestionPending | null { const text = localStorage.getItem(pendingKey(env)); return text === null ? null : parseJointQuestionPending(JSON.parse(text), env); }
export function readJointQuestionLocator(env: NextState): JointQuestionLocator | null {
  const text = localStorage.getItem(locatorKey(env)); if (text === null) return null; const v: unknown = JSON.parse(text);
  check(object(v) && exact(v, ['protocol', 'environment_id', 'user_id', 'epoch_id', 'session_id', 'policy_id', 'revision', 'receipt_hash']) && v.protocol === 'zhiyu-joint-question-locator-v1' && v.environment_id === env.environment_id && v.epoch_id === env.epoch_id && v.user_id === env.dashboard.user_id && uuid(v.session_id) && uuid(v.policy_id) && revision(v.revision, 17) && digest(v.receipt_hash)); return v as JointQuestionLocator;
}
export function jointQuestionLocator(receipt: JointQuestionReceipt, env: NextState, policy: string): JointQuestionLocator { return { protocol: 'zhiyu-joint-question-locator-v1', environment_id: env.environment_id, user_id: env.dashboard.user_id, epoch_id: env.epoch_id, session_id: receipt.session_id, policy_id: policy, revision: receipt.revision, receipt_hash: receipt.receipt_hash }; }
export function completeJointQuestion(p: JointQuestionPending, receipt: JointQuestionReceipt, env: NextState, policy: string) { check(freshOriginals.has(receipt) && localStorage.getItem(pendingKey(env)) === JSON.stringify(p) && receipt.command_id === p.body.client_request_id && policy === p.policy_id); localStorage.setItem(locatorKey(env), JSON.stringify(jointQuestionLocator(receipt, env, policy))); localStorage.removeItem(pendingKey(env)); }
export async function makeJointQuestionPending(env: NextState, policy: { policy_id: string; current_version_id?: string }, current?: JointQuestionView, kind: 'refresh' | 'close' = 'refresh'): Promise<JointQuestionPending> {
  const client = crypto.randomUUID(), session = current?.receipt.session_id ?? await releaseUUID5(namespace, `${env.dashboard.user_id}:${env.epoch_id}:${client}`);
  if (!current) check(uuid(policy.current_version_id));
  const body: JointQuestionStart | JointQuestionCommand = current ? { expected_epoch_id: env.epoch_id, client_request_id: client, expected_revision: current.receipt.revision } : { expected_epoch_id: env.epoch_id, client_request_id: client, full_policy_id: policy.policy_id, expected_full_policy_version_id: policy.current_version_id!, supported_input: 'REGISTERED_CONFIGURATION' };
  return parseJointQuestionPending({ protocol: 'zhiyu-joint-question-pending-v1', environment_id: env.environment_id, user_id: env.dashboard.user_id, epoch_id: env.epoch_id, policy_id: policy.policy_id, path: `${JOINT_QUESTION_PREFIX}/sessions${current ? `/${session}/${kind}` : ''}`, body, body_json: JSON.stringify(body), session_id: session, previous_receipt_hash: current?.receipt.receipt_hash ?? null, previous_revision: current?.receipt.revision ?? null }, env);
}

// Reuse the original complete native/raw readers; their function bodies are unchanged.
export { rawHash as jointQuestionRawHash, sourceFingerprint as jointQuestionSourceFingerprint, evaluation as parseJointQuestionEvaluation };
