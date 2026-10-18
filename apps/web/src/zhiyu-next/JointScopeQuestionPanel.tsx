import { useEffect, useRef, useState } from 'react';
import type { NextState } from './api';
import type { PolicyRecord } from './policies';
import { businessText, money, userError } from './display';
import { completeScope, completeScopeRejection, lookupScope, makeScopePending, postScope, readScope, readScopeLocator, restoreScopePending, retainScope, scopeLocator, type ScopePending, type ScopeView } from './joint-scope-questions';

type Work = <T>(work: () => Promise<T>) => Promise<T>;
type Props = { environment: NextState; policies: PolicyRecord[]; signed: boolean; blocked: boolean; runRead: Work; runWrite: Work; onPendingChange?: (pending: boolean) => void };
export default function JointScopeQuestionPanel(props: Props) {
  const { environment } = props;
  return <EpochScopeQuestionPanel key={`${environment.environment_id}:${environment.dashboard.user_id}:${environment.epoch_id}`} {...props} />;
}
function EpochScopeQuestionPanel({ environment, policies, signed, blocked, runRead, runWrite, onPendingChange }: Props) {
  const [first, setFirst] = useState(''), [second, setSecond] = useState('');
  const [view, setView] = useState<ScopeView | null>(null), [pending, setPending] = useState<ScopePending | null>(null);
  const [working, setWorking] = useState(false), [error, setError] = useState('');
  const loaded = useRef(''), alive = useRef(true), identity = `${environment.environment_id}:${environment.epoch_id}`, activeIdentity = useRef(identity); activeIdentity.current = identity;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const isActive = () => alive.current && activeIdentity.current === identity;
  const available = policies.filter((p) => p.source_kind === 'FULL_POLICY' && p.template_name === 'GoalAllocationPolicy' && p.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status));
  const name = (id: string) => businessText(available.find((p) => p.policy_id === id)?.name ?? '原已确认联合规则');
  async function resolve(original: ScopePending) {
    const receipt = await runRead(() => lookupScope(original, environment));
    if (!isActive()) return;
    if (!receipt) { setError('原命令尚未定位，保留同一请求；不会重新生成替代选择。'); return; }
    if ('record_kind' in receipt) { completeScopeRejection(original, receipt, environment); setPending(null); onPendingChange?.(false); setView(null); setError(userError(new Error(receipt.message))); const locator = readScopeLocator(environment); if (locator) { const current = await runRead(() => readScope(locator, environment)); if (isActive()) setView(current); } return; }
    completeScope(original, receipt, environment); setPending(null); onPendingChange?.(false);
    const current = await runRead(() => readScope(scopeLocator(receipt, original), environment));
    if (isActive()) setView(current);
  }
  useEffect(() => {
    if (loaded.current === identity || blocked) return; loaded.current = identity;
    const active = () => alive.current && activeIdentity.current === identity;
    void (async () => {
      setWorking(true); setError('');
      try {
        const original = restoreScopePending(environment); if (active()) { setPending(original); onPendingChange?.(!!original); }
        if (original) {
          const receipt = await runRead(() => lookupScope(original, environment));
          if (!active()) return;
          if (receipt && 'record_kind' in receipt) { completeScopeRejection(original, receipt, environment); if (active()) { setPending(null); onPendingChange?.(false); setView(null); setError(userError(new Error(receipt.message))); } const locator = readScopeLocator(environment); if (locator) { const current = await runRead(() => readScope(locator, environment)); if (active()) setView(current); } }
          else if (receipt) { completeScope(original, receipt, environment); const current = await runRead(() => readScope(scopeLocator(receipt, original), environment)); if (active()) { setPending(null); onPendingChange?.(false); setView(current); } }
        } else { const locator = readScopeLocator(environment); if (locator) { const current = await runRead(() => readScope(locator, environment)); if (active()) setView(current); } }
      } catch (cause) { if (active()) setError(userError(cause)); } finally { if (active()) setWorking(false); }
    })();
  }, [identity, environment, blocked, runRead, onPendingChange]);
  async function queryOriginal(retry = false) {
    if (!pending || blocked || working || retry && !signed) return;
    setWorking(true); setError('');
    try { if (retry) await runWrite(() => postScope(pending, environment)); if (isActive()) await resolve(pending); }
    catch (cause) { if (isActive()) setError(userError(cause)); } finally { if (isActive()) setWorking(false); }
  }
  async function command(kind?: 'answers' | 'refresh' | 'close', selected?: string) {
    if (!signed || blocked || working || pending) return; setWorking(true); setError('');
    try {
      let current = view ?? undefined;
      let alternatives = [first, second].map((id) => available.find((p) => p.policy_id === id)).flatMap((p) => p ? [{ full_policy_id: p.policy_id, expected_full_policy_version_id: p.current_version_id }] : []);
      if (kind) {
        const locator = readScopeLocator(environment); if (!view || !locator) return;
        current = await runRead(() => readScope(locator, environment)); if (!isActive()) return; setView(current);
        if (current.receipt.receipt_hash !== view.receipt.receipt_hash) throw new Error('原观察出现新版本，请先核对当前结果。');
        alternatives = locator.alternatives;
      } else if (view && !['CLOSED', 'ARCHIVED'].includes(view.effective_state)) return;
      const original = await makeScopePending(environment, alternatives, kind ? current : undefined, kind, selected);
      if (!isActive()) return;
      const existing = await runRead(() => lookupScope(original, environment));
      if (!isActive()) return;
      if (existing) throw new Error('原命令已存在，请先恢复原定位。');
      retainScope(original, environment); setPending(original); onPendingChange?.(true);
      await runWrite(() => postScope(original, environment)); if (isActive()) await resolve(original);
    } catch (cause) { if (isActive()) setError(userError(cause)); } finally { if (isActive()) setWorking(false); }
  }
  const active = view && !['CLOSED', 'ARCHIVED'].includes(view.effective_state), current = view?.evaluation;
  const asking = view?.effective_state === 'AWAITING_INPUT' && current?.question;
  const reviewed = view?.effective_state === 'READY_FOR_REVIEW' && current?.status === 'STABLE' ? current.worlds.find((world) => world.policy_id === current.selected_policy_id) ?? current.worlds[0] : undefined;
  return <section className="zy-card" aria-label="联合规则分歧一次一问">
    <h3>比较两条已确认联合规则</h3><p>由你明确指定两条备选规则。只有实际安排结果存在分歧时，才询问一个必要选择；这次回答不确认付款。</p>
    {error && <p role="alert" className="zy-message zy-error">{error}</p>}{working && <p role="status">正在核对原观察及当前来源…</p>}
    {pending && <><p role="status">原命令尚未核实，刷新后仍保留同一请求。</p><button className="zy-secondary" disabled={blocked || working} onClick={() => void queryOriginal()}>核对原选择命令</button><button className="zy-secondary" disabled={blocked || working || !signed} onClick={() => void queryOriginal(true)}>按原请求续接</button></>}
    {!pending && !active && <><label>第一条备选规则<select value={first} disabled={blocked || working} onChange={(e) => setFirst(e.target.value)}><option value="">请选择已确认规则</option>{available.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label><label>第二条备选规则<select value={second} disabled={blocked || working} onChange={(e) => setSecond(e.target.value)}><option value="">请选择另一条已确认规则</option>{available.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label><button className="zy-secondary" disabled={!signed || blocked || working || !first || !second || first === second} onClick={() => void command()}>核对这两条规则</button></>}
    {view?.effective_state === 'STALE_RECOMPUTATION_REQUIRED' && <p>来源、规则版本或有效期已变化，旧回答已失效；请重新核对原观察。</p>}
    {view?.effective_state === 'UNKNOWN' && <p>至少一条候选的完整来源仍待核实，当前不通过偏好选择猜测结果。</p>}
    {view?.effective_state === 'READY_FOR_REVIEW' && <p>{current?.selected_policy_id ? '本次选择已按当前来源核实，可审阅方案。' : '两条规则产生相同安排，不需要追加偏好问题；此核对没有替你选择执行规则。'}资金执行仍需另行审阅和确认。</p>}
    {reviewed?.native.plan && <><h4>{current?.selected_policy_id ? '本次已核选择的规划' : '两规则共同的安排结果'}</h4><ul>{reviewed.native.plan.allocation.goals.map((goal) => <li key={goal.goal_id}>{businessText(environment.goals.find((g) => g.id === goal.goal_id)?.name ?? '原目标')}：{money(goal.amount_cents)}</li>)}</ul></>}
    {asking && <><h4>{asking.wording}</h4>{current.worlds.map((world) => <section key={world.policy_id}><h5>{name(world.policy_id)}</h5><ul>{world.native.plan?.allocation.goals.map((goal) => <li key={goal.goal_id}>{businessText(environment.goals.find((g) => g.id === goal.goal_id)?.name ?? '原目标')}：{money(goal.amount_cents)}</li>)}</ul><button className="zy-primary" disabled={!signed || blocked || working} onClick={() => void command('answers', world.policy_id)}>采用{name(world.policy_id)}</button></section>)}</>}
    {active && !pending && <div className="zy-buttons"><button className="zy-secondary" disabled={!signed || blocked || working || view.receipt.revision >= 16} onClick={() => void command('refresh')}>重新核对原观察</button><button className="zy-link" disabled={!signed || blocked || working || view.receipt.revision > 16} onClick={() => void command('close')}>关闭原观察</button></div>}
    <p className="zy-muted">其它资产、期限或自然语言分歧：开发中。</p>
  </section>;
}
