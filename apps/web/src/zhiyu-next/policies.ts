import { request } from '../api/http';
import { isRunId } from '../api/decisions';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import type { NextState, OperationResult } from './api';
import type { PendingOperation } from './operation';
import { initialForm, lifecycleFor, templateNames, templateTags, type TemplateName, type Lifecycle, type Schema, type ReferenceChoices } from './policy-schema';

export type PolicyTemplate = { template_name: TemplateName; title: string; lifecycle_backend: Lifecycle; candidate_available: boolean; confirm_available: boolean; execution_status: string; reason: string };
export type PolicyCatalog = { simulation: true; variant: 'zhiyu-next'; environment_id: string; epoch_id: string; bank_authority: false; templates: PolicyTemplate[] };
export type PolicySchema = { simulation: true; variant: 'zhiyu-next'; environment_id: string; epoch_id: string; template_name: TemplateName; dsl_version: 'MVP_V1' | 'FULL_V1'; lifecycle_backend: Lifecycle; json_schema: Schema; schema_sha256: string; cross_field_validation_required: true; candidate_only: true; bank_authority: false; reference_choices: ReferenceChoices };
export type PolicyImpact = { status: 'PROJECTED' | 'PARTIAL' | 'UNKNOWN'; summary: string; changes: { label: string; before_cents?: number | null; after_cents?: number | null }[]; uncovered: string[] };
export type PolicyCandidate = { simulation: true; variant: 'zhiyu-next'; environment_id: string; epoch_id: string; client_request_id: string; candidate_id: string; template_name: TemplateName; dsl_version: 'FULL_V1'; lifecycle_backend: Lifecycle; canonical_configuration: Record<string, unknown>; configuration_hash: string; summary: string; impact: PolicyImpact; can_confirm: boolean; bank_authority: false };
export type PolicyRecord = { source_kind: 'MVP_POLICY' | 'FULL_POLICY' | 'GOAL_BRIDGE'; lifecycle_backend: Lifecycle; dsl_version: 'MVP_V1' | 'FULL_V1'; lifecycle_available: boolean; policy_id: string; current_version_id: string; goal_id?: string | null; template_name: TemplateName; title: string; name: string; status: string; effective_status: string; configuration: Record<string, unknown>; configuration_hash: string; planning_confirmed: boolean; bank_authority: false; execution_status: string; reason: string };
export type PolicyRecords = { simulation: true; variant: 'zhiyu-next'; environment_id: string; epoch_id: string; items: PolicyRecord[] };
export const isCatalogUserCommand = (path: string) => ['/zhiyu-next/policy-commands/confirm', '/zhiyu-next/policy-commands/lifecycle'].includes(path);
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const valid = (value: unknown) => { if (!value) throw new Error('策略响应未通过原件核实，请保留原请求。'); };
export function context(value: unknown, environment: NextState): asserts value is Record<string, unknown> { valid(object(value) && value.simulation === true && value.variant === 'zhiyu-next' && value.environment_id === environment.environment_id && value.epoch_id === environment.epoch_id && (value.bank_authority === undefined || value.bank_authority === false)); }
function template(value: unknown): value is TemplateName { return templateNames.includes(value as TemplateName); }
export function safeConfiguration(value: unknown): value is Record<string, unknown> {
  if (!object(value)) return false;
  let count = 0;
  function visit(input: unknown, depth: number): boolean {
    if (++count > 4096 || depth > 16) return false;
    if (input === null || typeof input === 'string' || typeof input === 'boolean') return true;
    if (typeof input === 'number') return Number.isFinite(input) && (!Number.isInteger(input) || Number.isSafeInteger(input));
    return Array.isArray(input) ? input.every((child) => visit(child, depth + 1)) : object(input) && Object.entries(input).every(([key, child]) => !['__proto__', 'constructor', 'prototype'].includes(key) && (!/_cents(?:_per_day)?$/.test(key) || Number.isSafeInteger(child) && Number(child) >= 0) && visit(child, depth + 1));
  }
  if (!visit(value, 0)) return false; assertMoneyFields(value); return true;
}
export function parsePolicyCatalog(value: unknown, environment: NextState): PolicyCatalog {
  context(value, environment); valid(value.bank_authority === false && Array.isArray(value.templates) && value.templates.length === templateNames.length);
  const rows = value.templates as unknown[];
  valid(new Set(rows.map((row) => object(row) ? row.template_name : null)).size === templateNames.length && rows.every((row) => object(row) && template(row.template_name) && row.lifecycle_backend === lifecycleFor(row.template_name) && typeof row.title === 'string' && typeof row.candidate_available === 'boolean' && typeof row.confirm_available === 'boolean' && typeof row.execution_status === 'string' && typeof row.reason === 'string'));
  return value as PolicyCatalog;
}
export function parsePolicySchema(value: unknown, name: TemplateName, environment: NextState, version: 'MVP_V1' | 'FULL_V1' = 'FULL_V1'): PolicySchema {
  context(value, environment); valid(value.template_name === name && value.dsl_version === version && (version !== 'MVP_V1' || lifecycleFor(name) === 'MVP') && value.lifecycle_backend === lifecycleFor(name) && object(value.json_schema) && digest(value.schema_sha256) && value.cross_field_validation_required === true && value.candidate_only === true && value.bank_authority === false && (value.authority_granted === undefined || value.authority_granted === false) && object(value.reference_choices));
  valid(Object.entries(value.reference_choices as Record<string, unknown>).every(([path, options]) => /^[a-z_]+(?:\.[a-z_]+)*$/.test(path) && Array.isArray(options) && new Set(options.map((option) => object(option) ? option.value : null)).size === options.length && options.every((option) => object(option) && typeof option.value === 'string' && !!option.value && typeof option.label === 'string' && !!option.label)));
  initialForm(value.json_schema as Schema);
  return value as PolicySchema;
}
export function parsePolicyCandidate(value: unknown, original: PendingOperation): PolicyCandidate {
  const environment = { environment_id: original.environment_id, epoch_id: original.epoch_id } as NextState;
  context(value, environment); valid(original.path === '/zhiyu-next/policy-candidates' && value.client_request_id === original.client_request_id && value.candidate_id === original.client_request_id && value.template_name === original.body.template_name && template(value.template_name) && value.dsl_version === 'FULL_V1' && value.lifecycle_backend === lifecycleFor(value.template_name) && safeConfiguration(value.canonical_configuration) && digest(value.configuration_hash) && typeof value.summary === 'string' && typeof value.can_confirm === 'boolean' && value.bank_authority === false && object(value.impact));
  valid(object(original.body.configuration) && (value.canonical_configuration as Record<string, unknown>).type === original.body.configuration.type && (value.canonical_configuration as Record<string, unknown>).type === templateTags[value.template_name as TemplateName]);
  const impact = value.impact as Record<string, unknown>;
  valid(['PROJECTED', 'PARTIAL', 'UNKNOWN'].includes(String(impact.status)) && typeof impact.summary === 'string' && Array.isArray(impact.uncovered) && impact.uncovered.every((row) => typeof row === 'string') && Array.isArray(impact.changes) && impact.changes.every((row) => object(row) && typeof row.label === 'string' && ['before_cents', 'after_cents'].every((key) => row[key] === undefined || row[key] === null || Number.isSafeInteger(row[key]) && Number(row[key]) >= 0)));
  return value as PolicyCandidate;
}
export function parsePolicyRecords(value: unknown, environment: NextState): PolicyRecords {
  function route(row: Record<string, unknown>): boolean {
    if (!template(row.template_name) || !object(row.configuration)) return false;
    const name = row.template_name;
    if (row.source_kind === 'MVP_POLICY') return row.lifecycle_backend === 'MVP' && row.dsl_version === 'MVP_V1' && ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'LongTermGoalPolicy', 'AssetAuthorizationPolicy'].includes(name) && row.configuration.type === (name === 'LongTermGoalPolicy' ? 'goal_saving' : templateTags[name]) && (!['LongTermGoalPolicy', 'AssetAuthorizationPolicy'].includes(name) || row.lifecycle_available === false);
    return row.dsl_version === 'FULL_V1' && row.configuration.type === templateTags[name] && (row.source_kind === 'FULL_POLICY' && row.lifecycle_backend === 'FULL' && lifecycleFor(name) === 'FULL' || row.source_kind === 'GOAL_BRIDGE' && row.lifecycle_backend === 'GOAL_BRIDGE' && name === 'LongTermGoalPolicy' && isRunId(row.goal_id));
  }
  context(value, environment); valid(Array.isArray(value.items) && value.items.every((row) => object(row) && route(row) && typeof row.lifecycle_available === 'boolean' && isRunId(row.policy_id) && isRunId(row.current_version_id) && (row.goal_id === undefined || row.goal_id === null || isRunId(row.goal_id)) && ['title', 'name', 'status', 'effective_status', 'execution_status', 'reason'].every((key) => typeof row[key] === 'string') && safeConfiguration(row.configuration) && digest(row.configuration_hash) && typeof row.planning_confirmed === 'boolean' && row.bank_authority === false));
  valid(new Set((value.items as Record<string, unknown>[]).map((row) => `${String(row.source_kind)}:${String(row.policy_id)}`)).size === (value.items as unknown[]).length);
  return value as PolicyRecords;
}
export function same(left: unknown, right: unknown): boolean { if (left === right) return true; if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => same(value, right[index])); if (!object(left) || !object(right)) return false; const keys = Object.keys(left); return keys.length === Object.keys(right).length && keys.every((key) => Object.hasOwn(right, key) && same(left[key], right[key])); }
export async function readPolicyCommand(original: PendingOperation, environment: NextState): Promise<OperationResult> {
  const result = await request<OperationResult & { original_request: { path: string; body: Record<string, unknown> } }>(`/zhiyu-next/policy-commands/${original.client_request_id}`);
  context(result, environment); valid(result.client_request_id === original.client_request_id && ['COMPLETED', 'PENDING', 'REJECTED'].includes(result.status) && object(result.original_request) && result.original_request.path === `/api/v1${original.path}` && same(result.original_request.body, original.body));
  if (result.status === 'COMPLETED') {
    if (original.path === '/zhiyu-next/policy-candidates') parsePolicyCandidate(result.result, original);
    else if (original.path === '/zhiyu-next/policy-commands/lifecycle') valid(object(result.result) && result.result.policy_id === original.body.policy_id && isRunId(result.result.current_version_id) && result.result.configuration_hash === original.body.reviewed_hash && result.result.bank_authority === false && result.result.planning_confirmed === (original.body.command === 'RESUME') && (original.body.command === 'RESUME' ? ['ACTIVE', 'CONFIRMED'].includes(String(result.result.status)) && result.result.current_version_id !== original.body.expected_version_id : result.result.status === (original.body.command === 'SUSPEND' ? 'SUSPENDED' : 'REVOKED') && result.result.current_version_id === original.body.expected_version_id));
    else valid(object(result.result) && result.result.planning_confirmed === true && result.result.bank_authority === false && result.result.candidate_id === original.body.candidate_id && result.result.configuration_hash === original.body.reviewed_hash);
  }
  return result;
}
export const getPolicyCatalog = (environment: NextState) => request('/zhiyu-next/policy-templates', 'GET', undefined, (value) => parsePolicyCatalog(value, environment));
export const getPolicySchema = (name: TemplateName, environment: NextState, version: 'MVP_V1' | 'FULL_V1' = 'FULL_V1') => request(`/zhiyu-next/policy-schema/${name}?dsl_version=${version}`, 'GET', undefined, (value) => parsePolicySchema(value, name, environment, version));
export const getPolicyRecords = (environment: NextState) => request('/zhiyu-next/policy-records', 'GET', undefined, (value) => parsePolicyRecords(value, environment));
