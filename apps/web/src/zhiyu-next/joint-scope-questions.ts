import { request } from '../api/http';
import { parseLocalActorSession } from '../api/local-actor';
import { releaseUUID5 } from '../api/goal-release-authorizations';
import { spendingCanonicalJson as canonical, spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { object } from '../features/policy-form';
import { assetExact as exact } from './asset-recovery';
import { context } from './policies';
import { jointQuestionRawTree, jointQuestionRawHash as hash, jointQuestionSourceFingerprint, parseJointQuestionEvaluation, type JointQuestionEvaluation } from './joint-questions';
import type { NextState } from './api';

export const SCOPE_PREFIX = '/zhiyu-next/questions/full-joint-scopes';
export const SCOPE_PROTOCOL = 'zhiyu-full-joint-scope-question-v1';
const namespace = 'bc2ef236-d8da-4fb0-a77d-a751f5371a2b';
type RecordValue = Record<string, unknown>;
const check: (v: unknown) => asserts v = (v) => { if (!v) throw new Error('联合规则选择原件、来源或原请求不同，请保留原定位。'); };
const rawObject = (v: unknown): RecordValue => { check(object(v)); return v; };
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const rev = (v: unknown, max = 16): v is number => Number.isSafeInteger(v) && Number(v) >= 1 && Number(v) <= max;
const identityFields = ['simulation', 'variant', 'environment_id', 'epoch_id'];
export type ScopeReference = { full_policy_id: string; expected_full_policy_version_id: string };
type Start = { expected_epoch_id: string; client_request_id: string; alternatives: ScopeReference[]; supported_input: 'EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES' };
type Command = { expected_epoch_id: string; client_request_id: string; expected_revision: number };
type Answer = Command & { expected_source_fingerprint: string; expected_question_hash: string; selected_full_policy_id: string };
export type ScopeLocator = { protocol: 'zhiyu-joint-scope-locator-v1'; environment_id: string; user_id: string; epoch_id: string; session_id: string; start_command_id: string; alternatives: ScopeReference[]; revision: number; receipt_hash: string };
export type ScopePending = ScopeLocator & { path: string; body: Start | Command | Answer; body_json: string; previous_receipt_hash: string | null };
export type ScopeWorld = { policy_id: string; version_id: string; native: JointQuestionEvaluation; economic_signature: string | null; source_fingerprint: string };
export type ScopeQuestion = { wording: string; question_hash: string; source_fingerprint: string; choices: string[] };
export type ScopeEvaluation = { worlds: ScopeWorld[]; source_fingerprint: string; selected_policy_id: string | null; status: 'STABLE' | 'DIVERGENT' | 'UNKNOWN'; question: ScopeQuestion | null; as_of: string };
export type ScopeReceipt = { session_id: string; revision: number; receipt_hash: string; previous_receipt_hash: string | null; command_id: string; command_kind: 'START' | 'ANSWER' | 'REFRESH' | 'REBASE' | 'CLOSE'; state: 'READY_FOR_REVIEW' | 'AWAITING_INPUT' | 'UNKNOWN' | 'CLOSED'; as_of: string; valid_until: string; source_fingerprint: string; evaluation: ScopeEvaluation };
export type ScopeView = { receipt: ScopeReceipt; evaluation: ScopeEvaluation | null; effective_state: ScopeReceipt['state'] | 'STALE_RECOMPUTATION_REQUIRED' | 'ARCHIVED' };
export type ScopeRejection = { record_kind: 'NONFINANCIAL_REJECTION'; command_id: string; rejection_hash: string; code: 'JOINT_VERSION_CHANGED' | 'QUESTION_WIRE_CAPACITY_EXCEEDED' | 'STALE_QUESTION_REVISION' | 'STALE_QUESTION_ANSWER'; message: string; http_status: 409 | 413 };
const originalTexts = new WeakMap<ScopeReceipt, string>();
const originalGets = new WeakSet<ScopeReceipt>();
const rejectionTexts = new WeakMap<ScopeRejection, string>();
const rejectionGets = new WeakSet<ScopeRejection>();
export const originalScopeResponse = (receipt: ScopeReceipt) => originalTexts.get(receipt) ?? null;

function refs(v: unknown): asserts v is ScopeReference[] {
  check(Array.isArray(v) && v.length === 2 && v.every((r) => object(r) && exact(r, ['full_policy_id', 'expected_full_policy_version_id']) && uuid(r.full_policy_id) && uuid(r.expected_full_policy_version_id)) && v[0].full_policy_id !== v[1].full_policy_id);
}
const sorted = (values: ScopeReference[]) => [...values].sort((a, b) => a.full_policy_id.localeCompare(b.full_policy_id));
function commandKind(path: string): 'START' | 'ANSWER' | 'REFRESH' | 'CLOSE' {
  if (path === `${SCOPE_PREFIX}/sessions`) return 'START';
  const match = /^\/zhiyu-next\/questions\/full-joint-scopes\/sessions\/([0-9a-f-]{36})\/(answers|refresh|close)$/.exec(path);
  check(match && uuid(match[1])); return match[2] === 'answers' ? 'ANSWER' : match[2] === 'close' ? 'CLOSE' : 'REFRESH';
}
function bodyShape(v: unknown, kind: ReturnType<typeof commandKind>): asserts v is Start | Command | Answer {
  check(object(v) && uuid(v.expected_epoch_id) && uuid(v.client_request_id));
  if (kind === 'START') { check(exact(v, ['expected_epoch_id', 'client_request_id', 'alternatives', 'supported_input']) && v.supported_input === 'EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES'); refs(v.alternatives); }
  else {
    check(exact(v, ['expected_epoch_id', 'client_request_id', 'expected_revision', ...(kind === 'ANSWER' ? ['expected_source_fingerprint', 'expected_question_hash', 'selected_full_policy_id'] : [])]) && rev(v.expected_revision));
    if (kind === 'ANSWER') check(digest(v.expected_source_fingerprint) && digest(v.expected_question_hash) && uuid(v.selected_full_policy_id));
  }
}
function locatorShape(v: unknown, env: NextState): asserts v is ScopeLocator {
  check(object(v) && v.protocol === 'zhiyu-joint-scope-locator-v1' && v.environment_id === env.environment_id && v.user_id === env.dashboard.user_id && v.epoch_id === env.epoch_id && uuid(v.session_id) && uuid(v.start_command_id) && rev(v.revision, 17) && digest(v.receipt_hash)); refs(v.alternatives);
}
export function parseScopePending(v: unknown, env: NextState): ScopePending {
  check(object(v) && exact(v, ['protocol', 'environment_id', 'user_id', 'epoch_id', 'session_id', 'start_command_id', 'alternatives', 'revision', 'receipt_hash', 'path', 'body', 'body_json', 'previous_receipt_hash'])); locatorShape(v, env);
  const record = v as ScopePending; check(typeof record.path === 'string'); const kind = commandKind(record.path); bodyShape(record.body, kind);
  check(record.body.expected_epoch_id === env.epoch_id && record.body_json === JSON.stringify(record.body));
  if (kind === 'START') check('alternatives' in record.body && canonical(sorted(record.body.alternatives)) === canonical(sorted(record.alternatives)) && record.body.client_request_id === record.start_command_id && record.previous_receipt_hash === null && record.revision === 1 && record.receipt_hash === '0'.repeat(64));
  else { const selected = 'selected_full_policy_id' in record.body ? record.body.selected_full_policy_id : null; check(record.path.includes(`/sessions/${record.session_id}/`) && 'expected_revision' in record.body && record.body.expected_revision === record.revision && digest(record.previous_receipt_hash) && record.previous_receipt_hash === record.receipt_hash && (selected === null || record.alternatives.some((r) => r.full_policy_id === selected))); }
  return record;
}

export async function parseScopeEvaluation(v: unknown, raw: RecordValue, env: NextState, locator: ScopeLocator): Promise<ScopeEvaluation> {
  check(object(v) && exact(v, ['protocol', 'supported_input', 'worlds', 'source_fingerprint', 'selected_policy_id', 'status', 'distinct_signatures', 'pending_question', 'stable', 'should_ask', 'probability_model', 'question_selection', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'requires_separate_whole_financial_confirmation']) && v.protocol === SCOPE_PROTOCOL && v.supported_input === 'EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES' && v.probability_model === 'NONE' && v.question_selection === 'ONE_DECLARED_VARIABLE_MINIMAX' && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.requires_separate_whole_financial_confirmation === true && digest(v.source_fingerprint) && Array.isArray(v.worlds) && v.worlds.length === 2 && Array.isArray(raw.worlds));
  const worlds: ScopeWorld[] = []; let denominator: string | null = null, asOf = '', basis: string | null = null;
  for (let index = 0; index < 2; index++) {
    const w = rawObject(v.worlds[index]), r = rawObject(raw.worlds[index]);
    check(exact(w, ['full_policy_id', 'current_full_policy_version_id', 'native_evaluation', 'source_manifest', 'source_fingerprint', 'economic_signature']) && uuid(w.full_policy_id) && uuid(w.current_full_policy_version_id) && digest(w.source_fingerprint));
    const nativeRaw = rawObject(r.native_evaluation), sourceRaw = rawObject(r.source_manifest), source = rawObject(w.source_manifest);
    const native = await parseJointQuestionEvaluation(w.native_evaluation, nativeRaw, env, locator.session_id);
    const preview = rawObject(rawObject(w.native_evaluation).native_preview), nativeRequest = rawObject(preview.request);
    check(nativeRequest.full_policy_id === w.full_policy_id && nativeRequest.expected_full_policy_version_id === w.current_full_policy_version_id && source.user_id === env.dashboard.user_id && source.epoch_id === env.epoch_id && source.vector_signature === native.vector_signature && source.native_status === preview.status && canonical(source.native_reasons) === canonical(preview.reasons) && canonical(source.unresolved_action_ids) === canonical(preview.unresolved_original_action_ids) && canonical(source.registered_goal_ids) === canonical(preview.registered_goal_ids) && await jointQuestionSourceFingerprint(sourceRaw) === w.source_fingerprint);
    const goalKey = canonical(preview.registered_goal_ids), basisKey = await hash(sourceRaw.complete_native_basis);
    if (index === 0) { denominator = goalKey; asOf = native.as_of; basis = basisKey; }
    else check(denominator === goalKey && asOf === native.as_of && basis === basisKey);
    let economic: string | null = null;
    if (native.plan) {
      const p = rawObject(rawObject(nativeRaw.native_preview).plan), inputs = rawObject(p.inputs), children = p.children;
      check(Array.isArray(children) && await hash(sourceRaw.full_scope) === await hash(inputs.scope) && await hash(sourceRaw.allocation_input) === await hash(p.allocation_input));
      const retainedBasis = rawObject(rawObject(rawObject(rawObject(inputs.joint).original_actual_input).base).financial_basis);
      check(await hash(sourceRaw.complete_native_basis) === await hash(retainedBasis));
      economic = await hash({ protocol: SCOPE_PROTOCOL, registered_goal_ids: rawObject(nativeRaw.native_preview).registered_goal_ids, goal_allocations: rawObject(p.allocation).goals, total_cents: p.total_allocation_cents, children: children.map((item) => { const c = rawObject(item), effect = { ...rawObject(rawObject(c.command).effect) }; effect.operation_id = `derived_joint_child:${native.plan!.children.find((child) => child.goal_id === c.goal_id)!.child_number}`; delete effect.valid_from; delete effect.expires_at; return { number: c.child_number, goal_id: c.goal_id, effect, original_model_evidence_id: c.original_model_evidence_id, original_model_evidence_hash: c.original_model_evidence_hash }; }), execution_mode: p.execution_mode, cross_operation_atomicity: p.cross_operation_atomicity, requires_separate_whole_financial_confirmation: true });
    }
    check(w.economic_signature === economic); worlds.push({ policy_id: w.full_policy_id, version_id: w.current_full_policy_version_id, source_fingerprint: w.source_fingerprint, native, economic_signature: economic });
  }
  check(canonical(worlds.map((w) => w.policy_id)) === canonical(sorted(locator.alternatives).map((r) => r.full_policy_id)) && (v.selected_policy_id === null || worlds.some((w) => w.policy_id === v.selected_policy_id)));
  check(await hash({ protocol: SCOPE_PROTOCOL, source_fingerprint_version: 'full-joint-declared-scopes-source-v1', user_id: env.dashboard.user_id, epoch_id: env.epoch_id, worlds: worlds.map((w) => ({ full_policy_id: w.policy_id, current_full_policy_version_id: w.version_id, native_source_fingerprint: w.source_fingerprint, economic_signature: w.economic_signature })) }) === v.source_fingerprint);
  const known = worlds.every((w) => w.economic_signature !== null), active = worlds.filter((w) => v.selected_policy_id === null || w.policy_id === v.selected_policy_id), signatures = [...new Set(active.flatMap((w) => w.economic_signature === null ? [] : [w.economic_signature]))].sort();
  const status = !known ? 'UNKNOWN' : signatures.length === 1 ? 'STABLE' : 'DIVERGENT';
  check(v.status === status && v.stable === (!known ? null : status === 'STABLE') && v.should_ask === (!known ? null : status === 'DIVERGENT') && canonical(v.distinct_signatures) === canonical(signatures));
  let question: ScopeQuestion | null = null;
  if (status === 'DIVERGENT') {
    const q = rawObject(v.pending_question), qr = rawObject(raw.pending_question);
    check(exact(q, ['variable_id', 'wording', 'source_fingerprint', 'worst_residual_signature_count', 'partitions', 'question_hash']) && q.variable_id === 'REGISTERED_SCOPE_CHOICE' && q.wording === '这次安排采用哪一条已经确认的联合规则？' && q.source_fingerprint === v.source_fingerprint && q.worst_residual_signature_count === 1 && digest(q.question_hash) && Array.isArray(q.partitions) && q.partitions.length === 2);
    for (let i = 0; i < 2; i++) { const part = rawObject(q.partitions[i]), world = worlds[i]!; check(exact(part, ['choice_key', 'world_keys', 'signatures', 'residual_signature_count']) && part.choice_key === world.policy_id && canonical(part.world_keys) === canonical([world.policy_id]) && canonical(part.signatures) === canonical([world.economic_signature]) && part.residual_signature_count === 1); }
    const { question_hash: ignored, ...unhashed } = qr; void ignored; check(await hash(unhashed) === q.question_hash);
    question = { wording: q.wording, question_hash: q.question_hash, source_fingerprint: q.source_fingerprint as string, choices: worlds.map((w) => w.policy_id) };
  } else check(v.pending_question === null);
  return { worlds, source_fingerprint: v.source_fingerprint as string, selected_policy_id: v.selected_policy_id as string | null, status, question, as_of: asOf };
}

export async function parseScopeReceipt(v: unknown, raw: RecordValue, env: NextState, locator: ScopeLocator, text: string, pending?: ScopePending): Promise<ScopeReceipt> {
  check(object(v) && exact(v, ['protocol', 'user_id', 'epoch_id', 'session_id', 'revision', 'previous_receipt_hash', 'command_id', 'command_kind', 'authenticated_actor', 'original_request', 'as_of', 'valid_until', 'source_manifest', 'source_fingerprint', 'evaluation', 'state', 'receipt_hash', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited']) && v.protocol === SCOPE_PROTOCOL && v.user_id === env.dashboard.user_id && v.epoch_id === env.epoch_id && v.session_id === locator.session_id && uuid(v.command_id) && rev(v.revision, 17) && digest(v.receipt_hash) && digest(v.source_fingerprint) && time(v.as_of) && time(v.valid_until) && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false);
  check(locator.session_id === await releaseUUID5(namespace, `${env.dashboard.user_id}:${env.epoch_id}:${locator.start_command_id}`));
  const { receipt_hash: ignored, ...unhashed } = raw; void ignored; check(await hash(unhashed) === v.receipt_hash);
  const actor = parseLocalActorSession({ simulation: true, principal: v.authenticated_actor, bank_authority: false, confirms_financial_action: false }).principal;
  check(actor.role === 'USER' && actor.user_id === v.user_id && Date.parse(actor.issued_at) <= Date.parse(v.as_of) && Date.parse(v.as_of) < Date.parse(actor.expires_at));
  const original = rawObject(v.original_request); check(exact(original, ['kind', 'path', 'body']) && typeof original.path === 'string');
  const path = original.path.replace(/^\/api\/v1/, ''), kind = commandKind(path); bodyShape(original.body, kind);
  check(original.kind === `FULL_JOINT_SCOPE_${kind}` && original.body.client_request_id === v.command_id && original.body.expected_epoch_id === v.epoch_id && (kind === 'START' || path.includes(`/sessions/${v.session_id}/`)) && (v.command_kind === kind || v.command_kind === 'REBASE' && ['ANSWER', 'REFRESH'].includes(kind)));
  const evaluation = await parseScopeEvaluation(v.evaluation, rawObject(raw.evaluation), env, locator);
  const manifest = rawObject(v.source_manifest), mr = rawObject(raw.source_manifest);
  check(exact(manifest, ['protocol', 'source_fingerprint_version', 'user_id', 'epoch_id', 'worlds']) && manifest.protocol === SCOPE_PROTOCOL && manifest.source_fingerprint_version === 'full-joint-declared-scopes-source-v1' && manifest.user_id === v.user_id && manifest.epoch_id === v.epoch_id && canonical(manifest.worlds) === canonical(evaluation.worlds.map((w) => ({ full_policy_id: w.policy_id, current_full_policy_version_id: w.version_id, native_source_fingerprint: w.source_fingerprint, economic_signature: w.economic_signature }))) && await hash(mr) === v.source_fingerprint && evaluation.source_fingerprint === v.source_fingerprint);
  check(['READY_FOR_REVIEW', 'AWAITING_INPUT', 'UNKNOWN', 'CLOSED'].includes(String(v.state)) && (v.state === 'CLOSED') === (kind === 'CLOSE'));
  if (v.state !== 'CLOSED') check(v.revision <= 16 && evaluation.as_of === v.as_of && Date.parse(v.as_of) < Date.parse(v.valid_until) && Date.parse(v.valid_until) <= Date.parse(v.as_of) + 900000 && v.state === ({ STABLE: 'READY_FOR_REVIEW', DIVERGENT: 'AWAITING_INPUT', UNKNOWN: 'UNKNOWN' }[evaluation.status]));
  if (kind === 'START') check(v.revision === 1 && v.previous_receipt_hash === null && evaluation.selected_policy_id === null && 'alternatives' in original.body && canonical(sorted(original.body.alternatives)) === canonical(evaluation.worlds.map((w) => ({ full_policy_id: w.policy_id, expected_full_policy_version_id: w.version_id }))));
  else check('expected_revision' in original.body && v.revision === original.body.expected_revision + 1 && digest(v.previous_receipt_hash));
  if (v.command_kind === 'REBASE') check(evaluation.selected_policy_id === null);
  if (v.command_kind === 'ANSWER') check('selected_full_policy_id' in original.body && 'expected_source_fingerprint' in original.body && evaluation.selected_policy_id === original.body.selected_full_policy_id && evaluation.source_fingerprint === original.body.expected_source_fingerprint);
  if (pending) check(path === pending.path && canonical(original.body) === canonical(pending.body) && v.command_id === pending.body.client_request_id && v.previous_receipt_hash === pending.previous_receipt_hash);
  const receipt = { session_id: v.session_id as string, revision: v.revision, receipt_hash: v.receipt_hash as string, previous_receipt_hash: v.previous_receipt_hash as string | null, command_id: v.command_id, command_kind: v.command_kind as ScopeReceipt['command_kind'], state: v.state as ScopeReceipt['state'], as_of: v.as_of, valid_until: v.valid_until, source_fingerprint: v.source_fingerprint as string, evaluation };
  originalTexts.set(receipt, text); return receipt;
}

function rawResponse(v: unknown, text: string | undefined, env: NextState) { context(v, env); check(typeof text === 'string'); return jointQuestionRawTree(text); }
export async function parseScopeRejection(v: unknown, raw: RecordValue, p: ScopePending, env: NextState, text: string): Promise<ScopeRejection> {
  parseScopePending(p, env);
  check(object(v) && exact(v, ['protocol', 'record_kind', 'user_id', 'epoch_id', 'command_id', 'session_id', 'authenticated_actor', 'original_request', 'request_hash', 'as_of', 'code', 'message', 'http_status', 'rejection_hash', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'financial_absence_proven', 'nonfinancial_preference_pending_only', 'session_head_advanced']) && v.protocol === 'zhiyu-full-joint-scope-rejection-v1' && v.record_kind === 'NONFINANCIAL_REJECTION' && v.user_id === env.dashboard.user_id && v.epoch_id === env.epoch_id && v.command_id === p.body.client_request_id && v.session_id === p.session_id && time(v.as_of) && digest(v.request_hash) && digest(v.rejection_hash) && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.financial_absence_proven === false && v.nonfinancial_preference_pending_only === true && v.session_head_advanced === false);
  const actor = parseLocalActorSession({ simulation: true, principal: v.authenticated_actor, bank_authority: false, confirms_financial_action: false }).principal;
  check(actor.role === 'USER' && actor.user_id === v.user_id && Date.parse(actor.issued_at) <= Date.parse(v.as_of) && Date.parse(v.as_of) < Date.parse(actor.expires_at));
  const original = rawObject(v.original_request); check(exact(original, ['kind', 'path', 'body']) && original.kind === `FULL_JOINT_SCOPE_${commandKind(p.path)}` && original.path === `/api/v1${p.path}` && canonical(original.body) === canonical(p.body));
  check(typeof v.code === 'string' && ['JOINT_VERSION_CHANGED', 'QUESTION_WIRE_CAPACITY_EXCEEDED', 'STALE_QUESTION_REVISION', 'STALE_QUESTION_ANSWER'].includes(v.code) && v.http_status === (v.code === 'QUESTION_WIRE_CAPACITY_EXCEEDED' ? 413 : 409) && typeof v.message === 'string' && v.message.length >= 1 && v.message.length <= 200);
  check(await hash({ user_id: v.user_id, epoch_id: v.epoch_id, original_request: raw.original_request }) === v.request_hash);
  const { rejection_hash: ignored, ...unhashed } = raw; void ignored; check(await hash(unhashed) === v.rejection_hash);
  const rejection: ScopeRejection = { record_kind: 'NONFINANCIAL_REJECTION', command_id: p.body.client_request_id, rejection_hash: v.rejection_hash as string, code: v.code as ScopeRejection['code'], message: v.message, http_status: v.http_status as 409 | 413 };
  rejectionTexts.set(rejection, text); return rejection;
}
export async function readScope(locator: ScopeLocator, env: NextState): Promise<ScopeView> {
  locatorShape(locator, env);
  return request(`${SCOPE_PREFIX}/sessions/${locator.session_id}`, 'GET', undefined, async (v, text) => {
    const raw = rawResponse(v, text, env); check(object(v) && exact(v, [...identityFields, 'protocol', 'view_protocol', 'current_revision_ref', 'effective_state', 'current_source_fingerprint', 'current_evaluation', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'receipt_is_current_authority', 'current_read_required', 'requires_separate_whole_financial_confirmation', ...(v.current_read_error === undefined ? [] : ['current_read_error'])]) && v.protocol === SCOPE_PROTOCOL && v.view_protocol === 'full-joint-scope-current-read-v2' && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.receipt_is_current_authority === false && v.current_read_required === true && v.requires_separate_whole_financial_confirmation === true);
    const reference = rawObject(v.current_revision_ref);
    check(exact(reference, ['protocol', 'user_id', 'epoch_id', 'session_id', 'command_id', 'revision', 'previous_receipt_hash', 'receipt_hash']) && reference.protocol === 'full-joint-scope-revision-reference-v1' && reference.user_id === env.dashboard.user_id && reference.epoch_id === env.epoch_id && reference.session_id === locator.session_id && uuid(reference.command_id) && rev(reference.revision, 17) && digest(reference.receipt_hash) && (reference.revision === 1 ? reference.previous_receipt_hash === null : digest(reference.previous_receipt_hash)));
    const receipt = await readReferencedScopeReceipt(reference, locator, env);
    if (receipt.revision > locator.revision + 1) throw new Error('原观察跨过多个版本，需要核对完整原版本链；当前定位保留。');
    check(receipt.revision === locator.revision && receipt.receipt_hash === locator.receipt_hash || receipt.revision === locator.revision + 1 && receipt.previous_receipt_hash === locator.receipt_hash);
    const evaluation = v.current_evaluation === null ? null : await parseScopeEvaluation(v.current_evaluation, rawObject(raw.current_evaluation), env, locator);
    if (v.current_read_error !== undefined) check(object(v.current_read_error) && exact(v.current_read_error, ['code', 'message']) && typeof v.current_read_error.code === 'string' && typeof v.current_read_error.message === 'string' && v.effective_state === 'STALE_RECOMPUTATION_REQUIRED' && evaluation === null && v.current_source_fingerprint === null);
    check(['READY_FOR_REVIEW', 'AWAITING_INPUT', 'UNKNOWN', 'CLOSED', 'ARCHIVED', 'STALE_RECOMPUTATION_REQUIRED'].includes(String(v.effective_state)));
    if (['READY_FOR_REVIEW', 'AWAITING_INPUT', 'UNKNOWN'].includes(String(v.effective_state))) check(evaluation && v.effective_state === receipt.state && v.current_source_fingerprint === receipt.source_fingerprint && evaluation.source_fingerprint === receipt.source_fingerprint && evaluation.selected_policy_id === receipt.evaluation.selected_policy_id && Date.parse(evaluation.as_of) < Date.parse(receipt.valid_until));
    else if (v.effective_state === 'STALE_RECOMPUTATION_REQUIRED') check((!evaluation || evaluation.selected_policy_id === null && evaluation.source_fingerprint === v.current_source_fingerprint) && (v.current_source_fingerprint === null || digest(v.current_source_fingerprint)));
    else check(evaluation === null && v.current_source_fingerprint === null);
    return { receipt, evaluation, effective_state: v.effective_state as ScopeView['effective_state'] };
  });
}
async function readReferencedScopeReceipt(reference: RecordValue, locator: ScopeLocator, env: NextState): Promise<ScopeReceipt> {
  // This independent original GET verifies a snapshot reference, never a pending command.
  return request(`${SCOPE_PREFIX}/commands/${env.epoch_id}/by-key/${reference.command_id}`, 'GET', undefined, async (v, text) => {
    const raw = rawResponse(v, text, env);
    check(object(v) && exact(v, [...identityFields, 'protocol', 'status', 'client_request_id', 'original_request', 'original_receipt', ...(Object.hasOwn(v, 'original_rejection') ? ['original_rejection'] : []), 'not_found_is_final', 'replacement_allowed', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'receipt_is_current_authority', 'current_read_required']) && v.protocol === SCOPE_PROTOCOL && v.status === 'RECORDED' && v.client_request_id === reference.command_id && (!Object.hasOwn(v, 'original_rejection') || v.original_rejection === null) && v.not_found_is_final === false && v.replacement_allowed === false && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.receipt_is_current_authority === false && v.current_read_required === true);
    const original = rawObject(v.original_receipt);
    check(canonical(v.original_request) === canonical(original.original_request));
    const receipt = await parseScopeReceipt(original, rawObject(raw.original_receipt), env, locator, text!);
    check(original.user_id === reference.user_id && original.epoch_id === reference.epoch_id && receipt.session_id === reference.session_id && receipt.command_id === reference.command_id && receipt.revision === reference.revision && receipt.previous_receipt_hash === reference.previous_receipt_hash && receipt.receipt_hash === reference.receipt_hash);
    return receipt;
  });
}
export async function lookupScope(p: ScopePending, env: NextState): Promise<ScopeReceipt | ScopeRejection | null> {
  parseScopePending(p, env);
  return request(`${SCOPE_PREFIX}/commands/${p.epoch_id}/by-key/${p.body.client_request_id}`, 'GET', undefined, async (v, text) => {
    const raw = rawResponse(v, text, env); check(object(v) && exact(v, [...identityFields, 'protocol', 'status', 'client_request_id', 'original_request', 'original_receipt', ...(Object.hasOwn(v, 'original_rejection') ? ['original_rejection'] : []), 'not_found_is_final', 'replacement_allowed', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'receipt_is_current_authority', 'current_read_required']) && v.protocol === SCOPE_PROTOCOL && v.client_request_id === p.body.client_request_id && v.not_found_is_final === false && v.replacement_allowed === false && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false && v.receipt_is_current_authority === false && v.current_read_required === true);
    if (v.status === 'NOT_FOUND_NOT_FINAL') { check(v.original_request === null && v.original_receipt === null && (!Object.hasOwn(v, 'original_rejection') || v.original_rejection === null)); return null; }
    if (v.status === 'RECORDED_REJECTION') {
      check(v.original_receipt === null && Object.hasOwn(v, 'original_rejection') && canonical(v.original_request) === canonical(rawObject(v.original_rejection).original_request));
      const rejection = await parseScopeRejection(v.original_rejection, rawObject(raw.original_rejection), p, env, text!); rejectionGets.add(rejection); return rejection;
    }
    check(v.status === 'RECORDED' && (!Object.hasOwn(v, 'original_rejection') || v.original_rejection === null) && canonical(v.original_request) === canonical(rawObject(v.original_receipt).original_request));
    const receipt = await parseScopeReceipt(v.original_receipt, rawObject(raw.original_receipt), env, p, text!, p); originalGets.add(receipt); return receipt;
  });
}
export async function postScope(p: ScopePending, env: NextState): Promise<void> {
  parseScopePending(p, env);
  await request(p.path, 'POST', p.body, async (v, text) => { const raw = rawResponse(v, text, env); check(object(v) && exact(v, [...identityFields, 'current_read_required', 'receipt_is_current_authority', 'bank_authority', 'execution_eligible', 'old_confirmation_inherited', 'original_receipt']) && v.current_read_required === true && v.receipt_is_current_authority === false && v.bank_authority === false && v.execution_eligible === false && v.old_confirmation_inherited === false); await parseScopeReceipt(v.original_receipt, rawObject(raw.original_receipt), env, p, text!, p); });
}
const pendingKey = (env: NextState) => `zhiyu-next-joint-scope-pending-v1:${env.environment_id}:${env.epoch_id}`;
const locatorKey = (env: NextState) => `zhiyu-next-joint-scope-locator-v1:${env.environment_id}:${env.epoch_id}`;
export function restoreScopePending(env: NextState): ScopePending | null { const text = localStorage.getItem(pendingKey(env)); return text === null ? null : parseScopePending(JSON.parse(text), env); }
export function readScopeLocator(env: NextState): ScopeLocator | null { const text = localStorage.getItem(locatorKey(env)); if (text === null) return null; const value: unknown = JSON.parse(text); check(object(value) && exact(value, ['protocol', 'environment_id', 'user_id', 'epoch_id', 'session_id', 'start_command_id', 'alternatives', 'revision', 'receipt_hash'])); locatorShape(value, env); return value; }
export function retainScope(p: ScopePending, env: NextState) { parseScopePending(p, env); check(localStorage.getItem(pendingKey(env)) === null); localStorage.setItem(pendingKey(env), JSON.stringify(p)); }
export function scopeLocator(receipt: ScopeReceipt, previous: ScopeLocator): ScopeLocator { return { protocol: 'zhiyu-joint-scope-locator-v1', environment_id: previous.environment_id, user_id: previous.user_id, epoch_id: previous.epoch_id, session_id: previous.session_id, start_command_id: previous.start_command_id, alternatives: previous.alternatives, revision: receipt.revision, receipt_hash: receipt.receipt_hash }; }
export function completeScope(p: ScopePending, receipt: ScopeReceipt, env: NextState) { check(originalGets.has(receipt) && localStorage.getItem(pendingKey(env)) === JSON.stringify(p) && receipt.command_id === p.body.client_request_id); localStorage.setItem(locatorKey(env), JSON.stringify(scopeLocator(receipt, p))); localStorage.removeItem(pendingKey(env)); }
export function completeScopeRejection(p: ScopePending, rejection: ScopeRejection, env: NextState) {
  parseScopePending(p, env); const original = localStorage.getItem(pendingKey(env)), text = rejectionTexts.get(rejection);
  check(rejectionGets.has(rejection) && original === JSON.stringify(p) && typeof text === 'string' && rejection.command_id === p.body.client_request_id);
  const archiveKey = `${pendingKey(env)}:rejected:${p.body.client_request_id}`, archive = JSON.stringify({ protocol: 'zhiyu-joint-scope-rejected-original-v1', original_pending_json: original, original_rejection_text: text, rejection_hash: rejection.rejection_hash });
  const existing = localStorage.getItem(archiveKey); check(existing === null || existing === archive); if (existing === null) localStorage.setItem(archiveKey, archive);
  localStorage.removeItem(pendingKey(env));
}
export async function makeScopePending(env: NextState, alternatives: ScopeReference[], current?: ScopeView, kind: 'answers' | 'refresh' | 'close' = 'refresh', selected?: string): Promise<ScopePending> {
  refs(alternatives); const previous = current ? readScopeLocator(env) : null; check(!current || previous && current.receipt.session_id === previous.session_id);
  const client = crypto.randomUUID(), session = previous?.session_id ?? await releaseUUID5(namespace, `${env.dashboard.user_id}:${env.epoch_id}:${client}`);
  let body: Start | Command | Answer = current ? { expected_epoch_id: env.epoch_id, client_request_id: client, expected_revision: current.receipt.revision } : { expected_epoch_id: env.epoch_id, client_request_id: client, alternatives: sorted(alternatives), supported_input: 'EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES' };
  if (current && kind === 'answers') { const evaluation = current.evaluation; check(current.effective_state === 'AWAITING_INPUT' && evaluation?.question && uuid(selected) && evaluation.question.choices.includes(selected)); body = { ...body as Command, expected_source_fingerprint: evaluation.source_fingerprint, expected_question_hash: evaluation.question.question_hash, selected_full_policy_id: selected }; }
  return parseScopePending({ protocol: 'zhiyu-joint-scope-locator-v1', environment_id: env.environment_id, user_id: env.dashboard.user_id, epoch_id: env.epoch_id, session_id: session, start_command_id: previous?.start_command_id ?? client, alternatives: previous?.alternatives ?? sorted(alternatives), revision: current?.receipt.revision ?? 1, receipt_hash: current?.receipt.receipt_hash ?? '0'.repeat(64), path: `${SCOPE_PREFIX}/sessions${current ? `/${session}/${kind}` : ''}`, body, body_json: JSON.stringify(body), previous_receipt_hash: current?.receipt.receipt_hash ?? null }, env);
}
