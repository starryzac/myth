import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import { ApiError } from '../api/http';
import { parseFullGoalConfiguration } from '../api/full-goals';
import type { FullGoalRepairs } from '../api/full-goal-conflicts';
import type { NextState } from './api';
import { businessText, money, userError } from './display';
import { checkReviewedGoalRepair, getNextGoalConflicts, goalRepairRanges, prepareGoalRepairSelection, previewNextGoalRepairs, type GoalModelUpdate, type GoalRepairVersion } from './goal-repairs';
import type { GoalPerform } from './GoalsPanel';

export default function GoalRepairPanel({ environment, session, blocked, update, recovering, perform, runRead, retryOriginal }: { environment: NextState; session: LocalActorSession | null; blocked: boolean; update?: GoalModelUpdate | null; recovering?: boolean; perform: GoalPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; retryOriginal?: () => Promise<void> }) {
  const [open, setOpen] = useState(false), [ranges, setRanges] = useState<Record<string, string>>({}), [proposal, setProposal] = useState<FullGoalRepairs | null>(null), [working, setWorking] = useState(false), [error, setError] = useState(''), [baselineInvalid, setBaselineInvalid] = useState(false);
  const conflicts = useQuery({ queryKey: ['zhiyu-next-goal-conflicts', environment.environment_id, environment.epoch_id], queryFn: () => getNextGoalConflicts(environment), enabled: open && !blocked, retry: false, staleTime: Infinity });
  const signed = !!session && session.principal.role === 'USER' && session.principal.user_id === environment.dashboard.user_id && Date.parse(session.principal.expires_at) > Date.now();
  useEffect(() => { if (update || recovering) { setOpen(true); setProposal(null); setRanges({}); } }, [update, recovering]);
  const baseline = baselineInvalid ? undefined : conflicts.data;
  const rows = baseline?.state === 'COMPUTED' ? goalRepairRanges(baseline, environment) : [];
  const name = (id: string) => businessText(environment.goals.find((g) => g.id === id)?.name ?? '本轮目标');
  async function rebaseline() { setBaselineInvalid(true); setProposal(null); setRanges({}); const read = await conflicts.refetch(); setBaselineInvalid(read.isError || !read.data); }
  async function preview() {
    if (blocked || working || baseline?.state !== 'COMPUTED') return;
    setWorking(true); setError(''); setProposal(null);
    try {
      setProposal(await runRead(async () => previewNextGoalRepairs(baseline, await prepareGoalRepairSelection(baseline, ranges, environment), environment)));
    } catch (cause) { setError(userError(cause)); if (cause instanceof ApiError && cause.status === 409) await rebaseline(); }
    finally { setWorking(false); }
  }
  async function confirm(version: GoalRepairVersion) {
    if (blocked || working || !signed || !proposal || !baseline) return;
    setWorking(true); setError('');
    try {
      await runRead(() => checkReviewedGoalRepair(baseline, version, environment));
      await perform(`/zhiyu-next/goals/models/${version.goal_id}/confirm`, { ...structuredClone(version.confirmation_bindings), accepted: true, reason: '用户审阅当前冲突修复，仅上调该目标月上限并保留完整现行配置', idempotency_key: crypto.randomUUID() });
      // Every confirmed version changes the baseline. Remaining previews are never reused.
      setProposal(null); setRanges({});
    } catch (cause) { setError(userError(cause)); await rebaseline(); }
    finally { setWorking(false); }
  }
  return <section className="zy-card" aria-label="目标冲突修复"><div className="zy-section-heading"><h3>目标条件冲突</h3><button className="zy-secondary" disabled={blocked || working} onClick={() => setOpen(!open)}>{open ? '收起冲突条件' : '核实当前冲突与修复范围'}</button></div>
    <p>只在你选定的月上限范围内寻找必要调整，保留现行最低保证与全年保护。查看建议不会执行资金。</p>
    {open && <>
      {(error || conflicts.isError) && <p className="zy-message zy-error" role="alert">{error || userError(conflicts.error)}</p>}
      {conflicts.isFetching && <p role="status">正在核实当前全部目标与原版本…</p>}
      {baselineInvalid && <><p>变更后的来源尚未重新核实，旧范围不再可提交。</p><button className="zy-secondary" disabled={blocked || working || conflicts.isFetching} onClick={() => void rebaseline()}>重新核实当前冲突</button></>}
      {update && <p role="status">{name(update.goal_id)}的新版本与原确认已独立核实。请重新核实其它目标的当前修复范围。</p>}
      {recovering && <><p role="status">正在核实同一目标版本确认，原请求保持不变；尚未找到原件时不会另建请求。</p>{signed && retryOriginal && <button className="zy-secondary" onClick={() => void retryOriginal()}>按原请求恢复会话后的续接</button>}</>}
      {baseline?.state === 'UNKNOWN' && <p>当前目标来源或冲突结果待核实，暂不提交调整。</p>}
      {baseline?.state === 'COMPUTED' && <>
        <p>{baseline.explanation?.conflict.status === 'MINIMAL_CONFLICT' ? `本次已核实 ${baseline.explanation.conflict.constraint_ids.length} 个相互冲突条件。` : '当前没有可确认的最小冲突修复；以服务器当前结果为准。'}</p>
        <p>最小冲突集只说明其中一组矛盾。下列保留全部当前目标，可为其它目标一同选择有限月上限；预览时核实完整范围。</p>
        {rows.map((r) => <div key={r.goal_id}><h4>{name(r.goal_id)}</h4><p>现行月上限 {money(r.original_parameter_cents)}；本月已归属 {money(r.current_month_contributed_cents)}。</p><label className="zy-field">可接受的最高月上限（元）<input inputMode="decimal" value={ranges[r.goal_id] ?? ''} disabled={blocked || working} onChange={(e) => { setRanges({ ...ranges, [r.goal_id]: e.target.value }); setProposal(null); }} placeholder="留空表示不调整此目标" /></label></div>)}
        <button className="zy-secondary" disabled={blocked || working || !rows.length || baseline.explanation?.conflict.status !== 'MINIMAL_CONFLICT'} onClick={() => void preview()}>预览必要的月上限调整</button>
      </>}
      {proposal && <section aria-label="月上限调整建议"><h4>已核实的调整建议</h4><p>多个目标分别确认版本；确认一个目标后，重新核实其余目标。每份确认只采用下列已展示范围。</p>
        {proposal.version_previews.map((v) => { const before = parseFullGoalConfiguration(v.original_full_configuration); return <article key={v.goal_id}><h4>{name(v.goal_id)}</h4><p>月上限 {money(v.original_monthly_max_cents)} → {money(v.proposed_monthly_max_cents)}。</p><p>目标总额 {money(before.target_cents)}；月最低 {money(before.monthly_contribution.min_cents)}、月目标 {money(before.monthly_contribution.target_cents)}、最低保证 {money(before.minimum_guarantee_cents)}、截止日期 {before.deadline}。</p><details><summary>保留的其它目标条件</summary><p>重要程度 {before.importance}；{before.allow_partial ? '允许部分达成' : '要求完整达成'}；{before.allow_deferral ? `允许延期，每日代价 ${money(before.deferral_cost_cents_per_day)}` : '不允许延期'}。生效范围 {before.valid_from ?? '按现行规则'} 至 {before.valid_until ?? '未另设结束日期'}。{before.asset_policy_id ? '保留原资产权限关联' : '尚未关联资产权限'}；不进行跨目标资金重分配。</p></details><button className="zy-primary" disabled={blocked || working || !signed || proposal.state !== 'PROPOSAL'} onClick={() => void confirm(v)}>确认此目标的新月上限</button></article>; })}
        {!proposal.version_previews.length && <p>当前选定范围没有可确认的新版本。请调整有限范围或重新核实来源。</p>}
        {proposal.reasons.length > 0 && <ul>{proposal.reasons.map((r, i) => <li key={i}>{businessText(r)}</li>)}</ul>}
      </section>}
    </>}
  </section>;
}
