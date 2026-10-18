import type { Compilation } from '../api/policies';
import { request } from '../api/http';
import { object } from '../features/policy-form';
import { parseState, type State } from '../zhiyu/api';
import { isRunId } from '../api/decisions';
import { templateNames, type TemplateName } from './policy-schema';

export type Capability = { id: string; title: string; status: string; reason?: string };
export type Autonomy = { state: 'ACTIVE' | 'PAUSED' | 'NOT_CONFIGURED'; authorization_id?: string | null; goal_id?: string | null; summary?: string | null; pending_count: number; last_result?: { summary?: string; status?: string } | string | null };
export type NextState = State & { variant: 'zhiyu-next'; autonomy: Autonomy; capabilities: Capability[] };
export type AgentFullReview = { template_name: TemplateName; configuration: Record<string, unknown>; source_status: 'ANCHORED' | 'EXPLICIT_FIELDS_REQUIRED'; source_text_sha256: string; requires_user_fields: boolean; bank_authority: false };
export type AgentReply = { simulation: true; client_request_id: string; reply: string; candidate?: { compilation: Compilation; summary: string; can_confirm: boolean } | null; questions: string[]; model_used: boolean; full_review?: AgentFullReview | null; facts?: { summary: string }[] };
export type ModelSettings = { simulation: true; enabled: boolean; configured: boolean; base_url: string; model: string; api_key_configured: boolean; api_key_mask: string | null; timeout_seconds: number; max_calls_per_turn: number };
export type ModelInput = { enabled: boolean; base_url: string; model: string; api_key?: string; timeout_seconds: number; max_calls_per_turn: number };
export type ConnectionResult = { simulation: true; status: string; connection: string; authentication: string; protocol: string; error_code?: string | null };
export type OperationResult = { simulation: true; client_request_id: string; status: 'COMPLETED' | 'PENDING' | 'REJECTED'; environment_id: string; epoch_id: string; action_id?: string | null; result?: Record<string, unknown> | null };
const valid = (condition: unknown) => { if (!condition) throw new Error('扩展环境响应未通过核实，请保留原请求。'); };
function metadataActivity(row: unknown): unknown {
  if (!object(row)) return row;
  const expected = row.intent === '开启自动安排' ? 'ACTIVE' : row.intent === '暂停自动安排' ? 'PAUSED' : row.intent === '实际模拟收入到账' ? 'SETTLED' : null;
  if (expected === null) return row;
  // Only these server-owned metadata rows have no financial action association.
  // Do not turn damaged action/goal references into an unassociated activity.
  valid(row.status === expected && (!('action_id' in row) || row.action_id === null) && (!('goal_id' in row) || row.goal_id === null) && (!('scenario' in row) || row.scenario === null));
  valid(expected === 'SETTLED' ? Number.isSafeInteger(row.amount_cents) && Number(row.amount_cents) >= 0 : row.amount_cents === null);
  return 'action_id' in row ? row : { ...row, action_id: null };
}
export function parseNextState(value: unknown): NextState {
  const adapted = object(value) && Array.isArray(value.activity) ? { ...value, activity: value.activity.map(metadataActivity) } : value;
  const state = parseState(adapted);
  valid(object(value) && value.variant === 'zhiyu-next' && object(value.autonomy) && ['ACTIVE', 'PAUSED', 'NOT_CONFIGURED'].includes(String(value.autonomy.state)) && Number.isSafeInteger(value.autonomy.pending_count) && Number(value.autonomy.pending_count) >= 0 && Array.isArray(value.capabilities) && value.capabilities.every((row) => object(row) && ['id', 'title', 'status'].every((key) => typeof row[key] === 'string')));
  return state as NextState;
}
export const getNextState = () => request('/zhiyu-next/state', 'GET', undefined, parseNextState);
export function parseAgentReply(result: AgentReply, client_request_id: string): AgentReply {
  valid(result.simulation === true);
  valid(result.client_request_id === client_request_id && typeof result.reply === 'string' && typeof result.model_used === 'boolean' && Array.isArray(result.questions) && result.questions.every((row) => typeof row === 'string'));
  if (result.candidate) {
    valid(object(result.candidate.compilation) && typeof result.candidate.summary === 'string' && typeof result.candidate.can_confirm === 'boolean');
    if (result.candidate.can_confirm) valid(object(result.candidate.compilation.configuration) && isRunId(result.candidate.compilation.proposal_id) && /^[0-9a-f]{64}$/.test(result.candidate.compilation.configuration_hash ?? ''));
  }
  if (result.full_review) {
    const review = result.full_review;
    valid(templateNames.includes(review.template_name) && object(review.configuration) && ['ANCHORED', 'EXPLICIT_FIELDS_REQUIRED'].includes(review.source_status) && /^[0-9a-f]{64}$/.test(review.source_text_sha256) && typeof review.requires_user_fields === 'boolean' && review.bank_authority === false);
    valid(review.requires_user_fields === (review.source_status === 'EXPLICIT_FIELDS_REQUIRED') && (!review.requires_user_fields || Object.keys(review.configuration).length === 0));
  }
  if (result.facts !== undefined) valid(Array.isArray(result.facts) && result.facts.every((row) => object(row) && typeof row.summary === 'string'));
  return result;
}
export async function messageAgent(text: string, engine: 'llm' | 'rules', client_request_id: string, parent_request_id?: string, context_request_id?: string, action_id?: string): Promise<AgentReply> {
  valid(isRunId(client_request_id) && (parent_request_id === undefined || isRunId(parent_request_id)));
  valid((context_request_id === undefined || isRunId(context_request_id)) && (action_id === undefined || isRunId(action_id)));
  const result = await request<AgentReply>('/zhiyu-next/agent/messages', 'POST', { text, engine, client_request_id, ...(parent_request_id ? { parent_request_id } : {}), ...(context_request_id ? { context_request_id } : {}), ...(action_id ? { action_id } : {}) });
  return parseAgentReply(result, client_request_id);
}
export function parseModelSettings(value: unknown): ModelSettings {
  valid(object(value) && ['enabled', 'configured', 'api_key_configured'].every((key) => typeof value[key] === 'boolean') && ['base_url', 'model'].every((key) => typeof value[key] === 'string') && (value.api_key_mask === null || typeof value.api_key_mask === 'string') && Number.isFinite(value.timeout_seconds) && Number.isSafeInteger(value.max_calls_per_turn) && !('api_key' in value));
  return value as ModelSettings;
}
export const getModelSettings = () => request('/zhiyu-next/model-settings', 'GET', undefined, parseModelSettings);
export const saveModelSettings = (input: ModelInput) => request('/zhiyu-next/model-settings', 'PUT', input, parseModelSettings);
export const testModelConnection = () => request<ConnectionResult>('/zhiyu-next/model-settings/test', 'POST', {});
export async function readOperation(id: string, environment: NextState): Promise<OperationResult> {
  valid(isRunId(id));
  const result = await request<OperationResult>(`/zhiyu-next/operations/${id}`);
  valid(result.client_request_id === id && result.environment_id === environment.environment_id && result.epoch_id === environment.epoch_id && ['COMPLETED', 'PENDING', 'REJECTED'].includes(result.status));
  return result;
}
