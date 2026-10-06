import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import FullPolicyCompilerPanel from './FullPolicyCompilerPanel';
import { compiledCandidateFixture, compilerFixture, compilerGrammarFixture, compilerUser } from '../tests/full-policy-compiler-fixture';
import type { FullCompilation } from '../api/full-policy-compilation';
type Call = { path: string; method: string; body: unknown };
beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open(props: Parameters<typeof FullPolicyCompilerPanel>[0] = {}) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><FullPolicyCompilerPanel userId={compilerUser} {...props} /></QueryClientProvider>); }
function transport(options: { unknown?: boolean; failCompile?: boolean; edited?: boolean; comparison?: boolean; defer?: Promise<FullCompilation> } = {}) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-natural.local'); const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input)).pathname, body = init?.body ? JSON.parse(String(init.body)) : undefined; calls.push({ path, method: init?.method ?? 'GET', body });
    if (path.endsWith('/grammar')) return new Response(JSON.stringify(compilerGrammarFixture()));
    if (path.endsWith('/validate')) { const result = compiledCandidateFixture(); if (options.edited) { result.normalized_configuration = { ...result.normalized_configuration, amount_cents: 420007 }; result.configuration_hash = 'b'.repeat(64); } return new Response(JSON.stringify(result)); }
    if (options.failCompile) throw new Error('UNIT_COMPILER_REPLY_LOST');
    const result = options.defer ? await options.defer : await compilerFixture(body.text as string);
    if (options.unknown) { result.compilation.status = 'MISSING'; result.compilation.configuration = null; result.compilation.configuration_hash = null; result.compilation.issues = [{ code: 'MISSING_FIELD', field: 'amount_cents', message: 'SYNTHETIC_AMOUNT_MISSING', source_fragment: '' }]; }
    if (options.comparison) { result.comparison_source = 'USER_PROVIDED_CANDIDATE_NOT_CURRENT_VERSION'; result.compilation.differences = [{ field: 'amount_cents', before: 250000, after: 300000, explanation: 'SYNTHETIC_USER_CANDIDATE_DIFF' }]; }
    return new Response(JSON.stringify(result));
  })); return calls;
}
async function compile() { fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '保留3000元应急金' } }); fireEvent.click(screen.getByRole('button', { name: '只读编译完整候选' })); return screen.findByRole('region', { name: '完整自然策略编译结果' }); }
async function validate() { fireEvent.click(screen.getByRole('button', { name: '仅校验当前编辑候选' })); await screen.findByText(/当前编辑校验hash/); }
test('十二句式为服务器合成说明，手动填入不会编译或确认', async () => {
  const calls = transport(); open(); await screen.findByText('SYNTHETIC_READONLY_TWELVE_GRAMMAR'); expect(screen.getByText(/以下为合成句式说明/)).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '填入RecoveryPolicy句式' })); expect(screen.getByLabelText('完整策略原句')).toHaveValue('RecoveryPolicy · SYNTHETIC_CONTROLLED_SENTENCE'); expect(calls).toHaveLength(1); expect(calls[0]!.method).toBe('GET');
});
test('原句只提交preview，候选/默认/原来源未授权限且不能自动填父草稿', async () => {
  const calls = transport(); const onCandidate = vi.fn(); open({ onCandidate }); await compile(); expect(screen.getByText(/READY_FOR_REVIEW · 仍需用户完整复核/)).toBeVisible(); expect(screen.getByText(/引用 NOT_SERVER_VERIFIED/)).toBeVisible(); expect(screen.getByText(/原schema默认字段/)).toHaveTextContent('name、valid_from、valid_until'); expect(onCandidate).not.toHaveBeenCalled(); expect(screen.queryByRole('button', { name: '作为待验证草稿使用' })).not.toBeInTheDocument(); expect(calls.filter((c) => c.method === 'POST')).toEqual([{ path: '/api/v1/full-policy-compilations/preview', method: 'POST', body: { text: '保留3000元应急金', engine: 'rules' } }]);
});
test('手动编辑→原严格validate→手动回调完整normalized/hash，不POST确认', async () => {
  const calls = transport({ edited: true }); const onCandidate = vi.fn(); open({ onCandidate }); await compile(); const config = { ...compiledCandidateFixture().normalized_configuration, amount_cents: 420007 }; fireEvent.change(screen.getByLabelText('自然候选完整配置JSON（金额为整数分）'), { target: { value: JSON.stringify(config) } }); await validate(); expect(onCandidate).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '作为待验证草稿使用' })); expect(onCandidate).toHaveBeenCalledWith({ templateName: 'EmergencyBufferPolicy', configuration: config, configurationHash: 'b'.repeat(64) }); expect(calls.filter((c) => c.path.endsWith('/validate'))[0]!.body).toEqual({ template_name: 'EmergencyBufferPolicy', dsl_version: 'FULL_V1', configuration: config }); expect(calls.every((c) => !/confirm|activate|execute/.test(c.path))).toBe(true);
});
test('编辑或原句变化立刻清旧校验/候选，没有自动重新提交', async () => {
  const calls = transport(); open({ onCandidate: vi.fn() }); await compile(); await validate(); fireEvent.change(screen.getByLabelText('自然候选完整配置JSON（金额为整数分）'), { target: { value: '{}' } }); expect(screen.queryByRole('button', { name: '作为待验证草稿使用' })).not.toBeInTheDocument(); fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '新的明确原句' } }); expect(screen.queryByRole('region', { name: '完整自然策略编译结果' })).not.toBeInTheDocument(); expect(calls).toHaveLength(3);
});
test('错误bool/fraction金额在发送validate之前拒绝，原失败不变成功', async () => {
  const calls = transport(); open(); await compile(); fireEvent.change(screen.getByLabelText('自然候选完整配置JSON（金额为整数分）'), { target: { value: '{"type":"emergency_buffer","amount_cents":true}' } }); fireEvent.click(screen.getByRole('button', { name: '仅校验当前编辑候选' })); expect(await screen.findByRole('alert')).toHaveTextContent('无法精确显示'); expect(calls.filter((c) => c.path.endsWith('/validate'))).toHaveLength(0);
});
test('MISSING具体不足保留分母，没有编辑/确认或可用配置', async () => {
  transport({ unknown: true }); const onCandidate = vi.fn(); open({ onCandidate }); await compile(); expect(screen.getByText(/MISSING · 仍需用户完整复核/)).toBeVisible(); expect(screen.getByLabelText('候选具体不足')).toHaveTextContent('SYNTHETIC_AMOUNT_MISSING'); expect(screen.queryByLabelText('自然候选完整配置JSON（金额为整数分）')).not.toBeInTheDocument(); expect(onCandidate).not.toHaveBeenCalled();
});
test('跨family门阻止新preview及采用，目录GET仍可只读', async () => {
  const calls = transport(); open({ mutationBlocked: true, onCandidate: vi.fn() }); await screen.findByText('SYNTHETIC_READONLY_TWELVE_GRAMMAR'); fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '保留3000元应急金' } }); expect(screen.getByRole('button', { name: '只读编译完整候选' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '只读编译完整候选' })); expect(calls).toHaveLength(1);
});
test('响应丢失无自动retry，晚到旧响应不能覆盖新原句', async () => {
  let resolve: ((value: FullCompilation) => void) | undefined; const deferred = new Promise<FullCompilation>((r) => { resolve = r; }); const calls = transport({ defer: deferred }); open(); fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '保留3000元应急金' } }); fireEvent.click(screen.getByRole('button', { name: '只读编译完整候选' })); await waitFor(() => expect(calls).toHaveLength(2)); fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '后来改写的原句' } }); resolve!(await compilerFixture()); await waitFor(() => expect(screen.getByRole('button', { name: '只读编译完整候选' })).toBeEnabled()); expect(screen.queryByRole('region', { name: '完整自然策略编译结果' })).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
