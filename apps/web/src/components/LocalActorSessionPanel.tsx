import { useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../api/http';
import { LOCAL_USERNAME, loginLocalActor, logoutLocalActor, readLocalActorSession } from '../api/local-actor';
import type { LocalActorSession } from '../api/local-actor';
import { isWriteInFlight, useWriteInFlight } from '../features/write-flight';

type State = 'NOT_READ' | 'CURRENT_SNAPSHOT' | 'UNAUTHENTICATED' | 'UNKNOWN' | 'EXPIRED';
const labels: Record<State, string> = {
  NOT_READ: '尚未读取当前身份', CURRENT_SNAPSHOT: '服务器读取时的身份',
  UNAUTHENTICATED: '未取得当前有效会话', UNKNOWN: '当前身份未知', EXPIRED: '身份显示已到期',
};
function safeFailure(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 401) return '凭证无效、会话失效或服务未配置。当前身份显示已清空。';
  if (cause instanceof ApiError && cause.status === 404) return '本地身份接口尚未提供，未取得登录证明。';
  return '结果未知或响应无法完整核对。显示身份已清空，请只读核对服务器会话；不会自动重发登录或注销。';
}

/** Principal is display-only, never an authorization store or a financial gate. */
export default function LocalActorSessionPanel({ mutationBlocked = false, allowSameUserReauthentication = false }: { mutationBlocked?: boolean; allowSameUserReauthentication?: boolean }) {
  const heading = useId();
  const secretInput = useRef<HTMLInputElement>(null);
  const [principal, setPrincipal] = useState<LocalActorSession['principal'] | null>(null);
  const [state, setState] = useState<State>('NOT_READ');
  const [busy, setBusy] = useState(false);
  const [needsRead, setNeedsRead] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const writeInFlight = useWriteInFlight();
  // This narrow escape is a fixed server USER login, not actor selection or a
  // grant. Unknown identity writes must first resolve through an actual GET.
  const renewalKnown = state === 'UNAUTHENTICATED' || state === 'EXPIRED' || (state === 'CURRENT_SNAPSHOT' && principal?.role === 'USER');
  const loginBlocked = mutationBlocked && !(allowSameUserReauthentication && renewalKnown);

  useEffect(() => {
    if (!principal) return;
    const remaining = Date.parse(principal.expires_at) - Date.now();
    const timer = setTimeout(() => {
      setPrincipal(null); setState(principal.role === 'USER' ? 'EXPIRED' : 'UNKNOWN');
      setNotice('本地到期显示已清空；实际身份有效性仍需服务器重新验证。');
    }, Math.max(0, Math.min(remaining, 900_000)));
    return () => clearTimeout(timer);
  }, [principal]);

  async function readCurrent() {
    const value = await readLocalActorSession();
    setPrincipal(value.principal); setState('CURRENT_SNAPSHOT'); setNeedsRead(false);
  }
  async function refresh() {
    if (busy) return;
    setBusy(true); setPrincipal(null); setState('UNKNOWN'); setError(''); setNotice('');
    try { await readCurrent(); }
    catch (cause) {
      setPrincipal(null); setState(cause instanceof ApiError && cause.status === 401 ? 'UNAUTHENTICATED' : 'UNKNOWN');
      if (cause instanceof ApiError && cause.status === 401) setNeedsRead(false);
      setError(safeFailure(cause));
    } finally { setBusy(false); }
  }
  async function login() {
    if (busy || loginBlocked || needsRead || isWriteInFlight()) return;
    const secret = secretInput.current?.value ?? '';
    if (secretInput.current) secretInput.current.value = '';
    if (!secret.length || secret.length > 1024) { setError('请输入1至1024字符的本地凭证。'); return; }
    setBusy(true); setPrincipal(null); setState('UNKNOWN'); setError(''); setNotice(''); setNeedsRead(true);
    try {
      await loginLocalActor(secret);
      // Only a separate actual GET can establish the current cookie-backed
      // display; a successful login body is not a reusable authentication grant.
      await readCurrent();
      setNotice('已只读核对服务器当前会话。每次金融请求仍独立核验身份与银行权限。');
    } catch (cause) {
      setPrincipal(null); setState(cause instanceof ApiError && cause.status === 401 ? 'UNAUTHENTICATED' : 'UNKNOWN');
      setError(safeFailure(cause));
    } finally { setBusy(false); }
  }
  async function logout() {
    if (busy || mutationBlocked || needsRead || isWriteInFlight()) return;
    if (secretInput.current) secretInput.current.value = '';
    setBusy(true); setPrincipal(null); setState('UNKNOWN'); setError(''); setNotice(''); setNeedsRead(true);
    try {
      await logoutLocalActor();
      try {
        await readCurrent();
        setPrincipal(null); setState('UNKNOWN'); setNeedsRead(true);
        setError('注销后服务器仍返回有效会话，不能证明已注销；请继续只读核对。');
      } catch (cause) {
        if (!(cause instanceof ApiError && cause.status === 401)) throw cause;
        setState('UNAUTHENTICATED'); setNeedsRead(false);
        setNotice('注销后服务器未返回当前有效会话。历史金融请求仍保留，注销不撤销其原件。');
      }
    } catch (cause) {
      setPrincipal(null); setState('UNKNOWN'); setError(safeFailure(cause));
    } finally { setBusy(false); }
  }
  return <section className="local-actor-session-panel" aria-labelledby={heading}>
    <h2 id={heading}>本地模拟身份</h2>
    <p role="status">{labels[state]}{busy ? ' · 正在核对…' : ''}</p>
    <p>身份仅用于展示。本地签名会话不是银行授权、金融确认或真人身份核验；每个新金融请求仍由服务器独立验真。</p>
    {error && <p role="alert">{error}</p>}{notice && <p>{notice}</p>}
    {principal && <dl><dt>用户</dt><dd>{principal.user_id}</dd><dt>服务器角色</dt><dd>{principal.role}</dd>
      <dt>签发</dt><dd>{principal.issued_at}</dd><dt>到期</dt><dd>{principal.expires_at}</dd>
      <dt>会话</dt><dd>{principal.session_id}</dd><dt>真人核验</dt><dd>未核验</dd></dl>}
    <button type="button" disabled={busy} onClick={() => void refresh()}>只读读取当前身份</button>
    <form onSubmit={(event) => { event.preventDefault(); void login(); }}>
      <label>服务器固定用户名<input name="username" value={LOCAL_USERNAME} readOnly autoComplete="username" /></label>
      <label>本地凭证<input ref={secretInput} name="secret" type="password" maxLength={1024} autoComplete="off" disabled={busy || loginBlocked || needsRead || writeInFlight} /></label>
      <button type="submit" disabled={busy || loginBlocked || needsRead || writeInFlight}>明确登录本地会话</button>
    </form>
    <button type="button" disabled={busy || mutationBlocked || needsRead || writeInFlight} onClick={() => void logout()}>明确注销本地会话</button>
    {allowSameUserReauthentication && mutationBlocked && <p>金融原请求仍保留。先只读核对身份后，可手动续登录服务器固定的同一 USER；不能切换用户、注销、确认金融结果或重发付款。</p>}
    {needsRead && <p>上次身份写入结果尚需核对。仅可读取服务器当前状态，不自动登录、注销或替换身份。</p>}
    <p>凭证提交即清空，不写入浏览器存储或返回正文。15分钟 HttpOnly / SameSite=Strict Cookie 的实际行为须由服务器与浏览器验收证明。</p>
  </section>;
}
