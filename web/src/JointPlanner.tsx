import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, stateText } from './api';
import type { Client, Dashboard } from './api';
import { money } from './money';
import { businessState, timestamp, userError } from './display';
import { nextPoll } from './recovery';
import { bindJointGroup, JointClient, jointTerminal, loadJointLocators, saveJointLocator } from './joint-api';
import type { JointGroup, JointLocator, JointPlan } from './joint-api';
import QuestionPanel from './QuestionPanel';
import BridgePlanner from './BridgePlanner';
import './template-pages.css';

const total = (values: string[]) => values.reduce((sum, value) => sum + BigInt(value), 0n).toString();
function groupSummary(group: JointGroup): string {
  if (group.operations.every((op) => op.state === 'SETTLED')) return '整组已全部结清';
  if (jointTerminal(group)) return '整组已结束，已结清与拒绝／取消分别保留';
  if (group.operations.some((op) => op.state === 'UNKNOWN' || op.state === 'RECONCILING')) return '原结果仍待核实，后继保持等待';
  if (group.operations.some((op) => op.state === 'SETTLED')) return '已部分结清，其余原步骤继续核对';
  return '整组已受理，尚未全部结清';
}

export default function JointPlanner({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId();
  const api = useMemo(() => new JointClient(client), [client]);
  const scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [zone, setZone] = useState<'UTC' | 'Asia/Shanghai'>('Asia/Shanghai');
  const [candidate, setCandidate] = useState<JointPlan | null>(null), [consent, setConsent] = useState(false);
  const [bridgePending, setBridgePending] = useState(false), [reviewRevision, setReviewRevision] = useState(0);
  const [locators, setLocators] = useState<JointLocator[]>([]), [selectedKey, setSelectedKey] = useState(''), [storageReady, setStorageReady] = useState(false), [storageFault, setStorageFault] = useState('');
  const [originalPlan, setOriginalPlan] = useState<JointPlan | null>(null), [group, setGroup] = useState<JointGroup | null>(null);
  const [busy, setBusy] = useState(false), [checking, setChecking] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [notFound, setNotFound] = useState(false), [replayConsent, setReplayConsent] = useState(false), [batch, setBatch] = useState(0);
  const submitting = useRef(false), priorGroup = useRef<JointGroup | null>(null), changed = useRef(onChanged);
  changed.current = onChanged;
  const selected = locators.find((item) => item.key === selectedKey) ?? null;
  const unresolved = !!selected && (!group || !jointTerminal(group));
  const blocked = busy || !!storageFault || !storageReady || unresolved || bridgePending;
  const nameOfGoal = (id: string) => dashboard.goals.find((goal) => goal.id === id)?.name ?? '原计划目标';
  const nameOfAccount = (id: string) => dashboard.accounts.find((account) => account.id === id)?.name ?? '原计划账户';
  function clearCandidate() { setCandidate(null); setConsent(false); }
  useEffect(() => {
    setStorageReady(false); setStorageFault(''); setSelectedKey(''); setLocators([]); setGroup(null); setOriginalPlan(null); clearCandidate();
    try { const originals = loadJointLocators(client); setLocators(originals); setSelectedKey(originals.at(-1)?.key ?? ''); }
    catch (cause) { setStorageFault(userError(cause)); }
    setStorageReady(true);
  }, [client, scope]);
  useEffect(() => { clearCandidate(); }, [dashboard.as_of]);
  useEffect(() => {
    setGroup(null); priorGroup.current = null; setOriginalPlan(null); setReadError(''); setNotFound(false); setReplayConsent(false);
    if (!selected) return;
    const original = selected, controller = new AbortController(); let cancelled = false; let timer: ReturnType<typeof setTimeout> | undefined; let tries = 0; let frozen: JointPlan | null = null; let lastSignature = '';
    async function poll() {
      if (cancelled) return;
      setChecking(true); let again = true;
      try {
        if (!frozen) { frozen = await api.plan(original.command.plan_id, controller.signal); if (cancelled) return; if (frozen.reviewed_bundle_hash !== original.command.reviewed_bundle_hash) throw new Error('独立回读的原计划与本次原审阅不一致'); setOriginalPlan(frozen); }
        const result = await api.byKey(original.key, controller.signal); if (cancelled) return;
        const value = bindJointGroup(original, frozen, result, priorGroup.current);
        const signature = value.operations.map((op) => `${op.version}:${op.state}:${op.processing_health}`).join('|');
        tries = signature === lastSignature ? tries + 1 : 0; lastSignature = signature;
        priorGroup.current = value; setGroup(value); setReadError(''); setNotFound(false);
        again = !jointTerminal(value);
        if (!again) await changed.current();
      } catch (cause) {
        if (cancelled) return;
        tries++;
        if (cause instanceof ApiError && cause.status === 404) { setNotFound(true); setReadError('暂未查到这份原键的联合结果。未查到不代表没有效果，仍保留原请求继续核对。'); }
        else { setReadError(userError(cause)); if (cause instanceof ApiError && [401, 403].includes(cause.status)) again = false; }
      } finally {
        if (!cancelled) { setChecking(false); if (again) timer = setTimeout(() => { void poll(); }, nextPoll(tries)); }
      }
    }
    void poll();
    return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, selected?.key, batch]);

  async function preview() {
    if (blocked) return;
    setBusy(true); setError(''); clearCandidate();
    try { setCandidate(await api.preview(dashboard, zone)); } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function submit() {
    if (blocked || !candidate || !consent || submitting.current) return;
    if (candidate.result.status !== 'OPTIMAL' || !candidate.result.children.length || candidate.result.children.some((child) => child.source_account_id !== child.destination_account_id)) return;
    submitting.current = true; setBusy(true); setError('');
    try {
      if (Date.now() >= Date.parse(candidate.expires_at)) { clearCandidate(); throw new Error('本次联合预览已过期，请重新读取当前事实生成预览'); }
      const session = client.session; if (!session) throw new Error('请先重新登录当前主体');
      const original: JointLocator = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), command: { plan_id: candidate.id, reviewed_bundle_hash: candidate.reviewed_bundle_hash } };
      const originals = saveJointLocator(client, original);
      // Save the exact original before await, and consume the candidate before
      // a response can be lost. No stale card or checked consent can make a key.
      setLocators(originals); setSelectedKey(original.key); clearCandidate();
      const accepted = await api.accept(original);
      bindJointGroup(original, candidate, accepted);
      setBatch((value) => value + 1);
    } catch (cause) { setError(`${userError(cause)} 若原请求已保存，将继续沿原键核对；受理或断连均不表示已经到账。`); }
    finally { submitting.current = false; setBusy(false); }
  }
  async function replayOriginal() {
    if (busy || checking || !selected || !originalPlan || !replayConsent || submitting.current || storageFault) return;
    submitting.current = true; setBusy(true); setError(''); setReplayConsent(false);
    try { const originals = loadJointLocators(client), original = originals.find((item) => item.key === selected.key); if (!original) throw new Error('原联合请求资料缺失，已停止提交'); bindJointGroup(original, originalPlan, await api.accept(original)); setBatch((value) => value + 1); }
    catch (cause) { setError(`${userError(cause)} 原键与原载荷仍保留，不会自动换键。`); }
    finally { submitting.current = false; setBusy(false); }
  }

  return <section className="zy-card zy-template-section" aria-label="联合目标计划与原操作">
    <h2>联合安排多个目标</h2><p>同一笔真实收入只使用一次。按完整未来保护、目标优先级和原收入资格形成整组计划，逐个原子操作结清；预测收入不会成为当前额度。</p>
    {storageFault && <p className="zy-message zy-error" role="alert">{storageFault} 请保留浏览器原资料。</p>}
    {!!locators.length && <label className="zy-field" htmlFor={`${formID}-original`}>已保存的原联合请求<select id={`${formID}-original`} aria-label="已保存的原联合请求" value={selectedKey} disabled={busy} onChange={(event) => { setSelectedKey(event.target.value); clearCandidate(); }}>{locators.map((item, index) => <option key={item.key} value={item.key}>第 {index + 1} 组原联合请求</option>)}</select></label>}
    {selected && <div className="zy-template-review" aria-label="原联合组恢复"><h3>核对这份原联合组</h3><p>原请求和完整审阅已经保存在当前环境与本人名下。重新登录或重开页面会读取同一原键。</p><button className="zy-secondary" disabled={busy || checking} onClick={() => setBatch((value) => value + 1)}>立即核对原联合组</button>{checking && <p role="status">正在独立读取原计划与原键结果…</p>}{readError && <p className="zy-message zy-error" role="alert">{readError}</p>}
      {notFound && originalPlan && !group && <><label className="zy-check"><input type="checkbox" checked={replayConsent} disabled={busy || checking} onChange={(event) => setReplayConsent(event.target.checked)} />我确认仅沿同一原键和完整原载荷继续这份原请求。</label><button className="zy-secondary" disabled={busy || checking || !replayConsent} onClick={() => { void replayOriginal(); }}>沿原键继续提交原请求</button></>}
      {originalPlan && <p>原计划读取时点 {timestamp(originalPlan.created_at)}；本次原审阅包含 {originalPlan.result.children.length} 个依次执行步骤。</p>}
      {group && <><h3>{groupSummary(group)}</h3><p>已结清 {group.operations.filter((op) => op.state === 'SETTLED').length}／{group.operations.length} 步；已结清金额 {money(total(group.operations.filter((op) => op.state === 'SETTLED').map((op) => op.amount_cents)))}。其余步骤不能显示为已到账。</p><ol>{group.operations.map((op, index) => { const child = originalPlan?.result.children[op.child_number - 1], priorUnsettled = group.operations.slice(0, index).some((item) => item.state !== 'SETTLED'); return <li key={op.id}><h4>第 {op.child_number} 步 · {child ? nameOfGoal(child.goal_id) : '原目标'} · {money(op.amount_cents)}</h4><p>{stateText[op.state]}；处理情况：{businessState(op.processing_health)}。{op.bank_observed_status ? `银行观察：${businessState(op.bank_observed_status)}。` : ''}</p>{op.state === 'QUEUED' && priorUnsettled && <p>前序原步骤尚未结清，本步骤继续等待。</p>}{op.state === 'UNKNOWN' || op.state === 'RECONCILING' ? <p>结果仍待核实，保留原预留和原键，不越过本步骤继续执行。</p> : null}{op.state === 'SETTLED' && <p>原银行回执已记录，最后核对于 {timestamp(op.updated_at)}。</p>}</li>; })}</ol></>}
    </div>}
    <label className="zy-field" htmlFor={`${formID}-zone`}>联合规划自然时区<select id={`${formID}-zone`} aria-label="联合规划自然时区" value={zone} disabled={blocked} onChange={(event) => { setZone(event.target.value as typeof zone); clearCandidate(); }}><option value="Asia/Shanghai">北京时间</option><option value="UTC">协调世界时</option></select></label>
    {unresolved && <p>这份原联合组还有未核实或未结清步骤。先沿原键核对，不能用另一份候选替换它。</p>}
    <button className="zy-secondary" disabled={blocked || dashboard.goals.length < 2} onClick={() => { void preview(); }}>{busy ? '正在核对…' : '按当前真实事实生成联合预览'}</button>
    {dashboard.goals.length < 2 && <p>先建立至少两个本人目标，并逐项确认相应长期规则。</p>}
    {candidate && <div className="zy-template-review" aria-label="联合计划完整预览"><h3>核对这份联合计划</h3><p>读取于 {timestamp(candidate.created_at)}，原预览到 {timestamp(candidate.expires_at)} 有效。</p>
      {candidate.result.status === 'UNKNOWN' ? <p className="zy-message zy-error">完整范围或计算预算不足，尚不能证明可行最优。本次没有可提交金额。</p> : candidate.result.status === 'INFEASIBLE' ? <p className="zy-message zy-error">当前真实资金与完整保护条件无法同时满足。先核对规则与原债，不会自动放宽保护或替你修复。</p> : <>
        <p>本次新增目标归属合计 {money(total(candidate.result.allocations.map((allocation) => allocation.amount_cents)))}；原来源可用约束与所有保护时点已由确定性服务核对。</p>
        <ul>{candidate.result.allocations.map((allocation) => <li key={allocation.goal_id}>{nameOfGoal(allocation.goal_id)}：新增 {money(allocation.amount_cents)}，投入后归属 {money(allocation.projected_owned_cents)}，当前最低缺口 {money(allocation.minimum_shortfall_cents)}。{allocation.delay_censored ? `尚未证明完成日，当前可证明的延期下界为${allocation.delay_lower_bound_days}天。` : allocation.completion_date ? `本次可完成，完成日${allocation.completion_date}。` : '目标已完成。'}</li>)}</ul>
        <details><summary>核对原收入及其当前位置</summary>{candidate.origins.map((origin, index) => { const positions = candidate.locations.filter((item) => item.origin_id === origin.id); return <div key={origin.id}><p>原收入 {index + 1}：{timestamp(origin.occurred_at)} 实际发生，原金额 {money(origin.amount_cents)}。</p>{positions.map((position) => <p key={position.fragment_id}>{nameOfAccount(position.account_id)}：可用 {money(position.available_cents)}、未决预留 {money(position.reserved_cents)}、已归属 {money(position.assigned_cents)}、已消费 {money(position.spent_cents)}。</p>)}</div>; })}</details>
        <h4>这次整组的不可变顺序</h4>{!candidate.result.children.length && <p>当前没有新增可执行步骤，无需提交。</p>}<ol>{candidate.result.children.map((child) => <li key={child.number}>第 {child.number} 步：{nameOfAccount(child.source_account_id)} → {nameOfGoal(child.goal_id)}（{nameOfAccount(child.destination_account_id)}），{money(child.amount_cents)}，使用 {child.uses.length} 项合格的原来源。</li>)}</ol>
        {candidate.result.children.some((child) => child.source_account_id !== child.destination_account_id) ? <p className="zy-message">该计划含跨账户到款衔接，请在下方另行核对每笔转账及真实到款后的完整后继，再明确确认跨账户整组。</p> : <><label className="zy-check"><input type="checkbox" checked={consent} disabled={blocked} onChange={(event) => setConsent(event.target.checked)} />本人已核对完整目标金额、原收入用途与逐步顺序，明确确认提交这一整组计划。</label><button className="zy-primary" disabled={blocked || !consent || !candidate.result.children.length} onClick={() => { void submit(); }}>本人确认并提交整组联合计划</button><p className="zy-muted">返回受理仅表示已建立原操作；每一步必须有真实结清结果才能显示到账。UNKNOWN 会阻断后继。</p></>}
      </>}
    </div>}
    <QuestionPanel client={client} dashboard={dashboard} plan={candidate} onReviewChanged={() => { setConsent(false); setReviewRevision((value) => value + 1); }} onNeedsReevaluation={clearCandidate} />
    <BridgePlanner client={client} dashboard={dashboard} plan={candidate} blocked={busy || !!storageFault || !storageReady || unresolved} reviewRevision={reviewRevision} onReviewChanged={() => setConsent(false)} onSubmitted={clearCandidate} onPendingChange={setBridgePending} onChanged={onChanged} />
    {error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
