import { useQuery } from '@tanstack/react-query';
import { getCurrentGoalAllocation, getOriginalJointPlanning } from '../api/joint-planning';
import type { JointPlanning } from '../api/joint-planning';
import type { Goal } from '../api/goals';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';

const money = (value: number | null) => value === null ? 'UNKNOWN · 金额尚未证明' : `¥${formatMoneyCents(value)}`;
const objectiveNames = ['硬义务零违反', '应急与生活准备金零违反', '目标最低储备短缺', '重要程度加权缺口', '当前决策延期下界', '接近月度建议target', '不超过月度max', '资金调动次数'];
const goalName = (id: string, goals?: readonly Pick<Goal, 'id' | 'name'>[]) => goals?.find((goal) => goal.id === id)?.name ?? '原目标（列表名称未提供）';
function Result({ data, goals }: { data: JointPlanning; goals?: readonly Pick<Goal, 'id' | 'name'>[] }) {
  const allocation = data.allocation;
  return <><p className="caption">服务端规划时点 {data.as_of} · 资金仅来自当前实际新增且尚未归属收入。原收入可能受当前策略确认/有效时点限制；零计划不等于不存在收入事实。</p>
    <dl className="full-goal-fields"><div><dt>已登记目标分母</dt><dd>{data.registered_goal_count} 个</dd></div><div><dt>已取得完整输入目标</dt><dd>{data.included_goal_ids.length} 个</dd></div><div><dt>未覆盖目标</dt><dd>{data.uncovered_goal_ids.length} 个</dd></div><div><dt>规划状态</dt><dd>{data.state === 'UNKNOWN' ? 'UNKNOWN · 来源或当前求解尚未证明' : `当前期已计算 · ${allocation?.status}`}</dd></div>
      <div><dt>原银行投影比对</dt><dd>{data.independent_bank_projection_matched ? '服务报告本次模拟银行投影匹配' : '未证明匹配，不能据此执行'}</dd></div><div><dt>完整模型专用审计事件</dt><dd>未实现 · dedicated_audit_event=false</dd></div></dl>
    {data.included_goal_ids.some((id) => data.uncovered_goal_ids.includes(id)) && <p className="notice">容量不足时，完整输入候选与未覆盖名单可能重叠；登记分母按原目标全集保留，不将两项数量相加。</p>}
    <p className="notice">保留全部原365日最低储备，当前可分配池可能保守。仅当前期规划，未证明原完整问题的全局多期最优；结果不提交行动、回拨或银行授权。</p>
    {data.uncovered_goal_ids.length > 0 && <div><h4>未覆盖的原目标</h4><ul>{data.uncovered_goal_ids.map((id) => <li key={id}>{goalName(id, goals)} · <code>{id}</code>：UNKNOWN，不用旧模型或零金额替代。</li>)}</ul></div>}
    {data.source_issues.length > 0 && <ul className="issues" aria-label="联合规划来源问题">{data.source_issues.map((issue, index) => <li key={index}><strong>{issue.code}</strong> · {issue.message}<p>原来源 <code>{issue.source_ref}</code></p></li>)}</ul>}
    {allocation ? <><dl className="full-goal-fields"><div><dt>当前期原资金池</dt><dd>{money(allocation.budget_cents)}</dd></div><div><dt>原求解状态</dt><dd>{allocation.status}</dd></div><div><dt>实际访问状态数</dt><dd>{allocation.visited_nodes}</dd></div></dl>
      <section className="joint-objectives" aria-label="当前期八层目标结果"><h4>当前期八层目标结果</h4><ol>{objectiveNames.map((name, index) => <li key={name}><span>{index + 1}. {name}</span><strong>{allocation.objective_vector === null ? 'UNKNOWN · 尚无结果' : String(allocation.objective_vector[index])}</strong></li>)}</ol>
        <p className="caption">展示服务端词典序原向量，不把加权分数、延期下界或调动次数当金额。第1、2、7层通过本次硬约束保持；不等于前端独立重算。</p></section>
      <div className="joint-goal-results" aria-label="当前期原目标计划">{allocation.goals.map((goal) => <article className="joint-goal-result" aria-label={`目标计划 ${goal.goal_id}`} key={goal.goal_id}><h4>{goalName(goal.goal_id, goals)}</h4><p><code>{goal.goal_id}</code></p>
        <dl className="full-goal-fields"><div><dt>原有效版本</dt><dd><code>{goal.effective_policy_version_id}</code></dd></div><div><dt>本次规划新增分配</dt><dd>{money(goal.amount_cents)}</dd></div><div><dt>最低储备短缺</dt><dd>{money(goal.minimum_shortfall_cents)}</dd></div><div><dt>条件预计归属</dt><dd>{money(goal.projected_owned_cents)}</dd></div>
          <div><dt>本次条件完成日期</dt><dd>{goal.completion_date ?? '未给出实际完成日期'}</dd></div><div><dt>延期下界</dt><dd>{goal.delay_lower_bound_days === null ? 'UNKNOWN' : `${goal.delay_lower_bound_days} 日`}</dd></div><div><dt>延期是否截尾</dt><dd>{goal.delay_censored ? '是；未完成，仅有下界' : '否；不重建既往完成史'}</dd></div><div><dt>延期成本下界</dt><dd>{money(goal.deferral_cost_lower_bound_cents)}</dd></div></dl>
        <p className="caption">预计归属不是已经到账或已执行分配。实际资金归属仍以原目标卡的核验来源为准。延期下界不是预测完成日期，0日也不表示已完成。</p></article>)}</div>
      <details><summary>实际收入碎片使用计划 · {allocation.income_uses.length} 项</summary><ul>{allocation.income_uses.map((use, index) => <li key={index}>原碎片 <code>{use.fragment_id}</code>；原收入 <code>{use.origin_transaction_id}</code>；原来源账户 <code>{use.source_account_id}</code> → 目标 <code>{use.goal_id}</code>：{money(use.amount_cents)}</li>)}</ul></details>
      {allocation.reasons.map((reason) => <p className="caption" key={reason}>原求解原因：{reason}</p>)}</> : <p className="notice">尚无当前期求解结果，原目标分母与问题保留，不生成八层成功或分配金额。</p>}
    {data.conflict && <section className="joint-conflict" aria-label="原目标冲突"><h4>原目标冲突 · {data.conflict.status}</h4><p>财务保护底线不放宽；以下是原服务的策略反事实检查，不修改策略或授权。</p>
      <ul>{data.conflict.constraint_ids.map((id) => <li key={id}><code>{id}</code></li>)}</ul>{data.conflict.deletion_checks.map((check) => <details key={check.removed_constraint_id}><summary>反事实移除 {check.removed_constraint_id}</summary><p>服务报告剩余约束可满足；仅反事实，不是建议已被确认。</p><dl className="full-goal-fields">{Object.entries(check.witness_amounts_cents).map(([id, amount]) => <div key={id}><dt>原目标 {id}</dt><dd>{money(amount)}</dd></div>)}</dl></details>)}
      {data.conflict.reasons.map((reason) => <p key={reason}>{reason}</p>)}</section>}
    <details><summary>原规划来源和范围限制</summary><p>原输入摘要 <code>{data.input_hash}</code></p><ul>{data.source_evidence_ids.map((id) => <li key={id}><a href={`#evidence/EVIDENCE/${id}`}>{id}</a></li>)}</ul><ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
    <details><summary>查看当前联合规划原JSON响应</summary><pre className="readonly-raw">{getOriginalJointPlanning(data) ?? '原响应文本未保留'}</pre></details></>;
}
export default function CurrentGoalAllocationPanel({ goals }: { goals?: readonly Pick<Goal, 'id' | 'name'>[] }) {
  const query = useQuery({ queryKey: ['joint-current-goal-allocation'], queryFn: getCurrentGoalAllocation, retry: false, structuralSharing: false });
  return <section className="card readonly-section current-goal-planning" aria-label="当前期联合目标规划"><h3>当前期联合目标规划</h3><button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>只读刷新联合规划</button>
    {query.isPending && <p role="status">正在读取当前期联合规划…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}；旧计划不作为本次读取成功。</p>}
    {query.isFetching && query.data && <p role="status">正在重新读取，下方仍是上次原计划。</p>}{!query.isError && query.data && <Result data={query.data} goals={goals} />}
  </section>;
}
