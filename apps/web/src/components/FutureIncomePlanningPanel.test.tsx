import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { futureCandidateBody, futureCandidateFixture, futureConfirmBody, futureConfirmationFixture, futureEpoch, futureLocalSessionFixture, futureLookupFixture, futurePlanningFixture, futureUnknownFixture, futureUser } from '../tests/future-income-planning-fixture';
import type { FutureIncomeCandidateBody, FutureIncomeConfirmBody } from '../api/future-income-planning';
let Panel: typeof import('./FutureIncomePlanningPanel').default;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); Panel = (await import('./FutureIncomePlanningPanel')).default; });
const props = { userId: futureUser, epochId: futureEpoch };
function install(options: { unknown?: boolean; responseLoss?: boolean; absent?: boolean; candidateExpired?: boolean } = {}) {
  let body = futureCandidateBody(), candidate = futureCandidateFixture(body), confirm = futureConfirmBody(candidate);
  return installHttpFixture((method, path, value) => {
    if (path.endsWith('/local-actor/session')) return futureLocalSessionFixture();
    if (path.endsWith('/planning/future-income')) return options.unknown ? futureUnknownFixture() : futurePlanningFixture();
    if (method === 'POST' && path.endsWith('/candidates')) { body = value as FutureIncomeCandidateBody; candidate = futureCandidateFixture(body); if (options.responseLoss) return new Response(JSON.stringify({ error: { code: 'SYNTHETIC_RESPONSE_LOSS', message: 'synthetic响应丢失' } }), { status: 500 }); return candidate; }
    if (method === 'POST' && path.endsWith('/confirm')) { confirm = value as FutureIncomeConfirmBody; return futureConfirmationFixture(candidate, confirm); }
    if (path.includes('/by-key/')) { const isConfirm = decodeURIComponent(path.split('/').at(-1)!) === confirm.idempotency_key; const lookup = futureLookupFixture(isConfirm ? 'CONFIRM' : 'CANDIDATE', isConfirm ? confirm : body, candidate, options.absent); if (options.candidateExpired && lookup.candidate) lookup.candidate.state = 'EXPIRED'; return lookup; }
    throw new Error(`SYNTHETIC_UNEXPECTED_${path}`);
  });
}
async function load() { fireEvent.click(screen.getByRole('button', { name: '只读加载当前条件规划与USER身份' })); await screen.findByRole('region', { name: '服务器365天条件规划' }); }
async function create() { await load(); const button = screen.getByRole('button', { name: '从选定原收入建立待确认候选' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await screen.findByRole('heading', { name: '原 CANDIDATE 待核对' }); }
async function readPending() { const button = screen.getByRole('button', { name: '独立读取原条件声明结果' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await waitFor(() => expect(screen.queryByRole('region', { name: '条件声明待核对原请求' })).not.toBeInTheDocument()); }
test('手动读真实路径、完整365且UNKNOWN为null，没有输入未来金额或自动金融执行', async () => {
  const calls = install(); render(<Panel {...props} />); expect(calls).toHaveLength(0); await load(); expect(calls.map(row => row.method)).toEqual(['GET', 'GET']);
  expect(screen.getByText(/365天条件合计 UNKNOWN/)).toBeVisible(); expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument(); expect(screen.getByText(/当前现金计入 ¥0.00/)).toBeVisible();
  const details = screen.getByText('完整365日期条件日程').closest('details')!; expect(details.querySelectorAll('li')).toHaveLength(365);
});
test('候选POST→独立GET→明确复核→确认POST→独立GET，绝不因HTTP直接解除门', async () => {
  const calls = install(); render(<Panel {...props} />); await create(); await screen.findByText(/原POST已返回/); expect(screen.queryByRole('button', { name: '明确确认这项条件假设' })).not.toBeInTheDocument();
  await readPending(); expect(screen.getByRole('button', { name: '明确确认这项条件假设' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /我完整复核来源/ })); fireEvent.click(screen.getByRole('button', { name: '明确确认这项条件假设' }));
  await screen.findByRole('heading', { name: '原 CONFIRM 待核对' }); await readPending(); expect(screen.getByRole('heading', { name: '原条件确认已记录' })).toBeVisible();
  const writes = calls.filter(row => row.method === 'POST'); expect(writes).toHaveLength(2); expect(Object.keys(writes[0]!.body as object)).toHaveLength(4); expect(writes[0]!.body).not.toHaveProperty('amount_cents'); expect(writes[1]!.body).toMatchObject({ accepted: true, reviewed_candidate_hash: futureCandidateFixture().candidate_hash, expected_epoch_id: futureEpoch });
});
test('响应丢失/NOT_FOUND留固定body/key，不自动重发，手动同请求只沿原键', async () => {
  const calls = install({ responseLoss: true, absent: true }); render(<Panel {...props} />); await create(); await screen.findByRole('alert');
  const button = screen.getByRole('button', { name: '独立读取原条件声明结果' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await screen.findByText(/NOT_FOUND不是最终未提交证明/);
  expect(screen.getByRole('heading', { name: '原 CANDIDATE 待核对' })).toBeVisible(); expect(calls.filter(row => row.method === 'POST')).toHaveLength(1);
  fireEvent.click(screen.getByRole('checkbox', { name: /我只恢复同一原body/ })); fireEvent.click(screen.getByRole('button', { name: '手动恢复同一条件声明请求' })); await waitFor(() => expect(calls.filter(row => row.method === 'POST')).toHaveLength(2));
  const writes = calls.filter(row => row.method === 'POST'); expect(writes[1]!.body).toEqual(writes[0]!.body);
});
test('UNKNOWN来源/expired候选不确认，不将收入证据当稳定工资或当前资金', async () => {
  const calls = install({ unknown: true }); render(<Panel {...props} />); await load(); expect(screen.getByRole('button', { name: '从选定原收入建立待确认候选' })).toBeDisabled(); expect(screen.getAllByText('ORIGINAL_INCOME_SOURCE_UNKNOWN', { selector: 'p' })).toHaveLength(2); expect(calls.every(row => row.method === 'GET')).toBe(true);
});
test('他族blocked仍可自身原GET，错误存储无POST，跨scope不确认旧候选', async () => {
  const calls = install(); const operation = await import('../features/future-income-operation'), api = await import('../api/future-income-planning');
  const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await operation.beginFutureIncomeOperation(intent); operation.endFutureIncomeAttempt();
  const view = render(<Panel {...props} mutationBlocked />); const button = await screen.findByRole('button', { name: '独立读取原条件声明结果' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); await screen.findByRole('heading', { name: '原条件候选待复核' });
  expect(api.isFreshFutureIncomeLookup(operation.getFutureIncomeOperation().workspace!)).toBe(true); expect(screen.getByRole('button', { name: '明确确认这项条件假设' })).toBeDisabled(); expect(calls.map(row => row.method)).toEqual(['GET']);
  view.rerender(<Panel {...props} epochId={futureUser} />); expect(screen.getByRole('checkbox', { name: /我完整复核来源/ })).toBeDisabled(); expect(calls).toHaveLength(1);
});
test('来源刷新废弃已复核状态；候选过确认窗只保原件而不确认', async () => {
  const calls = install({ candidateExpired: true }); render(<Panel {...props} />); await create(); await readPending(); expect(screen.getByRole('button', { name: '明确确认这项条件假设' })).toBeDisabled(); expect(calls.filter(row => row.path.endsWith('/confirm'))).toHaveLength(0);
});
test('存储拒绝保原意图且不发送候选POST', async () => {
  const calls = install(); render(<Panel {...props} />); await load(); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('SYNTHETIC_STORAGE_DENIED'); }); fireEvent.click(screen.getByRole('button', { name: '从选定原收入建立待确认候选' })); await screen.findAllByRole('alert'); expect(calls.every(row => row.method === 'GET')).toBe(true);
});
