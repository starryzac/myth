import { useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { getOriginalFinancialPreviewResponse, previewFullPolicyFinancialChange } from '../api/full-policy-financial-preview';
import type { FinancialChangePreview, FinancialPreviewBody } from '../api/full-policy-financial-preview';
import { formatMoneyCents } from '../features/money';

export type FinancialChangeImpactPanelProps = { policyId: string; userId: string; expectedVersionId: string; candidateConfiguration: FinancialPreviewBody['configuration']; mutationBlocked?: boolean };
const phases = ['付款前', '付款后', '本金到账后'];
const money = (cents: number | null | undefined): string => cents == null ? '未知 · 尚未证明' : `¥${formatMoneyCents(cents)}`;
export default function FinancialChangeImpactPanel({ policyId, userId, expectedVersionId, candidateConfiguration, mutationBlocked = false }: FinancialChangeImpactPanelProps) {
  const binding = JSON.stringify([policyId, userId, expectedVersionId, candidateConfiguration]);
  const latest = useRef(binding); latest.current = binding;
  const [result, setResult] = useState<{ binding: string; data: FinancialChangePreview } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [day, setDay] = useState(0);
  const data = result?.binding === binding ? result.data : null; const p = data?.financial_impact;
  async function preview() {
    if (busy || mutationBlocked) return; const originalBinding = binding;
    const body: FinancialPreviewBody = { expected_version_id: expectedVersionId, configuration: structuredClone(candidateConfiguration) };
    setBusy(true); setError(null); setResult(null);
    try { const received = await previewFullPolicyFinancialChange(policyId, body); if (latest.current === originalBinding) { setResult({ binding: originalBinding, data: received }); setDay(0); } }
    catch (failure) { if (latest.current === originalBinding) setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  return <section className="card" aria-label="财务修改影响预览">
    <h3>财务修改影响预览</h3>
    <p>手动读取服务端计算：当前策略 {policyId} · 原版本 {expectedVersionId}。只读假设不会确认策略、重算已有行动或执行资金。</p>
    <button type="button" onClick={() => void preview()} disabled={busy || mutationBlocked}>{busy ? '读取财务修改预览中…' : '计算财务修改影响'}</button>
    {mutationBlocked && <p>其他原请求尚未核对，暂不发起新的预览请求。</p>}
    {error && <p role="alert">{error}</p>}
    {data && p && <>
      <p role="status">{p.status === 'PROJECTED' ? '已计算条件曲线，候选尚未确认' : 'UNKNOWN：候选财务影响尚未证明'}</p>
      <p>服务端读取时刻 {data.as_of} · 模拟期 {data.epoch_id}。当前宿主用户 {userId}，策略归属由服务器核对。</p>
      <p>仅替换未来保守待付义务；今天、逾期义务和已到期月份保留。未来收入计入0，不等于未来真实收入为零。</p>
      <dl><dt>安全闲置资金</dt><dd>原值 {money(p.before?.safe_idle_cents ?? null)} → 候选 {money(p.after?.safe_idle_cents ?? null)} · 差额 {money(p.delta_safe_idle_cents)}</dd>
        <dt>最低日内余量</dt><dd>原值 {money(p.before?.minimum_margin_cents ?? null)} → 候选 {money(p.after?.minimum_margin_cents ?? null)} · 差额 {money(p.delta_minimum_margin_cents)}</dd></dl>
      {p.reasons.map((reason) => <p key={reason}>{reason}</p>)}
      {p.after?.source_account_limitations.map((reason) => <p key={reason}>来源账户限制：{reason}</p>)}
      {p.before && p.before.calculation_trace.length > 0 && <>
        <label>查看修改影响日期 <select aria-label="查看修改影响日期" value={day} onChange={(event) => setDay(Number(event.target.value))}>{p.before.calculation_trace.filter((point) => point.phase === 'BEFORE_PAYMENT').map((point) => <option key={point.day} value={point.day}>{point.date} · 第{point.day}日</option>)}</select></label>
        <div className="annual-phases">{phases.map((phase, index) => { const before = p.before!.calculation_trace[day * 3 + index]; const after = p.after?.calculation_trace[day * 3 + index]; return <article key={phase} aria-label={`修改影响${phase}`}><h4>{phase}</h4><p>原条件现金 {money(before?.cash_cents ?? null)} · 余量 {money(before?.margin_cents ?? null)}</p><p>候选条件现金 {money(after?.cash_cents ?? null)} · 余量 {money(after?.margin_cents ?? null)}</p><details><summary>原保护与候选保护</summary><pre className="readonly-raw">{JSON.stringify({ before: before?.protected_cents_by_reason ?? null, after: after?.protected_cents_by_reason ?? null, before_occurrences: before?.obligation_occurrence_ids ?? null, after_occurrences: after?.obligation_occurrence_ids ?? null, principal_position_ids: before?.principal_position_ids ?? null }, null, 2)}</pre></details></article>; })}</div>
      </>}
      <details><summary>每产品条件上限与差额</summary><p>资金容纳上限仅为规划条件，不授予购买权限。</p>{p.delta_max_allocatable_by_product === null ? <p>候选产品上限未知，差额不是零。</p> : <ul>{Object.keys(p.delta_max_allocatable_by_product).map((id) => <li key={id}><code>{id}</code> · 原 {money(p.before?.max_allocatable_by_product[id] ?? null)} → 候选 {money(p.after?.max_allocatable_by_product[id] ?? null)} · 差额 {money(p.delta_max_allocatable_by_product?.[id] ?? null)}</li>)}</ul>}</details>
      {p.status === 'UNKNOWN' && <p>目标归属与持仓影响尚未证明；空列表不表示没有目标或资产。</p>}
      <details><summary>当前目标归属与持仓（{p.goals.length} / {p.positions.length}）</summary>{p.goals.map((goal) => <article key={goal.goal_id}><h4>目标 {goal.goal_id}</h4><p>当前现金归属 {money(goal.current_owned_cash_cents)} · 当前本金归属 {money(goal.current_owned_principal_cents)}</p><p>当前分配差额 {money(goal.current_allocation_delta_cents)} · 当前本金差额 {money(goal.current_principal_delta_cents)}；未来分配 UNKNOWN：未调用候选目标求解器。</p><p>原证据 {goal.original_evidence_ids.join(' · ') || '无列出原件'}</p></article>)}{p.positions.map((position) => <article key={position.position_id}><h4>持仓 {position.position_id} · {position.original_status}</h4><p>原记录本金 {money(position.original_recorded_principal_cents)} · 当前未返还本金 {money(position.current_outstanding_principal_cents)} · 当前本金差额 {money(position.current_principal_delta_cents)}</p><p>原可用时间 {position.original_principal_available_at ?? 'UNKNOWN'}；未来处置 UNKNOWN：未生成候选行动。</p><p>原证据 {position.original_evidence_ids.join(' · ') || '无列出原件'}</p></article>)}</details>
      <details><summary>保留的原义务与未确认候选（{p.retained_original_occurrence_ids.length} / {p.candidate_commitments.length}）</summary><ul>{p.retained_original_occurrence_ids.map((id) => <li key={id}><code>{id}</code></li>)}</ul>{p.candidate_commitments.map((commitment) => <p key={commitment.identity}>{commitment.source_kind} · {commitment.due_date} · {money(commitment.amount_cents)} · <code>{commitment.identity}</code>；没有银行授权。</p>)}</details>
      <p>原行动 {data.original_action_ids.length}项不变；确认修改后仍需重新读取当前事实、重新计算与核验权限。该预览没有确认按钮。</p>
      <details><summary>规范化配置与实际引用</summary><p>变化字段 {data.changed_fields.join(' · ') || '无'}</p><pre className="readonly-raw">{JSON.stringify({ before_configuration: data.before_configuration, after_configuration: data.after_configuration, reference_snapshots: data.reference_snapshots }, null, 2)}</pre></details>
      <details><summary>摘要协议与具体限制</summary><p>当前事实摘要协议 full-change-preview-actual-facts-v1：<code>{data.current_fact_digest}</code>。含本次时刻，不与旧预览摘要比较。</p><p>原配置hash <code>{data.current_configuration_hash}</code> · 候选配置hash <code>{data.configuration_hash}</code> · 财务输入hash <code>{p.input_hash}</code> · 候选曲线hash <code>{p.after?.curve_hash ?? 'UNKNOWN'}</code>；这些值不构成资金授权。</p><ul>{p.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></details>
      <details><summary>财务修改预览原JSON响应</summary><pre className="readonly-raw">{getOriginalFinancialPreviewResponse(data) ?? '原文本未保留'}</pre></details>
    </>}
  </section>;
}
