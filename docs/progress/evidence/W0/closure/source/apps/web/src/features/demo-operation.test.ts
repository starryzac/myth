import { afterEach, expect, test, vi } from 'vitest';
import { beginDemoOperation, endDemoOperation, getDemoOperation, recoverDemoOperation } from './demo-operation';

afterEach(() => { endDemoOperation(true); sessionStorage.clear(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
test('演示命令全局单飞，组件离页后原身份仍保留，未知结果不被下一命令覆盖', () => {
  const original = { epoch_id: '10000000-0000-0000-0000-000000000080', kind: 'event' as const, event_kind: 'SALARY_RECEIVED' };
  beginDemoOperation(original); expect(getDemoOperation().busy).toBe(true);
  expect(() => beginDemoOperation({ ...original, event_kind: 'LARGE_CONSUMPTION' })).toThrow();
  endDemoOperation(false); expect(getDemoOperation().pending).toEqual(original); expect(getDemoOperation().busy).toBe(false);
  expect(() => beginDemoOperation({ ...original, event_kind: 'LARGE_CONSUMPTION' })).toThrow();
  beginDemoOperation(original); endDemoOperation(true); expect(getDemoOperation().pending).toBeNull();
});
test('重新加载只恢复固定身份，不自动发HTTP；reset原key跨连接保留', () => {
  vi.stubGlobal('fetch', vi.fn()); const original = { epoch_id: '10000000-0000-0000-0000-000000000080', kind: 'reset' as const, reset_key: 'unit-reset-original' };
  beginDemoOperation(original); endDemoOperation(false); expect(recoverDemoOperation().pending).toEqual(original); expect(fetch).not.toHaveBeenCalled();
});
test('无epoch只能以明确reset初始化，不能给事件或模板伪造null轮次', () => {
  beginDemoOperation({ kind: 'reset', epoch_id: null, reset_key: 'unit-first-reset' }); endDemoOperation(true);
  expect(() => beginDemoOperation({ kind: 'event', epoch_id: null, event_kind: 'SALARY_RECEIVED' })).toThrow('身份');
});
test('浏览器恢复元数据必须包含对应kind的原身份，不完整reset/action不得成为恢复命令', () => {
  expect(() => beginDemoOperation({ kind: 'reset', epoch_id: null })).toThrow('身份');
  expect(() => beginDemoOperation({ kind: 'action-execute', epoch_id: '10000000-0000-0000-0000-000000000090' })).toThrow('身份');
});
test('原身份保存失败时不进入发送阶段，保留错误供只读核对', () => {
  const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('unit storage denied'); });
  expect(() => beginDemoOperation({ kind: 'reset', epoch_id: null, reset_key: 'unit-save-failure' })).toThrow('无法保存');
  expect(getDemoOperation().busy).toBe(false); expect(getDemoOperation().storage_error).not.toBeNull(); save.mockRestore();
});
