import { isRunId } from '../api/decisions';
import { object } from '../features/policy-form';
import type { NextState } from './api';

export const REMINDER_CHECK_PATH = '/zhiyu-next/interventions/check-current';
export type ReminderCheck = { simulation: true; protocol: 'zhiyu-next-intervention-check-v1'; user_id: string; epoch_id: string; client_request_id: string; observation_run_id: string; kind: 'BoundaryCrossed' | 'BoundaryObserved' | null; source_complete: boolean; notification_status: 'OBSERVED' | 'ORIGINAL_RECOVERED' | 'NOT_CROSSED' | 'SOURCE_UNVERIFIED' | 'NOT_VERIFIED'; bank_authority: false; financial_write: false; authority_granted: false; delivered: false; acknowledged: false };
export function parseReminderCheck(value: unknown, environment: NextState, requestId: string): ReminderCheck {
  const keys = ['simulation', 'protocol', 'user_id', 'epoch_id', 'client_request_id', 'observation_run_id', 'kind', 'source_complete', 'notification_status', 'bank_authority', 'financial_write', 'authority_granted', 'delivered', 'acknowledged'];
  if (!object(value) || Object.keys(value).sort().join('|') !== keys.sort().join('|') || value.simulation !== true || value.protocol !== 'zhiyu-next-intervention-check-v1' || value.user_id !== environment.dashboard.user_id || value.epoch_id !== environment.epoch_id || value.client_request_id !== requestId || !isRunId(value.observation_run_id) || ![null, 'BoundaryCrossed', 'BoundaryObserved'].includes(value.kind as string | null) || typeof value.source_complete !== 'boolean' || !['OBSERVED', 'ORIGINAL_RECOVERED', 'NOT_CROSSED', 'SOURCE_UNVERIFIED', 'NOT_VERIFIED'].includes(String(value.notification_status)) || ['bank_authority', 'financial_write', 'authority_granted', 'delivered', 'acknowledged'].some(key => value[key] !== false) || (!value.source_complete && ['OBSERVED', 'ORIGINAL_RECOVERED'].includes(String(value.notification_status)))) throw new Error('原提醒核对结果无法验真，请保留原请求。');
  return value as ReminderCheck;
}
export function reminderCheckNotice(value: ReminderCheck): string {
  if (!value.source_complete) return '当前来源尚未完整核实，观察已保留；请继续处理页面中的必要问题。';
  if (['SOURCE_UNVERIFIED', 'NOT_VERIFIED'].includes(value.notification_status)) return '原观察已保留，提醒结果仍待核实；资金权限未改变。';
  if (value.kind === 'BoundaryCrossed') return '当前提醒已核对，请查看记录页的提醒状态。';
  return '当前提醒已核对，没有新增需要处理的边界变化。';
}
