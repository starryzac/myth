import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { nextBodyFixture, nextIntentFixture, nextLookupFixture, nextPreviewFixture } from '../tests/full-recovery-next-fixture';
import { recoveryActionFixture, recoveryEpoch, recoveryFixtureHash, recoveryLocalSessionFixture, recoveryLookupFixture, recoveryUser } from '../tests/full-recovery-execution-fixture';
import type { RecoveryNextBody } from '../api/full-recovery-next';
let Panel: typeof import('./FullRecoveryNextPanel').default;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); Panel = (await import('./FullRecoveryNextPanel')).default; });
const body = nextBodyFixture(), props = { policyId: body.policy_id, userId: recoveryUser, expectedVersionId: body.expected_version_id, epochId: recoveryEpoch };
const rootKey = 'bounded-funds-full-recovery-next-preview-v2:http://http-unit-fixture.local', pendingKey = 'bounded-funds-full-recovery-execution-operation-v1:http://http-unit-fixture.local';
function savedDraft() { return { protocol: 'full-recovery-next-preview-browser-v2', user_id: recoveryUser, body, body_json: JSON.stringify(body), request_hash: recoveryFixtureHash(body) }; }
function install(options: { unknown?: boolean; losePrepare?: boolean; revoked?: boolean } = {}) {
  let request = body;
  return installHttpFixture((method, path, value) => {
    if (path.endsWith('/local-actor/session')) return recoveryLocalSessionFixture();
    if (path.endsWith('/full-recovery-next-actions/preview')) { request = value as RecoveryNextBody; return nextPreviewFixture(request, options.unknown ? 'UNKNOWN' : 'READY'); }
    if (path.includes('/full-recovery-next-actions/by-key/')) { const lookup = nextLookupFixture(request, 'PREPARED'); if (options.revoked && lookup.original_v1_lookup.action) lookup.original_v1_lookup.action.status = 'INVALIDATED'; return lookup; }
    if (method === 'POST' && path.endsWith('/full-recovery-actions/prepare')) return options.losePrepare ? new Response(JSON.stringify({ error: { code: 'TOOL_ONLY_LOSS', message: 'synthetic原prepare响应丢失' } }), { status: 500 }) : recoveryActionFixture();
    if (method === 'GET' && path.includes('/full-recovery-actions/by-key/')) return recoveryLookupFixture(nextIntentFixture(request));
    throw new Error(`TOOL_ONLY_UNEXPECTED_${method}_${path}`);
  });
}
async function preview() { const button = screen.getByRole('button', { name: '读取服务器下一整仓预览（保留原root）' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await screen.findByRole('heading', { name: '服务器结果 READY_TO_PREPARE' }); }
test('原完整服务器选择预览无金额/clock输入，prepare只保存真实v1 body/path，不调用v2prepare', async () => {
  const calls = install(), callback = vi.fn(); render(<Panel {...props} onPrepared={callback} />); expect(calls).toHaveLength(0); await preview(); expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '明确准备服务器选中的同一整仓（原v1）' })); await screen.findByText(/实际v1 body\/path已在POST前保存/);
  const stored = JSON.parse(sessionStorage.getItem(pendingKey)!); const sent = calls.find((row) => row.path.endsWith('/full-recovery-actions/prepare'))!;
  expect(sent.body).toEqual(stored.body); expect(stored.prepare_request).toEqual(stored.body); expect(stored.path).toBe('/full-recovery-actions/prepare'); expect(stored.body.idempotency_key).toMatch(/^next-whole-v2:[a-f0-9]{64}$/); expect(stored.body).toHaveProperty('position_id'); expect(stored.body).not.toHaveProperty('amount_cents'); expect(calls.filter((row) => row.path.endsWith('/full-recovery-next-actions/prepare'))).toHaveLength(0); expect(callback).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: '明确开始新的独立只读请求' })).toBeDisabled();
});
test('刷新保原root无自动网络，重复root GET后handoff只原v1 GET交回同Action，无POST', async () => {
  const calls = install(); sessionStorage.setItem(rootKey, JSON.stringify(savedDraft())); render(<Panel {...props} />); await screen.findByRole('region', { name: '保留的下一整仓root' }); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '独立GET原root结果' })); await screen.findByRole('heading', { name: '原root RECORDED' }); const handoff = screen.getByRole('button', { name: '只用原GET交回同一原动作工作区' }); await waitFor(() => expect(handoff).toBeEnabled()); fireEvent.click(handoff); await screen.findByText(/没有发送POST/);
  expect(calls.map((row) => row.method)).toEqual(['GET', 'GET']); expect(sessionStorage.getItem(pendingKey)).toBe(null); const workspace = JSON.parse(sessionStorage.getItem(`${pendingKey}:workspace`)!); expect(workspace.action.action_id).toBe(nextLookupFixture(body, 'PREPARED').original_v1_lookup.action!.action_id); expect(workspace.original_request).toEqual(nextIntentFixture(body).prepare_request);
});
test('prepare响应丢失保真实原键/body和旧门，不自动重新prepare或推进新仓', async () => {
  const calls = install({ losePrepare: true }); render(<Panel {...props} />); await preview(); fireEvent.click(screen.getByRole('button', { name: '明确准备服务器选中的同一整仓（原v1）' })); await screen.findByRole('alert');
  expect(JSON.parse(sessionStorage.getItem(pendingKey)!).kind).toBe('PREPARE'); expect(calls.filter((row) => row.path.endsWith('/full-recovery-actions/prepare'))).toHaveLength(1); expect(screen.getByRole('button', { name: '明确开始新的独立只读请求' })).toBeDisabled();
});
test('版本变化/撤销后原root仍可GET，但原版本不换当前版本handoff或新prepare', async () => {
  const calls = install({ revoked: true }); sessionStorage.setItem(rootKey, JSON.stringify(savedDraft())); render(<Panel {...props} expectedVersionId="00000000-0000-0000-0000-000000000999" />); await screen.findByRole('region', { name: '保留的下一整仓root' });
  fireEvent.click(screen.getByRole('button', { name: '独立GET原root结果' })); await screen.findByText(/原Action .*INVALIDATED/); expect(screen.getByRole('button', { name: '只用原GET交回同一原动作工作区' })).toBeDisabled(); expect(screen.getByRole('button', { name: '读取服务器下一整仓预览（保留原root）' })).toBeDisabled(); expect(calls.map((row) => row.method)).toEqual(['GET']); expect(sessionStorage.getItem(pendingKey)).toBe(null);
});
test('其它族门阻所有新POST与handoff，本族root GET仍可读取', async () => {
  const calls = install(); sessionStorage.setItem(rootKey, JSON.stringify(savedDraft())); render(<Panel {...props} mutationBlocked />); await screen.findByRole('region', { name: '保留的下一整仓root' }); fireEvent.click(screen.getByRole('button', { name: '独立GET原root结果' })); await screen.findByRole('heading', { name: '原root RECORDED' }); expect(screen.getByRole('button', { name: '只用原GET交回同一原动作工作区' })).toBeDisabled(); expect(calls.map((row) => row.method)).toEqual(['GET']);
});
test('UNKNOWN保未覆盖原因，不把无effect预览当READY', async () => {
  const calls = install({ unknown: true }); render(<Panel {...props} />); const button = screen.getByRole('button', { name: '读取服务器下一整仓预览（保留原root）' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await screen.findByRole('heading', { name: '服务器结果 UNKNOWN' }); expect(screen.getByRole('button', { name: '明确准备服务器选中的同一整仓（原v1）' })).toBeDisabled(); expect(calls.filter((row) => row.path.endsWith('/prepare'))).toHaveLength(0);
});
test('坏root草稿或保存失败保原字节，无HTTP与新prepare', async () => {
  const calls = install(); sessionStorage.setItem(rootKey, '{broken'); render(<Panel {...props} />); await screen.findByRole('alert'); expect(sessionStorage.getItem(rootKey)).toBe('{broken'); expect(screen.getByRole('button', { name: '明确开始新的独立只读请求' })).toBeDisabled(); expect(calls).toHaveLength(0);
});
test('只读root保存无权限，尚未发送预览/prepare，也不假关闭原金融门', async () => {
  const calls = install(); render(<Panel {...props} />); const button = screen.getByRole('button', { name: '读取服务器下一整仓预览（保留原root）' }); await waitFor(() => expect(button).toBeEnabled()); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_STORAGE_DENIED'); }); fireEvent.click(button); await screen.findAllByRole('alert'); expect(calls).toHaveLength(0); expect(screen.getByRole('button', { name: '明确开始新的独立只读请求' })).toBeDisabled();
});
