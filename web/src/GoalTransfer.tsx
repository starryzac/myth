import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, stateText } from './api';
import type { Client, Dashboard } from './api';
import { GoalTransferClient, goalTransferTerminal, loadGoalTransferLocators, pairedRule, parseGoalTransferOperation, saveGoalTransferLocator } from './goal-transfer-api';
import type { GoalTransferLocator, GoalTransferOperation, GoalTransferPreview, GoalTransferRequest } from './goal-transfer-api';
import { TemplateClient } from './template-api';
import type { TemplatePolicyView } from './template-api';
import { money, yuanToCents } from './money';
import { nextPoll } from './recovery';
import { timestamp, userError } from './display';

function transferError(cause: unknown): string {
  if (cause instanceof ApiError) {
    const messages: Record<string, string> = {
      GOAL_TRANSFER_TWO_USER_CONFIRMATIONS_REQUIRED: '释放与接收必须由本人分别明确确认。',
      GOAL_TRANSFER_POLICY_REQUIRED: '请先确认当前有效的目标配对调配规则。',
      GOAL_TRANSFER_NOT_AUTHORIZED: '当前双向范围、期限或金额上限不允许这笔目标转移。',
      GOAL_TRANSFER_PROTECTED_REFERENCE_REDUCED: '这笔转移会减少明确不得降低的规则保护，已停止。',
      GOAL_TRANSFER_PROTECTION_DEFICIT: '转移后的年度保护有缺口，不能提交。',
      GOAL_TRANSFER_PROTECTION_INCOMPLETE: '完整保护依据尚未读取，不能提交。',
      GOAL_TRANSFER_CAPACITY: '原记录超出本次完整读取容量，不能把截断结果当作安全。',
      GOAL_CASH_UNAVAILABLE: '来源目标当前可释放的实际现金不足。',
      GOAL_OPERATION_PENDING: '相关目标还有原操作待核实，请先沿原键核对。',
      AUTHORIZATION_EXPIRED: '这份原预览已到期；先核对已发送的原操作，未发送的需重新预览。',
    };
    if (messages[cause.code]) return messages[cause.code]!;
  }
  return userError(cause);
}

