import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getComposedActionSet, getOriginalComposedActionSet } from '../api/composed-action-set';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';

const states = { INCLUDED: '已纳入', EXCLUDED: '已证明排除', UNKNOWN: '尚未证明' };
export default function ComposedActionSetPanel() {
  const [requested, setRequested] = useState(false);
  const query = useQuery({ queryKey: ['composed-current-action-set-v3'], queryFn: getComposedActionSet, enabled: requested, retry: false, refetchOnWindowFocus: false, structuralSharing: false });
  const data = query.data;
  return <section className="card" aria-label="周期划款与动作组合">
    <h3>周期划款与动作组合</h3><p>按当前本期付款关系核对；同一笔原付款只计一次。每个动作执行前仍需确认其实际授权和资金条件。</p>
    <button type="button" disabled={query.isFetching} onClick={() => { if (requested) void query.refetch(); else setRequested(true); }}>核对周期划款与当前动作</button>
    {requested && query.isPending && <p role="status">正在核对原件与当前动作…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)}；本次未证明当前动作，未采用旧结果。</p>}
    {!query.isError && data && <>
      <p role="status">{data.global_action_set_complete ? '当前登记来源已完整核对' : '当前动作仍未知：未覆盖或未证明来源保留'}</p>
      <p>核对时点：{data.as_of} · 本期范围：{data.periodic_family.periodic_family_complete ? '完整' : '未知'} · 原关系 {data.periodic_family.relation_source_count} 条 · 同笔替代 {data.replaced_original_candidate_keys.length} 笔</p>
      <p>只读核对没有付款、创建权限或投递介入通知。恢复、归属释放和目标联动有未支持来源时仍保留未知。</p>
      <ul>{data.candidates.map((row) => <li key={row.candidate_key}>{row.candidate_key} · {states[row.state]} · {row.amount_cents === null ? '金额未知' : `¥${formatMoneyCents(row.amount_cents)}`} · {row.autonomy_level ?? '权限未知'}</li>)}</ul>
      {data.unsupported_producers.length > 0 && <details open><summary>未覆盖来源</summary><ul>{data.unsupported_producers.map((row) => <li key={row}>{row}</li>)}</ul></details>}
      {data.reasons.length > 0 && <ul>{data.reasons.map((row) => <li key={row}>{row}</li>)}</ul>}
      <details><summary>完整原响应及原动作快照</summary><pre className="readonly-raw">{getOriginalComposedActionSet(data)}</pre></details>
    </>}
  </section>;
}
