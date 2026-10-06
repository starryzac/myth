import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import DecisionSearchPanel from './DecisionSearchPanel';
import { decisionSearchFixture, searchAction, searchOwner, searchQuery } from '../tests/decision-search-fixture';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function transport(partial = false) { vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const fetch = vi.fn(async () => new Response(JSON.stringify(decisionSearchFixture(searchQuery, partial)))); vi.stubGlobal('fetch', fetch); return fetch; }
function open(owner: string | undefined = searchOwner) { render(<DecisionSearchPanel ownerUserId={owner} />); fireEvent.change(screen.getByLabelText('动作 UUID（可选）'), { target: { value: searchAction } }); }
function search() { fireEvent.click(screen.getByRole('button', { name: '只读搜索原记录' })); }
test('explicit exact search only GET, original trace link and raw response remain available', async () => {
  const fetch = transport(); open(); expect(fetch).not.toHaveBeenCalled(); search(); await screen.findByRole('region', { name: '审计搜索结果' });
  expect(screen.getByText('当前限定来源已检索 · 无资金授权')).toBeVisible(); expect(screen.getByRole('link', { name: /查看原决策/ })).toHaveAttribute('href', '#decisions/00000000-0000-0000-0000-000000000002');
  expect(screen.getByText(/归档与完整审计链未在搜索中验证/)).toBeVisible(); expect(screen.getByText(JSON.stringify(decisionSearchFixture()))).toBeInTheDocument();
  const options = fetch.mock.calls[0] as unknown as [string, RequestInit]; expect(options[1].method).toBe('GET'); expect(options[1].body).toBeUndefined();
  expect(screen.queryByRole('button', { name: /执行|确认|修账/ })).not.toBeInTheDocument();
});
test('partial source preserves unknown and unverified denominator', async () => {
  transport(true); open(); search(); await screen.findByText('UNKNOWN · 来源或检索范围未完整');
  expect(screen.getByText('0 / 未知')).toBeVisible(); expect(screen.getByText('0 / 1')).toBeVisible(); expect(screen.queryByText('当前限定来源已检索 · 无资金授权')).not.toBeInTheDocument();
});
test('editing filters hides old response and does not automatically requery', async () => {
  const fetch = transport(); open(); search(); await screen.findByRole('region', { name: '审计搜索结果' });
  fireEvent.change(screen.getByLabelText('策略版本 UUID（可选）'), { target: { value: 'different' } }); expect(screen.queryByRole('region', { name: '审计搜索结果' })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(1);
  search(); expect(await screen.findByRole('alert')).toHaveTextContent('校验'); expect(fetch).toHaveBeenCalledTimes(1);
});
test('missing current owner refuses lookup without networking', async () => {
  const fetch = transport(); render(<DecisionSearchPanel ownerUserId={undefined} />); fireEvent.change(screen.getByLabelText('动作 UUID（可选）'), { target: { value: searchAction } }); search(); expect(await screen.findByRole('alert')).toHaveTextContent('没有发出请求'); expect(fetch).not.toHaveBeenCalled();
});
test('response loss hides previous source and keeps error without retry', async () => {
  const fetch = transport(); open(); search(); await screen.findByRole('region', { name: '审计搜索结果' });
  fetch.mockRejectedValueOnce(new Error('SYNTHETIC_RESPONSE_LOSS')); search(); await screen.findByRole('alert'); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('region', { name: '审计搜索结果' })).not.toBeInTheDocument();
});
