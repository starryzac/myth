import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, stateText } from './api';
import type { Client, Dashboard } from './api';
import type { JointPlan } from './joint-api';
import { bindBridgeGroup, BridgeClient, bridgeTerminal, loadBridgeLocators, saveBridgeLocator } from './bridge-api';
import type { BridgeGroup, BridgeLocator, BridgePreview, BridgeStep, BridgeStepState } from './bridge-api';
import { money } from './money';
import { timestamp, userError } from './display';
import { nextPoll } from './recovery';

const stateLabel = (state: BridgeStepState): string => state === 'AWAITING_ARRIVAL' ? '等待前序实际到款，尚未建立资金操作' : state === 'NOT_ACTIVATED_STOPPED' ? '前序已停止，本后继未激活' : state === 'NOT_ACTIVATED_EXPIRED' ? '原确认已到期，本后继未激活' : stateText[state];
const summary = (group: BridgeGroup): string => group.state === 'SETTLED' ? '跨账户整组已全部结清' : group.state === 'PARTIAL_EXPIRED' ? '部分原步骤已结清，其余未激活步骤已到期' : group.state === 'STOPPED' ? '跨账户整组已停止，逐步原结果分别保留' : group.steps.some((item) => ['UNKNOWN', 'RECONCILING'].includes(item.state)) ? '跨账户原结果待核实，整组仍在处理中' : '跨账户整组已受理，尚未全部结清';

