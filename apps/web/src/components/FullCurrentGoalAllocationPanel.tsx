import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Goal } from '../api/goals';
import { errorMessage } from '../api/http';
import { getFullCurrentGoalAllocation, getOriginalFullJointPlanning, validateFullJointGoals } from '../api/full-joint-planning';
import type { FullJointPlanning } from '../api/full-joint-planning';
import { formatMoneyCents } from '../features/money';

const money = (value: number | null | undefined) => value == null ? 'UNKNOWN · 金额未证明' : `¥${formatMoneyCents(value)}`;
const names = ['硬义务零违反', '应急与生活准备金零违反', '目标最低保障短缺', '重要程度加权缺口', '当前决策延期下界', '接近月度建议额', '不超过月度上限', '资金调动次数'];
const floorNames = ['原硬义务', '原生活备用', '原应急保护', '原已归属目标现金', '原目标最低保障'];

function Result({ data, goals }: { data: FullJointPlanning; goals: readonly Goal[] | undefined }) {
  const [day, setDay] = useState(0); const allocation = data.allocation; const binding = data.binding;
  const oldTrace = data.full_protection.projection.original_annual_projection.calculation_trace;
  const fullTrace = data.full_protection.projection.full_annual_projection?.calculation_trace;
  const goalName = (id: string) => goals?.find((row) => row.id === id)?.name ?? '原目标（未提供当前列表名称）';
  return <><p className="notice">仅当前期条件规划，保留原全部365日保护和目标最低。不提交行动、不确认或执行资金，不证明多期全局最优；未来收入不用于今天分配。</p>
    <p className="caption">本次原服务时点 {data.as_of} · 原Joint、Full保护与重算来自本次同一服务快照。独立目标列表只作身份/版本/归属交叉核对，不能称相同事务。</p>
    {goals === undefined && <p className="notice">当前目标列表未提供；下方保留本次服务原件，不声称已核当前Goal行。</p>}
    <dl className="full-goal-fields"><div><dt>原登记目标分母</dt><dd>{data.original_joint.registered_goal_count} 个</dd></div><div><dt>完整输入目标</dt><dd>{data.original_joint.included_goal_ids.length} 个</dd></div><div><dt>未覆盖原目标</dt><dd>{data.original_joint.uncovered_goal_ids.length} 个</dd></div><div><dt>Full重算状态</dt><dd>{data.state === 'UNKNOWN' ? 'UNKNOWN · 未证明可分配' : allocation?.status}</dd></div><div><dt>保护时点交叉绑定</dt><dd>{binding ? `${binding.bound_point_count} / ${binding.original_point_count} 原点 · ${binding.status}` : 'UNKNOWN · 原候选未取得'}</dd></div></dl>
    <ul className="issues" aria-label="Full联合规划原原因">{data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    {data.original_joint.uncovered_goal_ids.length > 0 && <ul aria-label="Full未覆盖目标">{data.original_joint.uncovered_goal_ids.map((id) => <li key={id}>{goalName(id)} · <code>{id}</code>：UNKNOWN，保留分母，不用零或旧模型替代。</li>)}</ul>}
    <dl className="full-goal-fields"><div><dt>原Joint当前期预算</dt><dd>{data.original_joint.allocation?.status === 'UNKNOWN' ? 'UNKNOWN · 原资金池未证明' : money(data.original_joint.allocation?.budget_cents)}</dd></div><div><dt>Full保护后预算</dt><dd>{allocation?.status === 'UNKNOWN' ? 'UNKNOWN · 来源或求解未证明' : money(allocation?.budget_cents)}</dd></div></dl>
    <section aria-label="Full当前期八层结果"><h4>Full当前期八层结果</h4><ol>{names.map((name, index) => <li key={name}>{index+1}. {name}：{allocation?.objective_vector ? String(allocation.objective_vector[index]) : 'UNKNOWN · 尚无向量'}</li>)}</ol><p className="caption">服务原词典序向量；加权缺口、延期下界与调动次数不冒充金额或独立经济验证。</p></section>
    {allocation?.goals.map((row) => { const input = binding?.status === 'VERIFIED' ? binding.candidate.goals.find((goal) => goal.goal_id === row.goal_id) : undefined; return <article key={row.goal_id} aria-label={`Full目标计划 ${row.goal_id}`}><h4>{goalName(row.goal_id)}</h4><p>原目标 <code>{row.goal_id}</code> · 原有效版本 <code>{row.effective_policy_version_id}</code></p><dl className="full-goal-fields"><div><dt>原实际已归属</dt><dd>{money(input?.current_owned_cents)}</dd></div><div><dt>本月原已贡献</dt><dd>{money(input?.current_month_contributed_cents)}</dd></div><div><dt>本次条件新增分配</dt><dd>{money(row.amount_cents)}</dd></div><div><dt>条件预计归属</dt><dd>{money(row.projected_owned_cents)}</dd></div><div><dt>最低保障缺口</dt><dd>{money(row.minimum_shortfall_cents)}</dd></div><div><dt>原最低保障</dt><dd>{money(input?.minimum_guarantee_cents)}</dd></div><div><dt>延期下界</dt><dd>{row.delay_lower_bound_days === null ? 'UNKNOWN' : `${row.delay_lower_bound_days} 日 · ${row.delay_censored ? '截尾下界' : '非截尾'}`}</dd></div></dl><p>预计归属和新增分配均未执行；原已归属不是可重新借出的公有资金。0日延期不表示目标已完成。</p></article>; })}
    <details><summary>实际原收入碎片使用计划（{!allocation || allocation.status === 'UNKNOWN' ? 'UNKNOWN' : allocation.income_uses.length}项）</summary>{(!allocation || allocation.status === 'UNKNOWN') && <p>UNKNOWN · 尚无证明的计划，不代表实际收入不存在。</p>}{allocation?.income_uses.map((use, index) => <p key={index}>原 fragment <code>{use.fragment_id}</code> · 原收入 <code>{use.origin_transaction_id}</code> · 源账户 <code>{use.source_account_id}</code> → 原目标 <code>{use.goal_id}</code>：{money(use.amount_cents)}</p>)}</details>
    <label className="field">逐层查看保护日期<select value={day} onChange={(event) => setDay(Number(event.target.value))}>{Array.from({ length: 366 }, (_, index) => <option key={index} value={index}>{data.full_protection.daily_checkpoints[index-1]?.date ?? data.full_protection.initial_checkpoint.date}</option>)}</select></label>
    <div className="annual-phases">{(['付款前', '付款后', '本金到账后'] as const).map((phase, index) => { const old = oldTrace[day*3+index]; const full = fullTrace?.[day*3+index]; const candidate = binding?.status === 'VERIFIED' ? binding.candidate.hard_protection_points[day*3+index] : undefined; return <section key={phase} aria-label={`Full联合保护${phase}`}><h4>{phase}</h4><p>原条件现金 {money(old?.cash_cents)} · Full条件现金 {money(full?.cash_cents)}</p><dl>{(['obligations', 'living', 'emergency', 'goal_cash', 'goal_minimum'] as const).map((key, position) => <div key={key}><dt>{floorNames[position]}</dt><dd>{money(old?.protected_cents_by_reason[key])}</dd></div>)}{(['full_dated_expense', 'full_periodic_transfer', 'pending_cash_reservations'] as const).map((key) => <div key={key}><dt>{key}</dt><dd>{money(full?.protected_cents_by_reason[key])}</dd></div>)}<div><dt>实际传入原求解器的附加后other floor</dt><dd>{money(candidate?.other_protection_floor_cents)}</dd></div></dl></section>; })}</div>
    {data.conflict && <section aria-label="Full联合目标冲突"><h4>Full目标冲突 · {data.conflict.status}</h4><p>财务底线保持；删除约束检查只是假设，不改策略。</p><ul>{data.conflict.constraint_ids.map((id) => <li key={id}>{id}</li>)}</ul></section>}
    <p className="notice">未来指定来源账户扣款尚未完整重放，相关周期义务保留UNKNOWN；不借另一账户现金冒充可支付。历史结清、跨目标产权重分配及多期联合最优未实现。</p>
    <details><summary>原版本、来源与绑定摘要</summary><p>原Joint摘要 <code>{data.original_joint.input_hash}</code> · Full输入摘要 <code>{data.full_protection.input_digest}</code></p><p>原捕获输入摘要 <code>{binding?.original_input_hash ?? 'UNKNOWN'}</code> · 绑定摘要 <code>{binding?.binding_hash ?? 'UNKNOWN'}</code> · 新求解输入 <code>{allocation?.input_hash ?? 'UNKNOWN'}</code></p><p>展示服务原hash及浏览器字段/数值交叉校验，不声称重新计算这些SHA或独立验证银行/经济效果。</p><ul>{binding?.verified_source_refs.map((source) => <li key={source.evidence_id}><a href={`#evidence/EVIDENCE/${source.evidence_id}`}>{source.evidence_id}</a> · owner {source.user_id} · hash {source.content_hash}</li>)}</ul><ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
    <details><summary>完整联合规划原JSON响应</summary><pre className="readonly-raw">{getOriginalFullJointPlanning(data) ?? '原文本未保留'}</pre></details>
  </>;
}

export default function FullCurrentGoalAllocationPanel({ goals }: { goals: readonly Goal[] | undefined }) {
  const query = useQuery({ queryKey: ['full-current-goal-allocation'], queryFn: getFullCurrentGoalAllocation, retry: false, structuralSharing: false });
  let mismatch: string | null = null;
  if (query.data) { try { validateFullJointGoals(query.data, goals); } catch (error) { mismatch = errorMessage(error); } }
  return <section className="card readonly-section" aria-label="完整保护当前期目标规划"><h3>完整保护当前期目标规划</h3><button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>只读刷新Full联合规划</button>{query.isPending && <p role="status">正在核对本次原目标与Full保护…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}；旧报告不作为本次读取成功。</p>}{mismatch && <p role="alert">{mismatch}；当前目标列表已变化或不一致，请刷新原目标与规划。</p>}{query.isFetching && query.data && <p role="status">正在重新读取；下方仍是上次原报告，不是本次成功。</p>}{!query.isError && !mismatch && query.data && <Result data={query.data} goals={goals} />}</section>;
}
