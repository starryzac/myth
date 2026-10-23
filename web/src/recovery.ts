import type { Candidate, CommandBody, Operation, Session } from './api';
import { cents } from './money';

export interface PendingCommand { version: 1; environment: string; user_id: string; key: string; plan: Candidate; body: CommandBody; operation_id?: string; recorded_at: string }
export function storageKey(session: Pick<Session, 'environment' | 'user_id'>): string { return `zhiyu-go:operation:v1:${session.environment}:${session.user_id}`; }
function validatePending(session: Session, value: PendingCommand): void {
  if (!value || typeof value !== 'object' || value.version !== 1 || value.environment !== session.environment || value.user_id !== session.user_id ||
      typeof value.key !== 'string' || !value.key || value.key.length > 160 || !value.plan || typeof value.plan !== 'object' ||
      typeof value.plan.id !== 'string' || !value.plan.id || typeof value.plan.policy_id !== 'string' || !value.plan.policy_id ||
      typeof value.plan.effect_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.plan.effect_hash) ||
      typeof value.plan.valid_until !== 'string' || !Number.isFinite(Date.parse(value.plan.valid_until)) || value.plan.status !== 'READY' ||
      !value.body || typeof value.body !== 'object' || value.body.plan_id !== value.plan.id || !['ONCE', 'STANDING'].includes(value.body.mode) ||
      Object.keys(value.body).some((key) => !['plan_id', 'mode', 'grant_id'].includes(key)) ||
      (value.body.mode === 'STANDING' && (typeof value.body.grant_id !== 'string' || !value.body.grant_id)) ||
      (value.body.mode === 'ONCE' && value.body.grant_id !== undefined) ||
      (value.operation_id !== undefined && (typeof value.operation_id !== 'string' || !value.operation_id)) ||
      typeof value.recorded_at !== 'string' || !Number.isFinite(Date.parse(value.recorded_at))) throw new Error('保存的原操作不完整，请保留记录并核对');
  if (BigInt(cents(value.plan.amount)) <= 0n || BigInt(cents(value.plan.max_safe)) < BigInt(value.plan.amount)) throw new Error('保存的原计划金额不一致，请保留记录并核对');
}
export function savePending(session: Session, value: PendingCommand, storage: Storage = localStorage): void {
  validatePending(session, value);
  storage.setItem(storageKey(session), JSON.stringify(value));
  if (storage.getItem(storageKey(session)) !== JSON.stringify(value)) throw new Error('无法完整保存原操作，尚未发送');
}
export function restorePending(session: Session, storage: Storage = localStorage): PendingCommand | null {
  const raw = storage.getItem(storageKey(session));
  if (!raw) return null;
  let value: PendingCommand;
  try { value = JSON.parse(raw) as PendingCommand; } catch { throw new Error('保存的原操作无法读取，请保留记录并核对'); }
  validatePending(session, value);
  return value;
}
export function bindOperation(pending: PendingCommand, operation: Operation): PendingCommand {
  if (operation.plan_id !== pending.body.plan_id || operation.amount_cents !== pending.plan.amount || (pending.operation_id && pending.operation_id !== operation.id)) throw new Error('服务端返回了另一笔操作，已停止自动核对');
  return { ...pending, operation_id: operation.id };
}
export function nextPoll(count: number): number { return Math.min(15000, 1000 * 2 ** Math.min(count, 4)); }
