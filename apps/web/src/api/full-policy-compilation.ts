import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import type { TemplateName } from './full-policies';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';

export type FullCompilationRequest = components['schemas']['FullCompilationRequest'];
/** The reader below requires the actual response's defaulted lists to be present. */
export type FullCompilation = Omit<components['schemas']['FullCompilationResponse'], 'compilation'> & { compilation: Required<components['schemas']['FullCompilationResult']> };
/** The actual parser checks all twelve registered keys, beyond an index signature. */
export type FullGrammar = Omit<components['schemas']['FullGrammarResponse'], 'examples'> & { examples: Record<TemplateName, string> };
export const fullCompilerVersion = 'full-offline-candidate-rules-v1';
export const compilerTemplates: readonly TemplateName[] = ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'DatedExpensePolicy', 'LongTermGoalPolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'];
const types: Record<TemplateName, string> = { RecurringObligationPolicy: 'recurring_obligation', LivingReservePolicy: 'living_reserve', EmergencyBufferPolicy: 'emergency_buffer', DatedExpensePolicy: 'dated_expense', LongTermGoalPolicy: 'long_term_goal', PeriodicTransferPolicy: 'periodic_transfer', AssetAuthorizationPolicy: 'asset_authorization', RecoveryPolicy: 'recovery', GoalAllocationPolicy: 'goal_allocation', CrossGoalReallocationPolicy: 'cross_goal_reallocation', SeasonalReservePolicy: 'seasonal_reserve', InterventionPolicy: 'intervention' };
const originals = new WeakMap<object, string>();
export const getOriginalFullCompilationResponse = (value: object) => originals.get(value) ?? null;
const text = (value: unknown): value is string => typeof value === 'string';
const digest = (value: unknown): value is string => text(value) && /^[0-9a-f]{64}$/.test(value);
const uuid = (value: unknown): value is string => text(value) && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === keys.sort().join('|');
const template = (value: unknown): value is TemplateName => text(value) && compilerTemplates.includes(value as TemplateName);
function check(value: unknown): asserts value { if (!value) throw new Error('自然候选的原文本、来源片段或无授权合同不一致'); }
export function requireCompilerJson(value: unknown): void {
  let nodes = 0;
  function visit(child: unknown, depth: number) {
    check(++nodes <= 4096 && depth <= 16);
    if (typeof child === 'number') check(Number.isFinite(child) && (!Number.isInteger(child) || Number.isSafeInteger(child)));
    else if (typeof child === 'string') check(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(child));
    else if (Array.isArray(child)) child.forEach((item) => visit(item, depth + 1));
    else if (object(child)) Object.values(child).forEach((item) => visit(item, depth + 1));
    else check(child === null || typeof child === 'boolean');
  }
  visit(value, 0); assertMoneyFields(value);
}
export async function fullCompilerTextHash(value: string): Promise<string> {
  requireCompilerJson(value); check(globalThis.crypto?.subtle);
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
function date(value: unknown): boolean {
  if (!text(value) || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) return false;
  const parsed = new Date(`${value}T00:00:00Z`); return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}
function timezone(value: unknown): boolean { if (!text(value)) return false; try { new Intl.DateTimeFormat('en', { timeZone: value }).format(0); return true; } catch { return false; } }
export function parseFullGrammar(value: unknown, original?: string): FullGrammar {
  check(object(value) && exact(value, ['simulation', 'compiler_version', 'examples', 'original_examples_are_synthetic', 'supported_scope', 'bank_authority']) && value.simulation === true && value.compiler_version === fullCompilerVersion && value.original_examples_are_synthetic === true && value.bank_authority === false && text(value.supported_scope));
  const examples = value.examples;
  check(object(examples) && Object.keys(examples).length === compilerTemplates.length && compilerTemplates.every((key) => text(examples[key]) && examples[key].length > 0));
  const result = value as FullGrammar; if (original !== undefined) originals.set(result, original); return result;
}
export function parseFullCompilerRequest(value: unknown): FullCompilationRequest {
  requireCompilerJson(value);
  check(object(value) && Object.keys(value).every((key) => ['text', 'engine', 'comparison_candidate'].includes(key)) && text(value.text) && Array.from(value.text).length > 0 && Array.from(value.text).length <= 4000 && (value.engine === undefined || value.engine === 'rules' || value.engine === 'llm'));
  if (value.comparison_candidate !== undefined && value.comparison_candidate !== null) check(object(value.comparison_candidate) && exact(value.comparison_candidate, ['template_name', 'configuration']) && template(value.comparison_candidate.template_name) && object(value.comparison_candidate.configuration));
  return { ...value, engine: value.engine ?? 'rules' } as FullCompilationRequest;
}
export async function parseFullCompilation(value: unknown, body: FullCompilationRequest, userId?: string, original?: string): Promise<FullCompilation> {
  parseFullCompilerRequest(body); if (userId !== undefined) check(uuid(userId));
  check(object(value) && exact(value, ['simulation', 'user_id', 'reference_date', 'timezone', 'compiler_version', 'compilation', 'comparison_source', 'confirmation_record_created', 'grants_authority', 'bank_authority']) && value.simulation === true && uuid(value.user_id) && (userId === undefined || value.user_id === userId) && date(value.reference_date) && timezone(value.timezone) && value.compiler_version === fullCompilerVersion && value.confirmation_record_created === false && value.grants_authority === false && value.bank_authority === false);
  check(value.comparison_source === (body.comparison_candidate ? 'USER_PROVIDED_CANDIDATE_NOT_CURRENT_VERSION' : 'NONE'));
  const item = value.compilation;
  check(object(item) && exact(item, ['simulation', 'compiler_version', 'dsl_version', 'engine', 'status', 'template_name', 'original_text_sha256', 'redacted_source_text', 'draft', 'configuration', 'configuration_hash', 'source_fragments', 'issues', 'defaulted_fields', 'differences', 'summary', 'evidence_level', 'requires_confirmation', 'grants_authority', 'bank_authority', 'policy_created', 'reference_validation', 'manual_review_required', 'privacy_redactions']));
  check(item.simulation === true && item.compiler_version === fullCompilerVersion && item.dsl_version === 'FULL_V1' && item.engine === (body.engine ?? 'rules') && text(item.status) && ['READY_FOR_REVIEW', 'MISSING', 'AMBIGUOUS', 'UNKNOWN', 'REVIEW_REQUIRED'].includes(item.status) && (item.template_name === null || template(item.template_name)) && item.original_text_sha256 === await fullCompilerTextHash(body.text) && text(item.redacted_source_text) && object(item.draft) && text(item.summary));
  check(item.requires_confirmation === true && item.grants_authority === false && item.bank_authority === false && item.policy_created === false && item.reference_validation === 'NOT_SERVER_VERIFIED' && item.manual_review_required === true && item.evidence_level === (item.engine === 'llm' ? 'MODEL_INFERRED' : 'USER_DECLARED'));
  check(Array.isArray(item.issues) && item.issues.every((issue) => object(issue) && exact(issue, ['code', 'field', 'message', 'source_fragment']) && ['code', 'field', 'message', 'source_fragment'].every((key) => text(issue[key]))));
  check(strings(item.defaulted_fields) && new Set(item.defaulted_fields).size === item.defaulted_fields.length && Array.isArray(item.differences) && item.differences.every((difference) => object(difference) && exact(difference, ['field', 'before', 'after', 'explanation']) && text(difference.field) && text(difference.explanation)));
  requireCompilerJson(item.draft); requireCompilerJson(item.differences);
  check(object(item.privacy_redactions) && Object.values(item.privacy_redactions).every((count) => Number.isSafeInteger(count) && Number(count) >= 0));
  if (item.status === 'READY_FOR_REVIEW') {
    check(template(item.template_name) && object(item.configuration) && item.configuration.type === types[item.template_name] && digest(item.configuration_hash) && item.issues.length === 0); requireCompilerJson(item.configuration);
  } else check(item.configuration === null && item.configuration_hash === null);
  const points = Array.from(body.text);
  check(Array.isArray(item.source_fragments) && item.source_fragments.length <= 4096);
  for (const fragment of item.source_fragments) {
    check(object(fragment) && exact(fragment, ['field', 'start', 'end', 'redacted_text', 'original_fragment_sha256']) && text(fragment.field) && text(fragment.redacted_text) && Number.isSafeInteger(fragment.start) && Number.isSafeInteger(fragment.end) && Number(fragment.start) >= 0 && Number(fragment.end) > Number(fragment.start) && Number(fragment.end) <= points.length && digest(fragment.original_fragment_sha256));
    check(fragment.original_fragment_sha256 === await fullCompilerTextHash(points.slice(Number(fragment.start), Number(fragment.end)).join('')));
  }
  const result = value as FullCompilation; if (original !== undefined) originals.set(result, original); return result;
}
export const getFullCompilerGrammar = () => request<FullGrammar>('/full-policy-compilations/grammar', 'GET', undefined, parseFullGrammar);
export async function compileFullPolicyCandidate(body: FullCompilationRequest, userId?: string): Promise<FullCompilation> {
  const admitted = structuredClone(parseFullCompilerRequest(body));
  const raw = await request('/full-policy-compilations/preview', 'POST', admitted, (value, original) => ({ value, original }));
  return parseFullCompilation(raw.value, admitted, userId, raw.original);
}
