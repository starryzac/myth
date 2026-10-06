/** Synthetic reader/component fixture; never original delivery, bank or product evidence. */
import type { Delivery, DeliveryAttempt } from '../api/delivery';
export const outboxId = '40000000-0000-4000-8000-000000000001';
export const actionId = '40000000-0000-4000-8000-000000000002';
export const epochId = '40000000-0000-4000-8000-000000000003';
export const attemptId = '40000000-0000-4000-8000-000000000004';
export const otherOutboxId = '40000000-0000-4000-8000-000000000005';
export function deliveryFixture(state: 'UNKNOWN' | 'VERIFIED' | 'ARCHIVED' | 'WAITING' | 'EMPTY' = 'UNKNOWN'): Delivery {
  const verified = state === 'VERIFIED'; const archived = state === 'ARCHIVED'; const empty = state === 'EMPTY';
  const actionStatus = archived ? 'NO_CURRENT_ACTION' : verified ? 'SUCCEEDED' : state === 'WAITING' || empty ? 'PLANNED' : 'UNKNOWN';
  const bankStatus = archived || state === 'WAITING' || empty ? null : 'SETTLED';
  const inbox = verified ? 'SERVICE_RECEIPT_VERIFIED' : archived ? 'STOPPED' : state === 'WAITING' ? 'WAITING_CONFIRMATION' : 'UNRESOLVED';
  const attempt: DeliveryAttempt = { attempt_id: attemptId, attempt_number: 1, state: inbox, started_at: '2026-10-05T12:00:00Z', finished_at: '2026-10-05T12:00:01Z', source_action_status: actionStatus, error: null,
    result: { action_id: actionId, root_id: actionId, attempt_id: attemptId, source_action_status: actionStatus, bank_status: bankStatus, service_receipt_verified: verified, economic_verified: false, deferred_reason: null, original_error_code: null } };
  return { simulation: true, outbox_id: outboxId, root_id: actionId, action_id: actionId, epoch_id: epochId, payload_hash: 'a'.repeat(64), outbox_state: verified ? 'DELIVERED' : archived ? 'STOPPED' : 'PENDING', inbox_state: empty ? null : inbox,
    source_action_status: actionStatus, bank_status: bankStatus, current_action_available: !archived, blocking_reason: archived ? 'NO_CURRENT_ACTION' : null, service_receipt_verified: verified, economic_verified: false, attempts: empty ? [] : [attempt], last_error: null, busy: false };
}
