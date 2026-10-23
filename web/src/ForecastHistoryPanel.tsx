import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, Client } from './api';
import { timestamp, userError } from './display';
import { ForecastHistoryClient, loadForecastHistoryPending, saveForecastHistoryPending } from './forecast-history-api';
import type { ForecastHistoryImport, ForecastHistorySource, PendingForecastHistory } from './forecast-history-api';

export default function ForecastHistoryPanel({ client, onChanged }: { client: Client; onChanged: () => Promise<void> }) {
  const api = useMemo(() => new ForecastHistoryClient(client), [client]), running = useRef(false), alive = useRef(true);
  const [sources, setSources] = useState<ForecastHistorySource[]>([]), [pending, setPending] = useState<PendingForecastHistory | null>(null), [result, setResult] = useState<ForecastHistoryImport | null>(null), [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function act(fn: () => Promise<void>) { if (running.current) return; running.current = true; setBusy(true); setError(''); try { await fn(); } catch (e) { if (alive.current) setError(userError(e)); } finally { running.current = false; if (alive.current) setBusy(false); } }
  async function refresh() { const rows = await api.sources(); if (alive.current) setSources(rows); }
  function show(p: PendingForecastHistory, value: ForecastHistoryImport) { if (!alive.current) return; const saved = saveForecastHistoryPending(client, { ...p, result: value }); setPending(saved); setResult(value); }
  useEffect(() => { alive.current = true; void act(async () => { const p = loadForecastHistoryPending(client); setPending(p); if (p) { try { show(p, await api.original(p)); } catch (e) { if (!(e instanceof ApiError && e.code === 'NOT_FOUND')) throw e; } } await refresh(); if (alive.current) setReady(true); }); return () => { alive.current = false; }; }, [api]);
  async function observe(p: PendingForecastHistory) { setPending(saveForecastHistoryPending(client, p)); setResult(null); try { show(p, await api.observe(p)); } catch (e) { try { show(p, await api.original(p)); } catch { throw e; } } await refresh(); await onChanged(); }
  const blocked = !ready || !!pending && !result;
  return <section className="zy-template-review" aria-label="历史消费来源"><h3>历史消费来源</h3><p>从已配置的独立签发来源核对过去消费，供生活储备与节假日规则计算。当前来源用于本地受控模拟；导入后可重新审阅规则，不会增加账户余额或收入资格。</p>
    <button className="zy-secondary" disabled={busy} onClick={() => { void act(refresh); }}>重新读取历史来源</button>
    {ready && !busy && !sources.length && <p>当前没有配置可核对的历史来源。</p>}
    {sources.map((s, i) => <article key={s.source_id}><p>历史来源 {i + 1} · {s.state === 'IMPORTED' ? '已保留历史原件' : '已配置，尚未核对'}</p><button className="zy-secondary" disabled={busy || blocked} onClick={() => { void act(async () => { if (!client.session) return; await observe({ protocol: 'zhiyu-browser-forecast-history-v1', environment: client.session.environment, user_id: client.session.user_id, key: crypto.randomUUID(), source_id: s.source_id }); }); }}>导入并核对历史消费</button></article>)}
    {pending && !result && <div role="status"><p>历史导入原结果尚未查回，请保留同一原请求继续核对。</p><button disabled={busy} onClick={() => { void act(async () => { show(pending, await api.original(pending)); }); }}>查询历史来源原请求</button><button disabled={busy} onClick={() => { void act(async () => { const p = loadForecastHistoryPending(client); if (!p || p.key !== pending.key || p.source_id !== pending.source_id) throw new Error('历史来源原请求发生变化'); await observe(p); }); }}>按原请求重试历史核对</button></div>}
    {result && <div role="status"><p>历史消费已核对并保留原件。</p><p>完整覆盖 {result.from_date} 至 {result.through_date}；核对于 {timestamp(result.recorded_at)}。</p></div>}
    {busy && <p role="status">正在核对历史消费来源…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
