import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '../../../../packages/contracts/schema';
import { createGoal, getAccounts, getAssetAllocation, getGoalAllocation, getGoals, getPositions, getProducts } from '../api/goals';
import type { Goal } from '../api/goals';
import { getPolicies } from '../api/policies';
import type { Policy } from '../api/policies';
import { getDashboard } from '../api/dashboard';
import type { Dashboard } from '../api/dashboard';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { lifecycleLabels } from '../features/policy-form';
import { ConfigurationReview } from '../components/PolicyConfigForm';

function Money({ cents }: { cents: number | null | undefined }) {
  const formatted = cents == null ? '待核验' : `¥${formatMoneyCents(cents)}`;
  return <span className={`money ${formatted.length > 18 ? 'money-long' : ''}`}>{formatted}</span>;
}
function EstablishGoal({ policy, accounts, done }: { policy: Policy; accounts: components['schemas']['AccountView'][]; done: () => Promise<void> }) {
  const [account, setAccount] = useState(''); const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function create() {
    if (!accepted || !account || !policy.current_version) return;
    setBusy(true); setError('');
    try { await createGoal({ policy_id: policy.id, expected_version_id: policy.current_version.id, account_id: account }); await done(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <article className="policy-card" aria-label={`待建立目标 ${policy.name}`}><h3>{policy.name}</h3><p className="notice">目标配置已确认，尚未建立目标归属。</p>
    {policy.current_version && <details><summary>查看目标配置</summary><ConfigurationReview configuration={policy.current_version.configuration} /></details>}
    <label className="field">目标归属账户<select value={account} disabled={busy} onChange={(e) => { setAccount(e.target.value); setAccepted(false); }}><option value="">选择本人现金或目标账户</option>
      {accounts.filter((item) => ['CASH', 'GOAL'].includes(item.account_type)).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.account_type}</option>)}</select></label>
    <p className="caption">建立初始零归属与配置关联，不划拨账户余额、不认领待归属现金。建立后不能更换该目标账户。</p>
    <label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={busy} onChange={(e) => setAccepted(e.target.checked)} />我已核对当前版本与账户，建立目标归属记录</label>
    <button disabled={!accepted || !account || busy || !policy.version_authorized} onClick={() => void create()}>建立目标归属</button>{error && <p role="alert">{error}</p>}
  </article>;
}
function AllocationPreview({ goal }: { goal: Goal }) {
  const [kind, setKind] = useState<'income' | 'asset' | null>(null);
  const income = useQuery({ queryKey: ['goal-allocation', goal.id], queryFn: () => getGoalAllocation(goal.id), enabled: kind === 'income', retry: false });
  const asset = useQuery({ queryKey: ['asset-allocation', goal.asset_policy_id], queryFn: () => getAssetAllocation(goal.asset_policy_id!), enabled: kind === 'asset' && !!goal.asset_policy_id, retry: false });
  return <div><div className="button-row"><button onClick={() => setKind(kind === 'income' ? null : 'income')}>查看当前收入分配预览</button>
    {goal.asset_policy_id && <button onClick={() => setKind(kind === 'asset' ? null : 'asset')}>查看当前资产规划预览</button>}</div>
    {kind && <section className="allocation-preview" aria-label="当前配置分配预览"><p className="caption">这是当前配置的只读规划，未执行资金动作，不能替代策略修改前后比较。</p>
      {kind === 'income' ? <>{income.isPending && <p role="status">正在读取预览…</p>}{income.isError && <p role="alert">{errorMessage(income.error)}</p>}
        {!income.isError && income.data && <><p>查询时点 {income.data.as_of} · 状态 {income.data.allocation.status}</p><dl className="goal-metrics">
          <div><dt>建议贡献</dt><dd><Money cents={income.data.allocation.suggested_cents} /></dd></div><div><dt>最大安全贡献</dt><dd><Money cents={income.data.allocation.max_safe_cents} /></dd></div>
          <div><dt>已核验新收入</dt><dd><Money cents={income.data.allocation.eligible_new_funds_cents} /></dd></div><div><dt>最低承诺缺口</dt><dd><Money cents={income.data.allocation.minimum_shortfall_cents} /></dd></div></dl>
          {(income.data.allocation.reasons ?? []).map((reason) => <p className="caption" key={reason}>{reason}</p>)}</>}
      </> : <>{asset.isPending && <p role="status">正在读取预览…</p>}{asset.isError && <p role="alert">{errorMessage(asset.error)}</p>}
        {!asset.isError && asset.data && <><p>查询时点 {asset.data.as_of} · 状态 {asset.data.allocation.status}</p><p>建议资产类别 {asset.data.allocation.selected_asset_class ?? '待核验'} · 产品 {asset.data.allocation.selected_product_id ?? '待核验'}</p>
          <dl className="goal-metrics"><div><dt>建议配置</dt><dd><Money cents={asset.data.allocation.suggested_cents} /></dd></div><div><dt>保留现金</dt><dd><Money cents={asset.data.allocation.retained_cash_cents} /></dd></div></dl>
          {(asset.data.allocation.reasons ?? []).map((reason) => <p className="caption" key={reason}>{reason}</p>)}</>}
      </>}
    </section>}
  </div>;
}
function GoalCard({ goal, policy, assetPolicy, dashboard, positions, products }: { goal: Goal; policy: Policy | undefined; assetPolicy: Policy | undefined; dashboard: Dashboard | undefined;
  positions: components['schemas']['PositionView'][] | undefined; products: components['schemas']['ProductView'][] | undefined }) {
  const ownership = dashboard?.goal_ownership.state === 'PROVEN' ? dashboard.goal_ownership.items.find((item) => item.goal_id === goal.id) : undefined;
  const managed = dashboard?.managed_assets.state === 'PROVEN' ? dashboard.managed_assets.by_goal.find((item) => item.goal_id === goal.id) : undefined;
  const matching = !!policy?.current_version && policy.current_version.id === goal.policy_version_id;
  return <article className="goal-card" aria-label={`目标 ${goal.name}`}><div className="card-heading"><h3>{goal.name}</h3><span className="badge">{policy ? lifecycleLabels[policy.effective_status] ?? policy.effective_status : '策略待核验'}</span></div>
    <p>目标 <Money cents={goal.target_cents} /> · 截止 {goal.deadline}</p>
    {!matching && <p className="notice">目标与当前策略版本未对齐或未完整读取，请刷新核验；当前不能据此发起配置变更。</p>}
    <dl className="goal-metrics"><div><dt>每月最低</dt><dd><Money cents={goal.monthly_min_cents} /></dd></div><div><dt>每月目标</dt><dd><Money cents={goal.monthly_target_cents} /></dd></div><div><dt>每月最高</dt><dd><Money cents={goal.monthly_max_cents} /></dd></div>
      <div><dt>已归属现金</dt><dd><Money cents={ownership?.cash_owned_cents} /></dd></div><div><dt>已归属本金</dt><dd><Money cents={ownership?.principal_owned_cents} /></dd></div><div><dt>已归属合计 / 目标</dt><dd><Money cents={ownership?.allocated_cents} /> / <Money cents={goal.target_cents} /></dd></div>
      <div><dt>其中已自主配置本金</dt><dd><Money cents={managed?.principal_cents} /></dd></div><div><dt>待完成自主购买</dt><dd><Money cents={managed?.pending_purchase_cents} /></dd></div></dl>
    <p className="caption">已自主配置本金可能属于目标本金，不能再次加总。归属来源不足时不以目标投影的 allocated 数值推测现金。</p>
    <p>当前资产策略：{goal.asset_policy_id ? assetPolicy ? `${assetPolicy.name} · ${lifecycleLabels[assetPolicy.effective_status] ?? assetPolicy.effective_status}` : '关联配置待核验' : '未绑定'}；跨目标重分配：{goal.cross_goal_reallocation_allowed ? '配置允许，仍需具体动作权限核验' : '不允许'}。</p>
    {goal.asset_policy_id && <details><summary>查看关联资产策略</summary>{assetPolicy?.current_version
      ? <><p>资产策略版本 {assetPolicy.current_version.version_number} · {goal.asset_policy_id}</p><ConfigurationReview configuration={assetPolicy.current_version.configuration} /></>
      : <p>尚未完整读取关联策略，不自动选择一般闲置授权或其他目标策略。</p>}</details>}
    <details><summary>目标优先级与资产放置</summary><p>重要程度 {goal.importance} · 最低保护 <Money cents={goal.minimum_protection_cents} /> · 降低承诺 {goal.reducible ? '允许' : '不允许'} · 延期 {goal.deferrable ? '允许' : '不允许'}</p>
      {positions === undefined || products === undefined ? <p>持仓或产品事实暂未完整读取，资产放置待核验。</p>
        : <ul>{positions.filter((position) => position.goal_id === goal.id).map((position) => { const product = products.find((item) => item.id === position.product_id); return <li key={position.id}>{product?.name ?? '产品信息待核验'} · 本金 <Money cents={position.principal_cents} /> · {position.status} · {position.policy_version_id ? '具有关联策略版本（管理归属以总览核验为准）' : '手工持仓'} · 可用时点 {position.available_at ?? '待核验'}</li>; })}</ul>}
    </details><p className="caption">目标配置修改与暂停/撤销在策略中心完成；不会因此划款或默认挪用其他目标。</p><a href="#policies">前往策略中心</a>
    <AllocationPreview goal={goal} />
  </article>;
}
export default function GoalsPage() {
  const client = useQueryClient();
  const goals = useQuery({ queryKey: ['goals'], queryFn: getGoals, retry: false });
  const policies = useQuery({ queryKey: ['policies'], queryFn: getPolicies, retry: false });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: getAccounts, retry: false });
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: getDashboard, retry: false, refetchOnWindowFocus: false });
  const positions = useQuery({ queryKey: ['positions'], queryFn: getPositions, retry: false });
  const products = useQuery({ queryKey: ['products'], queryFn: getProducts, retry: false });
  async function refresh() { await Promise.all(['goals', 'policies', 'accounts', 'dashboard', 'positions', 'products'].map((key) => client.invalidateQueries({ queryKey: [key] }))); }
  const fact = !dashboard.isError ? dashboard.data : undefined;
  const missing = !policies.isError && !goals.isError && goals.data ? policies.data?.items.filter((policy) => policy.policy_type === 'goal_saving' && policy.version_authorized && !goals.data.items.some((goal) => goal.policy_id === policy.id)) ?? [] : [];
  return <><section className="page-intro"><div><p className="eyebrow">归属清楚，配置各在其位</p><h2>目标储备</h2></div><button onClick={() => void refresh()}>刷新目标</button></section>
    <p className="caption">参数、策略与资金核验分别读取。资金归属时点 {fact?.as_of ?? '待核验'}；不同请求不构成单次事务快照。</p>
    {[goals, policies, accounts, dashboard, positions, products].filter((query) => query.isError).map((query, index) => <p key={index} role="alert">{errorMessage(query.error)}</p>)}
    {(goals.isPending || policies.isPending) && <p role="status">正在读取目标与策略…</p>}
    {missing.length > 0 && <section className="policy-section" aria-label="待建立目标"><h3>已确认，待建立目标</h3>{missing.map((policy) => <EstablishGoal key={policy.current_version?.id} policy={policy} accounts={!accounts.isError ? accounts.data?.accounts ?? [] : []} done={refresh} />)}</section>}
    {!goals.isError && goals.data?.items.length === 0 && missing.length === 0 && <p className="empty">还没有目标。可先在策略中心起草并确认目标储蓄策略。</p>}
    {!goals.isError && goals.data?.items.map((goal) => <GoalCard key={goal.id} goal={goal} policy={!policies.isError ? policies.data?.items.find((item) => item.id === goal.policy_id) : undefined}
      assetPolicy={!policies.isError ? policies.data?.items.find((item) => item.id === goal.asset_policy_id) : undefined}
      dashboard={fact} positions={!positions.isError ? positions.data?.items : undefined} products={!products.isError ? products.data?.items : undefined} />)}
    <section className="evidence-panel" aria-label="目标账户待归属"><h3>目标账户待归属现金</h3><Money cents={fact?.goal_ownership.unassigned_goal_cash_cents} /><p className="caption">待归属现金仍受保护，不能当成已经属于某个目标。当前没有跨目标资金划拨按钮。</p></section>
  </>;
}
