import { useSyncExternalStore } from 'react';
let busy = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
export const isWriteInFlight = () => busy;
export function beginWriteFlight() {
  if (busy) throw new Error('已有资金或策略请求正在处理，请等待原请求完成');
  busy = true; emit();
}
export function endWriteFlight() { busy = false; emit(); }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useWriteInFlight = () => useSyncExternalStore(subscribe, isWriteInFlight, isWriteInFlight);
