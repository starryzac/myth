import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { request } from './http';

export type Delivery = components['schemas']['DeliveryView'];
export type DeliveryAttempt = components['schemas']['AttemptView'];
export type Deliveries = components['schemas']['DeliveryList'];
export type DeliveryIdentity = Pick<Delivery, 'outbox_id' | 'root_id' | 'action_id' | 'epoch_id' | 'payload_hash'>;
const originalResponses = new WeakMap<Delivery | Deliveries, string>();
export const getOriginalDeliveryResponse = (value: Delivery | Deliveries): string | null => originalResponses.get(value) ?? null;
const inboxStates = ['RECEIVED', 'PROCESSING', 'WAITING_CONFIRMATION', 'UNRESOLVED', 'SERVICE_RECEIPT_VERIFIED', 'STOPPED', 'FAILED'];
const routes = ['EXECUTE', 'RESUME', 'WAITING_CONFIRMATION', 'UNRESOLVED', 'STOPPED', 'VERIFIED'];
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const text = (value: unknown): value is string => typeof value === 'string';
const nonempty = (value: unknown): value is string => text(value) && value.length > 0;
const digest = (value: unknown) => text(value) && /^[0-9a-f]{64}$/.test(value);
const nullable = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
const timestamp = (value: unknown): value is string => text(value) && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const sameId = (left: string, right: string) => left.toLowerCase() === right.toLowerCase();
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('持久命令响应未通过原消息身份或尝试完整性校验'); }

function parseAttempt(value: unknown, index: number, delivery: DeliveryIdentity): DeliveryAttempt {
  requireValue(object(value) && uuid(value.attempt_id) && value.attempt_number === index + 1 && inboxStates.includes(value.state as string));
  requireValue(timestamp(value.started_at) && nullable(value.finished_at, timestamp) && nullable(value.source_action_status, nonempty) && nullable(value.error, text));
  requireValue(value.finished_at === null || Date.parse(value.finished_at as string) >= Date.parse(value.started_at));
  requireValue(value.result === null || object(value.result));
  if (value.result !== null) {
    const result = value.result;
    requireValue(object(result) && result.action_id === delivery.action_id && result.root_id === delivery.root_id && result.attempt_id === value.attempt_id);
    requireValue(result.source_action_status === value.source_action_status && nonempty(result.source_action_status) && nullable(result.bank_status, nonempty));
    requireValue(typeof result.service_receipt_verified === 'boolean' && result.economic_verified === false && nullable(result.deferred_reason, (reason) => routes.includes(reason as string)) && nullable(result.original_error_code, text));
    requireValue(value.finished_at !== null && result.original_error_code === value.error);
    if (result.service_receipt_verified) requireValue(['SUCCEEDED', 'RECONCILED'].includes(result.source_action_status) && result.bank_status === 'SETTLED');
  } else requireValue(value.finished_at === null);
  if (['RECEIVED', 'PROCESSING'].includes(value.state as string)) requireValue(value.finished_at === null);
  return value as DeliveryAttempt;
}

/** Presentation checks bind original identities; service flags are not independent bank verification. */
export function parseDelivery(value: unknown, originalText?: string, expected?: DeliveryIdentity | string): Delivery {
  requireValue(object(value) && value.simulation === true && value.economic_verified === false);
  requireValue(uuid(value.outbox_id) && uuid(value.root_id) && uuid(value.action_id) && uuid(value.epoch_id) && digest(value.payload_hash));
  requireValue(sameId(value.root_id, value.action_id) && ['PENDING', 'DELIVERED', 'STOPPED'].includes(value.outbox_state as string));
  requireValue(nullable(value.inbox_state, (state) => inboxStates.includes(state as string)) && nonempty(value.source_action_status) && nullable(value.bank_status, nonempty));
  requireValue(typeof value.current_action_available === 'boolean' && typeof value.service_receipt_verified === 'boolean' && typeof value.busy === 'boolean');
  requireValue(nullable(value.blocking_reason, text) && nullable(value.last_error, text) && Array.isArray(value.attempts));
  if (!value.current_action_available) requireValue(value.source_action_status === 'NO_CURRENT_ACTION' && value.bank_status === null && value.blocking_reason === 'NO_CURRENT_ACTION' && !value.service_receipt_verified);
  if (value.service_receipt_verified) requireValue(value.current_action_available && ['SUCCEEDED', 'RECONCILED'].includes(value.source_action_status) && value.bank_status === 'SETTLED');
  const delivery = value as Delivery;
  if (expected !== undefined) {
    if (typeof expected === 'string') requireValue(uuid(expected) && sameId(delivery.outbox_id, expected));
    else for (const key of ['outbox_id', 'root_id', 'action_id', 'epoch_id', 'payload_hash'] as const) {
      const original = expected[key];
      requireValue(key === 'payload_hash' ? digest(original) && value[key] === original : uuid(original) && text(value[key]) && sameId(value[key], original));
    }
  }
  value.attempts.forEach((attempt, index) => parseAttempt(attempt, index, delivery));
  requireValue(new Set(delivery.attempts.map((attempt) => attempt.attempt_id.toLowerCase())).size === delivery.attempts.length);
  requireValue((delivery.inbox_state === null) === (delivery.attempts.length === 0));
  if (originalText !== undefined) originalResponses.set(delivery, originalText);
  return delivery;
}
export function parseDeliveries(value: unknown, originalText?: string, limit = 50): Deliveries {
  requireValue(object(value) && value.simulation === true && value.economic_verified === false && Array.isArray(value.items) && value.items.length <= limit);
  const items = value.items.map((item) => parseDelivery(item));
  requireValue(new Set(items.map((item) => item.outbox_id.toLowerCase())).size === items.length && new Set(items.map((item) => item.action_id.toLowerCase())).size === items.length);
  const result = value as Deliveries;
  if (originalText !== undefined) originalResponses.set(result, originalText);
  return result;
}
export function getDeliveries(limit = 50): Promise<Deliveries> {
  requireValue(Number.isSafeInteger(limit) && limit >= 1 && limit <= 100);
  return request<Deliveries>(`/delivery?limit=${limit}`, 'GET', undefined, (value, original) => parseDeliveries(value, original, limit));
}
export function getDelivery(outboxId: string, expected?: DeliveryIdentity): Promise<Delivery> {
  requireValue(uuid(outboxId));
  if (expected !== undefined) requireValue(sameId(outboxId, expected.outbox_id));
  return request<Delivery>(`/delivery/${outboxId}`, 'GET', undefined, (value, original) => parseDelivery(value, original, expected ?? outboxId));
}