export default function GoalTransfer({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId(), api = useMemo(() => new GoalTransferClient(client), [client]), templateAPI = useMemo(() => new TemplateClient(client), [client]);
  const scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [rules, setRules] = useState<TemplatePolicyView[]>([]), [rulesReady, setRulesReady] = useState(false), [rulesError, setRulesError] = useState(''), [listVersion, setListVersion] = useState(0);
  const [ruleID, setRuleID] = useState(''), [sourceID, setSourceID] = useState(''), [targetID, setTargetID] = useState(''), [yuan, setYuan] = useState('');
  const [preview, setPreview] = useState<GoalTransferPreview | null>(null), [release, setRelease] = useState(false), [receive, setReceive] = useState(false), [replayConsent, setReplayConsent] = useState(false);
  const [records, setRecords] = useState<GoalTransferLocator[]>([]), [selectedKey, setSelectedKey] = useState(''), [storageReady, setStorageReady] = useState(false), [storageError, setStorageError] = useState('');
  const [operation, setOperation] = useState<GoalTransferOperation | null>(null), [frontierTerminal, setFrontierTerminal] = useState(false), [independent, setIndependent] = useState(false), [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false), [checking, setChecking] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [batch, setBatch] = useState(0);
  const sending = useRef(false), revision = useRef(0), prior = useRef<GoalTransferOperation | null>(null), changed = useRef(onChanged); changed.current = onChanged;
  const selected = records.find((item) => item.key === selectedKey), frontierKey = records.at(-1)?.key;
  const unresolved = records.length > 0 && !frontierTerminal, blocked = busy || !storageReady || !!storageError || unresolved;
  const rule = rules.find((item) => item.version.version_id === ruleID), config = rule?.version.compiled.configuration;
  const sourceChoices = dashboard.goals.filter((goal) => config && pairedRule(config) && config.source_goal_ids.includes(goal.id));
  const sourceGoal = dashboard.goals.find((goal) => goal.id === sourceID);
  const targetChoices = dashboard.goals.filter((goal) => config && pairedRule(config) && config.target_goal_ids.includes(goal.id) && goal.id !== sourceID && goal.account_id !== sourceGoal?.account_id);
  const nameGoal = (id: string) => dashboard.goals.find((goal) => goal.id === id)?.name ?? '原审阅目标';
  const nameAccount = (id: string) => dashboard.accounts.find((account) => account.id === id)?.name ?? '原审阅账户';
  function clearReview() { revision.current++; setPreview(null); setRelease(false); setReceive(false); }

  useEffect(() => {
    setStorageReady(false); setStorageError(''); setRecords([]); setSelectedKey(''); setFrontierTerminal(false); clearReview();
    setRuleID(''); setSourceID(''); setTargetID(''); setYuan('');
    try { const saved = loadGoalTransferLocators(client); setRecords(saved); setSelectedKey(saved.at(-1)?.key ?? ''); }
    catch (cause) { setStorageError(transferError(cause)); }
    setStorageReady(true);
  }, [client, scope]);
  useEffect(() => {
    const controller = new AbortController(); let cancelled = false;
    setRulesReady(false); setRulesError(''); clearReview();
    void templateAPI.list(controller.signal).then((items) => { if (!cancelled) { setRules(items.filter((item) => item.effective_state === 'ACTIVE' && pairedRule(item.version.compiled.configuration))); setRulesReady(true); } }).catch((cause) => { if (!cancelled) setRulesError(transferError(cause)); });
    return () => { cancelled = true; controller.abort(); };
  }, [templateAPI, listVersion, dashboard]);
  useEffect(() => {
    setOperation(null); prior.current = null; setIndependent(false); setNotFound(false); setReadError(''); setReplayConsent(false);
    if (!selected) return;
    const original = selected, controller = new AbortController(); let cancelled = false, verified = false, failures = 0, signature = ''; let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      if (cancelled) return;
      setChecking(true); let again = true;
      try {
        if (!verified) { const value = await api.original(original.command.preview_id, controller.signal); if (cancelled) return; if (JSON.stringify(value) !== JSON.stringify(original.preview)) throw new Error('独立读取的原目标转移与本机完整审阅不一致'); verified = true; setIndependent(true); }
        const keyed = await api.byKey(original, controller.signal); if (cancelled) return;
        const value = parseGoalTransferOperation(await api.operation(keyed.id, original, controller.signal), original, prior.current ?? keyed); if (cancelled) return;
        const next = `${value.version}:${value.state}:${value.receipt_id ?? ''}`; failures = signature === next ? failures + 1 : 0; signature = next;
        prior.current = value; setOperation(value); setReadError(''); setNotFound(false); again = !goalTransferTerminal(value);
        if (original.key === frontierKey) setFrontierTerminal(!again);
        if (!again) await changed.current();
      } catch (cause) {
        if (cancelled) return;
        failures++;
        if (cause instanceof ApiError && cause.status === 404) { setNotFound(verified); setReadError('原目标转移暂未查到，不能据此判断没有效果。保留原键继续核对。'); }
        else { setReadError(transferError(cause)); if (cause instanceof ApiError && [401, 403].includes(cause.status)) again = false; }
      } finally { if (!cancelled) { setChecking(false); if (again) timer = setTimeout(() => { void poll(); }, nextPoll(failures)); } }
    }
    void poll(); return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, selected?.key, frontierKey, batch]);

  async function createPreview() {
    if (blocked || !rulesReady || !rule || !config || !pairedRule(config) || !sourceID || !targetID || sending.current) return;
    clearReview(); const expected = revision.current; setBusy(true); setError('');
    try {
      const body: GoalTransferRequest = { policy_version_id: rule.version.version_id, source_goal_id: sourceID, target_goal_id: targetID, amount_cents: yuanToCents(yuan), timezone: rule.timezone };
      const value = await api.preview(body, dashboard); if (expected !== revision.current) throw new Error('当前目标或规则已经变化，请重新展开完整审阅'); setPreview(value);
    } catch (cause) { setError(transferError(cause)); } finally { setBusy(false); }
  }
  async function submit() {
    if (blocked || !preview || !release || !receive || sending.current) return;
    sending.current = true; setBusy(true); setError('');
    try {
      const session = client.session; if (!session) throw new Error('请先登录本人当前环境');
      if (Date.now() >= Date.parse(preview.expires_at)) { clearReview(); throw new Error('目标转移原预览已到期，请重新核对当前完整事实'); }
      const original: GoalTransferLocator = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), command: { preview_id: preview.id, reviewed_hash: preview.reviewed_hash, release_source: true, receive_target: true }, preview };
      const saved = saveGoalTransferLocator(client, original); setRecords(saved); setSelectedKey(original.key); setFrontierTerminal(false); clearReview();
      await api.accept(original); setBatch((value) => value + 1);
    } catch (cause) { setError(`${transferError(cause)} 已保存的原请求会沿原键核对；受理或断连都不表示资金已转移结清。`); }
    finally { sending.current = false; setBusy(false); }
  }
  async function replay() {
    if (busy || checking || !selected || !independent || !notFound || !replayConsent || !!storageError || sending.current) return;
    sending.current = true; setBusy(true); setReplayConsent(false); setError('');
    try { const original = loadGoalTransferLocators(client).find((item) => item.key === selected.key); if (!original) throw new Error('本机原目标转移资料缺失'); await api.accept(original); setBatch((value) => value + 1); }
    catch (cause) { setError(`${transferError(cause)} 完整原请求和原键继续保留。`); }
    finally { sending.current = false; setBusy(false); }
  }

  return <section className="zy-template-review" aria-label="目标间配对转移"><h3>目标间配对转移</h3><p>已有目标资金从来源目标释放，再由另一账户中的目标接收。两边需要本人分别确认；这不会产生新收入，也不增加接收目标当月的新收入贡献额度。</p>
    {storageError && <p className="zy-message zy-error" role="alert">{storageError}</p>}
    {!!records.length && <label className="zy-field" htmlFor={`${formID}-history`}>已保存的原目标转移<select id={`${formID}-history`} aria-label="已保存的原目标转移" value={selectedKey} disabled={busy} onChange={(event) => { setSelectedKey(event.target.value); clearReview(); }}>{records.map((item, i) => <option key={item.key} value={item.key}>第{i + 1}笔目标转移 · {money(item.preview.request.amount_cents)}</option>)}</select></label>}
    {selected && <div aria-label="原目标转移结果"><button className="zy-secondary" disabled={busy || checking} onClick={() => setBatch((value) => value + 1)}>立即核对原目标转移</button>{checking && <p role="status">正在独立读取原预览与原键结果…</p>}{readError && <p className="zy-message zy-error" role="alert">{readError}</p>}
      <p>原审阅：“{nameGoal(selected.preview.source.id)}”向“{nameGoal(selected.preview.target.id)}”转移 {money(selected.preview.request.amount_cents)}。</p>
      {notFound && independent && !operation && <><label className="zy-check"><input type="checkbox" disabled={busy || checking} checked={replayConsent} onChange={(event) => setReplayConsent(event.target.checked)} />本人确认仅沿原目标转移键和完整原载荷继续提交。</label><button className="zy-secondary" disabled={busy || checking || !replayConsent} onClick={() => { void replay(); }}>沿原键继续原目标转移</button></>}
      {operation && <><h4>{stateText[operation.state]}</h4>{operation.state === 'SETTLED' ? <p>来源释放与目标接收均已按原银行回执结清 {money(operation.amount_cents)}；原收入身份保留。</p> : ['REJECTED', 'CANCELLED'].includes(operation.state) ? <p>本笔没有产生已确认的目标转移效果，原资金仍归来源目标。</p> : <p>这笔目标转移尚未结清；保留原预留与原键继续核对。</p>}{['UNKNOWN', 'RECONCILING'].includes(operation.state) && <p>结果待核实。规则暂停、预览到期或页面重开都不能证明没有效果，也不会释放原预留。</p>}</>}
    </div>}
    {unresolved && <p>最新原目标转移尚未核实为终态，暂不能提交另一候选。</p>}
    <button className="zy-secondary" disabled={busy} onClick={() => { clearReview(); setListVersion((value) => value + 1); }}>重新读取配对规则</button>
    {!rulesReady && !rulesError && <p role="status">正在读取本人当前配对规则…</p>}{rulesError && <p className="zy-message zy-error" role="alert">{rulesError}</p>}
    {rulesReady && !rules.length && <p>当前没有有效的目标配对调配规则。请在规则页确认“目标间调配”，选择目标配对范围，并为两个不同账户的目标各自确认长期目标规则。</p>}
    {rulesReady && !!rules.length && <form className="zy-template-grid" onSubmit={(event) => { event.preventDefault(); void createPreview(); }}>
      <label className="zy-field" htmlFor={`${formID}-rule`}>配对调配规则<select id={`${formID}-rule`} aria-label="配对调配规则" value={ruleID} disabled={blocked} onChange={(event) => { setRuleID(event.target.value); setSourceID(''); setTargetID(''); clearReview(); }}><option value="">请选择本人已确认的规则</option>{rules.map((item, i) => <option key={item.version.version_id} value={item.version.version_id}>{item.version.compiled.configuration.name ?? `目标间调配规则 ${i + 1}`} · 第{item.version.version}版</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-source`}>释放资金的来源目标<select id={`${formID}-source`} aria-label="释放资金的来源目标" value={sourceID} disabled={blocked || !rule} onChange={(event) => { setSourceID(event.target.value); setTargetID(''); clearReview(); }}><option value="">请选择来源目标</option>{sourceChoices.map((goal) => <option key={goal.id} value={goal.id}>{goal.name} · {nameAccount(goal.account_id)}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-target`}>接收资金的目标<select id={`${formID}-target`} aria-label="接收资金的目标" value={targetID} disabled={blocked || !sourceID} onChange={(event) => { setTargetID(event.target.value); clearReview(); }}><option value="">请选择另一账户中的接收目标</option>{targetChoices.map((goal) => <option key={goal.id} value={goal.id}>{goal.name} · {nameAccount(goal.account_id)}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-amount`}>本次目标转移金额（元）<input id={`${formID}-amount`} aria-label="本次目标转移金额（元）" inputMode="decimal" value={yuan} disabled={blocked} required onChange={(event) => { setYuan(event.target.value); clearReview(); }} /></label>
      {sourceID && !targetChoices.length && <p>这份规则当前没有符合双向范围的其他账户目标，请先核对目标账户与规则范围。</p>}
      <button className="zy-secondary" type="submit" disabled={blocked || !rule || !sourceID || !targetID || !yuan}>预览双向目标转移</button>
    </form>}
    {preview && <div aria-label="双向目标转移完整审阅"><h4>分别核对释放与接收</h4><p>仅本份审阅有效至 {timestamp(preview.expires_at)}。配对规则第{preview.policy_version}版；费用、损失均为 {money('0')}。</p>
      <p>来源“{nameGoal(preview.source.id)}”（{nameAccount(preview.source.account_id)}）：现金 {money(preview.source.cash_owned_cents)} → {money((BigInt(preview.source.cash_owned_cents) - BigInt(preview.request.amount_cents)).toString())}；释放 {money(preview.request.amount_cents)}。</p>
      <p>接收“{nameGoal(preview.target.id)}”（{nameAccount(preview.target.account_id)}）：现金 {money(preview.target.cash_owned_cents)} → {money((BigInt(preview.target.cash_owned_cents) + BigInt(preview.request.amount_cents)).toString())}；接收 {money(preview.request.amount_cents)}。</p>
      <p>接收目标当月新收入贡献仍为 {money(preview.target.month_contributed_cents)}。原收入与实际本金返还继续保留原身份。</p>
      <p>规则单次上限 {money(preview.single_action_cap_cents)}，累计上限 {money(preview.total_cap_cents)}；既往已用及在途预留 {money(preview.already_used_and_held_cents)}。</p>
      <p>完整365日保护已重新核对 {preview.forecast_after.points.length} 个阶段点：今日保护余量 {money(preview.forecast_before.current_safe_cents)} → {money(preview.forecast_after.current_safe_cents)}，转移后最大缺口 {money(preview.forecast_after.maximum_deficit_cents)}。</p>
      <details><summary>展开原来源与保护变化</summary><ul>{preview.lot_uses.map((use, i) => <li key={`${use.source_kind}:${use.source_id}`}>第{i + 1}项{use.source_kind === 'INCOME' ? '原收入归属' : '实际本金返还归属'}：{money(use.amount_cents)}。</li>)}</ul>{preview.protected_policies.length ? <ul>{preview.protected_policies.map((ref, i) => <li key={ref.policy_id}>明确不得降低的第{i + 1}项规则：保护 {money(ref.protected_before_cents)} → {money(ref.protected_after_cents)}；服务端已逐点核对。</li>)}</ul> : <p>这份配对规则没有另行指定不得降低的规则；全体年度保护仍须满足。</p>}</details>
      <label className="zy-check"><input type="checkbox" checked={release} disabled={blocked} onChange={(event) => setRelease(event.target.checked)} />本人明确确认从来源目标释放本次金额，并已核对该目标的保护与期限。</label>
      <label className="zy-check"><input type="checkbox" checked={receive} disabled={blocked} onChange={(event) => setReceive(event.target.checked)} />本人另行明确确认由接收目标接收本次金额，并已核对该目标与账户。</label>
      <button className="zy-primary" disabled={blocked || !release || !receive} onClick={() => { void submit(); }}>本人双向确认并提交目标转移</button><p className="zy-muted">202 仅表示已受理，等待真实银行回执后才显示结清。</p>
    </div>}
    {busy && <p role="status">正在核对目标配对原件或提交…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