test('网络失败显示真实UNKNOWN且没有清旧数据成成功或自动重复POST', async () => {
  const calls = transport({ failCompile: true }); open(); fireEvent.change(screen.getByLabelText('完整策略原句'), { target: { value: '保留3000元应急金' } }); fireEvent.click(screen.getByRole('button', { name: '只读编译完整候选' })); expect(await screen.findByRole('alert')).toHaveTextContent('连接中断'); expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1); expect(screen.queryByRole('region', { name: '完整自然策略编译结果' })).not.toBeInTheDocument();
});
test('比较完整用户候选发送明确source，差异不称当前版本差异', async () => {
  const calls = transport({ comparison: true }); open(); fireEvent.click(screen.getByLabelText('与我提供的另一个配置候选比较（不是当前策略版本）')); fireEvent.change(screen.getByLabelText('比较候选模板'), { target: { value: 'EmergencyBufferPolicy' } }); const config = { ...compiledCandidateFixture().normalized_configuration, amount_cents: 250000 }; fireEvent.change(screen.getByLabelText('比较候选完整JSON'), { target: { value: JSON.stringify(config) } }); await compile(); expect(screen.getByText(/比较来源为用户提供候选/)).toBeInTheDocument(); expect(screen.getByText('SYNTHETIC_USER_CANDIDATE_DIFF')).toBeInTheDocument(); expect(calls.filter((c) => c.method === 'POST')[0]!.body).toEqual({ text: '保留3000元应急金', engine: 'rules', comparison_candidate: { template_name: 'EmergencyBufferPolicy', configuration: config } });
});
