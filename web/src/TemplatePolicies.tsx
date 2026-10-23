import { useEffect, useId, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from './api';
import type { Client, Dashboard } from './api';
import { money } from './money';
import { timestamp, userError } from './display';
import TemplateEditor, { defaultTemplate } from './TemplateEditor';
import { ForecastCurve } from './Forecast';
import { clearTemplateConfirmation, loadTemplateConfirmation, saveTemplateConfirmation, TemplateClient } from './template-api';
import type { PendingTemplateConfirmation, TemplatePolicyView, TemplatePreview } from './template-api';
import { assetLabels, parseConfiguration, templateLabels, templateNames } from './template-schema';
import type { TemplateConfiguration, TemplateName } from './template-schema';
import './template-pages.css';

const stateText: Record<string, string> = { ACTIVE: '当前有效', CONFIRMED: '已确认，尚未到生效日', SUSPENDED: '已暂停', EXPIRED: '已到期', REVOKED: '已撤销', CONFIRMATION_IN_FUTURE: '确认时间尚待核实' };
const actionText = { PAUSE: '暂停', RESUME: '恢复', REVOKE: '撤销' } as const;
const impactText: Record<string, string> = { READY: '已形成规则影响预览', NO_ACTION: '当前无需安排', HARD_OBLIGATION: '将保留已确认支出', PROTECTION_ONLY: '将增加现金保护', ADVICE_ONLY: '仅提供建议', MINIMUM_SHORTFALL: '最低要求存在缺口', GUARANTEE_SHORTFALL: '最低保证存在缺口', USER_QUESTION_REQUIRED: '需要本人补充确认', SILENT_STABLE_OR_DEDUPLICATED: '当前动作未变，保持安静', LIQUIDITY_RISK: '流动性保护尚未满足' };
const nameOf = (view: TemplatePolicyView) => view.version.compiled.configuration.name ?? templateLabels[view.version.compiled.template_name];
export function commitmentMeaning(kind: string): { label: string; timing: string } {
  if (kind === 'EMERGENCY_BUFFER') return { label: '应急备用金保护', timing: '保护起点' };
  if (kind === 'LIVING_RESERVE') return { label: '生活费保护', timing: '保护起点' };
  if (kind === 'ADOPTED_SEASONAL_FLOOR') return { label: '已确认节庆资金保护', timing: '保护起点' };
  if (kind === 'RECURRING_OBLIGATION') return { label: '定期应付支出保留', timing: '到期日' };
  if (kind === 'PERIODIC_TRANSFER') return { label: '周期转账资金保留', timing: '到期日' };
  if (kind === 'DATED_EXPENSE') return { label: '专项支出资金保留', timing: '窗口开始' };
  if (kind.startsWith('BILL_')) return { label: '已核实账单资金保留', timing: '到期日' };
  return { label: '规则资金保留项', timing: '参考日期' };
}

function PreviewFacts({ preview, dashboard }: { preview: TemplatePreview; dashboard: Dashboard }) {
  const f = preview.facts;
  const account = (id: string) => dashboard.accounts.find((a) => a.id === id)?.name ?? '已核实账户';
  const goal = (id: string) => dashboard.goals.find((g) => g.id === id)?.name ?? '已核实目标';
  return <details><summary>核对本次真实事实与来源范围</summary><p>已确认现金 {money(f.current_cash_cents)}；完整保护后的当前余量 {money(f.current_safe_cents)}；已有管理金额 {money(f.managed_amount_cents)}；全周期已调配及未决占用 {money(f.already_reallocated_cents)}。</p>
    <p>收入范围{f.income_complete ? '已完整读取' : '尚未完整核实'}。{f.standing_authorization_covered ? '服务端核实了对应的既有持续范围；本次仍只确认规则配置。' : '本次规则配置没有持续执行权限。'}</p>
    <ul>{f.accounts.map((a) => <li key={a.id}>{account(a.id)}：余额 {money(a.balance_cents)}</li>)}{f.goals.map((g) => <li key={g.id}>{goal(g.id)}：归属 {money(g.owned_cents)}，其中现金 {money(g.cash_owned_cents)}；{g.month_period} 已投入 {money(g.month_contributed_cents)}。</li>)}</ul>
    <details><summary>已核实收入（{f.income.length} 笔）与账单（{f.bills.length} 项）</summary>{f.income.map((income, index) => <p key={income.id}>收入 {index + 1}：{timestamp(income.occurred_at)}，原来源可用 {money(income.available_cents)}。</p>)}{f.bills.map((bill) => <p key={bill.id}>{account(bill.account_id)}，到期 {bill.due_date}：总额 {money(bill.total_cents)}，已付 {money(bill.paid_cents)}。</p>)}</details>
    <details><summary>真实支出历史（{f.history.length} 笔）与覆盖范围</summary>{f.coverage.map((coverage) => <p key={coverage.account_id}>{account(coverage.account_id)}：{coverage.dates.length} 个已覆盖自然日，{coverage.dates[0] ?? '尚无日期'} 至 {coverage.dates.at(-1) ?? '尚无日期'}。</p>)}{!f.history.length && <p>本次完整范围没有可用于估计的支出历史；需要历史依据的模板可能正常拒绝。</p>}{f.history.slice(0, 100).map((row, index) => <p key={`${row.date}-${index}`}>{row.date}，{row.category || '尚未分类'}，{money(row.amount_cents)}；{row.category_confirmed ? '类别已确认' : '类别未确认'}，{row.one_off ? '一次性支出' : '非一次性标记'}。</p>)}{f.history.length > 100 && <p>本预览使用全部 {f.history.length} 笔核实历史，页面列出前100笔。</p>}</details>
    <details><summary>支付原件（{f.settlements.length} 期）与资产范围</summary>{f.settlements.map((row, index) => <p key={`${row.period}-${index}`}>{row.period}：真实已付 {money(row.paid_cents)}{row.final_total_cents ? `，核实总额 ${money(row.final_total_cents)}` : '，尚无独立最终总额'}。</p>)}{!f.products.length && <p>当前没有可核实的产品目录；不会生成虚假产品候选。</p>}{f.products.map((product, index) => <p key={product.id}>已核实产品 {index + 1}：{assetLabels[product.asset_class as keyof typeof assetLabels] ?? '已登记资产类别'}，起点 {money(product.minimum_cents)}，锁定 {product.lock_days} 天，赎回等待 {product.redemption_delay_days} 天，风险等级 {product.risk_level}。</p>)}{f.positions.map((position, index) => <p key={position.id}>真实持仓 {index + 1}：本金 {money(position.principal_cents)}，可收回 {money(position.recoverable_cents)}，费用 {money(position.fee_cents)}，本金损失 {money(position.loss_cents)}。</p>)}</details>
  </details>;
}

export default function TemplatePolicies({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId();
  const api = useMemo(() => new TemplateClient(client), [client]);
  const scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [name, setName] = useState<TemplateName>('EmergencyBufferPolicy');
  const [configuration, setConfiguration] = useState<TemplateConfiguration>(() => defaultTemplate('EmergencyBufferPolicy', dashboard));
  const [zone, setZone] = useState<'UTC' | 'Asia/Shanghai'>('Asia/Shanghai');
  const [goalID, setGoalID] = useState(dashboard.goals[0]?.id ?? '');
  const [editing, setEditing] = useState<TemplatePolicyView | null>(null), [history, setHistory] = useState<TemplatePolicyView | null>(null);
  const [knownVersions, setKnownVersions] = useState<TemplatePolicyView[]>([]);
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [consent, setConsent] = useState(false), [targetConsent, setTargetConsent] = useState(false);
  const [pending, setPending] = useState<PendingTemplateConfirmation | null>(null), [storageFault, setStorageFault] = useState(''), [storageRead, setStorageRead] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [stateChoice, setStateChoice] = useState<{ view: TemplatePolicyView; action: keyof typeof actionText } | null>(null), [stateConsent, setStateConsent] = useState(false);
  const query = useQuery({ queryKey: ['native-template-policies', scope], queryFn: ({ signal }) => api.list(signal), retry: false, refetchOnWindowFocus: false });
  const views = query.data ?? [];
  const blocked = busy || !!pending || !!storageFault || !storageRead;
  function invalidatePreview() { setPreview(null); setConsent(false); setTargetConsent(false); setMessage(''); }
  useEffect(() => {
    setStorageRead(false); setStorageFault(''); setPending(null);
    try { setPending(loadTemplateConfirmation(client)); } catch (cause) { setStorageFault(userError(cause)); }
    setStorageRead(true);
  }, [client, scope]);
  useEffect(() => { invalidatePreview(); }, [dashboard.as_of]);
  function remember(view: TemplatePolicyView) { setKnownVersions((prior) => prior.some((item) => item.version.version_id === view.version.version_id) ? prior : [...prior, view]); }
  async function refreshAll() { await onChanged(); await query.refetch({ throwOnError: true }); }
  async function createPreview(form: HTMLFormElement) {
    if (blocked || !form.reportValidity()) return;
    invalidatePreview(); setError(''); setBusy(true);
    try {
      const parsed = parseConfiguration(name, configuration);
      if (name === 'LongTermGoalPolicy' && !dashboard.goals.some((goal) => goal.id === goalID)) throw new Error('请先选择一个当前本人目标');
      const value = await api.preview({ template_name: name, configuration: parsed, policy_id: editing?.version.policy_id, goal_id: name === 'LongTermGoalPolicy' ? goalID : undefined, timezone: zone });
      if (value.compiled.template_name !== name || value.goal_id !== (name === 'LongTermGoalPolicy' ? goalID : undefined) || editing && value.policy_id !== editing.version.policy_id) throw new Error('本次返回的预览不属于所选规则，请重新核对');
      setPreview(value);
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (blocked || !preview || !consent || preview.target_mutation && !targetConsent) return;
    setBusy(true); setError(''); setMessage('');
    let saved = false;
    try {
      const existing = loadTemplateConfirmation(client);
      if (existing) { setPending(existing); invalidatePreview(); throw new Error('还有一份原规则确认需要先核对'); }
      const session = client.session;
      if (!session || preview.scope.user_id !== session.user_id) throw new Error('请重新登录当前主体');
      const original: PendingTemplateConfirmation = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), policy_id: preview.policy_id, version_id: preview.version_id, request: { preview_id: preview.id, reviewed_bundle_hash: preview.reviewed_bundle_hash } };
      // Durable locator precedes every await. Consume the reviewed candidate
      // immediately so a dropped confirmation cannot produce another key.
      saveTemplateConfirmation(client, original); saved = true; setPending(original); invalidatePreview();
      await api.confirm(original);
      await finishOriginal(original);
    } catch (cause) { setError(`${userError(cause)} ${saved ? '已保存的原确认仍会保留，请核对上次确认。' : '尚未开始新的确认提交，请先核对本机原记录。'}`); } finally { setBusy(false); }
  }
  async function finishOriginal(original: PendingTemplateConfirmation) {
    const value = await api.version(original.version_id);
    if (value.version.policy_id !== original.policy_id || value.reviewed_bundle_hash !== original.request.reviewed_bundle_hash) throw new Error('原确认回读与本次审阅不一致');
    remember(value); await refreshAll();
    clearTemplateConfirmation(client); setPending(null); setEditing(null); setConsent(false); setTargetConsent(false);
    setMessage(`已独立读取“${nameOf(value)}”第${value.version.version}版原确认。规则配置已保存，资金执行仍需独立授权。`);
  }
  async function recover() {
    if (busy || storageFault) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const original = loadTemplateConfirmation(client); if (!original) throw new Error('没有可核对的原规则确认');
      setPending(original);
      try { await api.version(original.version_id); }
      catch (cause) { if (!(cause instanceof ApiError) || cause.status !== 404) throw cause; await api.confirm(original); }
      await finishOriginal(original);
    } catch (cause) { setError(`${userError(cause)} 请保留原确认资料，继续沿原请求核对。`); } finally { setBusy(false); }
  }
  async function readVersion(view: TemplatePolicyView, forEdit = false) {
    if (blocked) return;
    setBusy(true); setError(''); invalidatePreview();
    try {
      const value = forEdit ? await api.policy(view.version.policy_id) : await api.version(view.version.version_id);
      remember(value);
      if (forEdit) { setEditing(value); setName(value.version.compiled.template_name); setConfiguration(structuredClone(value.version.compiled.configuration)); setZone(value.timezone); setGoalID(value.goal_id ?? ''); setHistory(null); }
      else setHistory(value);
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function changeState() {
    if (blocked || !stateChoice || !stateConsent) return;
    setBusy(true); setError(''); invalidatePreview();
    try { const value = await api.state(stateChoice.view, stateChoice.action); remember(value); await refreshAll(); setStateChoice(null); setStateConsent(false); setMessage(`已独立重新读取规则状态：${stateText[value.effective_state] ?? '状态尚待核对'}。原债务和在途操作继续保留。`); }
    catch (cause) { setError(`${userError(cause)} 请重新读取规则状态，原债务与在途操作继续保留。`); } finally { setBusy(false); }
  }
  return <section className="zy-card zy-template-section" aria-label="十二类规则配置">
    <div className="zy-section-heading"><h2>我的规则</h2><button className="zy-link" disabled={busy} onClick={() => { invalidatePreview(); void query.refetch(); }}>重新读取当前规则</button></div>
    <p>选择结构化模板，先核对真实事实、保护影响与完整规则，再由本人确认配置。每次资金执行仍需匹配独立授权。</p>
    {query.isPending && <p role="status">正在读取当前规则与版本…</p>}{query.isError && <p role="alert" className="zy-message zy-error">{userError(query.error)} 尚未获得完整当前规则。</p>}
    {query.data && !views.length && <p>当前尚未确认这十二类规则。可以从下方明确配置第一项。</p>}
    {views.map((view) => <article className="zy-template-state" key={view.version.policy_id}><h3>{nameOf(view)} · 第{view.version.version}版</h3><p>{templateLabels[view.version.compiled.template_name]} · {stateText[view.effective_state] ?? '状态尚待核实'}，确认于 {timestamp(view.version.confirmed_at)}。</p><div className="zy-actions">
      <button className="zy-link" disabled={blocked} onClick={() => { void readVersion(view); }}>独立读取此原版本</button><button className="zy-link" disabled={blocked || view.effective_state === 'REVOKED'} onClick={() => { void readVersion(view, true); }}>修改并预览新版本</button>
      {(view.effective_state === 'ACTIVE' || view.effective_state === 'CONFIRMED') && <button className="zy-link" disabled={blocked} onClick={() => { setStateChoice({ view, action: 'PAUSE' }); setStateConsent(false); }}>暂停此规则</button>}
      {view.effective_state === 'SUSPENDED' && <button className="zy-link" disabled={blocked} onClick={() => { setStateChoice({ view, action: 'RESUME' }); setStateConsent(false); }}>恢复此规则</button>}
      {view.effective_state !== 'REVOKED' && <button className="zy-link" disabled={blocked} onClick={() => { setStateChoice({ view, action: 'REVOKE' }); setStateConsent(false); }}>撤销此规则</button>}</div></article>)}
    {stateChoice && <div className="zy-template-review"><h3>{actionText[stateChoice.action]}“{nameOf(stateChoice.view)}”</h3><p>仅改变第{stateChoice.view.version.version}版规则的状态。已采用旧债、目标归属与所有未决原操作仍需保留和核对。</p><label className="zy-check"><input type="checkbox" checked={stateConsent} disabled={blocked} onChange={(event) => setStateConsent(event.target.checked)} />我确认{actionText[stateChoice.action]}这项规则。</label><button className="zy-secondary" disabled={blocked || !stateConsent} onClick={() => { void changeState(); }}>确认{actionText[stateChoice.action]}规则</button><button className="zy-link" disabled={busy} onClick={() => { setStateChoice(null); setStateConsent(false); }}>取消状态修改</button></div>}
    {!!knownVersions.length && <details className="zy-template-history"><summary>本次已读取的原版本</summary>{knownVersions.map((view) => <button className="zy-link" disabled={blocked} key={view.version.version_id} onClick={() => { void readVersion(view); }}>读取{nameOf(view)}第{view.version.version}版原件</button>)}</details>}
    {history && <div className="zy-template-review"><h3>{nameOf(history)} · 第{history.version.version}版原件</h3><p>{history.version.current ? '当前版本' : '历史版本'}，{stateText[history.effective_state] ?? '状态待核对'}；原确认时间 {timestamp(history.version.confirmed_at)}。</p><TemplateEditor key={history.version.version_id} configuration={history.version.compiled.configuration} onChange={() => {}} dashboard={dashboard} policies={views} disabled /></div>}
    {storageFault && <p className="zy-message zy-error" role="alert">{storageFault} 当前已停止新的规则确认，请保留浏览器原资料。</p>}
    {pending && <div className="zy-template-review" aria-label="原规则确认恢复"><h3>核对上次规则确认</h3><p>上次提交结果尚待独立回读。继续使用同一份原确认；不会自动建立另一份请求。</p><button className="zy-primary" disabled={busy || !!storageFault} onClick={() => { void recover(); }}>{busy ? '正在核对原确认…' : '继续核对上次规则确认'}</button></div>}
    <form aria-label="结构化规则表单" onSubmit={(event) => { event.preventDefault(); void createPreview(event.currentTarget); }}>
      <h3>{editing ? `修改“${nameOf(editing)}”的下一版本` : '配置一项新规则'}</h3>
      <label className="zy-field" htmlFor={`${formID}-template`}>规则模板<select id={`${formID}-template`} aria-label="规则模板" value={name} disabled={blocked || !!editing} onChange={(event) => { const next = event.target.value as TemplateName; setName(next); setConfiguration(defaultTemplate(next, dashboard)); invalidatePreview(); }}>{templateNames.map((value) => <option key={value} value={value}>{templateLabels[value]}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-zone`}>规则自然时区<select id={`${formID}-zone`} aria-label="规则自然时区" value={zone} disabled={blocked || !!editing} onChange={(event) => { setZone(event.target.value as typeof zone); invalidatePreview(); }}><option value="Asia/Shanghai">北京时间</option><option value="UTC">协调世界时</option></select></label>
      {name === 'LongTermGoalPolicy' && <label className="zy-field" htmlFor={`${formID}-goal`}>所绑定的本人目标<select id={`${formID}-goal`} aria-label="所绑定的本人目标" value={goalID} required disabled={blocked || !!editing} onChange={(event) => { setGoalID(event.target.value); invalidatePreview(); }}>{dashboard.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select></label>}
      <TemplateEditor key={editing?.version.version_id ?? name} configuration={configuration} onChange={(value) => { setConfiguration(value); invalidatePreview(); }} dashboard={dashboard} policies={views} disabled={blocked} />
      {name === 'GoalAllocationPolicy' && <p>按确定性优先级仅分配新的未归属安全资金，不能挪用既有目标归属。</p>}{name === 'CrossGoalReallocationPolicy' && <p>释放方和接收方权限相互独立；规则配置不能代替双方对具体金额的明确确认。</p>}{name === 'SeasonalReservePolicy' && <p>节庆模板只形成基于真实历史的建议，每次调整仍需本人确认。</p>}{name === 'InterventionPolicy' && <p>安全事件始终需要确认；普通重复事件按边界去重，动作集合未变化时保持安静。</p>}
      <button className="zy-secondary" disabled={blocked || query.isError || query.isPending}>生成本次完整预览</button>{editing && <button type="button" className="zy-link" disabled={blocked} onClick={() => { setEditing(null); setConfiguration(defaultTemplate(name, dashboard)); invalidatePreview(); }}>取消修改，配置新规则</button>}
    </form>
    {preview && <div className="zy-template-review" aria-label="完整规则确认预览"><h3>请核对本次完整预览</h3><p>读取于 {timestamp(preview.created_at)}；预览到 {timestamp(preview.expires_at)} 有效。修改任何输入后须重新生成预览。</p><p>{impactText[preview.preview.state] ?? '确定性规则影响已计算，具体条件请核对下列金额与期限。'} 本次影响金额 {money(preview.preview.amount_cents)}，最低要求缺口 {money(preview.preview.minimum_shortfall_cents)}，最低保证缺口 {money(preview.preview.guarantee_shortfall_cents)}。</p>
      <details><summary>规范化后的完整规则</summary><TemplateEditor key={preview.id} configuration={preview.compiled.configuration} onChange={() => {}} dashboard={dashboard} policies={views} disabled /></details>
      <PreviewFacts preview={preview} dashboard={dashboard} />
      {!!preview.preview.commitments.length && <ul>{preview.preview.commitments.map((item, index) => { const meaning = commitmentMeaning(item.kind); return <li key={item.id}>{meaning.label} {index + 1}：{meaning.timing} {item.due_date}{item.end_date ? ` 至 ${item.end_date}` : ''}，保留 {money(item.amount_cents)}{item.payee_id ? `，收款对象 ${item.payee_id}` : ''}。尚未发生资金支付。</li>; })}</ul>}
      {preview.compiled.configuration.type === 'long_term_goal' && <p>原规则每月配置：最低 {money(preview.compiled.configuration.monthly_contribution.min_cents)}、本人期望 {money(preview.compiled.configuration.monthly_contribution.target_cents)}、最高 {money(preview.compiled.configuration.monthly_contribution.max_cents)}。</p>}
      {!!preview.preview.goal_demands.length && <ul>{preview.preview.goal_demands.map((demand) => <li key={demand.goal_id}>{dashboard.goals.find((goal) => goal.id === demand.goal_id)?.name ?? '当前目标'}：当前剩余最低要求 {money(demand.minimum_cents)}、本次确定性计算需求 {money(demand.desired_cents)}、当前剩余上限 {money(demand.maximum_cents)}，仅使用 {timestamp(demand.earliest_income_at)} 之后合格的原收入。本次计算需求结合期限、剩余目标和保证条件，不表示改写本人的每月期望，也未产生资金支付。</li>)}</ul>}
      {!!preview.preview.candidates.length && <p>本次形成 {preview.preview.candidates.length} 个确定性候选范围，仍须在对应执行入口单独确认；此页没有提交资金操作。</p>}
      {!!preview.preview.questions.length && <p>此规则还包含 {preview.preview.questions.length} 项需要本人明确的条件；未明确条件不会自动补成授权。</p>}
      <p>全年保护后的当前余量 {money(preview.forecast.current_safe_cents)}，最大缺口 {money(preview.forecast.maximum_deficit_cents)}。</p><ForecastCurve result={preview.forecast} />
      {preview.target_mutation && <div className="zy-message"><p>此长期规则将同步修改“{dashboard.goals.find((goal) => goal.id === preview.target_mutation!.goal_id)?.name ?? '所选目标'}”的目标总额：{money(preview.target_mutation.before_cents)} → {money(preview.target_mutation.after_cents)}。</p><label className="zy-check"><input type="checkbox" checked={targetConsent} disabled={busy} onChange={(event) => setTargetConsent(event.target.checked)} />我明确确认以上目标金额从原值修改为新值。</label></div>}
      <label className="zy-check"><input type="checkbox" checked={consent} disabled={busy} onChange={(event) => setConsent(event.target.checked)} />本人已核对完整规则、真实事实和年度影响，确认仅保存这份规则配置。</label>
      <button className="zy-primary" disabled={blocked || !consent || !!preview.target_mutation && !targetConsent} onClick={() => { void confirm(); }}>确认这份完整规则配置</button>
    </div>}
    {busy && <p role="status">正在核对真实规则原件…</p>}{message && <p className="zy-message" role="status">{message}</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
