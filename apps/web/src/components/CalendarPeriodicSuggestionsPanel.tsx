import { useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { getCalendarOriginalResponse, parseCalendarParameters, readCalendarPeriodicSuggestions } from '../api/calendar-periodic-suggestions';
import type { CalendarCandidateReview, CalendarCadence, CalendarParameters, CalendarPeriodicReport } from '../api/calendar-periodic-suggestions';
import { formatMoneyCents } from '../features/money';

export type CalendarPeriodicSuggestionsPanelProps = {
  userId?: string; mutationBlocked?: boolean;
  onReviewCandidate?: (candidate: CalendarCandidateReview) => void;
};
const cadences: Record<CalendarCadence, string> = { MONTHLY_DATE: '每月固定日期附近', MONTH_END: '相对月底', WEEKLY: '每周固定日期附近' };
export default function CalendarPeriodicSuggestionsPanel({ userId, mutationBlocked = false, onReviewCandidate }: CalendarPeriodicSuggestionsPanelProps) {
  const [parameters, setParameters] = useState<CalendarParameters>({ lookback_days: 56, minimum_cycles: 3, maximum_day_spread: 2, maximum_cv_bps: 1000 });
  const [report, setReport] = useState<CalendarPeriodicReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CalendarCadence | 'ALL'>('ALL');
  const generation = useRef(0);
  const change = (key: keyof CalendarParameters, value: number) => { generation.current += 1; setParameters((old) => ({ ...old, [key]: value })); setReport(null); setError(null); };
  async function read() {
    const current = ++generation.current; setBusy(true); setError(null); setReport(null);
    try { const result = await readCalendarPeriodicSuggestions(parseCalendarParameters(parameters), userId); if (current === generation.current) setReport(result); }
    catch (e) { if (current === generation.current) setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  function download() {
    if (!report) return; const raw = getCalendarOriginalResponse(report); if (raw === null) return;
    const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `calendar-periodic-${report.epoch_id}.json`; link.click(); URL.revokeObjectURL(url);
  }
  return <section className="panel" aria-label="周期日历规律候选">
    <h2>可能需要持续预留的规律</h2>
    <p>从原银行历史比较月、月底和周周期。这只是建议；不会建立义务、收款关系或资金权限，不是真实资金执行。</p>
    <fieldset disabled={busy}><legend>历史发现选项</legend>
      <label>回看天数<input type="number" min={1} max={365} value={parameters.lookback_days} onChange={(e) => change('lookback_days', e.target.valueAsNumber)} /></label>
      <label>最少连续周期<input type="number" min={3} max={12} value={parameters.minimum_cycles} onChange={(e) => change('minimum_cycles', e.target.valueAsNumber)} /></label>
      <label>日期跨度上限（天）<input type="number" min={0} max={2} value={parameters.maximum_day_spread} onChange={(e) => change('maximum_day_spread', e.target.valueAsNumber)} /></label>
      <label>金额变异系数上限（基点）<input type="number" min={0} max={1000} value={parameters.maximum_cv_bps} onChange={(e) => change('maximum_cv_bps', e.target.valueAsNumber)} /></label>
    </fieldset>
    <button type="button" disabled={busy} onClick={() => void read()}>{busy ? '读取原历史…' : '读取并比较周期'}</button>
    {error && <p role="alert">{error}</p>}
    {report && <>
      <p>原用户 {report.user_id} · 原开放期 {report.epoch_id} · 服务时点 {report.as_of}</p>
      <p>历史 {report.history_start} 至 {report.history_end}；覆盖 {report.history_proof.verified ? '原交易覆盖核对通过' : 'UNKNOWN'}。账单仅为已观察序列，完整审计和独立经济验真未在此证明。</p>
      <p>原组 × 日历假设共 {report.patterns.length} 项；普通/不合格消费排除 {report.excluded_transaction_count} 项。</p>
      {report.source_issues.map((issue, index) => <p key={index} role="status">来源问题 {issue.code}：{issue.message} · {issue.source_ref}</p>)}
      <label>显示的日历假设<select value={filter} onChange={(e) => setFilter(e.target.value as CalendarCadence | 'ALL')}><option value="ALL">全部假设与不成立原因</option>{Object.entries(cadences).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label>
      {report.patterns.length === 0 && <p>没有可展示的原序列；这不能证明未来没有义务。缺覆盖时仍为未知。</p>}
      {report.patterns.filter((p) => filter === 'ALL' || p.schedule.cadence === filter).map((p) => <article key={p.pattern_id} className="panel">
        <h3>{cadences[p.schedule.cadence]} · {p.kind}</h3>
        <p>原账户 {p.account_id} · 原收款标识 {p.payee_ref}</p>
        <p>{p.status} · {p.candidate_support} · {p.cycle_count} 周期 / {p.sample_count} 原样本；日期跨度 {p.schedule.day_spread} 天</p>
        <p>观察金额 ¥{formatMoneyCents(p.amount_min_cents)}—¥{formatMoneyCents(p.amount_max_cents)}；均值 {p.mean_fraction_cents} 分，方差 {p.variance_fraction_cents_squared} 分²，CV² {p.cv_squared_fraction ?? 'UNKNOWN'}。</p>
        <p>{p.schedule.cadence === 'WEEKLY' ? `建议星期 ${p.schedule.weekday === null ? 'UNKNOWN' : p.schedule.weekday + 1}（一=1，日=7）` : p.schedule.cadence === 'MONTH_END' ? `月底前 ${p.schedule.days_before_month_end ?? 'UNKNOWN'} 天` : `建议每月 ${p.schedule.due_day ?? 'UNKNOWN'} 日`}；下次日期假设 {p.schedule.next_occurrence ?? 'UNKNOWN'}，不是已开账单。</p>
        <p>来源范围 {p.source_scope}；{p.reason_codes.join('、') || '有限规则内未发现不稳定项'}。</p>
        {p.candidate_support === 'DSL_UNSUPPORTED' && <p>当前策略字段无法表达此周期。仅保留规律，不提供确认配置；不替换成月规则。</p>}
        {p.candidate_configuration !== null && <>
          <p>我发现一个可能需要持续预留的规律，是否建立策略？需在既有声明入口再次完整复核和明确确认；此处没有确认或执行请求。</p>
          <details><summary>可表达的原策略候选 JSON</summary><pre>{JSON.stringify(p.candidate_configuration, null, 2)}</pre><p>原配置 hash {p.candidate_configuration_hash}</p></details>
          {onReviewCandidate ? <button type="button" aria-label={`复核${cadences[p.schedule.cadence]}候选`} disabled={mutationBlocked} onClick={() => onReviewCandidate({ user_id: report.user_id, epoch_id: report.epoch_id, as_of: report.as_of, source_digest: report.source_digest, pattern: p })}>打开声明复核</button> : <a href="#policies">去策略中心按原候选逐项复核</a>}
          {mutationBlocked && <p>另有原请求待核对，暂不可移交新声明；当前只读历史仍可读取。</p>}
        </>}
        <details><summary>全部 {p.samples.length} 原样本与来源</summary>{p.samples.map((sample, index) => <div key={index}><p>{sample.occurred_on} · ¥{formatMoneyCents(sample.amount_cents)} · 原 fact {sample.original.source.fact_id} · evidence {sample.original.source.evidence_id}</p><pre>{JSON.stringify(sample.original, null, 2)}</pre></div>)}</details>
      </article>)}
      <details><summary>当前报告限制与完整原 JSON</summary><ul>{report.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul><p>原 source_digest {report.source_digest}；不是确认 hash 或授权。</p><pre>{getCalendarOriginalResponse(report) ?? JSON.stringify(report, null, 2)}</pre></details>
      <button type="button" onClick={download} disabled={getCalendarOriginalResponse(report) === null}>下载原 HTTP JSON</button>
    </>}
    <p>真人研究未开展，真实账单样本研究未完成；当前程序不推定工资、银行承诺或未来义务。</p>
  </section>;
}
