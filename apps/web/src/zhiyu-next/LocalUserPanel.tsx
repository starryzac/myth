import { useEffect, useState } from 'react';
import type { LocalActorSession } from '../api/local-actor';
import { ApiError } from '../api/http';
import type { NextState } from './api';
import { loginNextActor, logoutNextActor, readNextActor } from './policy-change';
import { userError } from './display';

export default function LocalUserPanel({ environment, session, update, inputId = 'zyn-local-confirm-secret' }: { environment: NextState; session: LocalActorSession | null; update: (value: LocalActorSession | null) => void; inputId?: string }) {
  const [secret, setSecret] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => {
    let mounted = true;
    void readNextActor(environment).then((value) => { if (mounted) update(value); }).catch((cause: unknown) => { if (mounted) { update(null); setError(userError(cause)); } });
    return () => { mounted = false; };
    // Identity is scoped to this epoch, not to every financial state refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [environment.environment_id, environment.epoch_id]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => update(null), Math.max(0, Date.parse(session.principal.expires_at) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session, update]);
  async function login() {
    if (busy || !secret) return; const submitted = secret; setSecret(''); setBusy(true); setError(''); update(null);
    try { update(await loginNextActor(submitted, environment)); }
    catch (cause) { setError(cause instanceof ApiError && cause.status === 401 ? '本轮确认口令无效或会话已过期，请核对后重新登录。' : userError(cause)); }
    finally { setBusy(false); }
  }
  async function logout() { if (busy) return; setBusy(true); setError(''); update(null); setSecret(''); try { await logoutNextActor(environment); } catch (cause) { setError(userError(cause)); } finally { setBusy(false); } }
  return <section className="zy-card" aria-label="本地确认身份"><div className="zy-section-heading"><h2>本地确认身份</h2><span>{session ? '本轮确认会话已建立' : '尚未建立确认会话'}</span></div><p>修改资金保护范围需要本人明确确认。此模拟身份只用于当前环境，最长十五分钟；身份会话本身不确认修改或授予银行权限。</p>
    {session ? <button className="zy-link" disabled={busy} onClick={() => void logout()}>退出确认身份</button> : <form onSubmit={(event) => { event.preventDefault(); void login(); }}><label className="zy-field" htmlFor={inputId}>本轮确认口令<input id={inputId} type="password" autoComplete="off" maxLength={1024} value={secret} disabled={busy} onChange={(event) => setSecret(event.target.value)} /></label><button className="zy-secondary" disabled={busy || !secret} type="submit">建立确认会话</button></form>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
