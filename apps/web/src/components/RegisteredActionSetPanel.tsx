import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRegisteredActionSet, originalRegisteredActionSet } from '../api/registered-action-set';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';

const states = { INCLUDED: '已纳入', EXCLUDED: '已证明排除', UNKNOWN: '尚未证明' };
export default function RegisteredActionSetPanel() {
  const [requested, setRequested] = useState(false);
  const query = useQuery({ queryKey: ['registered-current-action-set-v5'], queryFn: getRegisteredActionSet, enabled: requested, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, structuralSharing: false });
  const data = requested && !query.isFetching ? query.data : undefined;
  const previous = data?.original_recovery_composed_snapshot;
  const actual = previous?.original_composed_snapshot.original_actual_snapshot;
  return <section className="card" aria-label="当前完整登记动作集合">
    <h3>当前完整登记动作集合</h3><p>手动核对付款、整仓恢复、现金归属释放和目标联动的当前来源。未覆盖分支与未决原动作保留；完整核对只描述登记范围。</p>
    <button type="button" disabled={query.isFetching} onClick={() => { if (requested) void query.refetch(); else setRequested(true); }}>刷新当前登记动作集合</button>
    {requested && query.isPending && <p role="status">正在读取当前登记来源…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)}；此次没有一致原件，不采用先前完整结论。</p>}
    {!query.isError && data && previous && actual && <>
      <p role="status">{data.global_action_set_complete ? '当前登记范围完整：只读核对，不授执行权限' : '当前动作集合未知：未覆盖分母完整保留'}</p>
      <p>读取时点：{data.as_of} · 用户 {data.user_id} · 周期 {data.epoch_id}</p>
      <p>候选分母 {data.expected_candidate_keys.length} · 已返回 {data.candidates.length} · 尚无候选原件 {data.expected_candidate_keys.filter(key => !data.candidates.some(row => row.candidate_key === key)).length}</p>
      <dl>
        <dt>付款家族</dt><dd>策略 {previous.original_composed_snapshot.periodic_family.expected_full_policy_ids.length} · 返回 {previous.original_composed_snapshot.periodic_family.results.length} · {previous.original_composed_snapshot.periodic_family.periodic_family_complete ? '来源完整' : '来源未知'}</dd>
        <dt>恢复家族</dt><dd>策略 {previous.recovery_family.expected_full_policy_ids.length} · 返回 {previous.recovery_family.results.length} · 原仓位 {previous.recovery_family.original_position_ids.length} · 原动作 {previous.recovery_family.original_action_ids.length} · 未决 {previous.recovery_family.unresolved_original_action_ids.length} · {previous.recovery_family.recovery_family_complete ? '来源完整' : '来源未知'}</dd>
        <dt>归属释放家族</dt><dd>策略 {data.release_family.expected_full_policy_ids.length} · 候选分母 {data.release_family.expected_candidate_keys.length} · 返回 {data.release_family.results.length} · 原授权 {data.release_family.original_authorization_source_ids.length} · 原动作 {data.release_family.original_action_ids.length} · 未决 {data.release_family.unresolved_original_action_ids.length} · {data.release_family.release_family_complete ? '来源完整' : '来源未知'}</dd>
        <dt>目标联动家族</dt><dd>目标 {data.joint_family.expected_goal_ids.length} · 候选分母 {data.joint_family.expected_candidate_keys.length} · 返回 {data.joint_family.results.length} · 原动作 {data.joint_family.original_action_ids.length} · 未决 {data.joint_family.unresolved_original_action_ids.length} · {data.joint_family.joint_family_complete ? '来源完整' : '来源未知'}</dd>
      </dl>
      <p>没有金融写入或观察、通知、确认操作。新现金到账、策略撤销和占用变化需再次手动刷新。纳入不等于可自动执行；任意手动意图不在本登记范围。</p>
      <ul>{data.candidates.map(row => <li key={row.candidate_key}>{row.candidate_key} · {states[row.state]} · {row.amount_cents === null ? '金额未知' : `¥${formatMoneyCents(row.amount_cents)}`} · {row.autonomy_level ?? '权限未知'}{row.reasons.length > 0 && <ul>{row.reasons.map((reason, index) => <li key={`${reason}:${index}`}>{reason}</li>)}</ul>}</li>)}</ul>
      <details open><summary>完整候选与来源分母</summary><ul>{data.expected_candidate_keys.map(key => <li key={key}>{key} · {data.candidates.some(row => row.candidate_key === key) ? '原件已返回' : '原件缺失，保持未知'}</li>)}</ul><ul>{actual.table_coverage.map(row => <li key={row.table}>{row.table}：实际 {row.actual_count ?? '未知'} / 捕获 {row.captured_count} · {row.complete ? '完整' : '未完整'} · {row.rows_hash}</li>)}</ul></details>
      <details open><summary>全部未覆盖与来源不足</summary>{[
        ['当前集合', data.reasons], ['未接生产者', data.unsupported_producers], ['原 v4', previous.reasons],
        ['付款来源', previous.original_composed_snapshot.periodic_family.reasons], ['恢复来源', previous.recovery_family.reasons],
        ['归属释放来源', data.release_family.reasons], ['目标联动来源', data.joint_family.reasons],
      ].map(([label, reasons]) => <div key={label as string}><p>{label}</p><ul>{(reasons as string[]).map((reason, index) => <li key={`${reason}:${index}`}>{reason}</li>)}</ul></div>)}</details>
      <details><summary>当前与保留来源哈希</summary><dl>{[
        ['v5快照', data.snapshot_hash], ['v5输入', data.input_hash], ['动作签名', data.action_set_signature ?? '未知'],
        ['完整原库存', data.original_inventory_hash], ['财务输入', data.financial_input_hash], ['原v4快照', previous.snapshot_hash],
        ['Release原结果', data.release_family.result_hash], ['Release原输入', data.release_family.input_hash],
        ['Joint原结果', data.joint_family.result_hash], ['Joint原输入', data.joint_family.input_hash],
      ].map(([label, hash]) => <div key={label}><dt>{label}</dt><dd>{hash}</dd></div>)}</dl><p>被明确替代的旧候选：{data.replaced_original_candidate_keys.join('、') || '无'}</p></details>
      <details><summary>完整原响应（保留 v2 / v3 / v4 / Release / Joint）</summary><pre className="readonly-raw">{originalRegisteredActionSet(data)}</pre></details>
    </>}
  </section>;
}
