import { useSyncExternalStore } from 'react';
import { object } from './policy-form';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation, sameFullPolicyJson } from './full-policy-operation';
import { isWriteInFlight } from './write-flight';

export type OnboardingBinding = { user_id: string; epoch_id: string; reference_date: string; timezone: 'UTC' | 'Asia/Shanghai' };
export type OnboardingInputs = {
  obligation_id: string; valid_from: string; valid_until: string; importance: string;
  horizon_days: string; lookback_days: string; quantile: string; extra_buffer_yuan: string; categories: string; exclude_one_off: boolean;
  emergency_text: string; goal_text: string;
  asset_name: string; asset_valid_from: string; asset_valid_until: string; asset_scope: string; asset_goal_id: string; asset_classes: string; total_cap_yuan: string; single_cap_yuan: string; redemption_days: string; lock_days: string; principal_risk: string; auto_recovery: boolean; penalty_withdrawal: boolean;
};
export type CandidateRef = { compilation_id: string; proposal_id: string | null; configuration_hash: string | null; configuration_type: string | null; input_text: string; binding: OnboardingBinding };
export type DeclarationKind = 'OBLIGATION' | 'LIVING' | 'ASSET';
export type OnboardingIntent = { kind: 'DISCOVERY' | 'EMERGENCY' | 'GOAL' | DeclarationKind; body_json: string; binding: OnboardingBinding };
export type DeclarationRef = { proposal_id: string; evidence_id: string; configuration_hash: string; request_hash: string; body_json: string; binding: OnboardingBinding };
export type OnboardingDraft = { protocol: 'onboarding-local-draft-v1'; step: number; inputs: OnboardingInputs; candidates: { emergency: CandidateRef | null; goal: CandidateRef | null }; declarations: { obligation: DeclarationRef | null; living: DeclarationRef | null; asset: DeclarationRef | null }; discovery_proposal_ids: string[]; pending: OnboardingIntent | null };
type State = { draft: OnboardingDraft; busy: boolean; storage_error: string | null };
export const emptyOnboardingDraft = (): OnboardingDraft => ({ protocol: 'onboarding-local-draft-v1', step: 1, inputs: { obligation_id: '', valid_from: '', valid_until: '', importance: '50', horizon_days: '14', lookback_days: '56', quantile: '0.8', extra_buffer_yuan: '500.00', categories: 'food,transport,daily_necessities', exclude_one_off: true, emergency_text: '', goal_text: '', asset_name: '自主资产授权', asset_valid_from: '', asset_valid_until: '', asset_scope: 'general_idle_funds', asset_goal_id: '', asset_classes: 'CASH_MGMT_T0', total_cap_yuan: '', single_cap_yuan: '', redemption_days: '0', lock_days: '0', principal_risk: '0', auto_recovery: false, penalty_withdrawal: false }, candidates: { emergency: null, goal: null }, declarations: { obligation: null, living: null, asset: null }, discovery_proposal_ids: [], pending: null });
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const sha = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const date = (value: unknown) => typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === keys.sort().join('|');
export const isDeclarationKind = (value: string): value is DeclarationKind => ['OBLIGATION', 'LIVING', 'ASSET'].includes(value);
function safeJson(value: unknown, depth = 0): boolean { if (depth > 32) return false; if (typeof value === 'number') return Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value)); if (Array.isArray(value)) return value.every((item) => safeJson(item, depth + 1)); if (object(value)) return Object.values(value).every((item) => safeJson(item, depth + 1)); return value === null || typeof value === 'string' || typeof value === 'boolean'; }
export function validDeclarationBody(value: unknown, kind: DeclarationKind, binding: OnboardingBinding): boolean { return object(value) && exact(value, ['configuration', 'idempotency_key', 'expected_epoch_id', 'source_proposal_id']) && value.expected_epoch_id === binding.epoch_id && typeof value.idempotency_key === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value.idempotency_key) && (value.source_proposal_id === null || uuid(value.source_proposal_id)) && object(value.configuration) && value.configuration.type === { OBLIGATION: 'recurring_obligation', LIVING: 'living_reserve', ASSET: 'asset_authorization' }[kind] && safeJson(value.configuration); }
export function validOnboardingBinding(value: unknown): value is OnboardingBinding { return object(value) && exact(value, ['user_id', 'epoch_id', 'reference_date', 'timezone']) && uuid(value.user_id) && uuid(value.epoch_id) && date(value.reference_date) && ['UTC', 'Asia/Shanghai'].includes(value.timezone as string); }
export function sameOnboardingBinding(left: OnboardingBinding, right: OnboardingBinding): boolean { return validOnboardingBinding(left) && validOnboardingBinding(right) && Object.keys(left).every((key) => left[key as keyof OnboardingBinding] === right[key as keyof OnboardingBinding]); }
export function validOnboardingIntent(value: unknown): value is OnboardingIntent {
  if (!object(value) || !exact(value, ['kind', 'body_json', 'binding']) || !['DISCOVERY', 'EMERGENCY', 'GOAL', 'OBLIGATION', 'LIVING', 'ASSET'].includes(value.kind as string) || typeof value.body_json !== 'string' || value.body_json.length > 15000 || !validOnboardingBinding(value.binding)) return false;
  try { const body: unknown = JSON.parse(value.body_json); if (!object(body) || JSON.stringify(body) !== value.body_json) return false; return isDeclarationKind(value.kind as string) ? validDeclarationBody(body, value.kind as DeclarationKind, value.binding) : value.kind === 'DISCOVERY' ? exact(body, []) : exact(body, ['text', 'engine']) && body.engine === 'rules' && typeof body.text === 'string' && body.text.trim().length > 0 && body.text.length <= 2000; } catch { return false; }
}
export function validDeclarationRef(value: unknown, kind: DeclarationKind): value is DeclarationRef { return object(value) && exact(value, ['proposal_id', 'evidence_id', 'configuration_hash', 'request_hash', 'body_json', 'binding']) && uuid(value.proposal_id) && uuid(value.evidence_id) && sha(value.configuration_hash) && sha(value.request_hash) && validOnboardingIntent({ kind, body_json: value.body_json, binding: value.binding }); }
export function validCandidateRef(value: unknown): value is CandidateRef { return object(value) && exact(value, ['compilation_id', 'proposal_id', 'configuration_hash', 'configuration_type', 'input_text', 'binding']) && uuid(value.compilation_id) && (value.proposal_id === null || uuid(value.proposal_id)) && (value.configuration_hash === null || sha(value.configuration_hash)) && (value.configuration_type === null || typeof value.configuration_type === 'string' && value.configuration_type.length <= 64) && typeof value.input_text === 'string' && value.input_text.trim().length > 0 && value.input_text.length <= 2000 && validOnboardingBinding(value.binding) && (value.proposal_id === null) === (value.configuration_hash === null); }
export function validOnboardingDraft(value: unknown): value is OnboardingDraft {
  if (!object(value) || !exact(value, ['protocol', 'step', 'inputs', 'candidates', 'declarations', 'discovery_proposal_ids', 'pending']) || value.protocol !== 'onboarding-local-draft-v1' || !Number.isSafeInteger(value.step) || (value.step as number) < 1 || (value.step as number) > 8 || !object(value.inputs) || !object(value.candidates) || !exact(value.candidates, ['emergency', 'goal']) || !object(value.declarations) || !exact(value.declarations, ['obligation', 'living', 'asset'])) return false;
  const defaults = emptyOnboardingDraft().inputs; const inputs = value.inputs; const candidates = value.candidates;
  if (!exact(inputs, Object.keys(defaults)) || !Object.entries(defaults).every(([key, fallback]) => typeof inputs[key] === typeof fallback && (typeof fallback !== 'string' || (inputs[key] as string).length <= 2000))) return false;
  const declarations = value.declarations;
  return ['emergency', 'goal'].every((key) => candidates[key] === null || validCandidateRef(candidates[key])) && (['obligation', 'living', 'asset'] as const).every((key) => declarations[key] === null || validDeclarationRef(declarations[key], key.toUpperCase() as DeclarationKind)) && Array.isArray(value.discovery_proposal_ids) && value.discovery_proposal_ids.length <= 10000 && value.discovery_proposal_ids.every(uuid) && new Set(value.discovery_proposal_ids).size === value.discovery_proposal_ids.length && (value.pending === null || validOnboardingIntent(value.pending));
}
function frozen<T>(value: T): T { if (typeof value === 'object' && value !== null) { Object.values(value).forEach(frozen); Object.freeze(value); } return value; }
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((fn) => fn());
const storageKey = () => `bounded-funds-onboarding-draft-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
let state: State = { draft: frozen(emptyOnboardingDraft()), busy: false, storage_error: null }; let recovered = false;
export const getOnboardingDraft = () => state;
export function recoverOnboardingDraft(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const parsed: unknown = JSON.parse(raw); if (!validOnboardingDraft(parsed)) throw new Error('INVALID_DRAFT'); state = { draft: frozen(parsed), busy: false, storage_error: null }; } }
  catch { state = { ...state, storage_error: '引导原草稿无法完整读取。禁止新的候选写入；本地记录没有授权效力，请保留原记录。' }; }
  emit(); return state;
}
function save(next: OnboardingDraft, proofRecovery = false): void {
  if (!validOnboardingDraft(next)) throw new Error('引导草稿含未知字段或不完整原请求');
  if (state.storage_error && !proofRecovery) throw new Error(state.storage_error);
  try { sessionStorage.setItem(storageKey(), JSON.stringify(next)); state = { ...state, draft: frozen(next), storage_error: null }; }
  catch { state = { ...state, storage_error: '无法保存引导原草稿，未开始新的候选请求；已发送的原请求必须继续核对。' }; emit(); throw new Error(state.storage_error!); }
  emit();
}
export function setOnboardingStep(step: number): void { recoverOnboardingDraft(); save({ ...state.draft, step }); }
export function updateOnboardingInput<K extends keyof OnboardingInputs>(key: K, value: OnboardingInputs[K]): void { recoverOnboardingDraft(); if (state.busy || state.draft.pending) throw new Error('原候选请求待核对，不能改写输入'); save({ ...state.draft, inputs: { ...state.draft.inputs, [key]: value } }); }
export function prepareOnboardingIntent(kind: 'DISCOVERY' | 'EMERGENCY' | 'GOAL', binding: OnboardingBinding, text?: string): OnboardingIntent { const original = { kind, binding: structuredClone(binding), body_json: kind === 'DISCOVERY' ? '{}' : JSON.stringify({ text, engine: 'rules' }) }; if (!validOnboardingIntent(original)) throw new Error('引导原请求必须绑定实际用户、周期和服务日期，且含完整原文'); return frozen(original); }
export function prepareOnboardingDeclaration(kind: DeclarationKind, binding: OnboardingBinding, configuration: Record<string, unknown>, sourceProposalId: string | null = null): OnboardingIntent { const original = { kind, binding: structuredClone(binding), body_json: JSON.stringify({ configuration: structuredClone(configuration), idempotency_key: `onboarding-${kind.toLowerCase()}:${crypto.randomUUID()}`, expected_epoch_id: binding.epoch_id, source_proposal_id: sourceProposalId }) }; if (!validOnboardingIntent(original)) throw new Error('声明候选须含完整配置、原键与当前周期'); return frozen(original); }
export function beginOnboardingCommand(intent: OnboardingIntent, blocked = false): void {
  recoverOnboardingDraft(); const demo = recoverDemoOperation(); const full = recoverFullPolicyOperation();
  if (blocked || state.busy || state.draft.pending || state.storage_error || demo.busy || demo.pending || demo.storage_error || full.busy || full.pending || full.storage_error || isWriteInFlight()) throw new Error('原请求正在处理或待核对，不能开始新的引导候选写入');
  if (!validOnboardingIntent(intent)) throw new Error('引导原请求未通过完整校验'); save({ ...state.draft, pending: intent }); state = { ...state, busy: true }; emit();
}
export function beginOnboardingDeclarationReplay(intent: OnboardingIntent): void { recoverOnboardingDraft(); samePending(intent); if (!isDeclarationKind(intent.kind)) throw new Error('规则编译与发现不能重放未知请求'); const demo = recoverDemoOperation(); const full = recoverFullPolicyOperation(); if (state.busy || state.storage_error || demo.busy || demo.pending || demo.storage_error || full.busy || full.pending || full.storage_error || isWriteInFlight()) throw new Error('其他原请求待核对，不能重放原声明'); save({ ...state.draft, pending: intent }); state = { ...state, busy: true }; emit(); }
export function endOnboardingAttempt(): void { state = { ...state, busy: false }; emit(); }
function samePending(intent: OnboardingIntent): void { if (!state.draft.pending || JSON.stringify(state.draft.pending) !== JSON.stringify(intent) || !validOnboardingIntent(intent)) throw new Error('不能用另一输入解除原引导请求'); }
/** Caller supplies a validated original server candidate read, never an authorization. */
export function retainOnboardingCandidate(intent: OnboardingIntent, ref: CandidateRef): void {
  samePending(intent); if (!['EMERGENCY', 'GOAL'].includes(intent.kind) || !validCandidateRef(ref) || !sameOnboardingBinding(ref.binding, intent.binding) || JSON.parse(intent.body_json).text !== ref.input_text) throw new Error('原候选与原输入/周期/日期不匹配'); const field = intent.kind === 'EMERGENCY' ? 'emergency' : 'goal'; save({ ...state.draft, candidates: { ...state.draft.candidates, [field]: ref }, pending: null }, true);
}
/** A recorded matching server candidate releases only the local write gate, never authority. */
export function retainOnboardingDeclaration(intent: OnboardingIntent, ref: DeclarationRef): void { samePending(intent); if (!isDeclarationKind(intent.kind) || !validDeclarationRef(ref, intent.kind) || !sameOnboardingBinding(ref.binding, intent.binding) || !sameFullPolicyJson(JSON.parse(ref.body_json), JSON.parse(intent.body_json))) throw new Error('原声明身份、body或周期不匹配'); save({ ...state.draft, declarations: { ...state.draft.declarations, [intent.kind.toLowerCase()]: ref }, pending: null }, true); }
/** Only the known, validated discovery reply identifies which original proposals it returned. */
export function retainOnboardingDiscovery(intent: OnboardingIntent, proposalIds: string[]): void { samePending(intent); if (intent.kind !== 'DISCOVERY' || !proposalIds.every(uuid) || new Set(proposalIds).size !== proposalIds.length || proposalIds.length > 10000) throw new Error('发现候选原身份不完整'); save({ ...state.draft, discovery_proposal_ids: proposalIds, pending: null }, true); }
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
export const useOnboardingDraft = () => useSyncExternalStore(subscribe, getOnboardingDraft, getOnboardingDraft);
