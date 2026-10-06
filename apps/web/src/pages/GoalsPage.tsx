import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '../../../../packages/contracts/schema';
import { createGoal, getAccounts, getAssetAllocation, getGoalAllocation, getGoals, getPositions, getProducts } from '../api/goals';
import type { Goal } from '../api/goals';
import { getPolicies } from '../api/policies';
import type { Policy } from '../api/policies';
import { getDashboard } from '../api/dashboard';
import type { Dashboard } from '../api/dashboard';
import { errorMessage, request } from '../api/http';
import { confirmDemoAction, executeDemoAction, getDemoAction } from '../api/demo';
import type { DemoAction } from '../api/demo';
import { isRunId } from '../api/decisions';
import { object } from '../features/policy-form';
import { formatMoneyCents } from '../features/money';
import { lifecycleLabels } from '../features/policy-form';
import { ConfigurationReview } from '../components/PolicyConfigForm';
import ActionEffectReview from '../components/ActionEffectReview';
import FullGoalModelPanel from '../components/FullGoalModelPanel';
import GoalAdjustmentsPanel from '../components/GoalAdjustmentsPanel';
import type { FullGoalPreview } from '../api/full-goals';
import CurrentGoalAllocationPanel from '../components/CurrentGoalAllocationPanel';
import DynamicGoalReservePanel from '../components/DynamicGoalReservePanel';
import FullCurrentGoalAllocationPanel from '../components/FullCurrentGoalAllocationPanel';
import GoalReallocationPanel from '../components/GoalReallocationPanel';
import GoalConflictRepairPanel from '../components/GoalConflictRepairPanel';
import GoalReleaseAuthorizationPanel from '../components/GoalReleaseAuthorizationPanel';
import { useGoalReleaseAuthorizationOperation } from '../features/goal-release-authorization-operation';
import GoalCashReleaseExecutionPanel from '../components/GoalCashReleaseExecutionPanel';
import { recoverGoalCashReleaseOperation, useGoalCashReleaseOperation } from '../features/goal-cash-release-operation';
import FullDynamicGoalExecutionPanel from '../components/FullDynamicGoalExecutionPanel';
import DynamicGoalOriginalRecoveryPanel from '../components/DynamicGoalOriginalRecoveryPanel';

