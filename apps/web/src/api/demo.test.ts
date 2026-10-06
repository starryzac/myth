import { afterEach, expect, test, vi } from 'vitest';
import { executeDemoAction, getDemoAction, getDemoCommand, getDemoPresets, getDemoState, resetDemo, sendDemoEvent } from './demo';
import { installHttpFixture } from '../tests/policy-fixture';
import { actionFixture, commandFixture, epochId, newEpochId, presetsFixture, stateFixture, demoActionId } from '../tests/demo-fixture';
import { request } from './http';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('控制台读取预设与原状态只走真实GET，未知假响应不能成为演示结果', async () => {
  const requests = installHttpFixture(() => ({ simulation: false }));
  await expect(getDemoPresets()).rejects.toThrow('模拟'); await expect(getDemoState()).rejects.toThrow('模拟');
  expect(requests.map((item) => `${item.method} ${item.path}`)).toEqual(['GET /api/v1/demo/presets', 'GET /api/v1/demo/state']);
});
test('正式七预设与四完整模板可读；非安全整数金额仍由默认HTTP guard拒绝', async () => {
  const presets = presetsFixture(); installHttpFixture(() => presets);
  expect((await getDemoPresets()).events).toHaveLength(7);
  presets.templates[0]!.configuration.target_cents = Number.MAX_SAFE_INTEGER + 1;
  await expect(getDemoPresets()).rejects.toThrow('金额');
});
test('事件只传固定kind和原epoch，返回不同轮次或命令身份会拒绝', async () => {
  const command = commandFixture(); const requests = installHttpFixture(() => command);
  await sendDemoEvent('SALARY_RECEIVED', epochId);
  expect(requests[0]!.body).toEqual({ event_kind: 'SALARY_RECEIVED', expected_epoch_id: epochId });
  command.epoch_id = newEpochId; await expect(sendDemoEvent('SALARY_RECEIVED', epochId)).rejects.toThrow('身份');
  command.epoch_id = epochId; await expect(getDemoCommand(demoActionId, epochId)).rejects.toThrow('身份');
});
test('状态显式null可用于reset初始化，undefined不可冒充无epoch', async () => {
  const state = stateFixture(); state.epoch_id = null; state.available = false; installHttpFixture(() => state);
  expect((await getDemoState()).epoch_id).toBeNull();
  Reflect.deleteProperty(state, 'epoch_id'); await expect(getDemoState()).rejects.toThrow('身份');
});
test('reset原键返回历史轮次与当前轮次，允许null并不捏造replayed', async () => {
  const requests = installHttpFixture(() => ({ simulation: true, reset_key: 'unit-original-reset', reset_epoch_id: epochId, epoch_id: newEpochId, seed_summary: { note: '原键历史回执，非当前余额' } }));
  const result = await resetDemo({ reset_key: 'unit-original-reset', expected_epoch_id: null, accepted: true });
  expect(result.reset_epoch_id).toBe(epochId); expect(result.epoch_id).toBe(newEpochId); expect(result).not.toHaveProperty('replayed');
  expect(requests[0]!.body).toEqual({ reset_key: 'unit-original-reset', expected_epoch_id: null, accepted: true });
});
test('具体执行仍绑定原action/effect hash，unsafe经济后果不能执行或展示', async () => {
  const action = actionFixture(); const requests = installHttpFixture(() => action);
  await executeDemoAction(action); expect(requests[0]).toEqual({ method: 'POST', path: `/api/v1/actions/${demoActionId}/execute`, body: {} });
  action.effect_hash = 'b'.repeat(64); await expect(getDemoAction(demoActionId)).rejects.toThrow('身份');
  action.effect_hash = action.prepared_validation.effect_hash; action.effect.amount_cents = Number.MAX_SAFE_INTEGER + 1;
  await expect(getDemoAction(demoActionId)).rejects.toThrow('金额');
});
test('嵌套模板或回执simulation:false不能被认可为真实模拟原件', async () => {
  const state = stateFixture(); Object.assign(state.templates[0]!, { simulation: false }); installHttpFixture(() => state);
  await expect(getDemoState()).rejects.toThrow('身份');
  const action = actionFixture(); action.receipt = { simulation: true, receipt_id: demoActionId, action_id: demoActionId, bank_operation_id: demoActionId, status: 'SUCCEEDED', executed_cents: 49400, fee_cents: 100, loss_cents: 500, posting_ids: [], occurred_at: action.as_of, reconciled_at: null };
  Object.assign(action.receipt, { simulation: false }); installHttpFixture(() => action); await expect(getDemoAction(demoActionId)).rejects.toThrow('身份');
});
test('跨页写请求串行；GET和POST财务预览可在原写请求期间读取，失败释放lease', async () => {
  let release!: (response: Response) => void;
  const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith('/original-write') ? new Promise<Response>((resolve) => { release = resolve; }) : Promise.resolve(new Response(JSON.stringify({ simulation: true }))));
  vi.stubGlobal('fetch', fetchMock);
  const original = request('/original-write', 'POST', {});
  await expect(request('/another-write', 'POST', {})).rejects.toThrow('正在处理');
  await expect(request('/demo/state')).resolves.toEqual({ simulation: true });
  await expect(request('/policies/unit/change-preview', 'POST', {})).resolves.toEqual({ simulation: true });
  release(new Response(JSON.stringify({ simulation: true }))); await original;
  await expect(request('/another-write', 'POST', {})).resolves.toEqual({ simulation: true });
  fetchMock.mockRejectedValueOnce(new Error('unit network')); await expect(request('/failed-write', 'POST', {})).rejects.toThrow('连接中断');
  await expect(request('/after-failure', 'POST', {})).resolves.toEqual({ simulation: true });
});
