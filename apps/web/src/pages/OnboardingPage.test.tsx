import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { OnboardingIntent } from '../features/onboarding-draft';
import { compilationFixture, declarationFixture, discoveryFixture, emergencyText, goalText, obligationProposal, onboardingBinding, proposalFixtures, reserveFixture, transactionFixture } from '../tests/onboarding-fixture';
import { dashboardFixture } from '../tests/dashboard-fixture';
import { stateFixture } from '../tests/demo-fixture';

// All HTTP bodies below are SYNTHETIC_HTTP_FIXTURE_ONLY; no bank/PG/browser proof.
let page: typeof import('./OnboardingPage'); let draft: typeof import('../features/onboarding-draft'); let queries: typeof import('@tanstack/react-query');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://unit-onboarding-page.local'); page = await import('./OnboardingPage'); draft = await import('../features/onboarding-draft'); queries = await import('@tanstack/react-query'); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
type Call = { path: string; method: string; body: Record<string, unknown> | undefined; raw: string | undefined };
type Options = { lose?: 'compile' | 'discovery' | 'declaration'; notFound?: boolean; wrongRecord?: boolean; emptyProposals?: boolean; readyReserve?: boolean; missingEpoch?: boolean; advanceDay?: boolean };
function transport(options: Options = {}) {
  const calls: Call[] = []; let declaration: OnboardingIntent | null = null; let declarationWrites = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const path = url.pathname; const method = init?.method ?? 'GET'; const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined; calls.push({ path, method, body, raw: init?.body === undefined ? undefined : String(init.body) });
    let value: unknown;
    if (path.endsWith('/accounts/summary')) value = dashboardFixture().account_facts.facts;
    else if (path.endsWith('/dashboard')) { const actual = dashboardFixture(); if (options.advanceDay) actual.as_of = '2026-10-05T02:00:00Z'; value = actual; }
    else if (path.endsWith('/demo/state')) value = options.missingEpoch ? { ...stateFixture(), available: false, epoch_id: null } : stateFixture();
    else if (path.endsWith('/transactions')) value = transactionFixture(Number(url.searchParams.get('offset')));
    else if (path.endsWith('/policy-proposals')) value = { simulation: true, items: options.emptyProposals ? [] : proposalFixtures() };
    else if (path.endsWith('/policies') || path.endsWith('/goals')) value = { simulation: true, items: [] };
    else if (path.endsWith('/living-reserve/estimate')) value = reserveFixture(options.readyReserve);
    else if (path.endsWith('/policies/discover')) { if (options.lose === 'discovery') throw new Error('UNIT_DISCOVERY_REPLY_LOST'); value = discoveryFixture(); }
    else if (path.endsWith('/policies/compile')) { if (options.lose === 'compile') throw new Error('UNIT_COMPILATION_REPLY_LOST'); value = compilationFixture(body?.text === goalText); }
    else if (path.includes('/policy-compilations/')) value = compilationFixture(path.endsWith(compilationFixture(true).compilation_id));
    else if (path.endsWith('/policy-declarations')) { declarationWrites++; declaration = draft.getOnboardingDraft().draft.pending; if (!declaration) throw new Error('UNIT_MISSING_ORIGINAL'); if (options.lose === 'declaration' && declarationWrites === 1) throw new Error('UNIT_DECLARATION_REPLY_LOST'); value = declarationFixture(declaration); }
    else if (path.includes('/policy-declarations/') && path.includes('/by-key/')) {
      const original = declaration ?? draft.getOnboardingDraft().draft.pending ?? (draft.getOnboardingDraft().draft.declarations.obligation ? { kind: 'OBLIGATION' as const, ...draft.getOnboardingDraft().draft.declarations.obligation! } : null); if (!original) throw new Error('UNIT_NO_ORIGINAL_LOOKUP'); const record = declarationFixture(original); if (options.wrongRecord) record.original_request = { ...record.original_request, idempotency_key: 'another-key' }; value = { simulation: true, grants_authority: false, not_found_is_final: false, status: options.notFound ? 'NOT_FOUND' : 'RECORDED', record: options.notFound ? null : record };
    } else throw new Error(`UNIT_UNEXPECTED_ROUTE ${path}`);
    return new Response(JSON.stringify(value));
  })); return calls;
}
function open() { const Component = page.default; const client = new queries.QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<queries.QueryClientProvider client={client}><Component /></queries.QueryClientProvider>); }
async function selectStep(number: number) { fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${number}\\. `) })); return screen.findByRole('region', { name: new RegExp(`^引导第${number}步 `) }); }
const writes = (calls: Call[]) => calls.filter((item) => item.method !== 'GET');
async function contextReady() { await screen.findByText(/服务日期 2026-10-04/); }
async function prepareObligation() { await selectStep(3); const select = await screen.findByRole('combobox', { name: '原义务候选' }); await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2)); fireEvent.change(select, { target: { value: obligationProposal } }); fireEvent.change(screen.getByLabelText('拟重要程度（0–100）'), { target: { value: '78' } }); fireEvent.change(screen.getByLabelText('拟开始日期'), { target: { value: '2026-10-05' } }); }

test('真实8步cursor和50/61交易分页；浏览末步不写、不声称完成或授权', async () => {
  const calls = transport(); open(); await contextReady(); await screen.findByText(/本页原交易 50 条 \/ 总数 61/); fireEvent.click(screen.getByRole('button', { name: '下一页原交易' })); await screen.findByText(/本页原交易 11 条 \/ 总数 61/); expect(calls.some((call) => call.path.endsWith('/transactions'))).toBe(true); await selectStep(8); expect(screen.getByText(/当前浏览步骤 8\/8/)).toBeVisible(); expect(screen.getByText(/原8步完整首次浏览器流程/)).toBeVisible(); expect(screen.getByText('当前安全闲置额（财务边界）')).toBeVisible(); expect(screen.getByText('UNKNOWN · 尚未合并当前资产授权与执行条件')).toBeVisible(); expect(writes(calls)).toHaveLength(0);
});
test('退出恢复原step/input，仅恢复草稿和GET；候选不自动compile/confirm', async () => {
  const original = draft.emptyOnboardingDraft(); original.step = 5; original.inputs.emergency_text = emergencyText; sessionStorage.setItem('bounded-funds-onboarding-draft-v1:http://unit-onboarding-page.local', JSON.stringify(original)); const calls = transport(); open(); await contextReady(); expect(await screen.findByRole('region', { name: /^引导第5步/ })).toBeVisible(); expect(screen.getByLabelText('应急策略原文')).toHaveValue(emergencyText); expect(writes(calls)).toHaveLength(0); expect(screen.queryByRole('region', { name: '原应急候选复核' })).not.toBeInTheDocument();
});
test('候选发现只在用户主动点击，按实际返回ID/跳过理由显示且不confirm', async () => {
  const calls = transport(); open(); await contextReady(); await selectStep(2); expect(writes(calls)).toHaveLength(0); fireEvent.click(screen.getByRole('button', { name: '用户主动从历史发现候选' })); await screen.findByText(/这次实际回执：新候选1、复用0；跳过1/); expect(screen.getByText(/SYNTHETIC_NO_LOAN_RULE/, { selector: 'li' })).toBeVisible(); expect(writes(calls)).toEqual([expect.objectContaining({ path: '/api/v1/policies/discover', raw: '{}' })]); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(draft.getOnboardingDraft().draft.discovery_proposal_ids).toEqual([obligationProposal]);
});
test('缺生活历史建议UNKNOWN且保56日/缺证据，无声明按钮或补0', async () => {
  const calls = transport(); open(); await contextReady(); await selectStep(4); fireEvent.click(screen.getByRole('button', { name: '只读估算生活准备金' })); const result = await screen.findByRole('region', { name: '真实生活准备金估算结果' }); await within(result).findByRole('heading', { name: 'INSUFFICIENT_HISTORY' }); expect(within(result).getByText(/建议准备金 UNKNOWN/)).toBeVisible(); expect(within(result).getByText(/实际日分母56/)).toBeVisible(); expect(within(result).getByText(/缺 56 天覆盖/)).toBeVisible(); expect(screen.queryByRole('button', { name: '用户主动提交生活保护候选（不确认）' })).not.toBeInTheDocument(); expect(writes(calls)).toHaveLength(0);
});
test('应急显式compile后GET原编译才存ref；goal仍需既有确认与账户登记，不自动授予', async () => {
  const calls = transport(); open(); await contextReady(); await selectStep(5); fireEvent.change(screen.getByLabelText('应急策略原文'), { target: { value: emergencyText } }); fireEvent.click(screen.getByRole('button', { name: '用户主动编译应急候选' })); await screen.findByRole('region', { name: '原应急候选复核' }); await screen.findByText(/原服务候选状态 PROPOSED/); expect(draft.getOnboardingDraft().draft.candidates.emergency!.input_text).toBe(emergencyText); expect(writes(calls)[0]!.body).toEqual({ text: emergencyText, engine: 'rules' }); expect(screen.getByRole('link', { name: '到策略中心读取并修订原编译、复核完整配置后明确确认' })).toHaveAttribute('href', '#policies');
  await selectStep(6); fireEvent.change(screen.getByLabelText('未来目标策略原文'), { target: { value: goalText } }); fireEvent.click(screen.getByRole('button', { name: '用户主动编译目标候选' })); await screen.findByRole('region', { name: '原目标候选复核' }); await screen.findByText(/原服务候选状态 PROPOSED/); expect(screen.getByText(/候选确认和目标账户登记分开/)).toBeVisible(); expect(writes(calls)).toHaveLength(2); expect(calls.every((call) => !call.path.endsWith('/confirm') && !call.path.endsWith('/execute'))).toBe(true);
});
test('编译丢响应保原text/日期，唯一原GET可以恢复候选且无POST重试', async () => {
  const calls = transport({ lose: 'compile' }); open(); await contextReady(); await selectStep(5); fireEvent.change(screen.getByLabelText('应急策略原文'), { target: { value: emergencyText } }); fireEvent.click(screen.getByRole('button', { name: '用户主动编译应急候选' })); await screen.findByText(/连接中断，请保留当前内容/); const original = draft.getOnboardingDraft().draft.pending; expect(original).not.toBeNull(); expect(screen.getByLabelText('应急策略原文')).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '只读核对原引导候选' })); await screen.findByText(/已通过唯一原提案\/原编译读取恢复候选身份/); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(draft.getOnboardingDraft().draft.candidates.emergency!.binding).toEqual(original!.binding); expect(writes(calls)).toHaveLength(1);
});
test('跨日期/空source编译读不能解除原请求；发现未知无法只靠列表冒原回执', async () => {
  const options: Options = { lose: 'compile', emptyProposals: true }; const calls = transport(options); open(); await contextReady(); await selectStep(5); fireEvent.change(screen.getByLabelText('应急策略原文'), { target: { value: emergencyText } }); fireEvent.click(screen.getByRole('button', { name: '用户主动编译应急候选' })); await screen.findByText(/连接中断，请保留当前内容/); const original = draft.getOnboardingDraft().draft.pending; options.advanceDay = true; fireEvent.click(screen.getByRole('button', { name: '只读核对原引导候选' })); await screen.findByText(/原用户\/周期\/日期已变化/); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); expect(writes(calls)).toHaveLength(1); expect(screen.queryByRole('button', { name: '手动重放原结构化候选（同原键/body）' })).not.toBeInTheDocument();
});
test('发现丢回复GET列表保pending，不自动新发现或当已确认', async () => {
  const calls = transport({ lose: 'discovery' }); open(); await contextReady(); await selectStep(2); fireEvent.click(screen.getByRole('button', { name: '用户主动从历史发现候选' })); await screen.findByText(/连接中断，请保留当前内容/); const original = draft.getOnboardingDraft().draft.pending; fireEvent.click(screen.getByRole('button', { name: '只读核对原引导候选' })); await screen.findByText(/发现接口缺按原请求查询的持久回执/); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); expect(screen.getByRole('button', { name: '用户主动从历史发现候选' })).toBeDisabled(); expect(writes(calls)).toHaveLength(1);
});
test('修改义务提交新完整候选，原source/hash不变，原键GET核对后仍独立confirm', async () => {
  const sourceBefore = proposalFixtures()[0]!.configuration; const calls = transport(); open(); await contextReady(); await prepareObligation(); fireEvent.click(screen.getByRole('button', { name: '用户主动提交完整义务候选（不确认）' })); await screen.findByText(/已原键核对这份USER_DECLARED新候选/); const section = await screen.findByRole('region', { name: '已登记的原结构化候选' }); await within(section).findByText(/grants_authority=false/); const write = writes(calls)[0]!; expect(write.body).toMatchObject({ configuration: { valid_from: '2026-10-05', priority: { importance: 78 }, amount_rule: sourceBefore.amount_rule, payee_id: sourceBefore.payee_id, auto_execute: sourceBefore.auto_execute }, expected_epoch_id: onboardingBinding().epoch_id, source_proposal_id: obligationProposal }); expect(draft.getOnboardingDraft().draft.declarations.obligation).not.toBeNull(); expect(calls.every((call) => !call.path.endsWith('/confirm'))).toBe(true); expect(writes(calls)).toHaveLength(1);
});
test('声明未知NOT_FOUND保门，显式同原key/body重放后匹配lookup解除，绝无换键自动POST', async () => {
  const options: Options = { lose: 'declaration', notFound: true }; const calls = transport(options); open(); await contextReady(); await prepareObligation(); fireEvent.click(screen.getByRole('button', { name: '用户主动提交完整义务候选（不确认）' })); await screen.findByText(/连接中断，请保留当前内容/); const original = draft.getOnboardingDraft().draft.pending; fireEvent.click(screen.getByRole('button', { name: '只读核对原引导候选' })); await screen.findByText(/NOT_FOUND · 不是最终未提交证明/); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); expect(screen.getByLabelText('拟重要程度（0–100）')).toBeDisabled(); expect(writes(calls)).toHaveLength(1);
  options.notFound = false; fireEvent.click(screen.getByRole('button', { name: '手动重放原结构化候选（同原键/body）' })); await screen.findByText(/已原键核对这份USER_DECLARED新候选/); expect(writes(calls)).toHaveLength(2); expect(writes(calls)[0]!.raw).toBe(writes(calls)[1]!.raw); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(calls.every((call) => !call.path.endsWith('/confirm'))).toBe(true);
});
test('声明原键返回另一原body保持pending；所有新输入/请求都blocked', async () => {
  const calls = transport({ lose: 'declaration', wrongRecord: true }); open(); await contextReady(); await prepareObligation(); fireEvent.click(screen.getByRole('button', { name: '用户主动提交完整义务候选（不确认）' })); await screen.findByText(/连接中断，请保留当前内容/); const original = draft.getOnboardingDraft().draft.pending; fireEvent.click(screen.getByRole('button', { name: '只读核对原引导候选' })); await screen.findByText(/原body和日期锚继续保留/); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); expect(screen.getByRole('button', { name: '用户主动提交完整义务候选（不确认）' })).toBeDisabled(); expect(writes(calls)).toHaveLength(1);
});
test('资产cap待补不写；明确完整容忍/恢复条件后只新候选、无银行或ScenarioRPC', async () => {
  const calls = transport(); open(); await contextReady(); await selectStep(7); expect(screen.getByRole('button', { name: '用户主动提交资产候选（不确认、不执行）' })).toBeDisabled(); fireEvent.change(screen.getByLabelText('拟自主总本金上限（元）'), { target: { value: '1500.03' } }); fireEvent.change(screen.getByLabelText('拟单次金额上限（元）'), { target: { value: '670.03' } }); fireEvent.click(screen.getByLabelText('拟允许无损自动安全恢复（仍须正式确认原策略）')); fireEvent.click(screen.getByRole('button', { name: '用户主动提交资产候选（不确认、不执行）' })); await screen.findByText(/已原键核对这份USER_DECLARED新候选/); expect(writes(calls)[0]!.body).toMatchObject({ configuration: { type: 'asset_authorization', max_auto_managed_cents: 150003, single_action_cap_cents: 67003, max_redemption_delay_days: 0, max_lock_days: 0, max_principal_risk_level: 0, allow_auto_recovery_without_penalty: true, allow_early_withdrawal_with_penalty: false }, source_proposal_id: null }); expect(writes(calls)).toHaveLength(1); expect(calls.every((call) => !/scenario|confirm|execute|bank/.test(call.path))).toBe(true);
});
test('READY生活估算只读后需第二次主动声明，不以估算成功直接确认', async () => {
  const calls = transport({ readyReserve: true }); open(); await contextReady(); await selectStep(4); fireEvent.click(screen.getByRole('button', { name: '只读估算生活准备金' })); await screen.findByRole('button', { name: '用户主动提交生活保护候选（不确认）' }); expect(writes(calls)).toHaveLength(0); fireEvent.click(screen.getByRole('button', { name: '用户主动提交生活保护候选（不确认）' })); await screen.findByText(/已原键核对这份USER_DECLARED新候选/); expect(writes(calls)[0]!.body!.configuration).toEqual(reserveFixture(true).candidate_configuration); expect(draft.getOnboardingDraft().draft.declarations.living).not.toBeNull(); expect(writes(calls)).toHaveLength(1);
});
