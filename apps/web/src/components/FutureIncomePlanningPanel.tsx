import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { readLocalActorSession } from '../api/local-actor';
import { futureCheck, futureOriginalJson, isFreshFutureIncomeLookup, lookupFutureIncomeCommand, readFutureIncomePlanning, submitFutureIncomeCandidate, submitFutureIncomeConfirmation } from '../api/future-income-planning';
import type { FutureIncomePlanning } from '../api/future-income-planning';
import { acceptFutureIncomeRead, acceptFutureIncomeWorkspaceRead, beginFutureIncomeOperation, discardFutureIncomeReview, endFutureIncomeAttempt, isFutureIncomeWorkspaceUnresolved, prepareFutureIncomeIntent, recoverFutureIncomeOperation, useFutureIncomeOperation } from '../features/future-income-operation';
import type { FutureIncomeIntent } from '../features/future-income-operation';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';

export type FutureIncomePlanningProps = { userId: string; epochId: string; mutationBlocked?: boolean };
const money = (value: number | null) => value === null ? 'UNKNOWN · 未登记或未证明' : `¥${formatMoneyCents(value)}`;
function Raw({ value, label }: { value: object; label: string }) { const raw = futureOriginalJson(value); return <details><summary>{label}{raw ? ' · HTTP原文' : ' · 解析/保存副本'}</summary><pre>{raw ?? JSON.stringify(value, null, 2)}</pre></details>; }
export default function FutureIncomePlanningPanel({ userId, epochId, mutationBlocked = false }: FutureIncomePlanningProps) {
  const operation = useFutureIncomeOperation(), writing = useWriteInFlight();
  const [planning, setPlanning] = useState<FutureIncomePlanning | null>(null), [origin, setOrigin] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [accepted, setAccepted] = useState(false), [resumeAccepted, setResumeAccepted] = useState(false), [pendingRead, setPendingRead] = useState(false);
  const scope = `${userId}:${epochId}`, current = useRef(scope); current.current = scope;
  const loadedScope = useRef<string | null>(null), displayed = loadedScope.current === scope;
  useEffect(() => { void recoverFutureIncomeOperation(); }, []);
  const workspace = operation.workspace, candidate = workspace?.candidate ?? null;
  const ownScope = !!workspace && workspace.user_id === userId && workspace.epoch_id === epochId;
  const unresolved = !!workspace && isFutureIncomeWorkspaceUnresolved(workspace);
  const locked = mutationBlocked || writing || busy || operation.busy || operation.recovering || !!operation.storage_error || !!operation.pending;
  const canConfirm = ownScope && !!workspace && isFreshFutureIncomeLookup(workspace) && workspace.command_kind === 'CANDIDATE' && candidate?.state === 'REQUIRES_EXPLICIT_CONFIRMATION';
  const source = displayed ? planning?.sources.sources.find(row => row.origin.origin_transaction_id === origin) : null;
  const alreadyRegistered = !!planning?.plans.some(row => row.state === 'USER_CONFIRMED_CONDITION' && row.candidate?.source.origin.origin_transaction_id === origin);
  async function load() {
    if (busy || operation.busy) return;
    const requested = scope; setBusy(true); setError(''); setAccepted(false); setPlanning(null);
    try {
      const [identity, result] = await Promise.all([readLocalActorSession(), readFutureIncomePlanning(userId, epochId)]);
      futureCheck(identity.principal.user_id === userId && identity.principal.role === 'USER');
      if (current.current === requested) { loadedScope.current = requested; setPlanning(result); setOrigin(result.sources.sources[0]?.origin.origin_transaction_id ?? ''); }
    } catch (cause) { if (current.current === requested) setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }
  async function send(intent: FutureIncomeIntent) {
    await beginFutureIncomeOperation(intent, mutationBlocked); setPendingRead(false); setResumeAccepted(false); setAccepted(false);
    try {
      if (intent.kind === 'CANDIDATE') await submitFutureIncomeCandidate(intent.body as Parameters<typeof submitFutureIncomeCandidate>[0], intent.user_id);
      else { futureCheck(intent.candidate !== null); await submitFutureIncomeConfirmation(intent.body as Parameters<typeof submitFutureIncomeConfirmation>[0], intent.candidate); }
      setNotice('原POST已返回，仍保留待核对门；请独立GET匹配原body/key/hash，不自动确认。');
    } finally { endFutureIncomeAttempt(); }
  }
  async function create() {
    if (locked || unresolved || !displayed || !source || planning?.sources.complete !== true || planning.epoch_id !== epochId || alreadyRegistered) return;
    setBusy(true); setError('');
    try { await send(await prepareFutureIncomeIntent('CANDIDATE', userId, { expected_epoch_id: epochId, origin_transaction_id: source.origin.origin_transaction_id, expected_origin_hash: source.origin_hash, idempotency_key: `future.${crypto.randomUUID()}` })); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (locked || !canConfirm || !accepted || !candidate) return;
    setBusy(true); setError('');
    try { await send(await prepareFutureIncomeIntent('CONFIRM', userId, { expected_epoch_id: epochId, candidate_id: candidate.candidate_id, reviewed_candidate_hash: candidate.candidate_hash, accepted: true, idempotency_key: `future.${crypto.randomUUID()}` }, candidate)); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function recover() {
    const intent = operation.pending; if (!intent || busy || operation.busy || operation.recovering) return;
    setBusy(true); setError(''); setPendingRead(false); setResumeAccepted(false); setAccepted(false);
    try {
      const result = await acceptFutureIncomeRead(intent, await lookupFutureIncomeCommand(intent.user_id, intent.epoch_id, intent.body.idempotency_key));
      setPendingRead(!result.complete);
      setNotice(result.complete ? '独立原GET已匹配原body/key/hash及候选或确认原件；这只是条件声明，不是真实资金授权。' : 'NOT_FOUND不是最终未提交证明，继续保留原请求；禁止换键或来源。');
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readWorkspace() {
    if (!workspace || busy || operation.busy || operation.pending || operation.recovering) return;
    setBusy(true); setError(''); setAccepted(false);
    try { await acceptFutureIncomeWorkspaceRead(await lookupFutureIncomeCommand(workspace.user_id, workspace.epoch_id, workspace.idempotency_key)); setNotice('原工作区已独立GET核对；旧确认/历史回执不能当当前现金或授权。'); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function resume() {
    if (!operation.pending || !pendingRead || !resumeAccepted || busy || operation.busy || mutationBlocked || writing || operation.storage_error) return;
    setBusy(true); setError(''); try { await send(operation.pending); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  function discard() { setAccepted(false); try { discardFutureIncomeReview(); setNotice('仅关闭本地复核；服务端原候选、确认与哈希全部保留，没有撤销已登记假设。'); } catch (cause) { setError(errorMessage(cause)); } }
  return <section className="card" aria-label="未来收入条件规划">
    <h3>未来收入 · 用户声明的条件假设</h3>
    <p>只从服务器已验原收入账本选择来源，不推定工资稳定，不输入金额或时钟。明确确认后，按原入账金额与本地日构建365天月度重复假设，短月取月末；这不是统计预测或银行付款承诺。</p>
    <p>当前现金计入 ¥0.00 · 执行额度计入 ¥0.00 · 不改变原自主边界、执行视图、资金事实或PolicyVersion授权。入账时点未知，条件金额不能承诺当天可用。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="条件声明待核对原请求"><h4>原 {operation.pending.kind} 待核对</h4><p>原键 {operation.pending.body.idempotency_key} · 原轮次 {operation.pending.epoch_id} · requestHash {operation.pending.request_hash}</p><Raw value={operation.pending} label="POST前保存的完整body与hash" /><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void recover()}>独立读取原条件声明结果</button>{pendingRead && <><label><input type="checkbox" checked={resumeAccepted} disabled={busy || mutationBlocked || writing || !!operation.storage_error} onChange={event => setResumeAccepted(event.target.checked)} />我只恢复同一原body/key，不更换来源或创建替代请求</label><button type="button" disabled={!resumeAccepted || busy || mutationBlocked || writing || !!operation.storage_error} onClick={() => void resume()}>手动恢复同一条件声明请求</button></>}<p>网络、解析和4xx均保留原请求；刷新不自动POST。自己的原GET不受自身待核对门阻挡。</p></section>}
    {workspace && !operation.pending && <section aria-label="条件声明原工作区"><h4>{workspace.command_kind === 'CONFIRM' ? '原条件确认已记录' : '原条件候选待复核'}</h4><p>原轮次 {workspace.epoch_id} · 原键 {workspace.idempotency_key} · {candidate?.state ?? 'UNKNOWN'}</p><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void readWorkspace()}>只读核对原条件声明工作区</button>{candidate && <><p>原收入 {money(candidate.source.origin.amount_cents)} · 条件每月 {money(candidate.assumption.conditional_amount_cents)} · 本地日 {candidate.assumption.monthly_local_day} · 时区 {candidate.assumption.timezone}</p><p>规划有效日期 {candidate.assumption.valid_from} 至 {candidate.assumption.valid_until} · 首次确认截止 {candidate.confirmation_deadline}</p><p>来源交易 {candidate.source.origin.origin_transaction_id} · originHash {candidate.source.origin_hash} · candidateHash {candidate.candidate_hash}</p><p>银行原证据 {candidate.source.origin.bank_evidence_id} · 原收入账本 {candidate.source.ledger_evidence_id}。原入账证明不能证明未来重复付款。</p></>}<Raw value={workspace} label="候选、原USER声明与确认原件" />{workspace.command_kind === 'CANDIDATE' && <><label><input type="checkbox" checked={accepted && canConfirm} disabled={locked || !canConfirm} onChange={event => setAccepted(event.target.checked)} />我完整复核来源、金额、轮次、有效日期和candidateHash，明确声明这项月度条件假设；不授权金融执行</label><button type="button" disabled={locked || !canConfirm || !accepted} onClick={() => void confirm()}>明确确认这项条件假设</button><p>刷新后须先独立GET，失效候选/不同轮次/来源未知不沿用旧复核。服务器每次再次验证当前USER及原收入来源。</p></>}<button type="button" disabled={busy || operation.busy || operation.recovering || !!operation.storage_error} onClick={discard}>仅关闭本地复核工作区</button></section>}
    <button type="button" disabled={busy || operation.busy} onClick={() => void load()}>只读加载当前条件规划与USER身份</button>
    {displayed && planning && <section aria-label="服务器365天条件规划"><h4>原状态 {planning.status}</h4><p>原读取 {planning.as_of} · 轮次 {planning.epoch_id ?? 'UNKNOWN'} · inputHash {planning.input_hash}</p><p>原来源完整度 {planning.sources.captured_origin_count} / {planning.sources.original_origin_count ?? 'UNKNOWN'} · {planning.sources.status}</p>{planning.issues.map((row, index) => <p key={index}>{row}</p>)}{planning.sources.issues.map((row, index) => <p key={index}>{row}</p>)}<label>已验证原收入来源<select value={origin} disabled={locked || unresolved} onChange={event => { setOrigin(event.target.value); setAccepted(false); }}><option value="">没有可证明来源/请选择</option>{planning.sources.sources.map(row => <option key={row.origin.origin_transaction_id} value={row.origin.origin_transaction_id}>{row.origin.origin_transaction_id} · {money(row.origin.amount_cents)} · {row.origin.occurred_at}</option>)}</select></label>{source && <p>原来源Hash {source.origin_hash} · ledgerHash {source.ledger_evidence_hash}。来源选择变更会清除复核状态。</p>}<button type="button" disabled={locked || unresolved || !source || !planning.sources.complete || planning.epoch_id !== epochId || alreadyRegistered} onClick={() => void create()}>从选定原收入建立待确认候选</button>{alreadyRegistered && <p>同原收入已有条件确认，不能重复计入。</p>}<p>365天条件合计 {money(planning.total_conditional_income_cents)}；未登记/UNKNOWN保持null，不能据此产生投资或支付动作。</p><ul>{planning.plans.map(row => <li key={row.candidate_id}>{row.candidate_id} · {row.state}{row.issues.map((issue, index) => <p key={index}>{issue}</p>)}</li>)}</ul><details><summary>完整365日期条件日程</summary><ol>{planning.daily_schedule.map(day => <li key={day.date}>{day.date} · {money(day.conditional_income_cents)} · 原候选 {day.candidate_ids.length ? day.candidate_ids.join('、') : '无'} · 日内可用时刻未知</li>)}</ol></details><Raw value={planning} label="完整规划与冲突原件JSON" /><p>本入口只登记有限条件假设，尚未替换年度保护/联合分配原引擎的未来收入0占位；登记撤回、修改和跨轮次续期尚未实现。</p></section>}
  </section>;
}

/** Own GET surface for Root's cross-page gate, without automatic write or authority. */
export function FutureIncomeOriginalRecoveryPanel() {
  const operation = useFutureIncomeOperation(); const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { void recoverFutureIncomeOperation(); }, []);
  async function read() {
    if (busy || operation.busy || operation.recovering) return; setBusy(true); setError('');
    try { if (operation.pending) { const i = operation.pending, result = await acceptFutureIncomeRead(i, await lookupFutureIncomeCommand(i.user_id, i.epoch_id, i.body.idempotency_key)); setNotice(result.complete ? '原条件声明已匹配，复核工作区保留；不授权资金。' : '原结果非终局，保留body/key。'); } else if (operation.workspace) { const w = operation.workspace; await acceptFutureIncomeWorkspaceRead(await lookupFutureIncomeCommand(w.user_id, w.epoch_id, w.idempotency_key)); setNotice('原条件声明已只读刷新，不代表未来收入已入账。'); } }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  if (!operation.pending && !operation.workspace && !operation.storage_error) return null;
  return <section className="card" aria-label="跨页原条件声明核对"><h3>未来收入原请求/复核工作区</h3>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}<button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void read()}>只读核对原未来收入声明</button><Raw value={operation.pending ?? operation.workspace ?? { storage_error: operation.storage_error }} label="保留的原body/hash/条件原件" /><p>本区域仅GET；NOT_FOUND和HTTP错误不解除原门，登录失效须手动重新登录。</p></section>;
}
