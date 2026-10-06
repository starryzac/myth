import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAnnualPlanning, getOriginalAnnualResponse } from '../api/planning';
import type { AnnualCheckpoint, AnnualPlanning, AnnualPoint } from '../api/planning';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import FullAnnualProtectionPanel from '../components/FullAnnualProtectionPanel';
import FutureIncomePlanningHost from '../components/FutureIncomePlanningHost';

const money = (value: number | null) => value === null ? '未知 · 尚未证明' : `¥${formatMoneyCents(value)}`;
const statusLabels = { READY: '财务口径已就绪', LIQUIDITY_RISK: '存在流动性缺口', INSUFFICIENT_EVIDENCE: '证据不足，金额未知' };
function AnnualCurve({ checkpoints }: { checkpoints: AnnualCheckpoint[] }) {
  const actual = checkpoints.flatMap((point) => point.minimum_intraday_margin_cents === null ? [] : [point.minimum_intraday_margin_cents]);
  if (!actual.length) return <p className="notice">全年余量尚未证明，无法绘制金额曲线。日期仍完整保留，未知值不填零。</p>;
  const magnitude = Math.max(1, ...actual.map(Math.abs)); const segments: string[][] = [[]];
  for (const point of checkpoints) {
    if (point.minimum_intraday_margin_cents === null) { if (segments.at(-1)!.length) segments.push([]); continue; }
    segments.at(-1)!.push(`${35 + point.day / 365 * 650},${120 - point.minimum_intraday_margin_cents / magnitude * 90}`);
  }
  return <figure className="annual-curve"><svg viewBox="0 0 720 250" role="img" aria-label="365日条件规划的每日最小日内余量，未知日期不连线">
    <title>365日每日最小日内余量</title><desc>只绘制服务端原金额；未来点是条件规划，不是已到账现金。精确值可在下方选择日期逐阶段阅读。</desc>
    <line x1="35" y1="120" x2="685" y2="120" stroke="#8b9e93" strokeDasharray="4 4" />
    <text x="5" y="116">0</text>{segments.filter((segment) => segment.length).map((segment, index) => <polyline key={index} points={segment.join(' ')} fill="none" stroke="#13756e" strokeWidth="2" />)}
    <text x="35" y="240">{checkpoints[0]!.date}</text><text x="685" y="240" textAnchor="end">{checkpoints.at(-1)!.date}</text>
  </svg><figcaption className="caption">每日最小日内余量的相对形状；横轴含今日初始点与未来365日。金额以所选日期的服务端原值为准。</figcaption></figure>;
}
function Phase({ title, point }: { title: string; point: AnnualPoint | null }) {
  return <section className="annual-phase" aria-label={title}><h4>{title}</h4>{!point ? <p className="notice">此阶段尚未证明；现金、保护与余量均未知。</p> : <><dl className="annual-amounts">
    <div><dt>条件现金</dt><dd>{money(point.cash_cents)}</dd></div><div><dt>余量（含负值）</dt><dd>{money(point.margin_cents)}</dd></div>
  </dl><details><summary>保护分项和原引用</summary><dl className="annual-amounts">{Object.entries(point.protected_cents_by_reason).map(([reason, amount]) => <div key={reason}><dt>{reason}</dt><dd>{money(amount)}</dd></div>)}</dl>
    <p>义务发生原编号：{point.obligation_occurrence_ids.join('、') || '本阶段未列出'}</p><p>本金持仓原编号：{point.principal_position_ids.join('、') || '本阶段未列出'}</p></details></>}</section>;
}
function PlanningResult({ data }: { data: AnnualPlanning }) {
  const [day, setDay] = useState(0); const checkpoints = [data.initial_checkpoint, ...data.daily_checkpoints]; const chosen = checkpoints[day]!;
  return <><section className="annual-summary" aria-label="执行参考与年度规划"><article className="card"><h3>90日财务执行参考</h3><p>{statusLabels[data.execution_view.status]}</p>
    <dl className="annual-amounts"><div><dt>安全闲置</dt><dd>{money(data.execution_view.safe_idle_cents)}</dd></div><div><dt>最小余量</dt><dd>{money(data.execution_view.minimum_margin_cents)}</dd></div><div><dt>资金缺口</dt><dd>{money(data.execution_view.deficit_cents)}</dd></div></dl>
    <p className="caption">保持原90日财务口径，具体动作仍须原策略、授权与即时资金核验。</p></article>
    <article className="card"><h3>365日条件规划</h3><p>{statusLabels[data.annual_projection.status]}</p><dl className="annual-amounts"><div><dt>规划安全闲置</dt><dd>{money(data.annual_projection.safe_idle_cents)}</dd></div><div><dt>最小余量</dt><dd>{money(data.annual_projection.minimum_margin_cents)}</dd></div><div><dt>规划缺口</dt><dd>{money(data.annual_projection.deficit_cents)}</dd></div></dl>
      <p className="caption">基于当前核验事实与条件承诺。未来曲线不是已到账现金，年度页面不授予执行权限。</p></article></section>
    <section className="card readonly-section" aria-label="未来收入与本金"><h3>未来收入与本金</h3><p>{data.future_income.reason}</p><p>现金边界未计入未来收入；下方条件假设独立展示，不能当作未来到账承诺。</p>
      {data.unavailable_principal.length > 0 ? <ul>{data.unavailable_principal.map((item) => <li key={item.position_id}>持仓 <code>{item.position_id}</code>：缺少已核验本金到账日期，不能推定可用。</li>)}</ul> : <p>服务端未列出缺少本金到账日期的持仓；这不等于所有未来本金已到账。</p>}</section>
    <section className="card readonly-section" aria-label="年度日期时间轴"><h3>年度日期时间轴</h3><p className="caption">今日初始点 + 365个未来日期 · {data.timezone} · 服务端查询时点 {data.as_of}</p>
      <AnnualCurve checkpoints={checkpoints} /><label className="field">查看规划日期<select value={day} onChange={(e) => setDay(Number(e.target.value))}>{checkpoints.map((point) => <option value={point.day} key={point.day}>{point.date} · {point.day === 0 ? '今日初始点' : `第${point.day}日`} · {point.status === 'PROVEN' ? '已证明' : '未知'}</option>)}</select></label>
      <p className="annual-selected-date" role="status">{chosen.date} · 最小日内余量 {money(chosen.minimum_intraday_margin_cents)}</p>
      <div className="annual-phases"><Phase title="付款前" point={chosen.before_payment} /><Phase title="付款后" point={chosen.after_payment} /><Phase title="本金到账后" point={chosen.after_principal} /></div></section>
    <section className="card readonly-section" aria-label="规划约束与证据"><h3>约束与证据</h3>
      {data.source_issues.length > 0 && <ul className="issues">{data.source_issues.map((issue, index) => <li key={index}>{issue.code} · {issue.message}<p>来源 <code>{issue.source_ref}</code></p></li>)}</ul>}
      {data.annual_projection.blocking_constraints.map((item, index) => <p key={index}>{item.code} · {item.date ?? '未指定日期'} · 原编号 {item.entity_id ?? '未提供'}；需要 {money(item.required_cents ?? null)}，可用 {money(item.available_cents ?? null)}</p>)}
      {(data.annual_projection.calculation_notes ?? []).map((note) => <p className="caption" key={note}>{note}</p>)}
      <p>服务端当前审计范围 {data.audit.scope}：{data.audit.status}；{data.audit.complete ? '报告范围完整' : '报告范围不完整'}。</p>
      <p className="caption">输入摘要 <code>{data.input_digest}</code>。此处展示服务器报告，未另执行银行外部锚点或历史所有epoch验证。</p>
      <details><summary>实际来源证据（{data.source_evidence_ids.length}项）</summary><ul>{data.source_evidence_ids.map((id) => <li key={id}><a href={`#evidence/EVIDENCE/${id}`}>{id}</a></li>)}</ul></details>
      <details><summary>查看原JSON响应</summary><pre className="readonly-raw">{getOriginalAnnualResponse(data) ?? '原响应字节未保留，不能替代原件。'}</pre></details></section>
  </>;
}
export default function AnnualPlanningPage({ mutationBlocked = false }: { mutationBlocked?: boolean }) {
  const query = useQuery({ queryKey: ['annual-planning'], queryFn: getAnnualPlanning, retry: false });
  return <div className="annual-planning"><section className="page-intro"><div><p className="eyebrow">看清日期与条件，不提前使用未来资金</p><h2>年度规划</h2></div><button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新年度规划</button></section>
    <p className="simulation-note">现金边界未计入未来收入。下方可单独查看和明确确认条件假设，始终不能扩大今天的自主边界。</p>
    {query.isPending && <p role="status">正在读取服务端年度规划…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}</p>}
    {!query.isError && query.data && <PlanningResult key={query.data.as_of + query.data.input_digest} data={query.data} />}
    <FullAnnualProtectionPanel />
    <FutureIncomePlanningHost mutationBlocked={mutationBlocked} />
  </div>;
}
