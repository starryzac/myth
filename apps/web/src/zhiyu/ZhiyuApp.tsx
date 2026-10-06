import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage, request } from '../api/http';
import { compilePolicy, getPolicies, getProposals } from '../api/policies';
import type { Compilation, Policy } from '../api/policies';
import { ConfigurationReview } from '../components/PolicyConfigForm';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';
import { getAllocationPreview, getPresets, getState, postAction, statusText, terminal } from './api';
import type { Action, AllocationPreview, Scenario, State } from './api';
import { beginOperation, bindAction, endAttempt, finishOperation, getOperation, makeLocator, recoverOperation, useOperation } from './operation';
import type { Kind, Locator } from './operation';
import { displayAuthorization, displayText } from './display';
import './zhiyu.css';

const money = (value: number | null) => value === null ? '待核实' : `¥${formatMoneyCents(value)}`;
const names = ['总览', '我的规则', '目标与执行', '活动记录'] as const;
const reasons: Record<string, string> = { obligations: '房租与已确认账单', living: '生活准备金', emergency: '应急保护', goal_cash: '已有目标现金', goal_minimum: '目标最低承诺' };
function Timestamp({ value }: { value: string }) { return <time dateTime={value}>{new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))}</time>; }
function Status({ value }: { value: string }) { return <span className={`zy-status zy-status-${['SUCCEEDED', 'RECONCILED', 'COMPLETED', 'SETTLED', 'ACTIVE'].includes(value) ? 'good' : ['UNKNOWN', 'SUBMITTED'].includes(value) ? 'wait' : 'plain'}`}>{statusText[value] ?? value}</span>; }
function RuleDetails({ policy }: { policy: Policy }) { return <details className="zy-details"><summary>查看规则内容与授权来源</summary>{policy.current_version && <><ConfigurationReview configuration={policy.current_version.configuration} /><p>版本 {policy.current_version.version_number} · 确认时间 {policy.current_version.confirmed_at ?? '尚未确认'}</p><p>有效期 {policy.current_version.valid_from ?? '未限制'} 至 {policy.current_version.valid_until ?? '未限制'}</p><p className="zy-code">规则 {policy.id}<br />版本 {policy.current_version.id}</p></>}</details>; }
function AllocationSummary({ value, pending, error, environment }: { value?: AllocationPreview; pending: boolean; error: string; environment: State }) {
  const allocation = value?.allocation;
  return <section className="zy-card" aria-label="单目标只读执行预览"><div className="zy-section-heading"><h2>执行前，先看预计变化</h2><span>只读预览</span></div>
    {pending && <p role="status">正在读取服务器计划与预计边界…</p>}{error && <p role="alert">{error}。此处不沿用旧预览作为当前计划。</p>}
    {!pending && !error && allocation && <><dl className="zy-facts"><div><dt>建议计划金额</dt><dd>{money(allocation.suggested_cents ?? null)}</dd></div><div><dt>安排后的预计财务边界</dt><dd>{money(allocation.candidate_boundary?.safe_idle_cents ?? null)}</dd></div><div><dt>当前财务边界</dt><dd>{money(allocation.baseline_boundary.safe_idle_cents)}</dd></div><div><dt>确定来源</dt><dd>{allocation.lot_allocations?.length ? [...new Set(allocation.lot_allocations.map((row) => environment.dashboard.account_facts.facts.accounts.find((account) => account.id === row.account_id)?.name ?? '原来源账户'))].join('、') : '暂无可用的已到账收入来源'}</dd></div></dl>
      <p className="zy-muted">{allocation.status === 'READY' ? '本计划保护房租、生活准备金和应急金。准备时服务端还会重算，金额可能变化。' : '当前计划受限；缺失的预计边界保持待核实。'}预览不预留资金，不产生执行权限。</p>
      <details className="zy-details"><summary>查看预览原因</summary>{allocation.reasons?.map((row, index) => <p key={index}>{row}</p>)}<p className="zy-code">目标 {value?.goal_id}<br />预览时间 {value?.as_of}</p></details></>}
  </section>;
}

