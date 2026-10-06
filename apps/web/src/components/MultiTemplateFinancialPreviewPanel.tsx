import { useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { getOriginalMultiPreview, parseMultiPreviewCandidate, previewMultiTemplate, readMultiPreviewSources } from '../api/full-policy-change-multi';
import type { MultiPreview, MultiPreviewInventory, MultiPreviewSource } from '../api/full-policy-change-multi';
import { formatMoneyCents } from '../features/money';
import { object } from '../features/policy-form';

export type MultiTemplateFinancialPreviewPanelProps = { mutationBlocked?: boolean };
const money = (v: number | null | undefined) => v == null ? 'UNKNOWN · 未证明' : `¥${formatMoneyCents(v)}`;
const key = (source: MultiPreviewSource) => `${source.sourceKind}:${source.policyId}`;
const phaseNames = ['付款前', '付款后', '本金到账后'];
const scopeNames: Record<string, string> = { MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS: 'MVP保护差量，保留全部原FULL负担', INDIVIDUAL_PRODUCT_CAPACITY: '每产品独立容量，不能相加为组合', WHOLE_POSITION_RECOVERY_CANDIDATES: '全持仓逐仓候选，未生成多仓执行', CURRENT_JOINT_GOAL_ALLOCATION: '当前期目标联合分配假设', UNSUPPORTED: '候选金融影响未支持' };
export default function MultiTemplateFinancialPreviewPanel({ mutationBlocked = false }: MultiTemplateFinancialPreviewPanelProps) {
  const [inventory, setInventory] = useState<MultiPreviewInventory | null>(null);
  const [selected, setSelected] = useState(''); const [text, setText] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ binding: string; data: MultiPreview } | null>(null); const [day, setDay] = useState(0);
  const source = inventory?.items.find((v) => key(v) === selected);
  const binding = JSON.stringify([source, text]); const latest = useRef(binding); latest.current = binding;
  const data = result?.binding === binding ? result.data : null; const p = data?.financial_impact;
  async function load() {
    if (busy) return; setBusy(true); setError(null); setResult(null); setInventory(null); setSelected(''); setText('');
    try { const current = await readMultiPreviewSources(); setInventory(current); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  function choose(id: string) { const next = inventory?.items.find((v) => key(v) === id); setSelected(id); setText(next ? JSON.stringify(next.configuration, null, 2) : ''); setResult(null); setError(null); }
  async function preview() {
    if (!source?.eligible || busy || mutationBlocked) return; const originalBinding = binding;
    setBusy(true); setError(null); setResult(null);
    try { const candidate = parseMultiPreviewCandidate(text, source.configuration); const received = await previewMultiTemplate(source, candidate); if (latest.current === originalBinding) { setResult({ binding: originalBinding, data: received }); setDay(0); } }
    catch (e) { if (latest.current === originalBinding) setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <section className="card" aria-label="多模板财务修改预览">
    <h2>多模板财务修改预览</h2><p>手动选择服务端当前策略并修改配置假设。没有策略确认或资金执行；未来收入计入当前现金和执行均为0。</p>
    <button type="button" disabled={busy} onClick={() => void load()}>{busy ? '读取中…' : '读取当前策略来源'}</button>
    {error && <p role="alert">{error}</p>}
    {inventory && <><p>当前用户 {inventory.userId} · 当前live模拟期 {inventory.epochId ?? 'UNKNOWN'} · 列表读取时刻 {inventory.asOf}。OPEN状态及完整来源由每次POST服务端重新验证。</p>
      <label>选择当前策略 <select aria-label="选择当前策略" value={selected} disabled={busy} onChange={(e) => choose(e.target.value)}><option value="">请选择原策略</option>{inventory.items.map((item) => <option key={key(item)} value={key(item)} disabled={!item.eligible}>{item.sourceKind} · {item.name} · {item.template}{item.eligible ? '' : ' · UNKNOWN/不可用'}</option>)}</select></label>
      {inventory.items.length === 0 && <p>没有当前已确认策略，无法构造预览来源。</p>}
      <details><summary>所有原策略与可用边界（{inventory.items.length}）</summary><ul>{inventory.items.map((item) => <li key={key(item)}>{item.name} · {item.policyId} · 版本 {item.versionId}：{item.reason}</li>)}</ul></details>
    </>}
    {source && <><p>原{source.template} · 策略 {source.policyId} · 当前版本 {source.versionId} · {source.reason}</p>
      <label>完整候选配置（仅当前模板字段）<textarea className="readonly-raw" aria-label="完整候选配置（仅当前模板字段）" rows={14} value={text} disabled={busy} onChange={(e) => { setText(e.target.value); setResult(null); setError(null); }} /></label>
      <p>可编辑原配置字段，不能增加字段或更换type。金额为整数分，日期和额度只是候选规则，不是BANK事实；服务端执行严格Schema与真实引用核对。</p>
      <button type="button" disabled={busy || mutationBlocked || !source.eligible} onClick={() => void preview()}>计算多模板只读影响</button>
      {mutationBlocked && <p>其他原请求未核对，暂不发起新的POST预览；当前来源GET仍可读。</p>}
      {['DatedExpensePolicy', 'PeriodicTransferPolicy'].includes(source.template) && <p>本v3对此模板保持UNKNOWN；请回到策略中心原日期支出/历史Dated或周期付款预览入口。</p>}
    </>}
    {data && p && <><p role="status">{p.status} · {scopeNames[p.scope]} · 候选尚未确认</p><p>服务端截面 {data.as_of} · 原期 {data.epoch_id}。本次读取不证明历史回执构成当前权限。</p>
      <dl><dt>安全闲置资金</dt><dd>{money(p.before?.safe_idle_cents)} → {money(p.after?.safe_idle_cents)} · 差额 {money(p.delta_safe_idle_cents)}</dd><dt>最低日内余量</dt><dd>{money(p.before?.minimum_margin_cents)} → {money(p.after?.minimum_margin_cents)} · 差额 {money(p.delta_minimum_margin_cents)}</dd></dl>
      <p>当前目标现金归属差额 {money(p.current_owned_cash_delta_cents)}、持仓本金差额 {money(p.current_position_principal_delta_cents)}只表示没有修改原事实。未来行动 UNKNOWN：确认后仍需当前事实重新计算。</p>
      {p.reasons.map((reason) => <p key={reason}>{reason}</p>)}
      {p.before && p.before.calculation_trace.length > 0 && <><p>完整年度分母：今天+365日 × 3阶段 = 1098；原 {p.before.calculation_trace.length} / 候选 {p.after?.calculation_trace.length ?? 'UNKNOWN'}。</p>
        <label>选择预览日期<select aria-label="选择预览日期" value={day} onChange={(e) => setDay(Number(e.target.value))}>{p.before.calculation_trace.filter((row) => row.phase === 'BEFORE_PAYMENT').map((row) => <option key={row.day} value={row.day}>{row.date} · 第{row.day}日</option>)}</select></label>
        <div className="annual-phases">{phaseNames.map((name, i) => { const before = p.before!.calculation_trace[day * 3 + i], after = p.after?.calculation_trace[day * 3 + i]; return <article key={name} aria-label={`多模板${name}`}><h3>{name}</h3><p>原条件现金 {money(before?.cash_cents)} · 余量 {money(before?.margin_cents)}</p><p>候选条件现金 {money(after?.cash_cents)} · 余量 {money(after?.margin_cents)}</p><details><summary>完整阶段保护与原引用</summary><pre className="readonly-raw">{JSON.stringify({ before, after: after ?? null }, null, 2)}</pre></details></article>; })}</div>
      </>}
      <details><summary>逐产品金融曲线容量差额</summary>{p.delta_product_financial_capacity_cents === null ? <p>UNKNOWN：未证明曲线容量差额，不能填0。</p> : <ul>{Object.entries(p.delta_product_financial_capacity_cents).map(([id, amount]) => <li key={id}>{id} · 差额 {money(amount)}</li>)}</ul>}</details>
      <details><summary>每产品独立容量（{p.product_capacities.length} / 原目录 {data.source_counts.asset_catalogue}）</summary><p>独立上限不能相加为可买组合，也不授购买权限。</p>{p.product_capacities.map((row) => <article key={row.product_id}><h3>{row.product_id} · 版本 {row.product_version}</h3><p>{money(row.before_capacity_cents)} → {money(row.after_capacity_cents)} · 差额 {money(row.delta_cents)}</p><p>原原因 {row.before_reasons.join(' · ') || '无'} · 候选原因 {row.after_reasons.join(' · ') || '无'}</p><p>原条款hash {row.terms_digest}</p></article>)}</details>
      <details><summary>完整逐仓回收候选（{p.recovery_candidates.length} / 原持仓候选 {data.source_counts.recovery_holdings}）</summary><p>原报价与到账条件只作假设；未生成多仓时序/原子执行，候选本金不是当前现金。条件边界仅原MVP范围。</p>{p.recovery_candidates.map((row) => <article key={row.position_id}><h3>原持仓 {row.position_id}</h3><p>条件准时净额差额 {money(row.conditional_on_time_net_delta_cents)}</p>{(['before', 'after'] as const).map((side) => { const v = row[side]; return <div key={side}><p>{side === 'before' ? '原' : '候选'}判断 {String(v.decision)} · 本金 {money(v.principal_cents as number | null)} · fee {money(v.fee_cents as number | null)} · loss {money(v.independent_loss_cents as number | null)} · 净额 {money(v.net_cents as number | null)} · 到账 {typeof v.earliest_conditional_cash_at === 'string' ? v.earliest_conditional_cash_at : 'UNKNOWN'}</p><pre className="readonly-raw">{JSON.stringify(v, null, 2)}</pre></div>; })}</article>)}</details>
      <details><summary>当前期目标规划（原分母 {data.source_counts.joint_goals}）</summary><p>所有原Goal/income lot保留。8层是当前期目标solver，不是多期全局最优，不改变原产权。</p>{p.goal_allocation_before && p.goal_allocation_after ? <><p>原 {p.goal_allocation_before.status} / 候选 {p.goal_allocation_after.status}</p>{p.goal_allocation_after.goals.map((row) => { const old = p.goal_allocation_before!.goals.find((v) => v.goal_id === row.goal_id); return <article key={row.goal_id}><h3>目标 {row.goal_id}</h3><p>{money(old?.amount_cents)} → {money(row.amount_cents)} · 差额 {money(p.goal_allocation_delta_cents?.[row.goal_id])}</p><p>原版本 {row.effective_policy_version_id} · 完成日 {row.completion_date ?? 'UNKNOWN'} · 延迟下界 {row.delay_lower_bound_days ?? 'UNKNOWN'}天 {row.delay_censored ? '（截尾）' : ''}</p></article>; })}<pre className="readonly-raw">{JSON.stringify({ before: p.goal_allocation_before, after: p.goal_allocation_after }, null, 2)}</pre></> : <p>UNKNOWN：本模板未生成目标候选分配；不等于未来分配为0。</p>}</details>
      <details><summary>完整来源与未覆盖项</summary><p>原行动 {data.original_action_ids.length} · 原持仓 {data.original_position_ids.length} · 原证据 {data.source_evidence_ids.length}。</p><pre className="readonly-raw">{JSON.stringify(data.source_counts, null, 2)}</pre><ul>{[...data.limitations, ...p.limitations].map((line, i) => <li key={`${i}:${line}`}>{line}</li>)}</ul><p>当前事实协议 full-change-current-facts-v3 · {data.current_fact_digest}；不同于旧v1/history-v2，不能跨时点或协议比较。</p><p>原配置hash {data.current_configuration_hash} · 候选配置hash {data.candidate_configuration_hash} · 输入hash {p.input_hash}；无确认/授权含义。</p><pre className="readonly-raw">{JSON.stringify({ before: data.before_configuration, after: data.after_configuration, changed_fields: data.changed_fields, original_action_ids: data.original_action_ids, original_position_ids: data.original_position_ids, source_originals: data.source_originals }, null, 2)}</pre></details>
      <details><summary>服务端原JSON响应</summary><pre className="readonly-raw">{getOriginalMultiPreview(data) ?? '原文本未保留'}</pre></details>
      {object(p) && <p>该预览没有确认按钮；不会修改原策略、重置历史、生成Action或执行资金。</p>}
    </>}
  </section>;
}
