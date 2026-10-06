import { useQuery } from '@tanstack/react-query';
import { getActualActionSet, getOriginalActualActionSet } from '../api/actual-action-set';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import ComposedActionSetPanel from './ComposedActionSetPanel';
import RecoveryComposedActionSetPanel from './RecoveryComposedActionSetPanel';

const labels = { INCLUDED: '已纳入集合', EXCLUDED: '已证明排除', UNKNOWN: '尚未证明' };
export default function ActualActionSetPanel() {
  const query = useQuery({ queryKey: ['actual-full-policy-action-set-v2'], queryFn: getActualActionSet, retry: false });
  const data = query.data;
  return <section className="card" aria-label="当前完整动作集合">
    <h3>当前完整动作集合</h3><p>按当前已登记策略、收入原件和产品原件读取。每个动作执行时仍需即时核验。</p>
    <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新动作集合</button>
    {query.isPending && <p role="status">正在核对动作集合与原件数量…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)}</p>}
    {!query.isError && data && <>
      <p role="status">{data.global_action_set_complete ? '当前登记范围已完整核对' : '动作集合未知：仍有未覆盖或未证明来源'}</p>
      <p>原件时点：{data.as_of} · 登记动作：{data.expected_candidate_keys.length} · 原表分母：{data.table_coverage.length}</p>
      {data.unsupported_producers.length > 0 && <div><h4>未覆盖动作</h4><ul>{data.unsupported_producers.map((item) => <li key={item}>{item}</li>)}</ul></div>}
      {data.reasons.length > 0 && <ul>{data.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
      <ul>{data.candidates.map((candidate) => <li key={candidate.candidate_key}>
        <strong>{candidate.candidate_key}</strong> · {labels[candidate.state]} · {candidate.action_type ?? '动作类型未知'} · {candidate.amount_cents === null ? '金额未知' : `¥${formatMoneyCents(candidate.amount_cents)}`}
        {candidate.reasons.length > 0 && <p>{candidate.reasons.join('；')}</p>}
      </li>)}</ul>
      <details><summary>原表实际数量与捕获分母</summary><dl>{data.table_coverage.map((table) => <div key={table.table}><dt>{table.table}</dt><dd>实际 {table.actual_count ?? '未知'} · 已捕获 {table.captured_count} · {table.complete ? '完整' : '不完整'}</dd></div>)}</dl></details>
      <details><summary>服务端原始响应</summary><pre>{getOriginalActualActionSet(data)}</pre></details>
    </>}
    <ComposedActionSetPanel />
    <RecoveryComposedActionSetPanel />
  </section>;
}
