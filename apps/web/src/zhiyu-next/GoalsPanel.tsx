import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import type { NextState } from './api';
import { businessText, money, userError } from './display';
import { getPolicyRecords } from './policies';
import { getNextGoalPlanning, getNextJoint, jointExecutionOpen, type ThinJointResponse } from './goals';
import { readJointView, type JointUpdate } from './joint-operations';
import { jointV4PreparePath, type JointRecovery } from './joint-recovery';
import type { PaymentRecovery } from './payment-recovery';
import LocalUserPanel from './LocalUserPanel';
import GoalRepairPanel from './GoalRepairPanel';
import type { GoalModelUpdate } from './goal-repairs';

export type GoalPerform = (path: string, body: Record<string, unknown>, payment?: PaymentRecovery, joint?: JointRecovery) => Promise<void>;
const stageText: Record<ThinJointResponse['state'], string> = { PARTIALLY_PREPARED: '原准备尚未完整核实', PREPARED_UNRESERVED: '仅已准备，尚未确认或扣款', CONFIRMED_UNRESERVED: '整体确认已核实，逐项核对后执行', PARTIALLY_SETTLED: '部分原回执已核实', UNRESOLVED: '同一原动作正在核实', ORIGINAL_SERVICE_RECEIPTS_VERIFIED: '全部原回执已核实', STOPPED: '原安排已停止后续执行', RETAINED_HISTORY: '历史原件，只供查看' };
const phaseText = { BEFORE_PAYMENT: '付款前', AFTER_PAYMENT: '付款后', AFTER_PRINCIPAL: '本金变化后' };
export default function GoalsPanel({ environment, blocked, update, perform, runRead, retryOriginal, resumeOriginal, recovering = false, repairUpdate, repairRecovering }: { environment: NextState; blocked: boolean; update: JointUpdate | null; perform: GoalPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; retryOriginal?: () => Promise<void>; resumeOriginal?: () => Promise<void>; recovering?: boolean; repairUpdate?: GoalModelUpdate | null; repairRecovering?: boolean }) {
  const [open, setOpen] = useState(false); const [offset, setOffset] = useState(0); const [policyId, setPolicyId] = useState(''); const [session, setSession] = useState<LocalActorSession | null>(null); const [working, setWorking] = useState(false); const [error, setError] = useState('');
  useEffect(() => { if (update || recovering || repairUpdate || repairRecovering) setOpen(true); }, [update, recovering, repairUpdate, repairRecovering]);
  const identity = [environment.environment_id, environment.epoch_id];
  const planning = useQuery({ queryKey: ['zhiyu-next-goal-planning', ...identity, offset], queryFn: () => getNextGoalPlanning(environment, offset), enabled: open && !blocked, retry: false, staleTime: Infinity });
  const records = useQuery({ queryKey: ['zhiyu-next-policy-records', ...identity], queryFn: () => getPolicyRecords(environment), enabled: open && !blocked, retry: false, staleTime: Infinity });
  const original = useQuery({ queryKey: ['zhiyu-next-joint-view', ...identity], queryFn: () => readJointView(environment), enabled: open && !blocked && !update, retry: false, staleTime: Infinity, refetchInterval: (query) => !blocked && !update && query.state.data?.execution.state === 'UNRESOLVED' ? 15000 : false });
  const policies = records.data?.items.filter((row) => row.source_kind === 'FULL_POLICY' && row.template_name === 'GoalAllocationPolicy' && row.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(row.effective_status)) ?? [];
  const selected = policies.find((row) => row.policy_id === policyId) ?? (policies.length === 1 ? policies[0] : undefined);
  const signed = !!session && session.principal.role === 'USER' && session.principal.user_id === environment.dashboard.user_id && Date.parse(session.principal.expires_at) > Date.now();
  const view = planning.data; const execution = update?.execution ?? original.data?.execution; const plan = execution?.original_plan;
  const validation = plan?.current_execution_validation ?? view?.current_execution_validation ?? '';
  const available = jointExecutionOpen(validation);
  const readyToPrepare = !!selected && view?.state === 'COMPUTED' && view.allocation?.status === 'OPTIMAL' && view.goals.length >= 2 && view.goals.length <= 8 && view.allocation.goals.some((g) => g.amount_cents! > 0) && available && (!execution || ['ORIGINAL_SERVICE_RECEIPTS_VERIFIED', 'STOPPED', 'RETAINED_HISTORY'].includes(execution.state));
  const currentPolicy = plan && policies.find((p) => p.policy_id === plan.original_request.full_policy_id && p.current_version_id === plan.original_request.expected_full_policy_version_id);
  const canConfirm = !!plan && !!currentPolicy && execution?.state === 'PREPARED_UNRESERVED' && execution.original_consent === null && available && Date.parse(execution.as_of) < Date.parse(plan.expires_at);
  const name = (id: string) => businessText(environment.goals.find((g) => g.id === id)?.name ?? '本轮已关联目标');
  async function prepare() {
    if (blocked || working || !signed || !readyToPrepare || !selected) return;
    setWorking(true); setError('');
    try { await perform(jointV4PreparePath, { full_policy_id: selected.policy_id, expected_full_policy_version_id: selected.current_version_id, expected_epoch_id: environment.epoch_id, idempotency_key: crypto.randomUUID() }, undefined, { protocol: 'zhiyu-next-joint-recovery-v1', user_id: environment.dashboard.user_id, reviewed_plan: null, confirmation_key: null }); }
    catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function confirm() {
    if (blocked || working || !signed || !canConfirm || !plan) return;
    setWorking(true); setError('');
    try {
      // The immutable prepared original, never a newly generated preview hash, is the reviewed scope.
      const fresh = await runRead(() => getNextJoint(plan.plan_id, environment, plan));
      if (fresh.state !== 'PREPARED_UNRESERVED' || fresh.original_consent !== null || Date.parse(fresh.as_of) >= Date.parse(plan.expires_at) || !jointExecutionOpen(fresh.original_plan.current_execution_validation)) throw new Error('已审原安排的当前状态变化，请保留原件并核实。');
      const key = crypto.randomUUID();
      await perform(`/zhiyu-next/goals/joint/${plan.plan_id}/confirm`, { accepted: true, reviewed_plan_hash: plan.plan_hash, expected_epoch_id: plan.epoch_id, idempotency_key: key }, undefined, { protocol: 'zhiyu-next-joint-recovery-v1', user_id: plan.user_id, reviewed_plan: plan, confirmation_key: key });
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  return <section aria-label="联合目标与年度安排">
    <section className="zy-card"><div className="zy-section-heading"><h2>联合目标与年度安排</h2><button className="zy-secondary" disabled={blocked || working} onClick={() => setOpen((v) => !v)}>{open ? '收起年度安排' : '查看年度与联合规划'}</button></div><p>将本轮已到账、尚未归属目标的收入安排给多个目标，同时保留全年必要保护。</p></section>
    {open && <>
      <LocalUserPanel environment={environment} session={session} update={setSession} />
      <GoalRepairPanel environment={environment} session={session} blocked={blocked} update={repairUpdate} recovering={repairRecovering} perform={perform} runRead={runRead} retryOriginal={retryOriginal} />
      {(error || planning.isError || records.isError || original.isError) && <p className="zy-message zy-error" role="alert">{error || userError(planning.error ?? records.error ?? original.error)}</p>}
      {planning.isFetching && <p role="status">正在读取完整年度计算的显示分页…</p>}
      {!available && <p className="zy-message">联合执行尚待本轮真实链验收。当前可查看规划与原件；目标规则调整需本人单独确认。</p>}
      {recovering && !execution && <section className="zy-card"><h3>正在核实同一联合原请求</h3><p>原定位保持不变；尚无完整原件时，不生成替代计划或资金动作。</p>{retryOriginal && signed && <button className="zy-secondary" onClick={() => void retryOriginal()}>按原请求恢复会话后的续接</button>}</section>}
      {view && <section className="zy-card"><h3>当前已到账可安排</h3><strong className="zy-amount zy-amount-small">{money(view.state === 'COMPUTED' && view.allocation?.status === 'OPTIMAL' ? view.allocation.budget_cents : null)}</strong><p>该额度只使用实际已到账、尚未分配的收入。未来收入当前计入 ¥0.00；尚未登记可核实的未来收入预测来源。</p><p>{view.state === 'UNKNOWN' ? '当前来源或计算仍待核实。' : view.allocation?.status === 'INFEASIBLE' ? '当前目标条件存在冲突，先调整条件。' : '已计算本期规划，规划本身不授予执行权限。'}</p>
        <div className="zyn-goal-planning">{view.goals.map((g) => { const a = view.allocation?.goals.find((a) => a.goal_id === g.goal_id); return <article key={g.goal_id}><h4>{name(g.goal_id)}</h4><dl className="zy-facts"><div><dt>已有目标资金</dt><dd>{money(g.current_owned_cents)}</dd></div><div><dt>本期建议</dt><dd>{money(a?.amount_cents)}</dd></div><div><dt>每月最低 / 目标 / 上限</dt><dd>{money(g.monthly_min_cents)} / {money(g.monthly_target_cents)} / {money(g.monthly_max_cents)}</dd></div><div><dt>最低保证</dt><dd>{money(g.minimum_guarantee_cents)}</dd></div><div><dt>截止日期</dt><dd>{g.deadline}</dd></div><div><dt>建议后的目标资金</dt><dd>{money(a?.projected_owned_cents)}</dd></div></dl><p className="zy-muted">{a?.delay_censored ? '完成时间尚无完整证明，仅有当前决策下界。' : a?.completion_date ? `条件满足时预计 ${a.completion_date}` : '尚不能推断实际完成日期。'}</p></article>; })}</div>
        {view.conflict && <p role="status">{view.conflict.status === 'MINIMAL_CONFLICT' ? `已核实 ${view.conflict.constraint_ids.length} 项最小冲突条件；反事实方案不修改现行规则。` : '冲突结果需按服务端原范围核实。'}</p>}
        {view.reasons.length > 0 && <ul>{view.reasons.map((r, i) => <li key={i}>{businessText(r)}</li>)}</ul>}
        <label className="zy-field" htmlFor="zyn-joint-policy">采用已确认的分配范围<select id="zyn-joint-policy" value={selected?.policy_id ?? ''} disabled={blocked || working} onChange={(e) => setPolicyId(e.target.value)}><option value="">请选择当前范围</option>{policies.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label>
        <button className="zy-secondary" disabled={blocked || working || !signed || !readyToPrepare} onClick={() => void prepare()}>生成可审阅安排</button><p className="zy-muted">这一步只保存有限期内的原计划与固定动作，尚未确认、扣款或占用整笔资金。读取原件后再核对必要后果。</p>
      </section>}
      {view && <section className="zy-card"><h3>365 天保护与现金时间线</h3><p>{view.annual.all_points_used_for_safety ? `服务器使用全部 ${view.annual.calculation_points_total} 个计算时点核实安全，分页只影响显示。` : '完整计算尚未核实。'} 年度最低余量 {money(view.annual.minimum_margin_cents)}，安全闲置上限 {money(view.annual.safe_idle_cents)}。</p>
        <div className="zyn-table-scroll"><table className="zyn-table"><caption>月度保护摘要</caption><thead><tr><th>月份</th><th>最低现金</th><th>最高保护</th><th>最低余量</th></tr></thead><tbody>{view.annual.months.map((m) => <tr key={m.month}><th scope="row">{m.month}</th><td>{money(m.minimum_cash_cents)}</td><td>{money(m.maximum_protected_cents)}</td><td>{money(m.minimum_margin_cents)}</td></tr>)}</tbody></table></div>
        <details><summary>查看本页计算时点</summary><div className="zyn-table-scroll"><table className="zyn-table"><thead><tr><th>日期 / 时点</th><th>现金</th><th>保护后余量</th></tr></thead><tbody>{view.annual.points.map((p) => <tr key={`${p.date}:${p.phase}`}><th scope="row">{p.date} · {phaseText[p.phase]}</th><td>{money(p.cash_cents)}</td><td>{money(p.margin_cents)}</td></tr>)}</tbody></table></div></details>
        <div className="zy-buttons"><button className="zy-secondary" disabled={blocked || offset === 0} onClick={() => setOffset(Math.max(0, offset - 93))}>前一页时点</button><span>已显示 {Math.min(offset + 1, view.annual.calculation_points_total)}–{offset + view.annual.points.length} / {view.annual.calculation_points_total}</span><button className="zy-secondary" disabled={blocked || offset + 93 >= view.annual.calculation_points_total} onClick={() => setOffset(offset + 93)}>后一页时点</button></div>
        {view.limitations.length > 0 && <details><summary>当前证据范围</summary><ul>{view.limitations.map((x, i) => <li key={i}>{businessText(x)}</li>)}</ul></details>}
      </section>}
      {execution && plan && <section className="zy-card" aria-label="已准备联合原件"><h3>{stageText[execution.state]}</h3><strong className="zy-amount zy-amount-small">{money(plan.total_allocation_cents)}</strong><p>共 {plan.children.length} 个固定动作，按下列顺序安排。整笔资金未预留，操作之间没有整体原子性；每一步重检现行边界。有效至 {plan.expires_at.replace('T', ' ').slice(0, 19)}。</p>
        <ol>{plan.children.map((c, i) => { const row = execution.children[i]!; const source = [...new Set(c.command.effect.cash_uses!.map((u) => environment.dashboard.account_facts.facts.accounts.find((a) => a.id === u.account_id)?.name ?? '原来源账户'))]; return <li key={c.action_id}><strong>{name(c.goal_id)} · {money(c.command.effect.amount_cents)}</strong><p>来源 {source.map(businessText).join('、')}；费用 {money(c.command.effect.fee_cents)}，损失 {money(c.command.effect.loss_cents)}。</p><p>{row.state === 'ORIGINAL_RECEIPT_VERIFIED' ? `实际原回执已核实 ${money(row.original_action!.receipt!.executed_cents)}` : row.state === 'UNKNOWN' || row.state === 'SUBMITTED' ? '同一原动作正在核实，后续动作保持停止。' : row.state === 'STOPPED' ? '此原动作已停止。' : row.state === 'MISSING' ? '原准备尚未完整核实。' : '尚无已完成回执。'}</p></li>; })}</ol>
        {canConfirm && <><p>确认以上全部必要后果后，系统使用同一原计划逐项执行。任何结果未决都会停止后续动作。</p><button className="zy-primary" disabled={blocked || working || !signed} onClick={() => void confirm()}>确认并执行联合安排</button></>}
        {resumeOriginal && signed && <button className="zy-secondary" disabled={working} onClick={() => void resumeOriginal()}>继续同一原动作</button>}
        {retryOriginal && signed && <button className="zy-secondary" disabled={working} onClick={() => void retryOriginal()}>按原请求恢复会话后的续接</button>}
      </section>}
    </>}
  </section>;
}
