import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NextState } from './api';
import type { LocalActorSession } from '../api/local-actor';
import type { PaymentPerform } from './PaymentPanel';
import { getPolicyRecords } from './policies';
import { matchingGoalPermissions, prepareGoalAssetLink, type GoalAssetLinkReview } from './goal-asset-link';
import { businessText, money, userError } from './display';

export default function GoalAssetLinkPanel({ environment, session, blocked, perform, runRead, candidateEdited, openRequested = false, reviewing }: { environment: NextState; session: LocalActorSession | null; blocked: boolean; perform: PaymentPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; candidateEdited: () => void; openRequested?: boolean; reviewing: (source: GoalAssetLinkReview) => void }) {
  const [open, setOpen] = useState(false); const [goalId, setGoalId] = useState(''); const [permissionId, setPermissionId] = useState(''); const [review, setReview] = useState<GoalAssetLinkReview | null>(null); const [working, setWorking] = useState(false); const [error, setError] = useState('');
  useEffect(() => { if (openRequested) setOpen(true); }, [openRequested]);
  const records = useQuery({ queryKey: ['zhiyu-next-policy-records', environment.environment_id, environment.epoch_id], queryFn: () => getPolicyRecords(environment), enabled: open && !blocked, retry: false });
  const goal = environment.goals.find((g) => g.id === goalId); const permissions = goal ? matchingGoalPermissions(records.data?.items ?? [], goal) : []; const permission = permissions.find((p) => p.policy_id === permissionId);
  const signed = session?.principal.role === 'USER' && session.principal.user_id === environment.dashboard.user_id;
  async function preview() { if (!goal || !permission || blocked || working) return; setWorking(true); setError(''); setReview(null); candidateEdited(); try { const source = await runRead(() => prepareGoalAssetLink(goal, permission.policy_id, environment)); setReview(source); reviewing(source); await perform('/zhiyu-next/policy-candidates', source.body); } catch (cause) { setError(userError(cause)); } finally { setWorking(false); } }
  return <section className="zy-card" aria-label="重审目标资产关联"><div className="zy-section-heading"><h3>重审已有目标的资产关联</h3><button className="zy-secondary" disabled={working} onClick={() => setOpen((v) => !v)}>{open ? '收起目标资产关联' : '设置目标资产关联'}</button></div><p>购买权限独立确认后，还需本人重审原目标。保留当前完整目标全部字段，只选择关联同一目标的原购买权限，不创建另一目标或购买产品。</p>{open && <>
    <label className="zy-field" htmlFor="zyn-link-existing-goal">本轮原目标<select id="zyn-link-existing-goal" value={goalId} disabled={blocked || working} onChange={(e) => { setGoalId(e.target.value); setPermissionId(''); setReview(null); candidateEdited(); }}><option value="">请选择已有目标</option>{environment.goals.map((g) => <option key={g.id} value={g.id}>{businessText(g.name)}</option>)}</select></label>
    <label className="zy-field" htmlFor="zyn-link-native-permission">同一目标的当前原购买权限<select id="zyn-link-native-permission" value={permissionId} disabled={blocked || working} onChange={(e) => { setPermissionId(e.target.value); setReview(null); candidateEdited(); }}><option value="">请选择已独立确认的权限</option>{permissions.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label>
    {goal && !permissions.length && <p>尚无与该目标匹配的当前原购买权限。请先在资产安排页独立确认同一目标范围，不会把完整规划 ID 当作购买权限。</p>}
    {(error || records.isError) && <p role="alert">{error || userError(records.error)}</p>}
    <button className="zy-secondary" disabled={blocked || working || !goal || !permission} onClick={() => void preview()}>读取完整原目标并预览关联</button>
    {review && <section aria-label="原目标关联审阅"><h4>{businessText(review.goal.name)} → {businessText(review.permission.name)}</h4><dl className="zy-facts"><div><dt>原目标金额</dt><dd>{money(review.configuration.target_cents)}</dd></div><div><dt>原截止日期</dt><dd>{review.configuration.deadline}</dd></div><div><dt>每月最低 / 目标 / 上限</dt><dd>{money(review.configuration.monthly_contribution.min_cents)} / {money(review.configuration.monthly_contribution.target_cents)} / {money(review.configuration.monthly_contribution.max_cents)}</dd></div><div><dt>原最低保证</dt><dd>{money(review.configuration.minimum_guarantee_cents)}</dd></div><div><dt>原延期成本</dt><dd>{money(review.configuration.deferral_cost_cents_per_day)} / 天</dd></div></dl><p>部分完成、延期、重要程度及其他现有完整约束均来自原模型；本次只修改资产关联。请审阅下方服务端候选，再一次本人确认。</p></section>}
    {!signed && <p>完成候选后，请在本页建立本轮本人确认身份；最终确认仍使用同一候选原请求。</p>}
    <p className="zy-muted">确认会创建原目标的新原生版本及完整模型。已有完整资产规划须随后按原范围暂停、恢复以重审版本关联；本次不自动操作其他规则。</p>
  </>}</section>;
}
