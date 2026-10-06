/** Local HTTP doubles exercise user review only, not actual PG/browser/financial results. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { dependencyFixture, dependencyPolicyId, dependencyVersionId } from '../tests/full-policy-dependencies-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
import FullPolicyDependencyPanel from './FullPolicyDependencyPanel';
import type { FullPolicyDependencyPanelProps } from './FullPolicyDependencyPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
const panel = (props: FullPolicyDependencyPanelProps, queries = client()) => <QueryClientProvider client={queries}><FullPolicyDependencyPanel {...props} /></QueryClientProvider>;
const props = { policyId: dependencyPolicyId, currentVersionId: dependencyVersionId };
test('manual GET displays current and archived denominators and actual cyclic paths without POST', async () => {
  vi.stubGlobal('crypto', webcrypto); const onReview = vi.fn(); const calls = installHttpFixture(method => { expect(method).toBe('GET'); return dependencyFixture(); });
  render(panel({ ...props, onReviewCurrentVersion: onReview })); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '刷新当前策略依赖' })); await screen.findByText('当前声明图完整，引用或循环需要复核');
  expect(screen.getByText(/当前周期策略分母 2 · 原件 2 · 历史周期 3/)).toBeInTheDocument(); expect(screen.getByText(/仅需用户复核，不证明财务不可行/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看原当前版本变更工作区' })); expect(onReview).toHaveBeenCalledWith({ policyId: dependencyPolicyId, versionId: dependencyVersionId, reviewHash: dependencyFixture().review_hash }); expect(calls).toHaveLength(1);
});
test('failed refresh hides prior complete graph and prevents review navigation', async () => {
  vi.stubGlobal('crypto', webcrypto); let count = 0; const calls = installHttpFixture(() => ++count === 1 ? dependencyFixture() : new Response(JSON.stringify({ error: { code: 'AUDIT_SOURCE_MISSING', message: '当前原件缺失', request_id: 'TOOL_ONLY' } }), { status: 409 }));
  render(panel({ ...props, onReviewCurrentVersion: vi.fn() })); const button = screen.getByRole('button', { name: '刷新当前策略依赖' }); fireEvent.click(button);
  await screen.findByText('当前声明图完整，引用或循环需要复核'); fireEvent.click(button); await screen.findByRole('alert');
  expect(screen.queryByText('当前声明图完整，引用或循环需要复核')).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '查看原当前版本变更工作区' })).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
test('a changed selected version clears review and cache until another explicit GET', async () => {
  vi.stubGlobal('crypto', webcrypto); const queries = client(); const calls = installHttpFixture(() => dependencyFixture());
  const view = render(panel(props, queries)); fireEvent.click(screen.getByRole('button', { name: '刷新当前策略依赖' })); await screen.findByText('当前声明图完整，引用或循环需要复核');
  view.rerender(panel({ ...props, currentVersionId: dependencyFixture().policies[1]!.version_id }, queries));
  expect(screen.queryByText('当前声明图完整，引用或循环需要复核')).not.toBeInTheDocument(); expect(calls).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '刷新当前策略依赖' })); await screen.findByRole('alert'); expect(calls).toHaveLength(2);
});
