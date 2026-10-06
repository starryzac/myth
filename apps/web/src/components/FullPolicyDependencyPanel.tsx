import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFullPolicyDependencies, originalFullPolicyDependencies } from '../api/full-policy-dependencies';
import type { CurrentDependencyReview, DependencyReviewBinding } from '../api/full-policy-dependencies';
import { errorMessage } from '../api/http';

export type FullPolicyDependencyPanelProps = DependencyReviewBinding & { onReviewCurrentVersion?: (binding: CurrentDependencyReview) => void };
const states = { UNCHANGED: '引用绑定未变', CHANGED: '引用绑定已变化', ADDED: '新引用', UNAVAILABLE: '当前原件不可用' };
export default function FullPolicyDependencyPanel(props: FullPolicyDependencyPanelProps) {
  return <DependencyWorkspace key={`${props.policyId}:${props.currentVersionId ?? ''}`} {...props} />;
}
function DependencyWorkspace({ policyId, currentVersionId, onReviewCurrentVersion }: FullPolicyDependencyPanelProps) {
  const [requested, setRequested] = useState(false);
  const query = useQuery({ queryKey: ['full-current-dependencies-v1', policyId, currentVersionId ?? null], queryFn: () => getFullPolicyDependencies({ policyId, currentVersionId }), enabled: requested, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, structuralSharing: false });
  const data = requested && !query.isFetching && !query.isError ? query.data : undefined;
  const selected = data?.policies.find(row => row.policy_id === policyId);
  return <section className="card" aria-label="当前完整策略依赖">
    <h3>当前完整策略依赖</h3>
    <p>手动读取当前周期的全部完整策略声明，核对确认时引用与当前引用。循环只提示需要复核；读取不授资金权限，不自动修改策略。</p>
    <button type="button" disabled={query.isFetching} onClick={() => { if (requested) void query.refetch(); else setRequested(true); }}>刷新当前策略依赖</button>
    {requested && query.isPending && <p role="status">正在读取当前策略与引用原件…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)}；此次未取得一致原件，不采用先前结论。</p>}
    {data && <>
      <p role="status">{data.status === 'UNKNOWN' ? '依赖来源未知：原分母及不足完整保留' : data.review_required ? '当前声明图完整，引用或循环需要复核' : '当前声明图完整，引用绑定未变'}</p>
      <p>当前周期策略分母 {data.current_policy_count} · 原件 {data.captured_policy_count} · 历史周期 {data.archived_policy_count}（不作为当前依赖）</p>
      <p>读取时点 {data.as_of} · 用户 {data.user_id} · 当前周期 {data.epoch_id}</p>
      <dl>{data.policies.map(row => <div key={row.policy_id}><dt>{row.name} · {row.policy_id === policyId ? '当前所选' : '当前根'}</dt><dd>{row.effective_status} · 引用 {row.reference_validation} · 版本 {row.version_id}</dd></div>)}</dl>
      <h4>原确认与当前引用</h4>
      {data.edges.length === 0 && <p>{data.status === 'UNKNOWN' ? '未取得可完整呈现的当前引用' : '登记配置没有引用边'}</p>}
      <ul>{data.edges.map(row => <li key={`${row.source_policy_id}:${row.role}:${row.kind}:${row.target_id}`}>
        {row.source_policy_id} → {row.kind} {row.target_id} · {row.role} · {states[row.status]} · 当前目标状态 {row.current_target_status ?? '不适用或未知'}
        <details><summary>保留两份引用原件</summary><p>原绑定 {row.original_binding_hash ?? '无原引用'} · 当前绑定 {row.current_binding_hash ?? '未知'}</p><pre className="readonly-raw">{JSON.stringify({ original: row.original_reference, current: row.current_reference }, null, 2)}</pre></details>
      </li>)}</ul>
      <h4>声明闭环</h4>
      {data.cyclic_components.length ? <ul>{data.cyclic_components.map(row => <li key={row.policy_ids.join(':')}>{row.example_path.join(' → ')} · 仅需用户复核，不证明财务不可行或最小冲突集</li>)}</ul> : <p>{data.status === 'UNKNOWN' ? '当前来源不完整，不作无环结论' : '登记范围未发现声明闭环'}</p>}
      <h4>完整分母与具体不足</h4><ul>{data.current_policy_ids.map(id => <li key={id}>{id} · {data.policies.some(row => row.policy_id === id) ? '当前原件已返回' : '原件缺失，保持未知'}</li>)}</ul>
      <ul>{data.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
      <p>所有模板动作失效、在途恢复、持仓与边界重算尚未由此读取验证。用户复核后仍须进入原版本变更预览，再明确确认；当前引用或历史回执不能代替新确认。</p>
      {onReviewCurrentVersion && <button type="button" disabled={data.status !== 'COMPLETE_CURRENT_DECLARATION_GRAPH' || !selected} onClick={() => { if (selected && data.status === 'COMPLETE_CURRENT_DECLARATION_GRAPH') onReviewCurrentVersion({ policyId, versionId: selected.version_id, reviewHash: data.review_hash }); }}>查看原当前版本变更工作区</button>}
      <details><summary>读取摘要及完整原响应</summary><p>输入 {data.input_hash} · 本次读取 {data.review_hash}</p><ul>{data.limitations.map(reason => <li key={reason}>{reason}</li>)}</ul><pre className="readonly-raw">{originalFullPolicyDependencies(data)}</pre></details>
    </>}
  </section>;
}