type OriginalGoalAction = { goal_id: string; idempotency_key: string; action_id?: string; effect_hash?: string };
type GoalActionControls = { original: OriginalGoalAction | null; busy: boolean; storage_error: string; retain: (value: OriginalGoalAction | null) => void; setBusy: (value: boolean) => void };
const originalKey = () => `bounded-funds-goal-action-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function readOriginal(): { original: OriginalGoalAction | null; storage_error: string } {
  try {
    const raw = sessionStorage.getItem(originalKey());
    if (raw === null) return { original: null, storage_error: '' };
    const value: unknown = JSON.parse(raw);
    if (!object(value) || !isRunId(value.goal_id) || typeof value.idempotency_key !== 'string' || !/^goal-action:[0-9a-f-]{36}$/.test(value.idempotency_key) ||
      !Object.keys(value).every((key) => ['goal_id', 'idempotency_key', 'action_id', 'effect_hash'].includes(key)) ||
      (value.action_id !== undefined && (!isRunId(value.action_id) || typeof value.effect_hash !== 'string' || !/^[0-9a-f]{64}$/.test(value.effect_hash))) ||
      (value.action_id === undefined && value.effect_hash !== undefined)) throw new Error('原分配身份未通过校验');
    return { original: value as OriginalGoalAction, storage_error: '' };
  } catch { return { original: null, storage_error: '浏览器原分配身份无法读取，暂不能准备新的分配；请先核对服务端原项。' }; }
}
function GoalAllocationAction({ goal, ready, controls, done }: { goal: Goal; ready: boolean; controls: GoalActionControls; done: () => Promise<void> }) {
  const [action, setAction] = useState<DemoAction | null>(null); const [accepted, setAccepted] = useState(false); const [error, setError] = useState('');
  const original = controls.original?.goal_id === goal.id ? controls.original : null;
  const blocked = controls.busy || !!controls.storage_error || (!!controls.original && !original);
  function matching(actual: DemoAction, identity: OriginalGoalAction) {
    if (actual.effect.action_type !== 'ALLOCATE_GOAL' || actual.effect.goal_id !== goal.id || actual.effect.policy_id !== goal.policy_id || actual.effect.destination_account_id !== goal.account_id ||
      (identity.action_id && (actual.action_id !== identity.action_id || actual.effect_hash !== identity.effect_hash))) throw new Error('服务端原分配与目标或原经济后果摘要不一致，保留原身份');
  }
  function adopt(actual: DemoAction, identity: OriginalGoalAction) {
    matching(actual, identity); setAction(actual); setAccepted(false);
    const settled = ['SUCCEEDED', 'RECONCILED'].includes(actual.status) && actual.receipt != null;
    const noEffect = ['INVALIDATED', 'CANCELLED', 'EXPIRED', 'REJECTED'].includes(actual.status) && [null, 'REJECTED'].includes(actual.bank_status ?? null);
    controls.retain(settled || noEffect ? null : { ...identity, action_id: actual.action_id, effect_hash: actual.effect_hash });
  }
  async function perform(work: () => Promise<void>) {
    if (blocked) return;
    controls.setBusy(true); setError('');
    try { await work(); }
    catch (value) { setError(errorMessage(value)); }
    finally { controls.setBusy(false); }
  }
  async function prepare() {
    await perform(async () => {
      const identity = original ?? { goal_id: goal.id, idempotency_key: `goal-action:${crypto.randomUUID()}` };
      if (identity.action_id) throw new Error('原分配尚待核对，不能准备新的动作');
      controls.retain(identity); // Persist the original intent before the first financial request.
      const prepared = await request<DemoAction>('/actions/prepare', 'POST', { idempotency_key: identity.idempotency_key, intent: { kind: 'allocate_goal', goal_id: goal.id } } satisfies components['schemas']['PrepareActionRequest']);
      if (!isRunId(prepared.action_id) || !/^[0-9a-f]{64}$/.test(prepared.effect_hash)) throw new Error('服务端准备身份无效，保留原请求键');
      matching(prepared, identity);
      const bound = { ...identity, action_id: prepared.action_id, effect_hash: prepared.effect_hash };
      controls.retain(bound); adopt(await getDemoAction(bound.action_id), bound); await done();
    });
  }
  async function read() {
    await perform(async () => { if (!original?.action_id) throw new Error('原准备响应尚未知，请重试同一准备请求'); adopt(await getDemoAction(original.action_id), original); await done(); });
  }
  async function execute() {
    await perform(async () => {
      if (!original?.action_id || !action) throw new Error('请先读取原分配');
      let actual = await getDemoAction(original.action_id); matching(actual, original);
      if (['SUCCEEDED', 'RECONCILED', 'INVALIDATED', 'CANCELLED', 'EXPIRED', 'REJECTED'].includes(actual.status)) { adopt(actual, original); await done(); return; }
      if (actual.autonomy_level === 'ASK_ONCE' && actual.status === 'PLANNED') {
        if (!accepted) throw new Error('本次原分配仍需具体确认');
        actual = await confirmDemoAction(actual); matching(actual, original);
      }
      if (!['AUTO_EXECUTE', 'ASK_ONCE'].includes(actual.autonomy_level) || !['PLANNED', 'AUTHORIZED', 'UNKNOWN', 'SUBMITTED'].includes(actual.status)) throw new Error('当前原分配不可执行');
      adopt(await executeDemoAction(actual), original); await done();
    });
  }
  const ask = action?.autonomy_level === 'ASK_ONCE' && action.status === 'PLANNED';
  const executable = action && ['AUTO_EXECUTE', 'ASK_ONCE'].includes(action.autonomy_level) && ['PLANNED', 'AUTHORIZED', 'UNKNOWN', 'SUBMITTED'].includes(action.status);
  return <section className="state-review" aria-label={`目标收入分配 ${goal.id}`}><h4>将后续新收入分配给此目标</h4>
    <p className="caption">预览不划款。准备只提交此目标意图，金额、来源和权限由服务端当前事实决定。</p>
    {!original?.action_id && <button disabled={blocked || (!original && !ready)} onClick={() => void prepare()}>{original ? '重试原分配准备' : '准备当前收入分配'}</button>}
    {original && <p className="caption">保留的原准备键 {original.idempotency_key}；只用于恢复原请求，不能充当授权。</p>}
    {original?.action_id && <button disabled={blocked} onClick={() => void read()}>读取原分配</button>}
    {original?.action_id && !action && <p className="notice">请先读取服务端原项，再决定是否继续原动作。</p>}
    {action && <><ActionEffectReview action={action} />{action.receipt && <p className="caption">实际原回执 {action.receipt.receipt_id} · 原银行操作 {action.receipt.bank_operation_id} · {action.receipt.status}</p>}
      {ask && <label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={blocked} onChange={(event) => setAccepted(event.target.checked)} />我已复核此原分配金额、来源、费用、损失和经济后果摘要，明确确认本次动作</label>}
      {executable && original && <button disabled={blocked || (ask && !accepted)} onClick={() => void execute()}>{ask ? '具体确认并执行原分配' : ['UNKNOWN', 'SUBMITTED'].includes(action.status) ? '核对并恢复原分配' : '执行已复核的原分配'}</button>}
      {['UNKNOWN', 'SUBMITTED'].includes(action.status) && <p className="notice">原结果尚未核定，保留原 action_id 和摘要；不能换键重新准备。</p>}</>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function Money({ cents }: { cents: number | null | undefined }) {
  const formatted = cents == null ? '待核验' : `¥${formatMoneyCents(cents)}`;
  return <span className={`money ${formatted.length > 18 ? 'money-long' : ''}`}>{formatted}</span>;
}
function EstablishGoal({ policy, accounts, done, blocked }: { policy: Policy; accounts: components['schemas']['AccountView'][]; done: () => Promise<void>; blocked: boolean }) {
  const [account, setAccount] = useState(''); const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function create() {
    if (!accepted || !account || !policy.current_version || blocked) return;
    setBusy(true); setError('');
    try { await createGoal({ policy_id: policy.id, expected_version_id: policy.current_version.id, account_id: account }); await done(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <article className="policy-card" aria-label={`待建立目标 ${policy.name}`}><h3>{policy.name}</h3><p className="notice">目标配置已确认，尚未建立目标归属。</p>
    {policy.current_version && <details><summary>查看目标配置</summary><ConfigurationReview configuration={policy.current_version.configuration} /></details>}
    <label className="field">目标归属账户<select aria-label="目标归属账户" value={account} disabled={busy} onChange={(e) => { setAccount(e.target.value); setAccepted(false); }}><option value="">选择本人现金或目标账户</option>
      {accounts.filter((item) => ['CASH', 'GOAL'].includes(item.account_type)).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.account_type}</option>)}</select></label>
    <p className="caption">建立初始零归属与配置关联，不划拨账户余额、不认领待归属现金。建立后不能更换该目标账户。</p>
    <label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={busy} onChange={(e) => setAccepted(e.target.checked)} />我已核对当前版本与账户，建立目标归属记录</label>
    <button disabled={!accepted || !account || busy || blocked || !policy.version_authorized} onClick={() => void create()}>建立目标归属</button>{error && <p role="alert">{error}</p>}
  </article>;
}
function AllocationPreview({ goal, controls, done }: { goal: Goal; controls: GoalActionControls; done: () => Promise<void> }) {
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
        <GoalAllocationAction goal={goal} ready={!income.isError && income.data?.allocation.status === 'READY' && (income.data.allocation.suggested_cents ?? 0) > 0} controls={controls} done={done} />
      </> : <>{asset.isPending && <p role="status">正在读取预览…</p>}{asset.isError && <p role="alert">{errorMessage(asset.error)}</p>}
        {!asset.isError && asset.data && <><p>查询时点 {asset.data.as_of} · 状态 {asset.data.allocation.status}</p><p>建议资产类别 {asset.data.allocation.selected_asset_class ?? '待核验'} · 产品 {asset.data.allocation.selected_product_id ?? '待核验'}</p>
          <dl className="goal-metrics"><div><dt>建议配置</dt><dd><Money cents={asset.data.allocation.suggested_cents} /></dd></div><div><dt>保留现金</dt><dd><Money cents={asset.data.allocation.retained_cash_cents} /></dd></div></dl>
          {(asset.data.allocation.reasons ?? []).map((reason) => <p className="caption" key={reason}>{reason}</p>)}</>}
      </>}
    </section>}
  </div>;
}
function GoalCard({ goal, policy, assetPolicy, dashboard, positions, products, controls, done, mutationBlocked, modelBlocked, cashExecutionBlocked, dynamicExecutionBlocked, reviewCandidate }: { goal: Goal; policy: Policy | undefined; assetPolicy: Policy | undefined; dashboard: Dashboard | undefined;
  positions: components['schemas']['PositionView'][] | undefined; products: components['schemas']['ProductView'][] | undefined; controls: GoalActionControls; done: () => Promise<void>; mutationBlocked: boolean; modelBlocked: boolean; cashExecutionBlocked: boolean; dynamicExecutionBlocked: boolean; reviewCandidate: FullGoalPreview | null }) {
  const [fullModelOpen, setFullModelOpen] = useState(false);
  const [dynamicOpen, setDynamicOpen] = useState(false);
  const [dynamicExecutionOpen, setDynamicExecutionOpen] = useState(false);
  const [reallocationOpen, setReallocationOpen] = useState(false);
  const [authorizationOpen, setAuthorizationOpen] = useState(false);
  const [cashOpen, setCashOpen] = useState(false);
  const cash = useGoalCashReleaseOperation();
  const originalCash = cash.pending?.intent.owner_goal.id === goal.id ? cash.pending.intent : null;
  const release = useGoalReleaseAuthorizationOperation();
  const originalRelease = release.pending?.owner_goal_id === goal.id ? release.pending : null;
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
    <fieldset className="page-operation-gate" disabled={mutationBlocked}><AllocationPreview goal={goal} controls={controls} done={done} /></fieldset>
    <details open={fullModelOpen || !!reviewCandidate} onToggle={(event) => setFullModelOpen(event.currentTarget.open)}><summary>查看完整目标模型与只读预览</summary>
      {(fullModelOpen || !!reviewCandidate) && <FullGoalModelPanel goal={goal} reviewCandidate={reviewCandidate} blocked={modelBlocked || controls.busy || !!controls.original || !!controls.storage_error} />}</details>
    <details onToggle={(event) => setDynamicOpen(event.currentTarget.open)}><summary>查看动态月储备与真实进度</summary>
      {dynamicOpen && <DynamicGoalReservePanel goal={goal} />}</details>
    <details onToggle={(event) => setDynamicExecutionOpen(event.currentTarget.open)}><summary>准备动态目标原动作或核对原结果</summary>
      {dynamicExecutionOpen && (dashboard && dashboard.audit.epoch_id && goal.policy_version_id ? <FullDynamicGoalExecutionPanel goal={{ id: goal.id, policy_id: goal.policy_id, policy_version_id: goal.policy_version_id, name: goal.name }} userId={dashboard.user_id} epochId={dashboard.audit.epoch_id}
        mutationBlocked={dynamicExecutionBlocked || controls.busy || !!controls.original || !!controls.storage_error} />
        : <p className="notice">当前用户、策略版本与审计周期来源尚未读取，动态目标新动作待核验；原请求恢复在列表外保留。</p>)}</details>
    <details onToggle={(event) => setReallocationOpen(event.currentTarget.open)}><summary>查看紧急回拨规则与当前修复下界</summary>
      {reallocationOpen && (dashboard ? <GoalReallocationPanel goal={goal} userId={dashboard.user_id} epochId={dashboard.audit.epoch_id} />
        : <p className="notice">当前用户与审计周期来源尚未读取，回拨预览待核验。</p>)}</details>
    <details onToggle={(event) => setAuthorizationOpen(event.currentTarget.open)}><summary>复核专用紧急回拨授权或查询原确认</summary>
      {authorizationOpen && (dashboard || originalRelease ? <GoalReleaseAuthorizationPanel goal={goal}
        userId={dashboard?.user_id ?? originalRelease!.user_id} epochId={dashboard?.audit.epoch_id ?? null}
        mutationBlocked={mutationBlocked || modelBlocked || controls.busy || !!controls.original || !!controls.storage_error} />
        : <p className="notice">当前用户与审计周期尚未读取，暂不能确认专用授权。</p>)}</details>
    <details onToggle={(event) => setCashOpen(event.currentTarget.open)}><summary>复核紧急现金回拨行动或查询原回执</summary>
      {cashOpen && (dashboard || originalCash ? <GoalCashReleaseExecutionPanel goal={goal}
        userId={dashboard?.user_id ?? originalCash!.user_id} epochId={dashboard?.audit.epoch_id ?? null}
        mutationBlocked={cashExecutionBlocked || controls.busy || !!controls.original || !!controls.storage_error} />
        : <p className="notice">当前用户与审计周期未读取，暂不能准备新的回拨行动。</p>)}</details>
  </article>;
}
export default function GoalsPage({ mutationBlocked = false, modelBlocked = false, cashExecutionBlocked = mutationBlocked, dynamicExecutionBlocked = mutationBlocked }: { mutationBlocked?: boolean; modelBlocked?: boolean; cashExecutionBlocked?: boolean; dynamicExecutionBlocked?: boolean }) {
  const [adjustmentReview, setAdjustmentReview] = useState<{ goalId: string; preview: FullGoalPreview } | null>(null);
  const client = useQueryClient();
  const [jointOpen, setJointOpen] = useState(false);
  const [fullJointOpen, setFullJointOpen] = useState(false);
  const [conflictsOpen, setConflictsOpen] = useState(false);
  const [record, setRecord] = useState(readOriginal); const [actionBusy, setActionBusy] = useState(false);
  const release = useGoalReleaseAuthorizationOperation();
  const cash = useGoalCashReleaseOperation();
  useEffect(() => { void recoverGoalCashReleaseOperation(); }, []);
  const [cashRecovery, setCashRecovery] = useState<{ goal: Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'>; userId: string } | null>(null);
  useEffect(() => { if (cash.pending) setCashRecovery({ goal: cash.pending.intent.owner_goal, userId: cash.pending.intent.user_id }); }, [cash.pending]);
  function retain(original: OriginalGoalAction | null) {
    try { if (original) sessionStorage.setItem(originalKey(), JSON.stringify(original)); else sessionStorage.removeItem(originalKey()); setRecord({ original, storage_error: '' }); }
    catch { setRecord((current) => ({ ...current, storage_error: '浏览器无法保存原分配身份，请保留本页并先核对原请求。' })); throw new Error('浏览器无法保存原分配身份，资金请求暂不继续'); }
  }
  const controls: GoalActionControls = { ...record, busy: actionBusy, retain, setBusy: setActionBusy };
  const goals = useQuery({ queryKey: ['goals'], queryFn: getGoals, retry: false });
  const policies = useQuery({ queryKey: ['policies'], queryFn: getPolicies, retry: false });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: getAccounts, retry: false });
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: getDashboard, retry: false, refetchOnWindowFocus: false });
  const positions = useQuery({ queryKey: ['positions'], queryFn: getPositions, retry: false });
  const products = useQuery({ queryKey: ['products'], queryFn: getProducts, retry: false });
  async function refresh() { await Promise.all(['goals', 'policies', 'accounts', 'dashboard', 'positions', 'products', 'goal-allocation', 'full-goal-model', 'joint-current-goal-allocation', 'full-current-goal-allocation', 'dynamic-goal-reserve', 'full-policies', 'full-goal-conflicts'].map((key) => client.invalidateQueries({ queryKey: [key] }))); }
  const fact = !dashboard.isError ? dashboard.data : undefined;
  const savedSource = release.pending?.reviewed_scope.source_goals.find((source) => source.goal_id === release.pending?.owner_goal_id);
  const [releaseRecovery, setReleaseRecovery] = useState<{ goal: Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'>; userId: string } | null>(null);
  useEffect(() => {
    if (release.pending && savedSource) setReleaseRecovery({ goal: { id: savedSource.goal_id, name: '原授权来源目标', policy_id: savedSource.original_policy_id, policy_version_id: savedSource.original_policy_version_id }, userId: release.pending.user_id });
  }, [release.pending, savedSource]);
  const missing = !policies.isError && !goals.isError && goals.data ? policies.data?.items.filter((policy) => policy.policy_type === 'goal_saving' && policy.version_authorized && !goals.data.items.some((goal) => goal.policy_id === policy.id)) ?? [] : [];
  return <><section className="page-intro"><div><p className="eyebrow">归属清楚，配置各在其位</p><h2>目标储备</h2></div><button onClick={() => void refresh()}>刷新目标</button></section>
    <p className="caption">参数、策略与资金核验分别读取。资金归属时点 {fact?.as_of ?? '待核验'}；不同请求不构成单次事务快照。</p>
    <details onToggle={(event) => setJointOpen(event.currentTarget.open)}><summary>查看当前期联合目标规划</summary>
      {jointOpen && <CurrentGoalAllocationPanel goals={!goals.isError ? goals.data?.items : undefined} />}</details>
    <details onToggle={(event) => setFullJointOpen(event.currentTarget.open)}><summary>查看完整支出保护下的联合目标规划</summary>
      {fullJointOpen && <FullCurrentGoalAllocationPanel goals={!goals.isError ? goals.data?.items : undefined} />}</details>
    <details onToggle={(event) => setConflictsOpen(event.currentTarget.open)}><summary>查看目标最小冲突与限定修复预览</summary>
      {conflictsOpen && <GoalConflictRepairPanel goals={!goals.isError ? goals.data?.items : undefined}
        mutationBlocked={mutationBlocked || modelBlocked || controls.busy || !!controls.original || !!controls.storage_error} />}</details>
    {record.storage_error && <p role="alert">{record.storage_error}</p>}
    <DynamicGoalOriginalRecoveryPanel />
    {releaseRecovery && !goals.isPending && (goals.isError || !goals.data?.items.some((goal) => goal.id === releaseRecovery.goal.id)) &&
      <section aria-label="列表外原回拨授权恢复"><p className="notice">原授权来源目标未在当前列表中；保留原确认身份，只读查询原epoch与键。</p>
        <GoalReleaseAuthorizationPanel goal={releaseRecovery.goal} userId={releaseRecovery.userId} epochId={null} mutationBlocked /></section>}
    {cashRecovery && !goals.isPending && (goals.isError || !goals.data?.items.some((goal) => goal.id === cashRecovery.goal.id)) &&
      <section aria-label="列表外原现金回拨恢复"><p className="notice">原现金回拨目标未在当前列表中；保留完整原意图，只读核对原行动与回执。</p>
        <GoalCashReleaseExecutionPanel goal={cashRecovery.goal} userId={cashRecovery.userId} epochId={null} mutationBlocked /></section>}
    {record.original && !goals.isPending && !goals.isError && !goals.data?.items.some((goal) => goal.id === record.original?.goal_id) && <p className="notice">原分配目标暂不在当前列表，保留原请求身份；不能以新的目标或请求键替换。</p>}
    {[goals, policies, accounts, dashboard, positions, products].filter((query) => query.isError).map((query, index) => <p key={index} role="alert">{errorMessage(query.error)}</p>)}
    {(goals.isPending || policies.isPending) && <p role="status">正在读取目标与策略…</p>}
    {missing.length > 0 && <section className="policy-section" aria-label="待建立目标"><h3>已确认，待建立目标</h3>{missing.map((policy) => <EstablishGoal key={policy.current_version?.id} policy={policy} accounts={!accounts.isError ? accounts.data?.accounts ?? [] : []} done={refresh} blocked={mutationBlocked || actionBusy || !!record.original || !!record.storage_error} />)}</section>}
    {!goals.isError && goals.data?.items.length === 0 && missing.length === 0 && <p className="empty">还没有目标。可先在策略中心起草并确认目标储蓄策略。</p>}
    <GoalAdjustmentsPanel key={accounts.isError ? 'unverified-owner' : accounts.data?.user_id ?? 'pending-owner'} ownerUserId={accounts.isError ? undefined : accounts.data?.user_id} goals={goals.isError ? undefined : goals.data?.items} blocked={modelBlocked || controls.busy || !!controls.original || !!controls.storage_error} onReviewCandidate={(goalId, preview) => setAdjustmentReview({ goalId, preview })} />
    {adjustmentReview && <button type="button" onClick={() => setAdjustmentReview(null)}>仅关闭待复核调整候选</button>}
    {!goals.isError && goals.data?.items.map((goal) => <GoalCard key={goal.id} goal={goal} reviewCandidate={adjustmentReview?.goalId === goal.id ? adjustmentReview.preview : null} policy={!policies.isError ? policies.data?.items.find((item) => item.id === goal.policy_id) : undefined}
      assetPolicy={!policies.isError ? policies.data?.items.find((item) => item.id === goal.asset_policy_id) : undefined}
      dashboard={fact} positions={!positions.isError ? positions.data?.items : undefined} products={!products.isError ? products.data?.items : undefined} controls={controls} done={refresh} mutationBlocked={mutationBlocked} modelBlocked={modelBlocked} cashExecutionBlocked={cashExecutionBlocked} dynamicExecutionBlocked={dynamicExecutionBlocked} />)}
    <section className="evidence-panel" aria-label="目标账户待归属"><h3>目标账户待归属现金</h3><Money cents={fact?.goal_ownership.unassigned_goal_cash_cents} /><p className="caption">待归属现金仍受保护，不能当成已经属于某个目标。专用紧急现金回拨仅回到保护现金，须单独复核原授权和经济后果。</p></section>
  </>;
}
