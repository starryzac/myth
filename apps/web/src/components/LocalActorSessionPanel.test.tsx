import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { localActorFixture } from '../tests/local-actor-fixture';
import LocalActorSessionPanel from './LocalActorSessionPanel';
import { beginWriteFlight, endWriteFlight, isWriteInFlight } from '../features/write-flight';

afterEach(() => { cleanup(); endWriteFlight(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });
function http(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } }); }
const logout = { simulation: true, logged_out: true, bank_authority: false };
function credential(value = 'synthetic-ui-secret') { fireEvent.change(screen.getByLabelText('本地凭证'), { target: { value } }); }

describe('local actor session panel — synthetic HTTP, no browser authentication proof', () => {
  it('has no automatic login or GET and preserves the explicit boundary', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByLabelText('服务器固定用户名')).toHaveValue('bounded-user');
    expect(screen.getByLabelText('服务器固定用户名')).toHaveAttribute('readonly');
    expect(screen.getByText(/不是银行授权、金融确认或真人身份核验/)).toBeInTheDocument();
  });
  it('only a manual GET displays the actual server principal and never writes storage', async () => {
    const fixture = localActorFixture();
    const fetcher = vi.fn().mockResolvedValue(http(fixture)); vi.stubGlobal('fetch', fetcher);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    render(<LocalActorSessionPanel />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    expect(await screen.findByText(fixture.principal.user_id)).toBeInTheDocument();
    expect(storage).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('clears the input before POST resolves, then separately GETs actual cookie-backed state', async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; })).mockResolvedValueOnce(http(localActorFixture()));
    vi.stubGlobal('fetch', fetcher);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    render(<LocalActorSessionPanel />); credential();
    fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    expect(screen.getByLabelText('本地凭证')).toHaveValue('');
    expect(fetcher).toHaveBeenCalledTimes(1); expect(screen.queryByText(/服务器读取时的身份/)).not.toBeInTheDocument();
    await act(async () => { resolve(http(localActorFixture())); });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/服务器读取时的身份/)).toBeInTheDocument();
    expect(storage).not.toHaveBeenCalled(); expect(document.body.textContent).not.toContain('synthetic-ui-secret');
  });
  it('successful login body followed by 401 does not show a logged-in principal', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(http(localActorFixture())).mockResolvedValueOnce(http({ detail: '未配置' }, 401)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />); credential();
    fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('服务未配置');
    expect(screen.queryByText(localActorFixture().principal.user_id)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('network ambiguity locks identity writes until a manual GET; no automatic rePOST', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('synthetic-ui-secret')).mockResolvedValueOnce(http({ detail: '无会话' }, 401)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />); credential();
    fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('结果未知');
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(document.body.textContent).not.toContain('synthetic-ui-secret');
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled());
    expect(fetcher.mock.calls.filter((row) => row[1]?.method === 'POST')).toHaveLength(1);
  });
  it('does not render a server error that echoes the submitted secret', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(http({ error: { code: 'INVALID', message: 'synthetic-ui-secret' } }, 500)));
    render(<LocalActorSessionPanel />); credential();
    fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('结果未知');
    expect(document.body.textContent).not.toContain('synthetic-ui-secret');
    expect(screen.getByLabelText('本地凭证')).toHaveValue('');
  });
  it('unknown GET or 401 clears any previous principal', async () => {
    const fixture = localActorFixture();
    const fetcher = vi.fn().mockResolvedValueOnce(http(fixture)).mockResolvedValueOnce(http({ detail: '失效' }, 401)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await screen.findByText(fixture.principal.user_id);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('失效');
    expect(screen.queryByText(fixture.principal.user_id)).not.toBeInTheDocument();
  });
  it('explicit logout checks absence with a separate GET and does not reset finance', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(http(logout)).mockResolvedValueOnce(http({ detail: '无会话' }, 401)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />);
    fireEvent.click(screen.getByRole('button', { name: '明确注销本地会话' }));
    expect(await screen.findByText(/注销后服务器未返回当前有效会话/)).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.every((row) => String(row[0]).includes('/local-actor/'))).toBe(true);
  });
  it('logout success text cannot overcome a still-active GET', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(http(logout)).mockResolvedValueOnce(http(localActorFixture())); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />);
    fireEvent.click(screen.getByRole('button', { name: '明确注销本地会话' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('不能证明已注销');
    expect(screen.queryByText(localActorFixture().principal.user_id)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
  });
  it('other-family mutation block still permits its own read-only GET', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(http(localActorFixture())));
    render(<LocalActorSessionPanel mutationBlocked />);
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    expect(await screen.findByText(/服务器读取时的身份/)).toBeInTheDocument();
  });
  it('clears an expired display without silently querying or renewing the session', async () => {
    vi.useFakeTimers();
    const fixture = localActorFixture();
    fixture.principal.expires_at = new Date(Date.now() + 1000).toISOString();
    const fetcher = vi.fn().mockResolvedValue(http(fixture)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' })); });
    expect(screen.getByText(fixture.principal.user_id)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
    expect(screen.getByText('身份显示已到期')).toBeInTheDocument();
    expect(screen.queryByText(fixture.principal.user_id)).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('same-user renewal requires manual original identity GET before bypassing a financial pending gate', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(http({ detail: 'expired' }, 401)).mockResolvedValueOnce(http(localActorFixture())).mockResolvedValueOnce(http(localActorFixture()));
    vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel mutationBlocked allowSameUserReauthentication />);
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled());
    credential(); fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    await screen.findByText(/服务器读取时的身份/);
    expect(fetcher).toHaveBeenCalledTimes(3);
    const post = fetcher.mock.calls[1]?.[1], read = fetcher.mock.calls[2]?.[1];
    if (!post || !read) throw new Error('Actual fixed USER login and subsequent GET were not captured');
    expect(JSON.parse(post.body)).toEqual({ username: 'bounded-user', secret: 'synthetic-ui-secret' });
    expect(read.method).toBe('GET');
    expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
  });
  it('an ambiguous renewal still needs a new GET and never automatically replaces identity', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(http({ detail: 'expired' }, 401)).mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(http({ detail: 'absent' }, 401));
    vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel mutationBlocked allowSameUserReauthentication />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled());
    credential(); fireEvent.click(screen.getByRole('button', { name: '明确登录本地会话' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('结果未知'));
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(isWriteInFlight()).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled());
    expect(fetcher.mock.calls.filter((row) => row[1]?.method === 'POST')).toHaveLength(1);
  });
  it('an UNKNOWN read and a current non-USER principal cannot use the renewal escape', async () => {
    const nonUser = localActorFixture(); nonUser.principal.role = 'AGENT';
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('GET lost')).mockResolvedValueOnce(http(nonUser)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel mutationBlocked allowSameUserReauthentication />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('结果未知'));
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await screen.findByText('AGENT');
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(fetcher.mock.calls.filter((row) => row[1]?.method === 'POST')).toHaveLength(0);
  });
  it('a financial gate remains closed after known absence when renewal is not explicitly enabled', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(http({ detail: 'expired' }, 401)));
    render(<LocalActorSessionPanel mutationBlocked />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
  });
  it('renewal cannot overtake an actual financial write flight; own GET remains available', async () => {
    const fetcher = vi.fn().mockResolvedValue(http({ detail: 'expired' }, 401)); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel mutationBlocked allowSameUserReauthentication />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled());
    act(() => beginWriteFlight());
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('button', { name: '明确登录本地会话' }).closest('form')!);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '只读读取当前身份' })).not.toBeDisabled();
    act(() => endWriteFlight());
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled();
  });
  it('a manually read USER may renew while finance remains pending but logout stays blocked', async () => {
    const fetcher = vi.fn().mockResolvedValue(http(localActorFixture())); vi.stubGlobal('fetch', fetcher);
    render(<LocalActorSessionPanel mutationBlocked allowSameUserReauthentication />);
    fireEvent.click(screen.getByRole('button', { name: '只读读取当前身份' }));
    await screen.findByText(localActorFixture().principal.user_id);
    expect(screen.getByRole('button', { name: '明确登录本地会话' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '明确注销本地会话' }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
  });
});
