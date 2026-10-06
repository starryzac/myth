import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { isRunId } from './decisions';

export type DemoPresets = components['schemas']['DemoPresets'];
export type DemoState = components['schemas']['DemoState'];
export type DemoCommand = components['schemas']['DemoCommandView'];
export type DemoTemplate = components['schemas']['DemoTemplateView'];
export type DemoReset = components['schemas']['DemoResetResponse'];
export type DemoEventKind = components['schemas']['DemoEventRequest']['event_kind'];
export type DemoTemplateKind = DemoTemplate['kind'];
export type DemoAction = components['schemas']['ActionResponse'];
export const eventKinds = ['SALARY_RECEIVED', 'CREATE_CAR_GOAL', 'LARGE_CONSUMPTION', 'AUTO_REDEEM', 'FIXED_EARLY_WITHDRAWAL', 'CHANGE_RENT'] as const;
export const templateKinds = ['CAR_GOAL', 'LIQUID_ASSET', 'FIXED_ASSET', 'RENT'] as const;
const statuses = ['WAITING_TEMPLATE', 'WAITING_ACTION_CONFIRMATION', 'WAITING_POLICY_CHANGE', 'UNKNOWN', 'COMPLETED', 'BLOCKED', 'PENDING'];
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('演示响应未通过原身份或完整性校验'); }
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const optionalId = (value: unknown) => value == null || isRunId(value);
const timestamp = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const nonnegative = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
function validTemplate(value: unknown): boolean {
  return object(value) && value.simulation === true && templateKinds.includes(value.kind as DemoTemplateKind) && typeof value.title === 'string' && object(value.configuration) && digest(value.configuration_hash) &&
    typeof value.status === 'string' && [value.proposal_id, value.evidence_id, value.confirmed_policy_id].every(optionalId);
}
function validAction(value: unknown): value is DemoAction {
  if (!object(value) || !object(value.effect) || !object(value.prepared_validation)) return false;
  const effect = value.effect; const receipt = object(value.receipt) ? value.receipt : null;
  return object(value) && value.simulation === true && isRunId(value.action_id) && isRunId(value.user_id) && isRunId(value.decision_run_id) && typeof value.status === 'string' && typeof value.autonomy_level === 'string' &&
    digest(value.effect_hash) && object(value.effect) && value.effect.operation_id === value.action_id && value.effect.user_id === value.user_id && object(value.prepared_validation) &&
    value.prepared_validation.effect_hash === value.effect_hash && timestamp(value.prepared_at) && timestamp(value.as_of) &&
    value.effect.simulation === true && value.prepared_validation.simulation === true && value.prepared_validation.financial_only === true &&
    ['TRANSFER_INTERNAL', 'PAY_RECURRING', 'ALLOCATE_GOAL', 'PURCHASE_ASSET', 'REDEEM_ASSET'].includes(value.effect.action_type as string) &&
    ['amount_cents', 'fee_cents', 'loss_cents', 'settlement_delay_days'].every((key) => nonnegative(effect[key])) && timestamp(value.effect.valid_from) && timestamp(value.effect.expires_at) &&
    (value.receipt == null || (object(value.receipt) && value.receipt.simulation === true && value.receipt.action_id === value.action_id && isRunId(value.receipt.receipt_id) && isRunId(value.receipt.bank_operation_id) &&
      ['executed_cents', 'fee_cents', 'loss_cents'].every((key) => nonnegative(receipt?.[key])) && timestamp(value.receipt.occurred_at)));
}
function validCommand(value: unknown): value is DemoCommand {
  return object(value) && value.simulation === true && value.preset_version === 'demo-console-v1' && isRunId(value.command_id) && isRunId(value.epoch_id) &&
    eventKinds.includes(value.event_kind as DemoEventKind) && statuses.includes(value.status as string) && typeof value.message === 'string' && timestamp(value.admitted_at) &&
    Array.isArray(value.proposal_ids) && value.proposal_ids.every(isRunId) && optionalId(value.goal_id) && Array.isArray(value.actions) && value.actions.every(validAction) &&
    (value.fact == null || (object(value.fact) && value.fact.simulation === true && isRunId(value.fact.external_fact_id) && ['ACCEPTED', 'SETTLED', 'UNKNOWN', 'REJECTED'].includes(value.fact.bank_status as string) && ['PENDING', 'UNKNOWN', 'PROJECTED'].includes(value.fact.projection_status as string))) &&
    (value.recovery == null || (object(value.recovery) && value.recovery.simulation === true && isRunId(value.recovery.run_id) && object(value.recovery.plan) && object(value.recovery.actual_boundary) && Array.isArray(value.recovery.actions))) &&
    (value.policy_change == null || (object(value.policy_change) && isRunId(value.policy_change.policy_id) && isRunId(value.policy_change.expected_version_id) && object(value.policy_change.configuration) &&
      digest(value.policy_change.reviewed_hash) && typeof value.policy_change.reason === 'string' && typeof value.policy_change.idempotency_key === 'string'));
}
export async function getDemoPresets(): Promise<DemoPresets> {
  const result = await request<DemoPresets>('/demo/presets');
  requireValue(result.preset_version === 'demo-console-v1' && Array.isArray(result.templates) && result.templates.length === 4 && result.templates.every(validTemplate) && new Set(result.templates.map((item) => item.kind)).size === 4 &&
    Array.isArray(result.events) && result.events.length === 7 && new Set(result.events.map((item) => item.event_kind)).size === 7 &&
    result.events.every((item) => [...eventKinds, 'RESET'].includes(item.event_kind) && typeof item.title === 'string' && typeof item.description === 'string' && Array.isArray(item.required_templates) && item.required_templates.every((kind) => templateKinds.includes(kind))));
  return result;
}
export async function getDemoState(): Promise<DemoState> {
  const result = await request<DemoState>('/demo/state');
  requireValue(result.preset_version === 'demo-console-v1' && (result.epoch_id === null || isRunId(result.epoch_id)) && typeof result.available === 'boolean' && (!result.available || isRunId(result.epoch_id)) &&
    Array.isArray(result.templates) && result.templates.every(validTemplate) && Array.isArray(result.commands) && result.commands.every((item) => validCommand(item) && item.epoch_id === result.epoch_id) &&
    new Set(result.commands.map((item) => item.event_kind)).size === result.commands.length);
  return result;
}
export async function prepareDemoTemplate(kind: DemoTemplateKind, epoch: string): Promise<DemoTemplate> {
  requireValue(templateKinds.includes(kind) && isRunId(epoch));
  const result = await request<DemoTemplate>(`/demo/templates/${kind}/prepare`, 'POST', { expected_epoch_id: epoch } satisfies components['schemas']['DemoTemplateRequest']);
  requireValue(validTemplate(result) && result.kind === kind); return result;
}
export async function sendDemoEvent(event_kind: DemoEventKind, expected_epoch_id: string): Promise<DemoCommand> {
  requireValue(eventKinds.includes(event_kind) && isRunId(expected_epoch_id));
  const result = await request<DemoCommand>('/demo/events', 'POST', { event_kind, expected_epoch_id } satisfies components['schemas']['DemoEventRequest']);
  requireValue(validCommand(result) && result.event_kind === event_kind && result.epoch_id === expected_epoch_id); return result;
}
export async function getDemoCommand(id: string, epoch: string): Promise<DemoCommand> {
  requireValue(isRunId(id) && isRunId(epoch)); const result = await request<DemoCommand>(`/demo/commands/${id}`);
  requireValue(validCommand(result) && result.command_id === id && result.epoch_id === epoch); return result;
}
export async function resetDemo(body: components['schemas']['DemoResetRequest']): Promise<DemoReset> {
  requireValue(body.accepted === true && (body.expected_epoch_id === null || isRunId(body.expected_epoch_id)) && typeof body.reset_key === 'string' && body.reset_key.trim().length > 0);
  const result = await request<DemoReset>('/demo/reset', 'POST', body);
  requireValue(result.reset_key === body.reset_key && isRunId(result.epoch_id) && isRunId(result.reset_epoch_id) && object(result.seed_summary)); return result;
}
export async function getDemoAction(id: string): Promise<DemoAction> {
  requireValue(isRunId(id)); const result = await request<DemoAction>(`/actions/${id}`); requireValue(validAction(result) && result.action_id === id); return result;
}
export async function confirmDemoAction(action: DemoAction): Promise<DemoAction> {
  requireValue(validAction(action)); const result = await request<DemoAction>(`/actions/${action.action_id}/confirm`, 'POST', { effect_hash: action.effect_hash, accepted: true } satisfies components['schemas']['ConfirmActionRequest']);
  requireValue(validAction(result) && result.action_id === action.action_id && result.effect_hash === action.effect_hash); return result;
}
export async function executeDemoAction(action: DemoAction): Promise<DemoAction> {
  requireValue(validAction(action)); const result = await request<DemoAction>(`/actions/${action.action_id}/execute`, 'POST', {});
  requireValue(validAction(result) && result.action_id === action.action_id && result.effect_hash === action.effect_hash); return result;
}
