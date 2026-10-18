import { useEffect, useRef, useState } from 'react';
import type { NextState } from './api';
import type { PolicyRecord } from './policies';
import { businessText, money, userError } from './display';
import {
  completeJointQuestion, jointQuestionLocator, lookupJointQuestion,
  makeJointQuestionPending, postJointQuestion, readJointQuestion,
  readJointQuestionLocator, restoreJointQuestion, retainJointQuestion,
  type JointQuestionPending, type JointQuestionView,
} from './joint-questions';

type Work = <T>(work: () => Promise<T>) => Promise<T>;
export default function JointQuestionPanel({ environment, policy, signed, blocked, runRead, runWrite, onPendingChange }: {
  environment: NextState; policy?: PolicyRecord | null; signed: boolean; blocked: boolean;
  runRead: Work; runWrite: Work; onPendingChange?: (pending: boolean) => void;
}) {
  const [view, setView] = useState<JointQuestionView | null>(null);
  const [pending, setPending] = useState<JointQuestionPending | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const loaded = useRef('');
  const alive = useRef(true);
  const identity = `${environment.environment_id}:${environment.epoch_id}:${environment.dashboard.user_id}`;
  const activeIdentity = useRef(identity); activeIdentity.current = identity;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const validPolicy = policy?.source_kind === 'FULL_POLICY' && policy.template_name === 'GoalAllocationPolicy' && policy.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(policy.effective_status);
  const policyChanged = !!view && !!policy && view.evaluation?.plan !== null && view.evaluation?.plan !== undefined && (view.evaluation.plan.original_request.full_policy_id !== policy.policy_id || view.evaluation.plan.original_request.expected_full_policy_version_id !== policy.current_version_id);

  useEffect(() => {
    if (loaded.current === identity || blocked) return;
    loaded.current = identity;
    const active = () => alive.current && activeIdentity.current === identity;
    void (async () => {
      setWorking(true); setError('');
      try {
        const original = restoreJointQuestion(environment);
        if (active()) { setPending(original); onPendingChange?.(!!original); }
        if (original) {
          const receipt = await runRead(() => lookupJointQuestion(original, environment));
          if (receipt) {
            completeJointQuestion(original, receipt, environment, original.policy_id);
            const current = await runRead(() => readJointQuestion(jointQuestionLocator(receipt, environment, original.policy_id), environment));
            if (active()) { setPending(null); onPendingChange?.(false); setView(current); }
          }
        } else {
          const locator = readJointQuestionLocator(environment);
          if (locator) { const current = await runRead(() => readJointQuestion(locator, environment)); if (active()) setView(current); }
        }
      } catch (cause) { if (active()) setError(userError(cause)); }
      finally { if (active()) setWorking(false); }
    })();
  }, [identity, environment, blocked, runRead, onPendingChange]);

  async function resolve(original: JointQuestionPending) {
    const receipt = await runRead(() => lookupJointQuestion(original, environment));
    if (!receipt) { setError('原命令尚未找到，继续保留同一请求；不会另开观察。'); return; }
    completeJointQuestion(original, receipt, environment, original.policy_id);
    setPending(null); onPendingChange?.(false);
    setView(await runRead(() => readJointQuestion(jointQuestionLocator(receipt, environment, original.policy_id), environment)));
  }
  async function queryOriginal() {
    if (!pending || working || blocked) return;
    setWorking(true); setError('');
    try { await resolve(pending); } catch (cause) { setError(userError(cause)); }
    finally { setWorking(false); }
  }
  async function retryOriginal() {
    if (!pending || !signed || working || blocked) return;
    setWorking(true); setError('');
    try { await runWrite(() => postJointQuestion(pending, environment)); await resolve(pending); }
    catch (cause) { setError(userError(cause)); }
    finally { setWorking(false); }
  }
  async function command(kind?: 'refresh' | 'close') {
    if (!signed || working || blocked || pending || !kind && (!validPolicy || !policy)) return;
    setWorking(true); setError('');
    try {
      let current = view ?? undefined;
      let sourcePolicy: { policy_id: string; current_version_id?: string } | undefined = policy ?? undefined;
      if (kind) {
        if (!view || ['CLOSED', 'ARCHIVED'].includes(view.effective_state) || view.receipt.revision > 16) return;
        const locator = readJointQuestionLocator(environment); if (!locator) throw new Error('原观察定位缺失，请保留原件。');
        sourcePolicy = { policy_id: locator.policy_id };
        current = await runRead(() => readJointQuestion(locator, environment)); setView(current);
        if (current.receipt.receipt_hash !== view.receipt.receipt_hash) throw new Error('原观察已出现新版本，请先核对当前结果。');
      } else if (view && !['CLOSED', 'ARCHIVED'].includes(view.effective_state)) return;
      if (!sourcePolicy) return;
      const original = await makeJointQuestionPending(environment, sourcePolicy, kind ? current : undefined, kind);
      // Verify that the original-command GET route exists before retaining/sending a POST.
      if (await runRead(() => lookupJointQuestion(original, environment))) throw new Error('原命令已经存在，请先恢复其定位。');
      retainJointQuestion(original, environment); setPending(original); onPendingChange?.(true);
      await runWrite(() => postJointQuestion(original, environment)); await resolve(original);
    } catch (cause) { setError(userError(cause)); }
    finally { setWorking(false); }
  }
  const active = view && !['CLOSED', 'ARCHIVED'].includes(view.effective_state);
  return <section className="zy-card" aria-label="联合目标必要偏好">
    <h3>按明确规则安排多个目标</h3>
    <p>比较所有目标当前已确认的规则。核对规划不扣款，正式安排仍需独立审阅与确认。</p>
    <p className="zy-muted">偏好存在分歧时的必要提问：开发中。</p>
    {error && <p role="alert" className="zy-message zy-error">{error}</p>}
    {working && <p role="status">正在核对原观察与当前来源…</p>}
    {pending && <><p role="status">连接中断或响应未核实，原请求仍保留。刷新后继续核对同一个原命令。</p><div className="zy-buttons"><button className="zy-secondary" disabled={blocked || working} onClick={() => void queryOriginal()}>核对原命令</button><button className="zy-secondary" disabled={!signed || blocked || working} onClick={() => void retryOriginal()}>按原请求续接</button></div></>}
    {!pending && !active && <button className="zy-secondary" disabled={!signed || blocked || working || !validPolicy} onClick={() => void command()}>核对联合规划是否需要提问</button>}
    {view && <>
      <h4>{policyChanged || view.effective_state === 'STALE_RECOMPUTATION_REQUIRED' ? '规则或来源已变化，需要重新核对' : view.effective_state === 'READY_FOR_REVIEW' ? '规则已明确，可审阅整组方案' : view.effective_state === 'UNKNOWN' ? '整组来源仍待核实' : '原观察已关闭'}</h4>
      {view.effective_state === 'READY_FOR_REVIEW' && !policyChanged && <p>当前不需要再问偏好。此结果只供审阅，不延续以前的资金确认。</p>}
      {view.evaluation?.plan && <ul>{view.evaluation.plan.allocation.goals.map((goal) => <li key={goal.goal_id}>{businessText(environment.goals.find((g) => g.id === goal.goal_id)?.name ?? '本轮已关联目标')}：本期建议 {money(goal.amount_cents)}</li>)}</ul>}
      {view.effective_state === 'UNKNOWN' && <p>尚不能证明整组效果，当前不要求选择，也不把未知当作稳定。</p>}
      {active && !pending && <div className="zy-buttons"><button className="zy-secondary" disabled={!signed || blocked || working || view.receipt.revision >= 16} onClick={() => void command('refresh')}>重新核对原观察</button><button className="zy-link" disabled={!signed || blocked || working || view.receipt.revision > 16} onClick={() => void command('close')}>关闭原观察</button></div>}
    </>}
  </section>;
}
