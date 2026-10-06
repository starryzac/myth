import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRecoveryComposedActionSet, originalRecoveryComposedActionSet } from '../api/recovery-composed-action-set';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';

const states = { INCLUDED: '已纳入', EXCLUDED: '已证明排除', UNKNOWN: '尚未证明' };
export default function RecoveryComposedActionSetPanel() {
  const [requested, setRequested] = useState(false);
  const query = useQuery({ queryKey: ['recovery-composed-current-actions-v4'], queryFn: getRecoveryComposedActionSet, enabled: requested, retry: false, refetchOnWindowFocus: false, structuralSharing: false });
  const data = query.data;
  return <section className="card" aria-label="周期划款与整仓恢复动作">
    <h3>周期划款与整仓恢复动作</h3><p>核对当前登记的付款与整仓恢复来源。纳入的恢复动作仍需新的明确确认；归属释放和目标联动缺口保持未知。</p>
    <button type="button" disabled={query.isFetching} onClick={() => { if (requested) void query.refetch(); else setRequested(true); }}>核对付款、整仓恢复与当前动作</button>
    {requested && query.isPending && <p role="status">正在核对当前原件…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)}；此次未取得一致结果，未采用先前结论。</p>}
    {!query.isError && data && <>
      <p role="status">{data.global_action_set_complete ? '当前登记来源已完整核对' : '当前动作仍未知：全部未覆盖来源保留'}</p>
      <p>时点：{data.as_of} · 恢复范围：{data.recovery_family.recovery_family_complete ? '完整' : '未知'} · 原仓位 {data.recovery_family.original_position_ids.length} 笔 · 原恢复动作 {data.recovery_family.original_action_ids.length} 笔</p>
      <p>本次只读没有赎回、增加现金、授予权限或投递介入通知。T1、到期、部分和有损分支尚有具体限制。</p>
      <ul>{data.candidates.map(row => <li key={row.candidate_key}>{row.candidate_key} · {states[row.state]} · {row.amount_cents === null ? '金额未知' : `¥${formatMoneyCents(row.amount_cents)}`} · {row.autonomy_level ?? '权限未知'}</li>)}</ul>
      {data.unsupported_producers.length > 0 && <details open><summary>未覆盖来源</summary><ul>{data.unsupported_producers.map(row => <li key={row}>{row}</li>)}</ul></details>}
      {data.reasons.length > 0 && <ul>{data.reasons.map(row => <li key={row}>{row}</li>)}</ul>}
      <details><summary>完整原响应及保留的先前快照</summary><pre className="readonly-raw">{originalRecoveryComposedActionSet(data)}</pre></details>
    </>}
  </section>;
}
