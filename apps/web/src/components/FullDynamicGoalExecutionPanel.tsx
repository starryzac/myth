import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { getAccounts } from '../api/goals';
import { getFullGoalModel, getOriginalFullGoalResponse, parseFullGoalConfiguration } from '../api/full-goals';
import type { FullGoalModel, GoalModelBinding } from '../api/full-goals';
import { dynamicCheck, dynamicRequestHash, getOriginalDynamicExecutionResponse, isFreshDynamicExecutionRead, lookupDynamicGoal, parseDynamicPrepare, postDynamicGoal, previewDynamicGoal } from '../api/full-dynamic-goal-execution';
import type { DynamicIntent, DynamicLookup, DynamicPreview } from '../api/full-dynamic-goal-execution';
import { acceptDynamicGoalRead, acceptDynamicGoalWorkspaceRead, beginDynamicGoalOperation, dynamicGoalWorkspaceIntent, endDynamicGoalAttempt, isDynamicGoalWorkspaceUnresolved, prepareDynamicGoalIntent, recoverDynamicGoalOperation, useDynamicGoalOperation } from '../features/full-dynamic-goal-operation';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';

type Props = { goal: GoalModelBinding & { name?: string }; userId: string; epochId: string; mutationBlocked?: boolean };
function Raw({ value, label }: { value: object; label: string }) { return <details><summary>{label}</summary><pre>{getOriginalDynamicExecutionResponse(value) ?? JSON.stringify(value, null, 2)}</pre></details>; }
const money = (value: number | null) => value === null ? 'UNKNOWN · 尚未证明' : `¥${formatMoneyCents(value)}`;
export default function FullDynamicGoalExecutionPanel({ goal, userId, epochId, mutationBlocked = false }: Props) {
  const operation = useDynamicGoalOperation(); const writing = useWriteInFlight();
  const [model, setModel] = useState<FullGoalModel | null>(null); const [preview, setPreview] = useState<DynamicPreview | null>(null); const [original, setOriginal] = useState<DynamicLookup | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [accepted, setAccepted] = useState(false); const [resumeAccepted, setResumeAccepted] = useState(false); const [pendingRead, setPendingRead] = useState(false);
  const scope = `${userId}:${epochId}:${goal.id}:${goal.policy_version_id}`; const current = useRef(scope); current.current = scope;
  const sourceScope = useRef<string | null>(null); const displayed = sourceScope.current === scope;
  useEffect(() => { void recoverDynamicGoalOperation(); }, []);
  const locked = mutationBlocked || writing || busy || operation.busy || operation.recovering || !!operation.storage_error || !!operation.pending;
  const action = original?.action; const sameScope = !!original && isFreshDynamicExecutionRead(original) && displayed && original.user_id === userId && original.original_request?.goal_id === goal.id && original.original_request.expected_policy_version_id === goal.policy_version_id && original.original_request.expected_epoch_id === epochId && !original.historical && original.epoch_state === 'OPEN';
  const unresolved = !!operation.workspace && isDynamicGoalWorkspaceUnresolved(operation.workspace);
  const monthly = displayed && model?.status === 'VERIFIED' ? parseFullGoalConfiguration(model.full_configuration).monthly_contribution : null;
  const canConfirm = sameScope && action?.autonomy_level === 'ASK_ONCE' && action.status === 'PLANNED' && original?.confirmation_status === 'ABSENT';
  const canExecute = sameScope && action && ['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'UNKNOWN'].includes(action.status) && (action.autonomy_level === 'AUTO_EXECUTE' || original?.confirmation_status === 'VERIFIED_AT_CONFIRMATION') && !action.receipt && action.autonomy_level !== 'BLOCKED';
  async function loadModel() {
    if (busy || operation.busy) return; const requested = scope; setBusy(true); setError(''); setPreview(null); setModel(null); setOriginal(null); setAccepted(false);
    try { const [accounts, value] = await Promise.all([getAccounts(), getFullGoalModel(goal)]); dynamicCheck(accounts.user_id === userId && value.epoch_id === epochId); if (current.current === requested) { sourceScope.current = requested; setModel(value); } }
    catch (cause) { if (current.current === requested) setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function previewPlan() {
    if (locked || !displayed || model?.status !== 'VERIFIED' || !model.evidence_id || !model.evidence_hash) return; const requested = scope; setBusy(true); setError(''); setPreview(null);
    try {
      const body = parseDynamicPrepare({ goal_id: goal.id, expected_policy_version_id: goal.policy_version_id, expected_model_evidence_id: model.evidence_id, expected_model_evidence_hash: model.evidence_hash, expected_epoch_id: epochId, idempotency_key: `dynamic-${crypto.randomUUID()}` });
      // Read-only preview has no economic effect. Retain its complete original request before POST.
      sessionStorage.setItem(`bounded-funds-full-dynamic-goal-preview-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`, JSON.stringify({ user_id: userId, request: body, body_json: JSON.stringify(body), request_hash: await dynamicRequestHash(body) }));
      const value = await previewDynamicGoal(body, userId); if (current.current === requested) setPreview(value);
    } catch (cause) { if (current.current === requested) setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function send(intent: DynamicIntent) {
    await beginDynamicGoalOperation(intent, mutationBlocked); setPendingRead(false); setResumeAccepted(false);
    try { await postDynamicGoal(intent); setNotice('原请求已返回，完整原件继续待核对。请独立GET，不自动执行或释放权限。'); }
    finally { endDynamicGoalAttempt(); }
  }
  async function prepare() {
    if (locked || unresolved || !displayed || preview?.proof.status !== 'VERIFIED_RANGE') return; setBusy(true); setError('');
    try { await send(await prepareDynamicGoalIntent('PREPARE', userId, preview.request)); setPreview(null); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function next(kind: 'CONFIRM' | 'EXECUTE') {
    if (locked || !accepted || !original?.original_request || !action || (kind === 'CONFIRM' ? !canConfirm : !canExecute)) return; setBusy(true); setError(''); setAccepted(false);
    try { await send(await prepareDynamicGoalIntent(kind, userId, original.original_request, action)); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function recover() {
    const intent = operation.pending; if (!intent || busy || operation.busy) return; setBusy(true); setError(''); setPendingRead(false); setResumeAccepted(false); setAccepted(false);
    try { const value = await lookupDynamicGoal(intent); const result = await acceptDynamicGoalRead(intent, value); sourceScope.current = `${intent.user_id}:${intent.prepare_request.expected_epoch_id}:${intent.prepare_request.goal_id}:${intent.prepare_request.expected_policy_version_id}`; setOriginal(result.lookup); setPendingRead(!result.complete); setNotice(result.complete ? '独立原GET已核对原请求与对应原结果；原确认和回执不是当前银行授权。' : '原结果仍未决/确认缺失，保留同一原动作、body和键。NOT_FOUND并非最终未提交证明。'); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function resume() {
    if (!operation.pending || !pendingRead || !resumeAccepted || busy || operation.busy || mutationBlocked || writing || operation.storage_error) return; setBusy(true); setError('');
    try { await send(operation.pending); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readWorkspace() {
    if (!operation.workspace || operation.pending || busy || operation.busy) return; setBusy(true); setError(''); setAccepted(false);
    try { const intent = await dynamicGoalWorkspaceIntent(); const value = await acceptDynamicGoalWorkspaceRead(intent, await lookupDynamicGoal(intent)); sourceScope.current = `${intent.user_id}:${intent.prepare_request.expected_epoch_id}:${intent.prepare_request.goal_id}:${intent.prepare_request.expected_policy_version_id}`; setOriginal(value); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <section className="card" aria-label="动态目标原动作执行"><h3>{goal.name ?? '目标'} · 原月度范围内动态储备</h3><p>这是模拟资金操作。当前已到账收入、原月度 min/target/max、目标产权与全部365日保护由服务器重算；本页不输入金额、银行事实、Proof、时钟或新授权。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="动态目标待核对请求"><h4>原 {operation.pending.kind} 待核对</h4><p>目标 {operation.pending.prepare_request.goal_id} · 原周期 {operation.pending.prepare_request.expected_epoch_id} · 原键 {operation.pending.prepare_request.idempotency_key}</p><Raw value={operation.pending} label="POST前保存的完整原body/hash与固定动作" /><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void recover()}>独立读取原动态目标结果</button>{pendingRead && <><label><input type="checkbox" checked={resumeAccepted} disabled={mutationBlocked || busy || writing || !!operation.storage_error} onChange={(event) => setResumeAccepted(event.target.checked)} />我明确恢复同一原请求，不创建第二动作或更换键</label><button type="button" disabled={!resumeAccepted || mutationBlocked || busy || writing || !!operation.storage_error} onClick={() => void resume()}>手动恢复同一动态目标原请求</button></>}<p>网络、解析、4xx和NOT_FOUND均不会清门；刷新不自动POST。自己的只读核对可越过自身待核对门。</p></section>}
    {operation.workspace && !operation.pending && <section aria-label="动态目标保留工作区"><p>保留原目标动作 {operation.workspace.action?.action_id ?? 'UNKNOWN'} · 目标 {operation.workspace.original_request?.goal_id}。存储原件不是当前授权；刷新后必须独立GET，不能自动POST。{unresolved && '固定原动作未终局，禁止用新键准备另一动作。'}</p><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void readWorkspace()}>只读刷新保留的原目标动作</button></section>}
    <button type="button" disabled={busy || operation.busy} onClick={() => void loadModel()}>只读加载当前目标模型与证据</button>
    {displayed && model && <section aria-label="动态执行当前模型"><p>原模型 {model.status} · 当前MVP版本 {model.base_policy_version_id} · 原周期 {model.epoch_id}</p>{model.status === 'MODEL_MISSING' ? <p>UNKNOWN · 当前完整目标模型缺失，不能准备动态动作。</p> : <><p>原Evidence {model.evidence_id} · sourceHash {model.evidence_hash} · 当前状态 {model.policy_effective_status}</p><p>原月 min / target / max：{money(monthly?.min_cents ?? null)} / {money(monthly?.target_cents ?? null)} / {money(monthly?.max_cents ?? null)}</p><button type="button" disabled={locked} onClick={() => void previewPlan()}>读取服务器动态执行预览</button></>}<details><summary>当前模型原JSON响应</summary><pre>{getOriginalFullGoalResponse(model) ?? JSON.stringify(model, null, 2)}</pre></details><p>各只读接口分别采集快照；提交前服务器再次核对当前版本与证据，不复用跨请求授权。</p></section>}
    {displayed && preview && <section aria-label="动态服务器预览"><h4>执行范围 {preview.proof.status}</h4><dl>{[['原剩余最低额', preview.proof.minimum_cents], ['当前动态追加上界', preview.proof.dynamic_cap_cents], ['原剩余月最大额', preview.proof.remaining_max_cents], ['原名义target差额', preview.proof.nominal_remaining_target_cents]].map(([label, value]) => <div key={String(label)}><dt>{String(label)}</dt><dd>{money(value as number | null)}</dd></div>)}</dl><p>原proofHash {preview.proof.proof_hash} · 当前输入 {preview.proof.input_hash} · 365日保护输入 {preview.proof.full_projection_input_hash ?? 'UNKNOWN'}</p>{preview.proof.reasons.map((row, index) => <p key={index}>{row}</p>)}{preview.limitations.map((row, index) => <p key={index}>{row}</p>)}<Raw value={preview} label="动态执行预览原JSON响应" /><button type="button" disabled={locked || unresolved || preview.proof.status !== 'VERIFIED_RANGE'} onClick={() => void prepare()}>明确准备原动态目标动作</button></section>}
    {original && <section aria-label="动态固定原动作"><h4>原键读取 {original.status}</h4>{action && <><p>原Action {action.action_id} · 状态 {action.status} · 自主等级 {action.autonomy_level} · 银行 {action.bank_status ?? '尚未观察/UNKNOWN'}</p><p>固定原金额 {money(action.effect.amount_cents)} · effectHash {action.effect_hash} · 有效期 {action.effect.valid_from} 至 {action.effect.expires_at}</p><ul aria-label="固定原现金和收入来源">{(action.effect.cash_uses ?? []).map((row) => <li key={row.account_id}>原现金账户 {row.account_id} · {money(row.amount_cents)}</li>)}{(action.effect.income_uses ?? []).map((row) => <li key={`${row.origin_transaction_id}:${row.account_id}`}>原收入 {row.origin_transaction_id} · 原fragment {row.fragment_id} · 所在账户 {row.account_id} · {money(row.amount_cents)}</li>)}</ul><p>原确认 {original.confirmation_status} · {original.confirmation?.evidence_id ?? '没有已验证原确认'} · 原确认不视为当前银行授权。</p><p>客户端原键 {original.idempotency_key}；银行键字段未由当前响应直接提供，不能以客户端键代替银行键</p>{action.receipt && <p>原服务回执 {action.receipt.receipt_id} · 银行操作 {action.receipt.bank_operation_id} · 执行 {money(action.receipt.executed_cents)}。不是独立经济效果实验验真。</p>}{['UNKNOWN', 'SUBMITTED'].includes(action.status) && <p>银行与应用可能暂不同步；只恢复原动作和原键，不把无回执当资金未变化。</p>}{original.historical && <p>SEALED历史原件保留，只可核对，不提供当前执行授权。</p>}</>}<Raw value={original} label="原请求、原Action、确认与回执完整JSON" />{sameScope && action && <fieldset disabled={locked}><label><input type="checkbox" checked={accepted} disabled={!canConfirm && !canExecute} onChange={(event) => setAccepted(event.target.checked)} />我复核固定原金额、收入来源、目标与effectHash，明确{canConfirm ? '确认' : '执行/恢复'}这个原动作</label>{canConfirm && <button type="button" disabled={!accepted} onClick={() => void next('CONFIRM')}>提交原动作明确确认</button>}{canExecute && <button type="button" disabled={!accepted} onClick={() => void next('EXECUTE')}>仅执行/恢复这个固定原动作</button>}</fieldset>}</section>}
    <p>预览不预留资金、不是许可；准备金额可能因当前事实变化而与预览不同，必须再次复核原Action。逾期、部分、最低额不足、模型缺失保留原阻断。没有自动跨目标调度或定时执行承诺。</p>
  </section>;
}
