import { isRunId } from '../api/decisions';
import type { NextState } from './api';

const key = (environment: NextState) => `zhiyu-agent-reference:${environment.environment_id}:${environment.dashboard.user_id}:${environment.epoch_id}`;
export function rememberAgent(id: string, environment: NextState) {
  if (!isRunId(id)) throw new Error('原对话引用尚未核实。');
  sessionStorage.setItem(key(environment), id);
  if (sessionStorage.getItem(key(environment)) !== id) throw new Error('原对话引用未能保留。');
}
export function restoreAgent(environment: NextState): string | null {
  const saved = sessionStorage.getItem(key(environment));
  if (saved !== null && !isRunId(saved)) throw new Error('原对话引用已损坏，请保留现场核实。');
  return saved;
}
export function forgetAgent(environment: NextState) { sessionStorage.removeItem(key(environment)); }