export default function BridgePlanner({ client, dashboard, plan, blocked, reviewRevision, onReviewChanged, onSubmitted, onPendingChange, onChanged }: { client: Client; dashboard: Dashboard; plan: JointPlan | null; blocked: boolean; reviewRevision: number; onReviewChanged: () => void; onSubmitted: () => void; onPendingChange: (pending: boolean) => void; onChanged: () => Promise<void> }) {
  const formID = useId();
  const api = useMemo(() => new BridgeClient(client), [client]), scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [preview, setPreview] = useState<BridgePreview | null>(null), [consent, setConsent] = useState(false), [replayConsent, setReplayConsent] = useState(false);
  const [records, setRecords] = useState<BridgeLocator[]>([]), [selectedKey, setSelectedKey] = useState(''), [ready, setReady] = useState(false), [storageFault, setStorageFault] = useState('');
  const [group, setGroup] = useState<BridgeGroup | null>(null), [frontierTerminal, setFrontierTerminal] = useState(false), [independent, setIndependent] = useState(false), [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false), [checking, setChecking] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [batch, setBatch] = useState(0);
  const submitting = useRef(false), prior = useRef<BridgeGroup | null>(null), callbacks = useRef({ onReviewChanged, onSubmitted, onPendingChange, onChanged });
  const currentReview = useRef({ id: plan?.id, hash: plan?.reviewed_bundle_hash, revision: reviewRevision });
  currentReview.current = { id: plan?.id, hash: plan?.reviewed_bundle_hash, revision: reviewRevision };
  callbacks.current = { onReviewChanged, onSubmitted, onPendingChange, onChanged };
  const selected = records.find((item) => item.key === selectedKey), frontierKey = records.at(-1)?.key;
  const unresolved = records.length > 0 && !frontierTerminal, localBlocked = busy || !ready || !!storageFault || unresolved;
  const nameGoal = (id: string) => dashboard.goals.find((goal) => goal.id === id)?.name ?? '原计划目标';
  const nameAccount = (id: string) => dashboard.accounts.find((account) => account.id === id)?.name ?? '原计划账户';
  function clearPreview() { setPreview(null); setConsent(false); }
  useEffect(() => {
    setReady(false); setStorageFault(''); setRecords([]); setSelectedKey(''); setFrontierTerminal(false); clearPreview();
    try { const saved = loadBridgeLocators(client); setRecords(saved); setSelectedKey(saved.at(-1)?.key ?? ''); }
    catch (cause) { setStorageFault(userError(cause)); }
    setReady(true);
  }, [client, scope]);
  useEffect(() => { clearPreview(); }, [plan?.id, reviewRevision]);
  useEffect(() => { callbacks.current.onPendingChange(!ready || !!storageFault || unresolved || busy); }, [ready, storageFault, unresolved, busy]);
  useEffect(() => {
    setGroup(null); prior.current = null; setIndependent(false); setNotFound(false); setReadError(''); setReplayConsent(false);
    if (!selected) return;
    const original = selected, controller = new AbortController(); let cancelled = false, verified = false, tries = 0, signature = ''; let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      if (cancelled) return;
      setChecking(true); let again = true;
      try {
        if (!verified) { const value = await api.original(original.command.preview_id, controller.signal); if (cancelled) return; if (JSON.stringify(value) !== JSON.stringify(original.preview)) throw new Error('到款衔接原预览与本机保存的完整审阅不一致'); verified = true; setIndependent(true); }
        const keyed = await api.byKey(original, controller.signal); if (cancelled) return;
        const value = bindBridgeGroup(await api.group(original, controller.signal), original, prior.current ?? keyed); if (cancelled) return;
        const current = value.steps.map((item) => `${item.state}:${item.receipt_id ?? ''}`).join('|'); tries = signature === current ? tries + 1 : 0; signature = current;
        prior.current = value; setGroup(value); setReadError(''); setNotFound(false); again = !bridgeTerminal(value);
        if (original.key === frontierKey) setFrontierTerminal(!again);
        if (!again) await callbacks.current.onChanged();
      } catch (cause) {
        if (cancelled) return;
        tries++;
        if (cause instanceof ApiError && cause.status === 404) { setNotFound(verified); setReadError('原组暂未查到，不能据此判断没有效果。保留原键和完整原审阅继续核对。'); }
        else { setReadError(userError(cause)); if (cause instanceof ApiError && [401, 403].includes(cause.status)) again = false; }
      } finally { if (!cancelled) { setChecking(false); if (again) timer = setTimeout(() => { void poll(); }, nextPoll(tries)); } }
    }
    void poll(); return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, selected?.key, frontierKey, batch]);
  async function createPreview() {
    if (blocked || localBlocked || !plan || submitting.current || plan.result.status !== 'OPTIMAL' || !plan.result.children.some((child) => child.source_account_id !== child.destination_account_id)) return;
    setBusy(true); setError(''); clearPreview(); callbacks.current.onReviewChanged();
    const originalReview = currentReview.current;
    try { const value = await api.preview(plan); if (JSON.stringify(originalReview) !== JSON.stringify(currentReview.current)) throw new Error('审阅期间计划或本人偏好已变化，请重新展开当前跨账户审阅'); setPreview(value); } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function submit() {
    if (blocked || localBlocked || !plan || !preview || !consent || submitting.current) return;
    submitting.current = true; setBusy(true); setError('');
    try {
      const session = client.session; if (!session) throw new Error('请重新登录当前主体');
      if (preview.plan_id !== plan.id || preview.plan_hash !== plan.reviewed_bundle_hash || Date.now() >= Date.parse(preview.expires_at)) { clearPreview(); throw new Error('跨账户原预览已过期或已变更，请重新核对完整原事实'); }
      const original: BridgeLocator = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), command: { preview_id: preview.id, reviewed_hash: preview.review_hash }, preview };
      const saved = saveBridgeLocator(client, original); setRecords(saved); setSelectedKey(original.key); setFrontierTerminal(false); clearPreview(); callbacks.current.onSubmitted();
      await api.accept(original); setBatch((value) => value + 1);
    } catch (cause) { setError(`${userError(cause)} 已保存的原请求会沿同一键核对；受理或断连不表示转账或目标归属已经结清。`); }
    finally { submitting.current = false; setBusy(false); }
  }
  async function replay() {
    if (busy || checking || !selected || !independent || !notFound || !replayConsent || storageFault || submitting.current) return;
    submitting.current = true; setBusy(true); setError(''); setReplayConsent(false);
    try { const original = loadBridgeLocators(client).find((item) => item.key === selected.key); if (!original) throw new Error('原到款衔接请求资料缺失'); await api.accept(original); setBatch((value) => value + 1); }
    catch (cause) { setError(`${userError(cause)} 原键与完整原载荷保留，不创建另一份金融确认。`); }
    finally { submitting.current = false; setBusy(false); }
  }
  function describe(step: BridgeStep) {
    return step.command_kind === 'BRIDGE_TRANSFER' ? `${nameAccount(step.resources.cash_account_id)}转至${nameAccount(step.resources.destination_account_id!)} ${money(step.resources.amount_cents)}，尚未增加目标归属` : `${nameAccount(step.resources.cash_account_id)}为“${nameGoal(step.goal_id)}”增加归属 ${money(step.resources.amount_cents)}`;
  }
  return <section className="zy-template-review" aria-label="跨账户到款衔接与原组恢复"><h3>跨账户到款衔接</h3><p>先真实转账结清，再用实际到达的原收入建立下一步目标归属。每一步保留独立原键、实际预留和回执；未来到款不能提前成为可用资金。</p>
    {storageFault && <p className="zy-message zy-error" role="alert">{storageFault}</p>}
    {!!records.length && <label className="zy-field" htmlFor={`${formID}-original`}>已保存的原到款衔接<select id={`${formID}-original`} aria-label="已保存的原到款衔接" value={selectedKey} disabled={busy} onChange={(event) => { setSelectedKey(event.target.value); clearPreview(); }}>{records.map((item, i) => <option value={item.key} key={item.key}>第{i + 1}组原到款衔接</option>)}</select></label>}
    {selected && <div aria-label="原跨账户整组结果"><button className="zy-secondary" disabled={busy || checking} onClick={() => setBatch((value) => value + 1)}>立即核对原到款衔接</button>{checking && <p role="status">正在独立读取原预览与原键结果…</p>}{readError && <p className="zy-message zy-error" role="alert">{readError}</p>}
      {notFound && independent && !group && <><label className="zy-check"><input type="checkbox" disabled={busy || checking} checked={replayConsent} onChange={(event) => setReplayConsent(event.target.checked)} />本人确认仅沿原到款衔接键与完整原载荷继续核对及提交。</label><button className="zy-secondary" disabled={busy || checking || !replayConsent} onClick={() => { void replay(); }}>沿原键继续原到款衔接</button></>}
      {group && <><h4>{summary(group)}</h4><p>目标归属已结清 {money(group.steps.filter((item) => item.state === 'SETTLED' && item.step.command_kind === 'BRIDGE_ALLOCATE').reduce((total, item) => total + BigInt(item.step.resources.amount_cents), 0n).toString())}。转账结清只代表账户间到款，不重复计算为目标新增归属。</p><ol>{group.steps.map((item, i) => <li key={item.step.operation_id}><p>第{item.step.number}步：{describe(item.step)}。</p><p>{stateLabel(item.state)}。</p>{['UNKNOWN', 'RECONCILING'].includes(item.state) && <p>保留原预留与原键；即使原确认已到期，仍继续核对这笔未知操作，整组不能据此结束。</p>}{item.state === 'QUEUED' && group.steps.slice(0, i).some((step) => step.state !== 'SETTLED') && <p>前序尚未结清，本步骤继续等待。</p>}{item.state === 'SETTLED' && <p>本步骤已记录原银行回执。</p>}</li>)}</ol></>}
    </div>}
    {unresolved && <p>最新原到款衔接尚未完整核实，先沿原键核对，不能用另一候选替换。</p>}
    {plan?.result.status === 'OPTIMAL' && plan.result.children.some((child) => child.source_account_id !== child.destination_account_id) && <button className="zy-secondary" disabled={blocked || localBlocked} onClick={() => { void createPreview(); }}>展开跨账户逐步完整审阅</button>}
    {preview && <div aria-label="跨账户独立整组审阅"><h4>另行核对跨账户整组经济效果</h4><p>该审阅只适用于本份原计划，至 {timestamp(preview.expires_at)} 有效；此前联合计划的勾选不会转成这里的确认。</p><ol>{preview.steps.map((step) => <li key={step.operation_id}><p>第{step.number}步：{describe(step)}。{step.initial_source ? '只预留当前真实来源。' : '等待前序实际结清到款后才建立新资金操作。'}</p><p>使用{step.resources.income_uses.length}项原收入用途；费用与损失均为 {money('0')}。</p></li>)}</ol><label className="zy-check"><input type="checkbox" checked={consent} disabled={blocked || localBlocked} onChange={(event) => setConsent(event.target.checked)} />本人另行核对每笔转账、实际到款后的归属、金额和完整顺序，明确确认这一整组跨账户衔接。</label><button className="zy-primary" disabled={blocked || localBlocked || !consent} onClick={() => { void submit(); }}>本人确认并提交跨账户整组</button><p className="zy-muted">202 仅表示受理整组；等待到款的后继尚未受理，必须逐步核实真实结清。</p></div>}
    {busy && <p role="status">正在核对跨账户原审阅或提交…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
