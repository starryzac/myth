import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '../api/http';
import { readLocalActorSession } from '../api/local-actor';
import { getOriginalRecoveryNextResponse, isOriginalRecoveryNextRead, lookupRecoveryNext, parseRecoveryNextDraft, previewRecoveryNext, recoveryNextOriginalIntent } from '../api/full-recovery-next';
import type { RecoveryNextDraft, RecoveryNextLookup, RecoveryNextPreview } from '../api/full-recovery-next';
import { lookupRecoveryExecution, parseRecoveryPrepare, postRecoveryExecution, recoveryCheck as check, recoveryRequestHash } from '../api/full-recovery-execution';
import { acceptFullRecoveryRead, beginFullRecoveryOperation, endFullRecoveryAttempt, isFullRecoveryWorkspaceUnresolved, prepareRecoveryIntent, recoverFullRecoveryOperation, useFullRecoveryOperation } from '../features/full-recovery-execution-operation';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';
import type { FullRecoveryExecutionProps } from './FullRecoveryExecutionPanel';

export type FullRecoveryNextProps = FullRecoveryExecutionProps & { onPrepared?: () => void };
const storageKey = () => `bounded-funds-full-recovery-next-preview-v2:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const money = (value: number | null | undefined) => value === null || value === undefined ? 'UNKNOWN · 未证明' : `¥${formatMoneyCents(value)}`;
const raw = (value: object) => getOriginalRecoveryNextResponse(value) ?? JSON.stringify(value, null, 2);
export default function FullRecoveryNextPanel({ policyId, userId, expectedVersionId, epochId, mutationBlocked = false, onPrepared }: FullRecoveryNextProps) {
  const operation = useFullRecoveryOperation(), writing = useWriteInFlight();
  const [draft, setDraft] = useState<RecoveryNextDraft | null>(null), [preview, setPreview] = useState<RecoveryNextPreview | null>(null), [original, setOriginal] = useState<RecoveryNextLookup | null>(null);
  const [busy, setBusy] = useState(false), [restoring, setRestoring] = useState(true), [storageError, setStorageError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const scope = `${userId}:${epochId}:${policyId}:${expectedVersionId}`, current = useRef(scope); current.current = scope;
  const previewScope = useRef<string | null>(null);
  const unresolved = !!operation.workspace && isFullRecoveryWorkspaceUnresolved(operation.workspace);
  const locked = mutationBlocked || writing || busy || restoring || !!storageError || operation.busy || operation.recovering || !!operation.storage_error || !!operation.pending || unresolved;
  const boundNow = !!draft && draft.user_id === userId && draft.body.policy_id === policyId && draft.body.expected_version_id === expectedVersionId && draft.body.expected_epoch_id === epochId;
  const ready = previewScope.current === scope && boundNow && preview?.status === 'READY_TO_PREPARE';
  const handoffCurrent = !!draft && boundNow && !!original && isOriginalRecoveryNextRead(original) && original.status === 'RECORDED' && original.original_v1_lookup.epoch_state === 'OPEN';
  useEffect(() => { let alive = true; void recoverFullRecoveryOperation(); void (async () => { try { const saved = sessionStorage.getItem(storageKey()); const restored = saved === null ? null : await parseRecoveryNextDraft(JSON.parse(saved)); if (alive) setDraft(restored); } catch { if (alive) setStorageError('原root预览body/hash无法读取，保留原存储；尚未发送新准备请求。'); } finally { if (alive) setRestoring(false); } })(); return () => { alive = false; }; }, []);
  async function saveNewDraft(): Promise<RecoveryNextDraft> {
    const body = { policy_id: policyId, expected_version_id: expectedVersionId, expected_epoch_id: epochId, idempotency_key: `next-${crypto.randomUUID()}` };
    const saved = await parseRecoveryNextDraft({ protocol: 'full-recovery-next-preview-browser-v2', user_id: userId, body, body_json: JSON.stringify(body), request_hash: await recoveryRequestHash(body) });
    try { sessionStorage.setItem(storageKey(), JSON.stringify(saved)); } catch { setStorageError('只读root原body/hash无法保存，未发送预览或准备。'); throw new Error('无法保存原root请求'); }
    setDraft(saved); return saved;
  }
  async function readPreview(fresh: boolean) {
    if (locked || (!fresh && draft && !boundNow)) return; const requested = scope; setBusy(true); setError(''); setPreview(null); setOriginal(null);
    try { const saved = fresh || !draft ? await saveNewDraft() : draft; const identity = await readLocalActorSession(); check(identity.principal.user_id === userId && identity.principal.role === 'USER'); const value = await previewRecoveryNext(saved.body, saved.user_id); if (current.current === requested) { previewScope.current = requested; setPreview(value); setNotice('只读条件预览不是授权；prepare会重新读取事实，原截止不延长。'); } }
    catch (cause) { if (current.current === requested) setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readRoot() {
    if (!draft || busy || restoring) return; const saved = draft; setBusy(true); setError(''); setOriginal(null);
    try { const value = await lookupRecoveryNext(saved.body, saved.user_id); if (draft === saved) { setOriginal(value); setNotice(value.status === 'RECORDED' ? '已读原root映射的完整原Action；这是原v1记录投影，没有独立v2写入回执。' : 'NOT_FOUND_NOT_FINAL；不能据此声称资金未提交，已有原金融待核对门不变。'); } }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function prepare() {
    if (locked || !ready || !preview?.selection?.next_v1_request) return; const requested = scope; setBusy(true); setError('');
    try { const identity = await readLocalActorSession(); check(identity.principal.user_id === userId && identity.principal.role === 'USER' && current.current === requested); const body = parseRecoveryPrepare(preview.selection.next_v1_request); const intent = await prepareRecoveryIntent('PREPARE', userId, body); await beginFullRecoveryOperation(intent, mutationBlocked); try { await postRecoveryExecution(intent); setPreview(null); setNotice('实际v1 body/path已在POST前保存到原工作区；返回仍待独立GET。请在原恢复工作区核对、明确确认与执行。'); onPrepared?.(); } finally { endFullRecoveryAttempt(); } }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function handoff() {
    if (locked || !handoffCurrent || !original) return; setBusy(true); setError('');
    try { const intent = await recoveryNextOriginalIntent(original); await beginFullRecoveryOperation(intent, mutationBlocked); let value; try { value = await lookupRecoveryExecution(intent); } finally { endFullRecoveryAttempt(); } const result = await acceptFullRecoveryRead(intent, value); check(result.complete); setNotice('没有发送POST；独立v1原GET已将同一原Action交回原工作区。后续USER同意和执行仍使用该原动作。'); onPrepared?.(); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  const plan = preview?.planning?.plan, next = preview?.selection?.next_v1_request;
  return <section className="card" aria-label="服务器选择下一整仓恢复"><h3>服务器选择下一整仓 · 手动独立请求</h3><p>只读比较原当前策略与完整持仓，按服务器原计划顺序选择一个零费零损T0/T1整仓。不是多仓原子事务；到期、部分和有损分支未接，T1条件到账不等于今日现金或银行回执。</p>
    {error && <p role="alert">{error}</p>}{storageError && <p role="alert">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    {draft && <section aria-label="保留的下一整仓root"><p>原root键 {draft.body.idempotency_key} · 原策略版本 {draft.body.expected_version_id} · 原轮次 {draft.body.expected_epoch_id}</p><details><summary>只读预览原四字段/body/hash</summary><pre>{JSON.stringify(draft, null, 2)}</pre></details><button type="button" disabled={busy || restoring} onClick={() => void readRoot()}>独立GET原root结果</button>{!boundNow && <p role="alert">当前owner/版本/轮次已变化。只回读保留的原请求，不替换原版本或借历史授权准备新动作。</p>}</section>}
    <button type="button" disabled={locked || (!!draft && !boundNow)} onClick={() => void readPreview(false)}>读取服务器下一整仓预览（保留原root）</button>
    <button type="button" disabled={locked} onClick={() => void readPreview(true)}>明确开始新的独立只读请求</button><p>只有明确点击才生成新root；原Action未终局、待核对或其它族门打开时不能创建替代请求。刷新没有自动GET或POST。</p>
    {previewScope.current === scope && preview && <section aria-label="下一整仓服务器完整预览"><h4>服务器结果 {preview.status}</h4><p>原截止 {plan?.deadline_at ?? 'UNKNOWN'} · 当前scope现金 {money(plan?.actual_scope_cash_cents)} · 原待恢复额 {money(plan?.required_recovery_cents)} · 条件准时净额 {money(plan?.conditional_on_time_recovery_cents)}</p><p>候选分母 {preview.selection?.current_candidate_denominator ?? 'UNKNOWN'} · 原选中分母 {preview.selection?.current_selected_denominator ?? 'UNKNOWN'} · 未覆盖负检查点 {plan?.uncovered_checkpoints.length ?? 'UNKNOWN'}</p>{preview.planning?.source_issues.map((row, index) => <p key={index}>{row.code} · {row.source_ref} · {row.message}</p>)}<ul>{preview.selection?.eligibility.map((row) => <li key={row.position_id}>{row.position_id} · {row.eligible_for_v1_preview ? '可送原v1二次复核' : '本请求未覆盖'}{row.reasons.map((reason, index) => <p key={index}>{reason}</p>)}</li>)}</ul>{next && <p>服务器选择原仓位 {next.position_id} · 固定v1键 {next.idempotency_key}；用户没有输入或选择金融金额。</p>}{preview.original_v1_preview && <p>原范围 {preview.original_v1_preview.proof.status} · 本金 {money(preview.original_v1_preview.proof.candidate.principal_cents)} / fee {money(preview.original_v1_preview.proof.candidate.fee_cents)} / loss {money(preview.original_v1_preview.proof.candidate.independent_loss_cents)} · 原effectHash {preview.original_v1_preview.proof.effect_hash ?? 'UNKNOWN'}</p>}{preview.limitations.map((row, index) => <p key={index}>{row}</p>)}<details><summary>下一整仓预览原JSON</summary><pre>{raw(preview)}</pre></details><button type="button" disabled={locked || !ready} onClick={() => void prepare()}>明确准备服务器选中的同一整仓（原v1）</button><p>实际调用原v1 prepare；不把此请求伪记为v2 prepare。这里只准备ASK动作，确认和执行仍在原工作区分别由真实本地USER操作。</p></section>}
    {original && <section aria-label="下一整仓原root结果"><h4>原root {original.status}</h4>{original.original_v1_lookup.action && <p>原Action {original.original_v1_lookup.action.action_id} · {original.original_v1_lookup.action.status} · 原仓位 {original.original_v1_lookup.action.effect.position_id} · 银行 {original.original_v1_lookup.action.bank_status ?? 'UNKNOWN'}</p>}<p>绑定方式 {original.request_binding_kind} · 独立v2原件保存：否 · 自动推进：否。旧回执不是当前权限。</p><details><summary>原root与完整v1请求/回执JSON</summary><pre>{raw(original)}</pre></details><button type="button" disabled={locked || !handoffCurrent} onClick={() => void handoff()}>只用原GET交回同一原动作工作区</button>{!boundNow && <p>历史版本不匹配当前宿主，仅显示原Action；不会改成当前版本再确认或准备。</p>}</section>}
  </section>;
}
