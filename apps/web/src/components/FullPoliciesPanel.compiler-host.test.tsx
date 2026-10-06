import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullPoliciesPanel from './FullPoliciesPanel';
import type { FullCompiledDraft } from './FullPolicyCompilerPanel';
import { fullCandidateFixture, fullCatalogFixture, fullConfig, fullFlags, fullHash, fullSchemaFixture } from '../tests/full-policy-fixture';

vi.mock('./FullPolicyCompilerPanel', () => ({ default: ({ mutationBlocked, onCandidate }: { mutationBlocked?: boolean; onCandidate?: (value: FullCompiledDraft) => void }) => <button type="button" disabled={mutationBlocked} onClick={() => onCandidate?.({ templateName: 'DatedExpensePolicy', configuration: { ...fullConfig(), name: 'SYNTHETIC_COMPILED_PARENT_DRAFT' }, configurationHash: fullHash })}>测试采用已独立校验的编译草稿</button> }));
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function transport() {
  const calls: { path: string; method: string }[] = [];
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-compiler-parent.local');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input)).pathname; const method = init?.method ?? 'GET'; calls.push({ path, method });
    if (path === '/api/v1/full-policies') return new Response(JSON.stringify({ ...fullFlags, items: [] }));
    if (path === '/api/v1/policy-templates') return new Response(JSON.stringify(fullCatalogFixture()));
    if (path.endsWith('/schema')) return new Response(JSON.stringify(fullSchemaFixture()));
    if (path.endsWith('/validate')) return new Response(JSON.stringify(fullCandidateFixture()));
    throw new Error(`SYNTHETIC_UNEXPECTED_${method}_${path}`);
  }));
  return calls;
}
function open(blocked = false) { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FullPoliciesPanel blocked={blocked} /></QueryClientProvider>); }
test('编译草稿只填配置并清原确认，父原validate/理由/accepted门继续独立', async () => {
  const calls = transport(); open();
  fireEvent.click(await screen.findByRole('button', { name: '准备新完整版策略' }));
  await screen.findByRole('combobox', { name: '策略模板' });
  fireEvent.change(screen.getByLabelText('新策略完整配置JSON（金额为整数分）'), { target: { value: JSON.stringify(fullConfig()) } });
  fireEvent.click(screen.getByRole('button', { name: '仅校验候选配置' }));
  await screen.findByRole('region', { name: '服务器规范化策略候选' });
  fireEvent.change(screen.getByLabelText('首次确认理由'), { target: { value: 'SYNTHETIC_PREVIOUS_REASON' } });
  fireEvent.click(screen.getByLabelText('我已复核规范化候选和配置hash，明确确认这份新策略'));
  expect(screen.getByRole('button', { name: '明确确认新策略' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '打开自然策略候选编译' }));
  fireEvent.click(screen.getByRole('button', { name: '测试采用已独立校验的编译草稿' }));
  expect(screen.getByLabelText('新策略完整配置JSON（金额为整数分）')).toHaveValue(JSON.stringify({ ...fullConfig(), name: 'SYNTHETIC_COMPILED_PARENT_DRAFT' }, null, 2));
  expect(screen.getByLabelText('首次确认理由')).toHaveValue('');
  expect(screen.getByLabelText('我已复核规范化候选和配置hash，明确确认这份新策略')).not.toBeChecked();
  expect(screen.queryByRole('region', { name: '服务器规范化策略候选' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '明确确认新策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '仅校验候选配置' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/validate'))).toHaveLength(2));
  expect(calls.filter((call) => call.method === 'POST' && !call.path.endsWith('/validate'))).toHaveLength(0);
});
test('跨族pending门禁新增自然编译入口，未展开不额外请求目录', async () => {
  const calls = transport(); open(true);
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  expect(calls.some((call) => call.path.includes('full-policy-compilations'))).toBe(false);
  expect(screen.queryByRole('button', { name: '打开自然策略候选编译' })).not.toBeInTheDocument();
});
