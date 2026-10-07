import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NextState } from './api';
import type { AssetsState } from './assets-state';
import type { AssetPerform } from './AssetsPanel';
import { QUESTION_PREFIX, type QuestionRecovery, type Mode } from './question-recovery';
import { getQuestionView, readQuestionView, type QuestionView, type QuestionUpdate } from './questions';
import { businessText, money, userError } from './display';

const names: Record<Mode, string> = { PORTFOLIO: '有限产品组合', FIXED_LADDER: '分期到期阶梯' };
export default function QuestionPanel({ environment, state, signed, blocked, perform, runRead, update, recovering = false, retryOriginal }: { environment: NextState; state?: AssetsState; signed: boolean; blocked: boolean; perform: AssetPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; update: QuestionUpdate | null; recovering?: boolean; retryOriginal?: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [policyId, setPolicyId] = useState(''); const [working, setWorking] = useState(false); const [loaded, setLoaded] = useState<QuestionView | null>(null); const [error, setError] = useState('');
  useEffect(() => { if (update || recovering) setOpen(true); }, [update, recovering]); useEffect(() => { if (update) setLoaded(null); }, [update]);
  const saved = useQuery({ queryKey: ['zhiyu-next-question-view', environment.environment_id, environment.epoch_id], queryFn: () => readQuestionView(environment), enabled: open && !blocked && !update, retry: false, staleTime: Infinity });
  const view = loaded ?? update ?? saved.data; const receipt = view?.receipt; const evaluation = view?.evaluation;
  const policies = state?.policies.filter((p) => p.source_kind === 'FULL_POLICY' && p.template_name === 'AssetAuthorizationPolicy' && p.planning_confirmation_valid && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status) && p.configuration.scope === 'goal' && environment.goals.some((g) => g.id === p.configuration.goal_id)) ?? [];
  const policy = policies.find((p) => p.policy_id === policyId) ?? (policies.length === 1 ? policies[0] : undefined);
  const currentQuestion = !!view && view.effective_state === 'PENDING_ANSWER' && receipt?.question_id && view.current_source_fingerprint === receipt.source_fingerprint && evaluation?.should_ask === true;
  async function start() {
    if (!policy || !signed || blocked || working || receipt && !['CLOSED', 'ARCHIVED'].includes(view!.effective_state)) return; setWorking(true); setError('');
    try { const body = { expected_epoch_id: environment.epoch_id, client_request_id: crypto.randomUUID(), full_policy_id: policy.policy_id, expected_full_policy_version_id: policy.current_version_id }; const recovery: QuestionRecovery = { protocol: 'zhiyu-next-question-recovery-v1', user_id: environment.dashboard.user_id, start_request: body, session_id: null, previous_receipt_hash: null }; await perform(`${QUESTION_PREFIX}/sessions`, body, undefined, undefined, undefined, undefined, undefined, undefined, recovery); }
    catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function command(kind: 'answers' | 'refresh' | 'close', mode?: Mode) {
    if (!view || !receipt || blocked || working || !signed || receipt.revision > 16 || ['CLOSED', 'ARCHIVED'].includes(view.effective_state) || kind === 'answers' && !currentQuestion) return; setWorking(true); setError('');
    try {
      const fresh = await runRead(() => getQuestionView(view.recovery, environment)); setLoaded(fresh);
      if (fresh.receipt.receipt_hash !== receipt.receipt_hash || fresh.receipt.revision !== receipt.revision) throw new Error('原问题版本已经变化，请审阅当前问题。');
      if (kind === 'answers' && (fresh.effective_state !== 'PENDING_ANSWER' || fresh.current_source_fingerprint !== receipt.source_fingerprint || fresh.receipt.question_id !== receipt.question_id)) throw new Error('当前来源已经变化，旧答案不会应用；请先重新核实原会话。');
      const body = { expected_epoch_id: environment.epoch_id, client_request_id: crypto.randomUUID(), expected_revision: receipt.revision, ...(kind === 'answers' ? { question_id: receipt.question_id, mode } : {}) };
      await perform(`${QUESTION_PREFIX}/sessions/${receipt.session_id}/${kind}`, body, undefined, undefined, undefined, undefined, undefined, undefined, { ...view.recovery, previous_receipt_hash: receipt.receipt_hash });
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  return <section aria-label="少问必要偏好"><section className="zy-card"><div className="zy-section-heading"><h3>只问会改变整组安排的偏好</h3><button className="zy-secondary" disabled={working} onClick={() => setOpen((v) => !v)}>{open ? '收起必要问题' : '查看必要问题'}</button></div><p>比较整组购买、到期与保护结果。回答偏好不扣款，也不延续此前的资金确认。</p></section>
    {open && <section className="zy-card">
      {(error || saved.isError) && <p role="alert" className="zy-message zy-error">{error || userError(saved.error)}</p>}
      {!view && <><label className="zy-field" htmlFor="zyn-question-policy">当前目标资产范围<select id="zyn-question-policy" disabled={blocked || working} value={policy?.policy_id ?? ''} onChange={(e) => setPolicyId(e.target.value)}><option value="">请选择</option>{policies.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label><p className="zy-muted">目前只接受已有真实目标与原资产权限关联的完整范围；尚未接通服务时不能推断当前问题。</p><button className="zy-secondary" disabled={!policy || !signed || blocked || working} onClick={() => void start()}>核实是否需要询问</button></>}
      {recovering && <p role="status">正在核对同一个偏好原命令；未找到原件不能当作拒绝，也不会开启另一会话。</p>}
      {receipt && view && <>
        <h4>{view.effective_state === 'STALE_RECOMPUTATION_REQUIRED' ? '来源已变化，旧问题不能继续回答' : view.effective_state === 'READY_FOR_REVIEW' ? '当前无需再问偏好' : view.effective_state === 'PENDING_ANSWER' ? '只需选择一个安排偏好' : view.effective_state === 'UNKNOWN' ? '完整效果仍待核实' : '已关闭的偏好记录'}</h4>
        {view.effective_state === 'READY_FOR_REVIEW' && <p>原生整组评估给出零问结果{evaluation?.selected_mode ? `，已记录${names[evaluation.selected_mode]}` : ''}。正式购买仍需审阅新准备原组合并确认一次。</p>}
        {view.effective_state === 'UNKNOWN' && <p>尚不能证明两种完整效果，当前不要求选择，也不把未知结果推断为稳定。</p>}
        {view.effective_state === 'STALE_RECOMPUTATION_REQUIRED' && <p>保留原会话和版本，重新核实后由服务端重排；旧偏好和旧资金同意不会继承。</p>}
        {evaluation && <ul>{evaluation.worlds.map((w) => { const partition = evaluation.partitions.find((p) => p.choice_key === w.mode); return <li key={w.mode}><strong>{names[w.mode]}</strong><p>{w.status === 'KNOWN' ? `服务端原整组规划 ${money(w.native_preview.portfolio?.total_purchase_cents)}，共 ${w.native_preview.portfolio?.batches.length ?? 0} 个批次。` : '完整来源、权限或保护尚未核实。'}</p>{partition && <p>原生最小分区：选定此偏好后剩余 {partition.residual_signature_count} 组效果。此统计不授银行权限。</p>}</li>; })}</ul>}
        {currentQuestion && <div className="zy-buttons">{(['PORTFOLIO', 'FIXED_LADDER'] as const).map((mode) => <button key={mode} className="zy-primary" disabled={!signed || blocked || working} onClick={() => void command('answers', mode)}>偏好{names[mode]}</button>)}</div>}
        {!['CLOSED', 'ARCHIVED'].includes(view.effective_state) && <div className="zy-buttons"><button className="zy-secondary" disabled={!signed || blocked || working || receipt.revision > 16} onClick={() => void command('refresh')}>重新核实原会话</button><button className="zy-link" disabled={!signed || blocked || working || receipt.revision > 16} onClick={() => void command('close')}>关闭原偏好会话</button></div>}
      </>}
      {retryOriginal && signed && recovering && <button className="zy-secondary" onClick={() => void retryOriginal()}>恢复本人会话后续接原偏好命令</button>}
    </section>}
  </section>;
}
