import { useId, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Client, Dashboard } from './api';
import { money } from './money';
import { timestamp, userError } from './display';
import { TemplateClient } from './template-api';
import type { ForecastPoint, ForecastResult } from './template-api';
import './template-pages.css';

const phaseLabels: Record<ForecastPoint['phase'], string> = { BEFORE_PAYMENT: '支出前', AFTER_PAYMENT: '假设支出后', AFTER_PRINCIPAL: '已核实本金返还后' };

// Amounts remain BigInt throughout the projection. Only the final bounded
// screen-coordinate ratio is converted to Number; displayed money stays exact.
export function forecastPlot(result: ForecastResult): { cash: string; protection: string; forecast: string; minimum: string; maximum: string } {
  let low = 0n, high = 1n;
  for (const point of result.points) for (const value of [point.cash_cents, point.protected_cents, point.forecast_cash_cents]) {
    const amount = BigInt(value); if (amount < low) low = amount; if (amount > high) high = amount;
  }
  const path = (field: 'cash_cents' | 'protected_cents' | 'forecast_cash_cents') => result.points.map((point, index) => {
    const x = 48 + index * 704 / Math.max(1, result.points.length - 1);
    const ratio = Number((BigInt(point[field]) - low) * 1000000n / (high - low)) / 1000000;
    return `${x.toFixed(3)},${(214 - ratio * 174).toFixed(3)}`;
  }).join(' ');
  return { cash: path('cash_cents'), protection: path('protected_cents'), forecast: path('forecast_cash_cents'), minimum: low.toString(), maximum: high.toString() };
}

export function ForecastCurve({ result }: { result: ForecastResult }) {
  const plot = forecastPlot(result);
  return <figure className="zy-forecast-figure">
    <svg viewBox="0 0 800 254" role="img" aria-label={`未来${result.horizon_days}天已确认现金、保护金额与预测收入曲线`}>
      <line x1="48" y1="214" x2="752" y2="214" className="zy-forecast-axis" />
      <line x1="48" y1="40" x2="48" y2="214" className="zy-forecast-axis" />
      <polyline points={plot.forecast} className="zy-forecast-line zy-forecast-prediction" />
      <polyline points={plot.cash} className="zy-forecast-line zy-forecast-cash" />
      <polyline points={plot.protection} className="zy-forecast-line zy-forecast-protection" />
      <text x="48" y="24">{money(plot.maximum)}</text><text x="48" y="246">{result.points[0]?.date}</text>
      <text x="752" y="246" textAnchor="end">{result.points.at(-1)?.date}</text>
    </svg>
    <figcaption><span className="zy-forecast-cash-key">已确认现金</span><span className="zy-forecast-protection-key">受保护金额</span><span className="zy-forecast-prediction-key">含预测收入的展示</span></figcaption>
    <p className="zy-muted">虚线包含尚未到账的预测收入，不能扩大今天可安排的金额。支出与返本按已核实的时点模拟，页面没有执行这些资金动作。图示最低刻度为 {money(plot.minimum)}。</p>
  </figure>;
}