export default function ZhiyuApp() {
  const [page, setPage] = useState(0);
  const client = useQueryClient();
  const stateQuery = useQuery({ queryKey: ['zhiyu-state'], queryFn: getState, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const presets = useQuery({ queryKey: ['zhiyu-presets'], queryFn: getPresets, retry: false, refetchOnWindowFocus: false });
  const policies = useQuery({ queryKey: ['zhiyu-policies'], queryFn: getPolicies, enabled: page === 1 || page === 2, retry: false, refetchOnWindowFocus: false });
  const proposals = useQuery({ queryKey: ['zhiyu-proposals'], queryFn: getProposals, enabled: page === 1, retry: false, refetchOnWindowFocus: false });
  const data = stateQuery.isSuccess ? stateQuery.data : undefined;
  const op = useOperation(); const writing = useWriteInFlight();
  const [text, setText] = useState('');
  const [candidate, setCandidate] = useState<Compilation | null>(null); const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [scenario, setScenario] = useState<Scenario>('SAFE'); const [actionAccepted, setActionAccepted] = useState(false);
  const [reviewState, setReviewState] = useState<{ policy: Policy; kind: 'POLICY_SUSPEND' | 'POLICY_REVOKE' } | null>(null);
  const [stateAccepted, setStateAccepted] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => { document.title = '知余 · 模拟资金助手'; }, []);
  useEffect(() => { if (data) recoverOperation(data); }, [data]);
  const blocked = !data || !op.ready || !!op.error || busy || writing || op.busy || !!op.locator;
  const activeRules = policies.isSuccess ? policies.data.items.filter((row) => ['goal_saving', 'emergency_buffer'].includes(row.policy_type)) : [];
  const goal = data?.goals[0];
  const currentAction = data?.actions.find((row) => ['UNKNOWN', 'SUBMITTED'].includes(row.status)) ?? data?.actions.find((row) => !terminal(row)) ?? data?.actions[data.actions.length - 1];
  const actionUnresolved = data?.actions.some((row) => !terminal(row)) ?? true;
  const allocation = useQuery({ queryKey: ['zhiyu-allocation', goal?.id, goal?.policy_version_id, data?.epoch_id, data?.dashboard.as_of], queryFn: () => getAllocationPreview(goal!, data!.dashboard.user_id), enabled: page === 2 && !!goal && !!data && !actionUnresolved, retry: false, refetchOnWindowFocus: false });
  const travelRule = activeRules.find((row) => row.policy_type === 'goal_saving' && row.version_authorized && ['ACTIVE', 'CONFIRMED'].includes(row.effective_status));
  const unknown = data?.actions.some((row) => ['UNKNOWN', 'SUBMITTED'].includes(row.status));
  const sourceNames = (currentAction?.effect.income_uses ?? []).map((row) => data?.dashboard.account_facts.facts.accounts.find((account) => account.id === row.account_id)?.name ?? '原来源账户');
  async function refresh(): Promise<State> {
    const [result] = await Promise.all([stateQuery.refetch(), ...(page === 1 || page === 2 ? [policies.refetch()] : []), ...(page === 1 ? [proposals.refetch()] : [])]);
    if (result.isError || !result.data) throw result.error ?? new Error('服务端状态读取失败，请继续核对原操作');
    return result.data;
  }
  async function readOriginal(locator: Locator): Promise<boolean> {
    // These are new independent reads after the original POST. Query data is
    // published for display only; it never supplies an execution authorization.
    const needsPolicies = ['POLICY_CONFIRM', 'POLICY_SUSPEND', 'POLICY_REVOKE'].includes(locator.kind);
    const [stateRead, proposalRead, policyRead] = await Promise.all([
      stateQuery.refetch(),
      locator.kind === 'POLICY_CONFIRM' ? getProposals() : Promise.resolve(null),
      needsPolicies ? getPolicies() : Promise.resolve(null),
    ]);
    if (stateRead.isError || !stateRead.data) throw stateRead.error ?? new Error('原操作的服务端状态读取失败，请继续核对。');
    const fresh = stateRead.data;
    if (fresh.environment_id !== locator.environment_id || fresh.epoch_id !== locator.epoch_id) throw new Error('环境或轮次已变化，原请求不能在当前环境重发。');
    let complete = false;
    if (locator.action_id && locator.effect_hash) {
      // The dedicated state route verifies these original actions on the
      // server. Use this one new snapshot, never the preceding POST response.
      const action = fresh.actions.find((row) => row.action_id === locator.action_id);
      if (!action || action.effect_hash !== locator.effect_hash) throw new Error('独立快照未包含同一原动作与经济后果摘要；保留原件并继续核对。');
      if (action.user_id !== fresh.dashboard.user_id) throw new Error('原动作用户与演示环境不一致');
      complete = locator.kind === 'PREPARE' || locator.kind === 'CONFIRM' && (action.status === 'AUTHORIZED' || terminal(action)) || locator.kind === 'EXECUTE' && terminal(action);
    } else if (locator.kind === 'GOAL') complete = fresh.goals.some((row) => row.policy_id === locator.body.policy_id && row.policy_version_id === locator.body.expected_version_id);
    else if (locator.kind === 'PREPARE') complete = fresh.activity.some((row) => row.scenario === locator.body.scenario && row.goal_id === locator.body.goal_id && ['BLOCKED', 'REJECTED'].includes(row.status) && row.action_id === null);
    else if (locator.kind === 'INCOME') complete = fresh.income_received;
    else if (locator.kind === 'POLICY_CONFIRM') {
      const id = locator.path.split('/')[2];
      const proposal = proposalRead?.items.find((row) => row.id === id && row.configuration_hash === locator.body.reviewed_hash);
      if (proposal?.confirmed_policy_id) complete = !!policyRead?.items.some((row) => row.id === proposal.confirmed_policy_id && !!row.current_version);
    } else if (locator.kind === 'POLICY_SUSPEND' || locator.kind === 'POLICY_REVOKE') {
      const policy = policyRead?.items.find((row) => row.id === locator.path.split('/')[2]);
      complete = !!policy && policy.current_version?.id === locator.body.expected_version_id && policy.effective_status === (locator.kind === 'POLICY_REVOKE' ? 'REVOKED' : 'SUSPENDED');
    }
    if (complete) finishOperation(locator);
    if (policyRead) client.setQueryData(['zhiyu-policies'], policyRead);
    if (proposalRead) client.setQueryData(['zhiyu-proposals'], proposalRead);
    return complete;
  }
  async function send(locator: Locator) {
    beginOperation(locator);
    try {
      if (['PREPARE', 'CONFIRM', 'EXECUTE'].includes(locator.kind)) {
        const action = await postAction(locator.path, locator.body);
        if (locator.action_id && (action.action_id !== locator.action_id || action.effect_hash !== locator.effect_hash)) throw new Error('原动作身份发生变化，保留原件并停止。');
        if (data && action.user_id !== data.dashboard.user_id) throw new Error('动作用户不一致');
        bindAction(action.action_id, action.effect_hash);
      } else await request(locator.path, 'POST', locator.body);
    } finally { endAttempt(); }
    const retained = getOperation().locator;
    if (retained) {
      const complete = await readOriginal(retained);
      setNotice(complete ? '已独立读取服务端原结果。金额与状态已更新。' : '结果仍待核实。保留同一原操作；请读取或恢复原请求。');
    }
  }
  async function perform(kind: Kind, path: string, body: Record<string, unknown>, action?: Action) {
    if (!data || blocked) return;
    setBusy(true); setError(''); setNotice(''); setActionAccepted(false);
    try { await send(makeLocator(data, kind, path, body, action?.action_id ?? null, action?.effect_hash ?? null)); }
    catch (cause) { setError(errorMessage(cause)); const original = getOperation().locator; if (original) await readOriginal(original).catch(() => undefined); else await refresh().catch(() => undefined); }
    finally { setBusy(false); }
  }
  async function recover(retry: boolean) {
    if (!op.locator || busy || op.busy || writing || op.error) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (await readOriginal(op.locator)) setNotice('原结果已核对，未创建第二笔操作。');
      else if (retry) await send(op.locator);
      else setNotice('原结果仍未解决。继续保留原请求身份；不会因等待时间变长而显示成功。');
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }
  async function compile() {
    if (blocked || !text.trim() || unknown) return;
    setBusy(true); setError(''); setNotice(''); setCandidate(null); setAccepted(false);
    try { const value = await compilePolicy(text); setCandidate(value); await proposals.refetch(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }
  async function adoptUnknownAction(action: Action) {
    if (!data || blocked) return; setBusy(true); setError('');
    try { const locator = makeLocator(data, 'EXECUTE', `/zhiyu/actions/${action.action_id}/execute`, {}, action.action_id, action.effect_hash); beginOperation(locator); endAttempt(); await readOriginal(locator); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function confirmCandidate() {
    if (!candidate?.proposal_id || !candidate.configuration_hash || !accepted) return;
    await perform('POLICY_CONFIRM', `/policy-proposals/${candidate.proposal_id}/confirm`, { accepted: true, reviewed_hash: candidate.configuration_hash });
    if (!getOperation().locator) { setCandidate(null); setAccepted(false); }
  }
  const waiting = busy || op.busy || writing;
  return <main className="zy-shell">
    <a className="zy-skip" href="#zy-content" onClick={(event) => { event.preventDefault(); content.current?.focus(); }}>跳到当前内容</a>
    <header className="zy-header"><a className="zy-brand" href="#" onClick={(event) => { event.preventDefault(); setPage(0); }} aria-label="知余首页"><span className="zy-mark">知</span><span><strong>知余</strong><small>ZHIYU · 你的资金边界助手</small></span></a><span className="zy-simulation">模拟演示</span></header>
    <nav className="zy-nav" aria-label="知余页面导航">{names.map((name, index) => <button type="button" key={name} aria-current={page === index ? 'page' : undefined} onClick={() => { setPage(index); content.current?.focus(); }}>{name}</button>)}</nav>
    <div className="zy-toolbar"><span>{data ? <>服务端快照 <Timestamp value={data.dashboard.as_of} /></> : '正在连接隔离演示环境'}</span><button className="zy-link" disabled={stateQuery.isFetching || busy} onClick={() => void refresh().catch((cause: unknown) => setError(errorMessage(cause)))}>刷新实际状态</button></div>
    <div aria-live="polite">{waiting && <p className="zy-message" role="status">正在处理，请稍候。原操作身份已保留，请勿重复提交。</p>}{stateQuery.isPending && <p className="zy-message" role="status">正在读取账户、资金边界和活动记录…</p>}{(error || stateQuery.isError) && <p className="zy-message zy-error" role="alert">{error || errorMessage(stateQuery.error)}{getOperation().locator && ' 请先核对下方原操作。'}</p>}{notice && <p className="zy-message">{notice}</p>}{op.error && <p className="zy-message zy-error" role="alert">{op.error} 请返回产生该记录的原环境地址核对。</p>}</div>
    {op.locator && <section className="zy-recovery" aria-label="待核实原操作"><div><span className="zy-kicker">保留原操作</span><h2>先核对这一笔，再安排下一笔</h2><p>刷新保留相同请求。核对和恢复都使用原身份。</p></div><div className="zy-buttons"><button className="zy-primary" disabled={waiting || !!op.error} onClick={() => void recover(false)}>读取原结果</button><button className="zy-secondary" disabled={waiting || !!op.error} onClick={() => void recover(true)}>恢复同一原请求</button></div><details className="zy-details"><summary>查看原操作标识</summary><p className="zy-code">轮次 {op.locator.epoch_id}<br />原动作 {op.locator.action_id ?? '准备响应尚未取得；固定服务端场景身份保留'}<br />请求 {op.locator.path}</p></details></section>}
    <div id="zy-content" ref={content} tabIndex={-1} className="zy-content">
      {page === 0 && <><section className="zy-hero"><span className="zy-kicker">先守住生活，再安排余钱</span><h1>余额之外，<br />看见你能安心安排的钱。</h1><p>房租、日常生活和目标各有归属。知余先说明边界，再在你确认的规则内行动。</p></section>{data && <><section className="zy-boundary"><div><span className="zy-kicker">当前财务边界</span><h2>可自主使用上限</h2><strong className="zy-amount">{money(data.dashboard.boundary.state === 'PROVEN' && data.dashboard.boundary.status !== 'INSUFFICIENT_EVIDENCE' ? data.dashboard.boundary.safe_idle_cents : null)}</strong><p>{data.dashboard.boundary.status === 'READY' ? '未到账收入不计入；实际执行还会重检规则权限和资金条件。' : '资金边界受限，请先查看限制原因。'}</p></div><div className="zy-boundary-next"><p>从一句话开始，把授权写清楚。</p><button className="zy-primary" onClick={() => setPage(1)}>设置我的规则 <span aria-hidden="true">→</span></button></div></section><section className="zy-metrics"><article><span>账户现金余额</span><strong>{money(data.dashboard.account_facts.state === 'PROVEN' ? data.dashboard.account_facts.facts.cash_balance_cents : null)}</strong><p>包含目标账户现金</p></article><article><span>已保护资金</span><strong>{money(data.dashboard.boundary.state === 'PROVEN' ? data.dashboard.boundary.current_protected_cents : null)}</strong><p>服务端实际口径，已处理重叠</p></article><article><span>在途与持有本金</span><strong>{money(data.dashboard.account_facts.state === 'PROVEN' ? data.dashboard.account_facts.facts.position_principal_cents : null)}</strong><p>待核实动作 {data.dashboard.pending_actions.total} 项</p></article></section><section className="zy-card"><div className="zy-section-heading"><h2>这些钱先被守住</h2><span>为什么有边界</span></div><div className="zy-protection">{Object.entries(data.dashboard.boundary.current_protected_cents_by_reason ?? {}).map(([key, value]) => <div key={key}><span>{reasons[key] ?? key}</span><strong>{money(value)}</strong></div>)}</div><p className="zy-muted">保护分项可能重叠，明细不能直接相加。未来工资只作为提示，到账前不扩大今天的额度。</p><details className="zy-details"><summary>查看限制与来源说明</summary>{data.dashboard.boundary.blocking_constraints.map((row, i) => <p key={i}>{row.code}</p>)}{data.dashboard.boundary.calculation_notes.map((row, i) => <p key={i}>{row}</p>)}<p>计算窗口 {data.dashboard.boundary.window_start} 至 {data.dashboard.boundary.window_end}</p><p className="zy-code">环境 {data.environment_id}<br />轮次 {data.epoch_id}</p></details></section><section className="zy-card zy-income"><div><h2>{data.income_received ? '演示收入已到账' : '等待一笔演示收入'}</h2><p>服务端固定收入 {money(presets.data?.income_cents ?? null)}，到账后重新计算边界。</p></div><button className="zy-secondary" disabled={blocked || data.income_received || actionUnresolved} onClick={() => void perform('INCOME', '/zhiyu/income', { expected_epoch_id: data.epoch_id })}>{data.income_received ? '已读取到账事实' : '注入固定收入事件'}</button></section></>}</>}
      {page === 1 && <><section className="zy-page-title"><span className="zy-kicker">规则解析演示</span><h1>我的规则</h1><p>表达意图，审阅候选，再明确确认。生成候选不会转钱或授予权限。</p></section><section className="zy-card"><h2>你希望如何安排？</h2><div className="zy-presets">{presets.data?.intents.map((row) => <button className="zy-preset" disabled={blocked || !!unknown} key={row.id} onClick={() => { setText(row.text); setCandidate(null); setAccepted(false); }}>{row.title}<small>{row.text}</small></button>)}</div><label className="zy-field">你的意图<textarea value={text} maxLength={2000} disabled={blocked || !!unknown} onChange={(event) => { setText(event.target.value); setCandidate(null); setAccepted(false); }} placeholder="例如：保留3000元应急金" /></label><button className="zy-primary" disabled={blocked || !!unknown || !text.trim()} onClick={() => void compile()}>生成待确认候选</button>{candidate && <section className="zy-candidate" aria-label="待确认规则候选"><span className="zy-kicker">待确认 · 尚无权限</span><h3>{String(candidate.configuration?.name ?? '请补全规则')}</h3>{candidate.configuration && <ConfigurationReview configuration={candidate.configuration} />}{(candidate.compilation.issues ?? []).map((row, i) => <p className="zy-muted" key={i}>{row.message}</p>)}{(candidate.compilation.assumptions ?? []).map((row, i) => <p className="zy-muted" key={i}>解析假设：{row}</p>)}{!candidate.configuration && <p>当前意图缺少必要信息或超出支持范围。请选择上方模板，补充目标、金额或期限后重新解析。</p>}<label className="zy-check"><input type="checkbox" checked={accepted} disabled={blocked || !candidate.configuration} onChange={(event) => setAccepted(event.target.checked)} />我已复核金额、目标、有效期和允许的动作，明确确认此规则。</label><div className="zy-buttons"><button className="zy-primary" disabled={blocked || !accepted || !candidate.proposal_id || !candidate.configuration_hash} onClick={() => void confirmCandidate()}>确认这条规则</button><button className="zy-link" disabled={waiting} onClick={() => { setCandidate(null); setAccepted(false); }}>取消本次确认</button></div></section>}</section><section className="zy-rule-list" aria-label="当前已确认规则">{policies.isError && <p role="alert">{errorMessage(policies.error)}</p>}{activeRules.map((row) => <article className="zy-card" key={`${row.id}:${row.current_version?.id}:${row.effective_status}`}><div className="zy-section-heading"><h2>{row.name}</h2><Status value={row.effective_status} /></div><p>{row.version_authorized ? '用户已确认此版本；执行还需满足实际资金条件。' : '当前版本不具备有效执行权限。'}</p><RuleDetails policy={row} /><div className="zy-buttons"><button className="zy-link" disabled={blocked || !!unknown || !row.current_version || !['ACTIVE', 'CONFIRMED'].includes(row.effective_status)} onClick={() => { setReviewState({ policy: row, kind: 'POLICY_SUSPEND' }); setStateAccepted(false); }}>暂停规则</button><button className="zy-link zy-danger" disabled={blocked || !!unknown || !row.current_version || !['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(row.effective_status)} onClick={() => { setReviewState({ policy: row, kind: 'POLICY_REVOKE' }); setStateAccepted(false); }}>撤销规则</button></div></article>)}</section>{reviewState && <section className="zy-card" aria-label="规则状态变更复核"><h2>确认{reviewState.kind === 'POLICY_REVOKE' ? '撤销' : '暂停'}「{reviewState.policy.name}」</h2><p>旧权限将不能用于新执行。已有资金归属保留；在途原操作继续核对。</p><label className="zy-check"><input type="checkbox" checked={stateAccepted} onChange={(event) => setStateAccepted(event.target.checked)} />我明确接受这次规则状态变更。</label><div className="zy-buttons"><button className="zy-primary" disabled={blocked || !stateAccepted} onClick={() => { const { policy, kind } = reviewState; void perform(kind, `/policies/${policy.id}/${kind === 'POLICY_REVOKE' ? 'revoke' : 'suspend'}`, { expected_version_id: policy.current_version!.id }).then(() => setReviewState(null)); }}>确认变更</button><button className="zy-link" disabled={waiting} onClick={() => setReviewState(null)}>取消</button></div></section>}<details className="zy-details"><summary>读取已有待确认候选</summary>{proposals.data?.items.filter((row) => row.status === 'PROPOSED' && ['emergency_buffer', 'goal_saving'].includes(String(row.configuration.type))).map((row) => <article key={row.id}><p>{row.source_text}</p><button className="zy-link" disabled={blocked} onClick={() => { setCandidate({ simulation: true, user_id: data!.dashboard.user_id, compilation_id: row.compilation_id ?? '', configuration: row.configuration, configuration_hash: row.configuration_hash, proposal_id: row.id, proposal_status: row.status, compilation: { compiler_version: row.compiler_version, reference_date: '', timezone: 'Asia/Shanghai', draft: row.configuration, configuration: row.configuration, issues: [], assumptions: ['原候选来自服务端保存记录。'] } }); setAccepted(false); }}>审阅此原候选</button></article>)}</details></>}
      {page === 2 && <><section className="zy-page-title"><span className="zy-kicker">单来源 · 单目标 · 实际模拟执行</span><h1>目标与执行</h1><p>准备一笔固定计划，核对金额与权限，再读取真实回执。</p></section>{data && <>{!goal ? <section className="zy-card"><h2>先建立旅行目标</h2><p>确认旅行规则后，建立零归属目标。创建目标本身不会转入资金。</p><button className="zy-primary" disabled={blocked || !travelRule?.current_version || actionUnresolved} onClick={() => void perform('GOAL', '/zhiyu/goal', { policy_id: travelRule!.id, expected_version_id: travelRule!.current_version!.id })}>创建旅行目标</button>{!travelRule && <p className="zy-muted">请先在“我的规则”确认旅行模板。</p>}</section> : <section className="zy-card"><div className="zy-section-heading"><h2>{goal.name}</h2><span>截止 {goal.deadline}</span></div><strong className="zy-amount zy-amount-small">{money(goal.allocated_cents)} <small>/ {money(goal.target_cents)}</small></strong><progress value={goal.allocated_cents} max={goal.target_cents} aria-label="旅行目标进度" /><p>本月固定储备 {money(goal.monthly_target_cents)}。当前进度来自执行后的服务端读数。</p></section>}{goal && !actionUnresolved && <AllocationSummary value={allocation.isSuccess ? allocation.data : undefined} pending={allocation.isFetching} error={allocation.isError ? errorMessage(allocation.error) : ""} environment={data} />}{goal && <section className="zy-card"><h2>选择本轮演示场景</h2><label className="zy-field">场景<select disabled={blocked || actionUnresolved} value={scenario} onChange={(event) => { setScenario(event.target.value as Scenario); setActionAccepted(false); }}><option value="SAFE">安全资金安排</option><option value="REVOKED">规则撤销后的拒绝</option><option value="RESPONSE_LOSS">已提交但响应丢失</option></select></label><p className="zy-muted">{scenario === 'REVOKED' ? '先在我的规则撤销旅行规则，再准备原场景；系统必须拒绝且不转钱。' : scenario === 'RESPONSE_LOSS' ? '服务端演示预置仅使首次提交响应丢失。使用同一个原动作核对结果。' : '金额和来源由服务器从当前事实与已确认规则确定。'}</p><button className="zy-primary" disabled={blocked || actionUnresolved || !data.income_received} onClick={() => void perform('PREPARE', '/zhiyu/actions/prepare', { expected_epoch_id: data.epoch_id, goal_id: goal.id, scenario })}>准备并查看计划</button>{!data.income_received && <p className="zy-muted">先在总览注入固定到账收入。</p>}</section>}{currentAction && <section className="zy-card zy-action" aria-label="原资金计划与结果"><div className="zy-section-heading"><h2>{terminal(currentAction) ? '实际原操作结果' : '复核这一笔计划'}</h2><Status value={currentAction.status} /></div><strong className="zy-amount zy-amount-small">{money(currentAction.effect.amount_cents)}</strong><p>{currentAction.autonomy_level === 'AUTO_EXECUTE' ? '已确认规则允许在权限内自动执行；服务端仍逐次重检。' : currentAction.autonomy_level === 'ASK_ONCE' ? '这笔安排需要你明确确认，规则确认并不代替具体资金确认。' : '当前条件不允许执行。'}</p><dl className="zy-facts"><div><dt>来源</dt><dd>{sourceNames.length ? [...new Set(sourceNames)].join('、') : '来源待核实'}</dd></div><div><dt>目标</dt><dd>{goal?.name ?? '原目标'}</dd></div><div><dt>费用 / 损失</dt><dd>{money(currentAction.effect.fee_cents)} / {money(currentAction.effect.loss_cents)}</dd></div><div><dt>安排后的预计财务边界</dt><dd>{money(currentAction.prepared_validation.projected_boundary?.safe_idle_cents ?? null)}</dd></div></dl><p>{displayText((currentAction.prepared_validation.reasons ?? []).join('；'))}</p>{['UNKNOWN', 'SUBMITTED'].includes(currentAction.status) && <p className="zy-message">已提交，结果待核实。不能创建另一笔重试；请使用上方原操作恢复。</p>}{['UNKNOWN', 'SUBMITTED'].includes(currentAction.status) && !op.locator && <button className="zy-primary" disabled={blocked} onClick={() => void adoptUnknownAction(currentAction)}>核对这个原操作</button>}{['PLANNED', 'AUTHORIZED'].includes(currentAction.status) && !op.locator && <><label className="zy-check"><input type="checkbox" disabled={blocked || !['AUTO_EXECUTE', 'ASK_ONCE'].includes(currentAction.autonomy_level)} checked={actionAccepted} onChange={(event) => setActionAccepted(event.target.checked)} />我已复核固定金额、来源、目标、费用和权限，接受此原操作。</label><button className="zy-primary" disabled={blocked || !actionAccepted || !['AUTO_EXECUTE', 'ASK_ONCE'].includes(currentAction.autonomy_level)} onClick={() => { const confirm = currentAction.autonomy_level === 'ASK_ONCE' && currentAction.status === 'PLANNED'; void perform(confirm ? 'CONFIRM' : 'EXECUTE', confirm ? `/actions/${currentAction.action_id}/confirm` : `/zhiyu/actions/${currentAction.action_id}/execute`, confirm ? { accepted: true, effect_hash: currentAction.effect_hash } : {}, currentAction); }}>{currentAction.autonomy_level === 'ASK_ONCE' && currentAction.status === 'PLANNED' ? '确认这笔固定计划' : '执行这个原操作'}</button></>}{currentAction.receipt && <p className="zy-result">实际执行 {money(currentAction.receipt.executed_cents)}，回执已保存。目标进度与边界已重新读取。</p>}{['SUCCEEDED', 'RECONCILED'].includes(currentAction.status) && <p className="zy-muted">已使用本月这笔储备额度。演示其他资金场景请通过启动命令准备独立新轮次。</p>}<details className="zy-details"><summary>查看实际标识与执行依据</summary><p className="zy-code">原动作 {currentAction.action_id}<br />经济后果摘要 {currentAction.effect_hash}<br />银行状态 {currentAction.bank_status ?? '尚未受理'}<br />银行操作 {currentAction.receipt?.bank_operation_id ?? '尚未取得'}<br />回执 {currentAction.receipt?.receipt_id ?? '尚未取得'}</p><p className="zy-code">原始校验原因 {(currentAction.prepared_validation.reasons ?? []).join('；') || '未提供原因码'}<br />原始权限等级 {currentAction.autonomy_level}</p><p>有效期 {currentAction.effect.valid_from} 至 {currentAction.effect.expires_at}</p><p>准备时财务安全闲置 {money(currentAction.prepared_validation.baseline_boundary.safe_idle_cents)}</p><p>只读预览不会预留资金或授权。实际完成后以上方总览与目标服务端读数为准。</p></details></section>}</>}</>}
      {page === 3 && <><section className="zy-page-title"><span className="zy-kicker">每次决定，都留下原因</span><h1>活动记录</h1><p>实际执行、只读预览与历史回放按服务端事件标签展示。</p></section><section className="zy-timeline">{data?.activity.length === 0 && <p className="zy-card">本轮还没有活动。先确认规则，再开始演示。</p>}{data?.activity.map((row) => <article key={row.id} className="zy-timeline-item"><div className="zy-timeline-time"><Timestamp value={row.at} /></div><div className="zy-card"><div className="zy-section-heading"><h2>{displayText(row.intent)}</h2><Status value={row.status} /></div><p>{displayText(row.decision)}</p>{row.amount_cents !== null && <strong className="zy-event-amount">{money(row.amount_cents)}</strong>}<p className="zy-muted">授权来源：{displayAuthorization(row.authorization)}</p><details className="zy-details"><summary>为什么允许或拒绝 · 查看原标识</summary><p>{displayText(row.decision)}</p><p className="zy-code">原始意图 {row.intent}<br />原始决定 {row.decision}<br />原始授权 {row.authorization}<br />原始状态 {row.status}<br />事件 {row.id}<br />原动作 {row.action_id ?? '此事件没有资金动作'}</p></details></div></article>)}</section></>}
    </div>
    <details className="zy-session"><summary>隔离演示环境详情</summary>{data && <p className="zy-code">隔离环境 {data.environment_id}<br />轮次 {data.epoch_id}</p>}</details>
    <footer className="zy-footer"><span>知余 Zhiyu · 模拟资金助手</span><span>规则解析采用离线引擎 · 所有资金动作均为模拟</span></footer>
  </main>;
}
