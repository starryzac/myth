import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from './api';
import type { Client, Dashboard } from './api';
import type { JointPlan } from './joint-api';
import { money } from './money';
import { timestamp, userError } from './display';
import { TemplateClient } from './template-api';
import { templateYuan } from './template-schema';
import { loadPreferenceLocators, parsePreferenceRequest, PreferenceClient, savePreferenceLocator } from './question-api';
import type { PreferenceAnalysis, PreferenceAnswer, PreferenceChoice, PreferenceLocator, PreferenceRequest } from './question-api';

function choiceLabel(choice: PreferenceChoice): string { return choice.kind === 'money' ? `本月期望 ${money(choice.amount_cents)}` : `优先级 ${choice.priority}`; }

export default function QuestionPanel({ client, dashboard, plan, onReviewChanged, onNeedsReevaluation }: { client: Client; dashboard: Dashboard; plan: JointPlan | null; onReviewChanged: () => void; onNeedsReevaluation: () => void }) {
  const formID = useId();
  const api = useMemo(() => new PreferenceClient(client), [client]), templates = useMemo(() => new TemplateClient(client), [client]);
  const scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [mode, setMode] = useState<'money' | 'priority'>('money'), [goalID, setGoalID] = useState(dashboard.goals[0]?.id ?? '');
  const [first, setFirst] = useState(''), [second, setSecond] = useState('');
  const [records, setRecords] = useState<PreferenceLocator[]>([]), [selectedKey, setSelectedKey] = useState(''), [storageReady, setStorageReady] = useState(false), [storageFault, setStorageFault] = useState('');
  const [analysis, setAnalysis] = useState<PreferenceAnalysis | null>(null), [accepted, setAccepted] = useState<PreferenceAnswer | null>(null), [choiceKey, setChoiceKey] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [stale, setStale] = useState(false);
  const inFlight = useRef(false), invalidate = useRef(onReviewChanged), reevaluate = useRef(onNeedsReevaluation);
  invalidate.current = onReviewChanged; reevaluate.current = onNeedsReevaluation;
  const reference = plan?.references.find((ref) => ref.goal_id === goalID);
  const rule = useQuery({ queryKey: ['preference-original-goal-rule', scope, reference?.version_id], enabled: !!reference, queryFn: () => templates.version(reference!.version_id), retry: false, refetchOnWindowFocus: false });
  const monthly = rule.data?.version.compiled.configuration.type === 'long_term_goal' ? rule.data.version.compiled.configuration.monthly_contribution : null;
  const selected = records.find((item) => item.key === selectedKey);
  const question = analysis?.result.question;
  const existingAnswer = question ? records.find((item) => item.stage === 'ANSWER' && item.request.original_question_id === question.original_question_id) : undefined;
  const currentAnalysis = !!plan && !!analysis && analysis.request.plan_id === plan.id && analysis.plan_hash === plan.reviewed_bundle_hash;
  const expired = !!analysis && Date.now() >= Date.parse(analysis.expires_at);
  const blocked = busy || !storageReady || !!storageFault;

  function invalidateComparison() { setAnalysis(null); setAccepted(null); setChoiceKey(''); setStale(false); setError(''); invalidate.current(); }
  function showAnalysis(value: PreferenceAnalysis) {
    if (plan && value.request.plan_id === plan.id && value.plan_hash !== plan.reviewed_bundle_hash) throw new Error('比较结果与当前原计划审阅不一致');
    setAnalysis(value); setChoiceKey(''); setAccepted(null); setStale(false);
  }
  useEffect(() => {
    let active = true;
    setStorageReady(false); setStorageFault(''); setAnalysis(null); setAccepted(null); setChoiceKey('');
    try {
      const saved = loadPreferenceLocators(client); setRecords(saved); const latest = saved.at(-1); setSelectedKey(latest?.key ?? '');
      // Safe independent reads may resume automatically. POST recovery always
      // needs an explicit click and keeps the original key and exact payload.
      if (latest?.stage === 'ANALYSIS' && latest.analysis_id) {
        setBusy(true);
        void api.analysis(latest.analysis_id).then((value) => { if (active) showAnalysis(value); }).catch((cause) => { if (active) setError(userError(cause)); }).finally(() => { if (active) setBusy(false); });
      }
    } catch (cause) { setStorageFault(userError(cause)); }
    setStorageReady(true);
    return () => { active = false; };
  }, [client, api, scope]);
  useEffect(() => { setChoiceKey(''); setStale(false); if (plan && !plan.references.some((ref) => ref.goal_id === goalID)) setGoalID(plan.references[0]?.goal_id ?? ''); }, [plan?.id]);

  function handleError(cause: unknown) {
    if (cause instanceof ApiError && cause.code === 'ORIGINAL_QUESTION_STALE') { setStale(true); setError('原问题已过期或其真实对象、依赖、回答状态已经变化。原记录保留，请生成当前计划后重新比较。'); reevaluate.current(); }
    else setError(`${userError(cause)} 原请求如果已保存，会继续保留同一键与完整载荷。`);
  }
  async function completeAnalysis(original: Extract<PreferenceLocator, { stage: 'ANALYSIS' }>) {
    const value = original.analysis_id ? await api.analysis(original.analysis_id) : await api.analyze(original);
    if (JSON.stringify(value.request) !== JSON.stringify(original.request)) throw new Error('原比较结果与保存的声明范围不一致');
    const bound = { ...original, analysis_id: value.id }; setRecords(savePreferenceLocator(client, bound));
    const independent = await api.analysis(value.id);
    if (JSON.stringify(independent) !== JSON.stringify(value)) throw new Error('分析原件独立回读不一致');
    showAnalysis(independent);
  }
  async function compare() {
    if (blocked || !plan || !reference || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setChoiceKey(''); setAccepted(null); setAnalysis(null); invalidate.current();
    try {
      if (Date.now() >= Date.parse(plan.expires_at)) throw new Error('当前联合预览已过期，请重新读取真实事实并生成计划');
      let request: PreferenceRequest;
      if (mode === 'money') {
        if (!monthly || rule.data?.version.version_id !== reference.version_id || rule.data.version.policy_id !== reference.policy_id || rule.data.goal_id !== goalID) throw new Error('当前目标原规则尚未独立核实，不能填写范围外金额');
        const amounts = [templateYuan(first), templateYuan(second)];
        if (amounts.some((value) => BigInt(value) < BigInt(monthly.min_cents) || BigInt(value) > BigInt(monthly.max_cents))) throw new Error('本次期望金额必须在当前原规则的每月最低和最高金额内');
        request = parsePreferenceRequest({ plan_id: plan.id, mode: 'DECLARED_FINITE_CHOICES', domains: [{ goal_id: goalID, contribution_targets_cents: amounts }] });
      } else {
        if (![first, second].every((value) => /^(0|[1-9]\d{0,2})$/.test(value))) throw new Error('优先级请填写0到100的两个不同整数');
        request = parsePreferenceRequest({ plan_id: plan.id, mode: 'DECLARED_FINITE_CHOICES', domains: [{ goal_id: goalID, priorities: [Number(first), Number(second)] }] });
      }
      const session = client.session; if (!session) throw new Error('请重新登录当前主体');
      const saved = loadPreferenceLocators(client), prior = saved.find((item) => item.stage === 'ANALYSIS' && JSON.stringify(item.request) === JSON.stringify(request));
      const original: Extract<PreferenceLocator, { stage: 'ANALYSIS' }> = prior?.stage === 'ANALYSIS' ? prior : { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), stage: 'ANALYSIS', request };
      setRecords(savePreferenceLocator(client, original)); setSelectedKey(original.key);
      await completeAnalysis(original);
    } catch (cause) { handleError(cause); } finally { inFlight.current = false; setBusy(false); }
  }
  async function answer() {
    if (blocked || !analysis || !question || !choiceKey || !currentAnalysis || expired || stale || existingAnswer || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    const selectedChoice = choiceKey; setChoiceKey(''); invalidate.current();
    try {
      const session = client.session; if (!session) throw new Error('请重新登录当前主体');
      if (!question.choices.some((choice) => choice.key === selectedChoice)) throw new Error('选择不属于这条原问题');
      const original: Extract<PreferenceLocator, { stage: 'ANSWER' }> = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), stage: 'ANSWER', request: { analysis_id: analysis.id, original_question_id: question.original_question_id, question_hash: question.question_hash, choice_key: selectedChoice } };
      const saved = loadPreferenceLocators(client); if (saved.some((item) => item.stage === 'ANSWER' && item.request.original_question_id === question.original_question_id)) throw new Error('此原问题已有回答请求，请先沿原键核对');
      setRecords(savePreferenceLocator(client, original)); setSelectedKey(original.key);
      const result = await api.answer(original);
      if (result.preference.dependency_hash !== analysis.dependency_hash || result.preference.source_ref.id !== question.source_ref.id) throw new Error('回答原件不属于本次原对象与依赖');
      setAccepted(result); reevaluate.current();
    } catch (cause) { handleError(cause); } finally { inFlight.current = false; setBusy(false); }
  }
  async function recover() {
    if (blocked || !selected || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setChoiceKey('');
    try { const original = loadPreferenceLocators(client).find((item) => item.key === selected.key); if (!original) throw new Error('原偏好请求资料缺失'); if (original.stage === 'ANALYSIS') await completeAnalysis(original); else { setAccepted(await api.answer(original)); reevaluate.current(); } }
    catch (cause) { handleError(cause); } finally { inFlight.current = false; setBusy(false); }
  }

  return <section className="zy-template-review" aria-label="必要追问与有限方案比较"><h3>只比较你声明的两档方案</h3><p>这是有限范围内的偏好比较。金额、来源、目标归属、费用和结算时点均由确定性服务比较；结果相同才不追问，不能据此推断所有可能方案都稳定。</p>
    {storageFault && <p className="zy-message zy-error" role="alert">{storageFault} 请保留浏览器原资料。</p>}
    {!!records.length && <div><label className="zy-field" htmlFor={`${formID}-original`}>已保存的原比较或回答<select id={`${formID}-original`} aria-label="已保存的原比较或回答" value={selectedKey} disabled={busy} onChange={(event) => setSelectedKey(event.target.value)}>{records.map((item, index) => <option key={item.key} value={item.key}>第{index + 1}份 · {item.stage === 'ANALYSIS' ? '原有限比较' : '原偏好回答'}</option>)}</select></label><button className="zy-secondary" disabled={blocked || !selected} onClick={() => { void recover(); }}>沿原键核对这份比较或回答</button><p className="zy-muted">已知分析会独立回读；未知结果只有本次明确点击才沿同一原键和完整载荷重试，不自动生成新问题。</p></div>}
    <form onSubmit={(event) => { event.preventDefault(); if (event.currentTarget.reportValidity()) void compare(); }}>
      <label className="zy-field" htmlFor={`${formID}-goal`}>本次比较的本人目标<select id={`${formID}-goal`} aria-label="本次比较的本人目标" required value={goalID} disabled={blocked || !plan} onChange={(event) => { setGoalID(event.target.value); invalidateComparison(); }}>{(plan ? dashboard.goals.filter((goal) => plan.references.some((ref) => ref.goal_id === goal.id)) : dashboard.goals).map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-mode`}>比较哪一项偏好<select id={`${formID}-mode`} aria-label="比较哪一项偏好" value={mode} disabled={blocked || !plan} onChange={(event) => { setMode(event.target.value as typeof mode); setFirst(''); setSecond(''); invalidateComparison(); }}><option value="money">本月期望安排金额</option><option value="priority">目标优先级</option></select></label>
      {mode === 'money' && monthly && <p>原规则每月最低 {money(monthly.min_cents)}、最高 {money(monthly.max_cents)}。比较不能放宽这些已确认硬范围。</p>}
      {mode === 'money' && rule.isError && <p role="alert">{userError(rule.error)} 原规则尚未核实，金额比较暂不可用。</p>}
      <div className="zy-template-grid"><label className="zy-field">{mode === 'money' ? '第一档本月期望（元）' : '第一档优先级（0—100）'}<input required inputMode={mode === 'money' ? 'decimal' : 'numeric'} disabled={blocked || !plan} value={first} onChange={(event) => { setFirst(event.target.value); invalidateComparison(); }} /></label><label className="zy-field">{mode === 'money' ? '第二档本月期望（元）' : '第二档优先级（0—100）'}<input required inputMode={mode === 'money' ? 'decimal' : 'numeric'} disabled={blocked || !plan} value={second} onChange={(event) => { setSecond(event.target.value); invalidateComparison(); }} /></label></div>
      <button className="zy-secondary" disabled={blocked || !plan || !reference || mode === 'money' && (!monthly || rule.isFetching)}>比较这两档声明方案</button>{!plan && <p>先生成当前联合预览，再比较本次本人声明的方案。原比较与回答仍可沿原键核对。</p>}
    </form>
    {analysis && <div aria-label="有限比较原结果"><p>原事实时点 {timestamp(analysis.as_of)}，原比较到 {timestamp(analysis.expires_at)} 有效；声明范围 {analysis.result.expected_worlds} 个世界，实际比较 {analysis.result.evaluated_worlds} 个。</p>
      <ul>{analysis.request.domains.map((domain) => <li key={domain.goal_id}>原声明“{dashboard.goals.find((goal) => goal.id === domain.goal_id)?.name ?? '原目标'}”：{domain.priorities ? domain.priorities.map((value) => `优先级 ${value}`).join('／') : domain.contribution_targets_cents.map((value) => money(value)).join('／')}。</li>)}</ul>
      {!currentAnalysis || expired || stale ? <p className="zy-message">这是原比较记录，已不属于当前可回答的计划或有效期。不能把旧问题用于新的对象，需重新生成当前计划并比较。</p> : null}
      {analysis.result.status === 'STABLE' && <p className="zy-message">在你明确声明的有限范围内，完整经济效果相同，本次无需再问。它不代表确认了规则或授权付款。</p>}
      {['UNKNOWN', 'CAPACITY_EXCEEDED'].includes(analysis.result.status) && <p className="zy-message zy-error">比较范围或计算能力尚未完整覆盖，不能判断稳定，也不能拼出一条假问题。</p>}
      {analysis.result.status === 'ALL_WORLDS_BLOCKED' && <p>所有已声明方案都未通过当前硬条件。偏好回答不能解除保护或代替规则修复。</p>}
      {analysis.result.status === 'DIVERGENT_NO_SINGLE_DISCRIMINATING_QUESTION' && <p>方案的经济效果存在差异，但没有一条原问题能有效区分。请调整声明范围后重新比较，不临时编造价值判断。</p>}
      {question && <fieldset disabled={blocked || !currentAnalysis || expired || stale || !!existingAnswer || !!accepted}><legend>{question.prompt}</legend><p>仅针对“{dashboard.goals.find((goal) => goal.id === question.source_ref.id)?.name ?? '原问题所绑定目标'}”，本次最多问这一条已有的区分问题。</p>{question.choices.map((choice) => <label className="zy-check" key={choice.key}><input type="radio" name="original-preference-choice" value={choice.key} checked={choiceKey === choice.key} onChange={() => setChoiceKey(choice.key)} />{choiceLabel(choice)}</label>)}<button type="button" className="zy-secondary" disabled={!choiceKey || blocked || !currentAnalysis || expired || stale || !!existingAnswer || !!accepted} onClick={() => { void answer(); }}>提交这项原问题的偏好回答</button></fieldset>}
      {existingAnswer && !accepted && <p>这条原问题已经保存过一份回答请求。请在上方选择原偏好回答并沿原键核对，不改选项后创建另一份回答。</p>}
    </div>}
    {accepted && <p className="zy-message" role="status">已记录本人偏好：{choiceLabel(accepted.preference.choice)}。必须重新评估；尚未修改任何规则，也没有产生资金确认或执行权限。请重新读取当前事实，在规则表单明确修改后再生成计划。</p>}
    {busy && <p role="status">正在核对原有限比较或回答…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