export default function Forecast({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId();
  const api = useMemo(() => new TemplateClient(client), [client]);
  const [days, setDays] = useState(365), [zone, setZone] = useState<'UTC' | 'Asia/Shanghai'>('Asia/Shanghai');
  const [page, setPage] = useState(0), [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const scope = `${client.session?.environment}:${client.session?.user_id}`;
  const query = useQuery({ queryKey: ['native-forecast', scope, days, zone], queryFn: ({ signal }) => api.forecast(days, zone, signal), retry: false, refetchOnWindowFocus: false });
  const result = query.data?.result;
  const rows = result?.points.slice(page * 24, (page + 1) * 24) ?? [];
  async function refresh() {
    if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try { await api.refresh(); await onChanged(); await query.refetch({ throwOnError: true }); setMessage('已按当前真实事实重新核对保护边界。'); }
    catch (cause) { setError(userError(cause)); }
    finally { setBusy(false); }
  }
  return <section className="zy-card zy-template-section" aria-label="年度保护预测">
    <div className="zy-section-heading"><h2>未来现金与保护边界</h2><button className="zy-link" disabled={busy || query.isFetching} onClick={() => { setMessage(''); void query.refetch(); }}>重新读取预测</button></div>
    <p>依据当前 {dashboard.accounts.length} 个账户和已确认规则计算。预测收入单独显示，未决资金、既有目标归属和旧债继续保留。</p>
    <div className="zy-template-grid"><label className="zy-field" htmlFor={`${formID}-days`}>预测范围<select id={`${formID}-days`} aria-label="预测范围" value={days} disabled={busy} onChange={(event) => { setDays(Number(event.target.value)); setPage(0); setMessage(''); }}>{[30, 90, 180, 365].map((value) => <option value={value} key={value}>{value} 天</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-zone`}>自然日与月份时区<select id={`${formID}-zone`} aria-label="自然日与月份时区" value={zone} disabled={busy} onChange={(event) => { setZone(event.target.value as typeof zone); setPage(0); setMessage(''); }}><option value="Asia/Shanghai">北京时间</option><option value="UTC">协调世界时</option></select></label></div>
    {query.isPending && <p role="status">正在读取完整预测曲线…</p>}
    {query.isError && <p className="zy-message zy-error" role="alert">{userError(query.error)} 尚未获得本次完整预测。</p>}
    {result && <>
      <p className="zy-muted">真实事实读取于 {timestamp(result.as_of)}；{result.timezone === 'Asia/Shanghai' ? '北京时间' : '协调世界时'}，覆盖 {result.horizon_days} 天、{result.points.length} 个保护时点。{query.isFetching ? '正在重新核对…' : ''}</p>
      <div className="zy-template-summary"><div><span>按完整曲线保留保护后的当前余量</span><strong>{money(result.current_safe_cents)}</strong></div><div><span>最大保护缺口</span><strong>{money(result.maximum_deficit_cents)}</strong></div></div>
      <p className={result.state === 'PROTECTION_DEFICIT' ? 'zy-message zy-error' : 'zy-message'}>{result.state === 'PROTECTION_DEFICIT' ? '当前保护存在缺口。先核对原债务与规则；预测收入不会消除当前缺口。' : '本次完整曲线已核对。资金安排仍需另行生成当前计划并明确确认。'}</p>
      <ForecastCurve result={result} />
      <details><summary>查看每个时点的精确金额</summary><div className="zy-template-table-wrap"><table className="zy-template-table"><thead><tr><th>自然日期／阶段</th><th>已确认现金</th><th>受保护金额</th><th>保护余量</th><th>预测收入</th></tr></thead><tbody>{rows.map((point) => <tr key={point.id}><th>{point.date}<small>{phaseLabels[point.phase]}</small></th><td>{money(point.cash_cents)}</td><td>{money(point.protected_cents)}</td><td>{money(point.margin_cents)}</td><td>{money(point.future_income_cents)}</td></tr>)}</tbody></table></div>
        <div className="zy-actions"><button className="zy-secondary" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>上一页保护时点</button><span>第 {page + 1}／{Math.ceil(result.points.length / 24)} 页</span><button className="zy-secondary" disabled={(page + 1) * 24 >= result.points.length} onClick={() => setPage((value) => value + 1)}>下一页保护时点</button></div></details>
      <details><summary>查看已确认且仍需保留的支出（{result.obligations.length} 项）</summary>{!result.obligations.length ? <p>本次完整读取没有未清的已确认支出。</p> : <ol>{result.obligations.map((debt, index) => <li key={debt.id}>已确认支出 {index + 1}：{debt.due_date}，尚需保留 {money(debt.amount_cents)}。</li>)}</ol>}</details>
      {(result.external_liabilities?.length ?? 0) > 0 && <details><summary>查看独立核实的外部账单（{result.external_liabilities!.length} 项）</summary><p>这些已知账单继续保护现金；是否存在付款规则不改变原债务。此处没有授予付款权限。</p><ol>{result.external_liabilities!.map((debt, index) => <li key={debt.id}>外部账单 {index + 1}：到期日 {debt.due_date}，实际未付 {money(debt.remaining_cents)}。</li>)}</ol></details>}
      <button className="zy-secondary" disabled={busy || query.isFetching} onClick={() => { void refresh(); }}>{busy ? '正在核对保护边界…' : '按当前事实重新核对保护边界'}</button>
    </>}
    {message && <p className="zy-message" role="status">{message}</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
