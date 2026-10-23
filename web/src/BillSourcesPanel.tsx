import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, Client } from './api';
import { timestamp, userError } from './display';
import { BillsClient, loadBillPending, saveBillPending } from './bills-api';
import type { BillIntake, BillSource, PendingBill } from './bills-api';

export default function BillSourcesPanel({ client, onChanged }: { client: Client; onChanged: () => Promise<void> }) {
  const api = useMemo(() => new BillsClient(client), [client]), running = useRef(false), alive = useRef(true);
  const [sources, setSources] = useState<BillSource[]>([]), [pending, setPending] = useState<PendingBill | null>(null), [intake, setIntake] = useState<BillIntake | null>(null), [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  function display(p: PendingBill, v: BillIntake) { if (!alive.current) return; setIntake(v); setPending(saveBillPending(client, { ...p, capture_id: v.capture_id, capture_hash: v.capture_hash })); }
  async function act(fn: () => Promise<void>) { if (running.current) return; running.current = true; setBusy(true); setError(''); try { await fn(); } catch (e) { if (alive.current) setError(userError(e)); } finally { running.current = false; if (alive.current) setBusy(false); } }
  async function refresh() { const rows = await api.sources(); if (alive.current) setSources(rows); }
  useEffect(() => { alive.current = true; void act(async () => { const p = loadBillPending(client); setPending(p); setReady(true); if (p) { try { display(p, await api.original(p)); } catch (e) { if (!(e instanceof ApiError && e.code === 'NOT_FOUND')) throw e; } } await refresh(); }); return () => { alive.current = false; }; }, [api]);
  async function observe(p: PendingBill) { setPending(saveBillPending(client, p)); setIntake(null); try { display(p, await api.observe(p)); } catch (e) { try { display(p, await api.original(p)); } catch { throw e; } } await refresh(); await onChanged(); }
  const blocked = !ready || !!pending && intake?.state !== 'IMPORTED';
  return <section className="zy-template-review" aria-label="真实外部账单观察"><h3>真实外部账单观察</h3><p>从已配置的可信来源读取原账单，并与独立银行最终账单核对。新确认的负债会进入保护计算，付款仍需单独许可。</p><button className="zy-secondary" disabled={busy} onClick={() => { void act(refresh); }}>重新读取账单来源</button>
    {ready && !busy && !sources.length && <p>当前没有配置可观察的可信账单来源。</p>}
    {sources.map((s, i) => <article key={s.id}><p>账单来源 {i + 1} · {s.state === 'SCHEMA_UNAVAILABLE' ? '来源暂不可用' : s.state === 'CONFIGURED_NOT_OBSERVED' ? '已配置，尚未观察' : `最近核实于 ${timestamp(s.observed_at ?? '')}`}</p><button className="zy-secondary" disabled={busy || blocked || s.state === 'SCHEMA_UNAVAILABLE'} onClick={() => { void act(async () => { if (!client.session) return; await observe({ protocol: 'zhiyu-browser-bill-source-v1', environment: client.session.environment, user_id: client.session.user_id, key: crypto.randomUUID(), command: { source_id: s.id } }); }); }}>观察并核实最新账单</button>{s.capture_id && <button className="zy-link" disabled={busy} onClick={() => { void act(async () => { if (pending && intake?.state !== 'IMPORTED' && pending.capture_id !== s.capture_id) throw new Error('请先核对保存的账单原请求'); setIntake(await api.intake(s.capture_id!)); }); }}>读取这次原观察</button>}</article>)}
    {pending && !intake && <div role="status"><p>账单原观察结果尚未查回，请继续核对同一原请求。</p><button disabled={busy} onClick={() => { void act(async () => { display(pending, await api.original(pending)); }); }}>查询账单原请求</button><button disabled={busy} onClick={() => { void act(async () => { const p = loadBillPending(client); if (!p || p.key !== pending.key) throw new Error('账单原请求发生变化'); await observe(p); }); }}>按原请求重试账单观察</button></div>}
    {intake && <div role="status"><p>{intake.state === 'IMPORTED' ? `原账单已纳入保护核对，本次保留 ${intake.imported_count} 项账单。` : '已保留真实账单观察，等待原有资金操作对账后再纳入保护。新的资金安排会等待核对完成。'}</p>{intake.recorded_at && <p>核对于 {timestamp(intake.recorded_at)}</p>}{intake.state === 'CAPTURED_PENDING' && <button disabled={busy} onClick={() => { void act(async () => { const next = await api.resume(intake); setIntake(next); await refresh(); await onChanged(); }); }}>继续核对这次原观察</button>}</div>}
    {busy && <p role="status">正在读取和核对真实账单…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
