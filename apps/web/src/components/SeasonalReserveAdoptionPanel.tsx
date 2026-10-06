import { useEffect, useRef, useState } from 'react';
import type { FullPolicy } from '../api/full-policies';
import { errorMessage } from '../api/http';
import { readLocalActorSession } from '../api/local-actor';
import type { LocalActorSession } from '../api/local-actor';
import { createSeasonalIntent, lookupSeasonalAdoption, postSeasonalAdoption, previewSeasonalAdoption, readSeasonalAdoption, seasonalOriginalText } from '../api/seasonal-reserve-adoptions';
import type { SeasonalPreview, SeasonalProof, SeasonalReceipt, SeasonalScope } from '../api/seasonal-reserve-adoptions';
import { acceptSeasonalAdoptionRead, beginSeasonalAdoptionOperation, endSeasonalAdoptionAttempt, recoverSeasonalAdoptionOperation, useSeasonalAdoptionOperation } from '../features/seasonal-adoption-operation';
import { formatMoneyCents } from '../features/money';
import { object } from '../features/policy-form';

export type SeasonalReserveAdoptionPanelProps = { fullPolicy: FullPolicy; userId: string; epochId: string | null; mutationBlocked?: boolean; showOriginalRecovery?: boolean };
function Original({ receipt }: { receipt: SeasonalReceipt }) { return <section aria-label="原季节采纳回执"><p>原命令 {receipt.original.command_id} · 原证据 {receipt.evidence_id} / {receipt.evidence_hash} · trace {receipt.trace_hash}</p><p>仅证明原采纳记录，不继承当前保护有效性或银行授权。</p><details><summary>完整原采纳记录</summary><pre className="readonly-raw">{seasonalOriginalText(receipt) ?? JSON.stringify(receipt, null, 2)}</pre></details></section>; }
function Scope({ scope }: { scope: SeasonalScope }) {
  const raw = scope.suggestion_original, proof = object(raw.history_proof) ? raw.history_proof : null, suggestion = object(raw.suggestion) ? raw.suggestion : null;
  return <section aria-label="季节采纳完整复核"><dl><dt>原登记窗口</dt><dd>{scope.window_id} · {scope.official_start} → {scope.official_end}</dd><dt>当前保护窗口</dt><dd>{scope.protection_start} → {scope.protection_end} · {scope.timezone}</dd><dt>服务器固定采纳金额</dt><dd>¥{formatMoneyCents(scope.adopted_adjustment_cents)}</dd><dt>原历史所需调整</dt><dd>¥{formatMoneyCents(scope.required_adjustment_cents)} · {scope.cap_limited ? '受当前策略上限限制' : '未触及策略上限'}</dd><dt>完整历史覆盖</dt><dd>{String(proof?.period_start ?? 'UNKNOWN')} → {String(proof?.period_end ?? 'UNKNOWN')} · {String(suggestion?.window_count ?? 'UNKNOWN')} 个历史窗口</dd><dt>原证据分母</dt><dd>{scope.source_evidence_originals.length} 个原件；策略 {scope.version_id} / {scope.configuration_hash}</dd></dl><p>金额来自服务器已核的原历史与当前策略，本面板不编辑金额。今天可用现金不包含未来收入；采纳不支付资金、不证明未付金额、不授银行执行权限。</p><details><summary>原历史比较、公共窗口与来源</summary><pre className="readonly-raw">{JSON.stringify(scope.suggestion_original, null, 2)}</pre></details></section>;
}
/** This GET-only entry remains reachable without policy lists, a cookie or a current policy. */
export function SeasonalAdoptionOriginalRecoveryPanel() {
  const op = useSeasonalAdoptionOperation(); const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  useEffect(() => { void recoverSeasonalAdoptionOperation(); }, []);
  async function lookup() { const intent = op.pending; if (!intent || busy || op.busy) return; setBusy(true); setError(null); setNotice(null); try { const value = await lookupSeasonalAdoption(intent), cleared = await acceptSeasonalAdoptionRead(intent, value); setNotice(cleared ? '完整原GET已匹配原请求、周期与回执；本族待核对门已解除。' : '原键未终局，继续保留原请求，不自动重试或创建替代采纳。'); } catch (e) { setError(`${errorMessage(e)}；原请求继续保留。`); } finally { setBusy(false); } }
  if (!op.pending && !op.storage_error && !notice && !error) return null;
  return <section aria-label="季节采纳原键恢复"><h4>季节采纳原键核对</h4>{op.storage_error && <p role="alert">{op.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{op.pending && <><p>用户 {op.pending.user_id} · 原周期 {op.pending.epoch_id} · 策略 {op.pending.policy_id} · 原键 {op.pending.body.idempotency_key}</p><button type="button" disabled={busy || op.busy || op.recovering} onClick={() => void lookup()}>只读核对原季节采纳请求</button><details><summary>已保存的完整原请求</summary><pre className="readonly-raw">{op.pending.body_json}</pre></details></>}<p>本入口只读 GET，不重新提交采纳、不回答问题、不执行资金动作。</p></section>;
}
export default function SeasonalReserveAdoptionPanel({ fullPolicy, userId, epochId, mutationBlocked = false, showOriginalRecovery = true }: SeasonalReserveAdoptionPanelProps) {
  const op = useSeasonalAdoptionOperation(); useEffect(() => { void recoverSeasonalAdoptionOperation(); }, []);
  const [windowId, setWindowId] = useState(''), [preview, setPreview] = useState<SeasonalPreview | null>(null), [current, setCurrent] = useState<SeasonalProof | null>(null), [identity, setIdentity] = useState<LocalActorSession | null>(null);
  const [accepted, setAccepted] = useState(false), [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const policyCurrent = fullPolicy.template_name === 'SeasonalReservePolicy' && ['ACTIVE', 'CONFIRMED'].includes(fullPolicy.effective_status) && fullPolicy.planning_confirmation_valid && fullPolicy.reference_validation === 'CURRENT';
  const binding = `${userId}:${epochId}:${fullPolicy.policy_id}:${fullPolicy.current_version.version_id}:${fullPolicy.current_version.content_hash}:${fullPolicy.effective_status}:${fullPolicy.planning_confirmation_valid}:${fullPolicy.reference_validation}`;
  const [reviewBinding, setReviewBinding] = useState<string | null>(null);
  const latest = useRef({ binding, windowId, mutationBlocked, accepted, preview, identity }); latest.current = { binding, windowId, mutationBlocked, accepted, preview, identity };
  const matches = !!(policyCurrent && preview?.status === 'REVIEW_REQUIRED' && preview.scope && reviewBinding === binding && preview.scope.user_id === userId && preview.scope.epoch_id === epochId && preview.scope.policy_id === fullPolicy.policy_id && preview.scope.version_id === fullPolicy.current_version.version_id && preview.scope.configuration_hash === fullPolicy.current_version.content_hash && preview.scope.window_id === windowId);
  const currentUser = identity?.principal.user_id === userId && identity.principal.role === 'USER';
  const writesBlocked = mutationBlocked || busy || op.busy || op.recovering || !!op.pending || !!op.storage_error;
  function resetReview() { setPreview(null); setReviewBinding(null); setAccepted(false); setNotice(null); }
  async function actor() { if (busy || op.busy) return; setBusy(true); setError(null); setIdentity(null); const before = binding; try { const value = await readLocalActorSession(); if (latest.current.binding !== before || value.principal.user_id !== userId || value.principal.role !== 'USER') throw new Error('当前会话不是此用户的本地USER身份'); setIdentity(value); } catch (e) { setError(`${errorMessage(e)}；可在本地身份入口手动登录同USER，本面板不保存凭证。`); } finally { setBusy(false); } }
  async function readCurrent() { if (busy || op.busy) return; setBusy(true); resetReview(); setError(null); setCurrent(null); const before = binding; try { const value = await readSeasonalAdoption(userId, fullPolicy.policy_id); if (latest.current.binding !== before) throw new Error('读取期间用户或策略版本改变'); setCurrent(value); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } }
  async function prepareReview() {
    if (writesBlocked || !epochId || !windowId.trim() || !policyCurrent) return; const before = latest.current;
    setBusy(true); resetReview(); setError(null);
    try { const value = await previewSeasonalAdoption(userId, fullPolicy.policy_id, fullPolicy.current_version.version_id, windowId); if (latest.current.binding !== before.binding || latest.current.windowId !== before.windowId) throw new Error('预览期间用户、版本或窗口改变'); setPreview(value); setReviewBinding(before.binding); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (writesBlocked || !matches || !preview?.scope || !preview.reviewed_hash || !accepted || !reason.trim() || reason !== reason.trim() || !currentUser) return; const before = latest.current; setBusy(true); setError(null); let began = false;
    try { if (!crypto.randomUUID) throw new Error('原键生成能力缺失，未发送'); const intent = await createSeasonalIntent(preview.scope, { expected_version_id: preview.scope.version_id, window_id: windowId, expected_epoch_id: preview.scope.epoch_id, reviewed_hash: preview.reviewed_hash, accepted: true, reason, idempotency_key: `seasonal-adopt:${crypto.randomUUID()}` }); const now = latest.current; if (now.binding !== before.binding || now.windowId !== before.windowId || now.mutationBlocked || !now.accepted || now.preview !== before.preview || now.identity !== before.identity) throw new Error('复核期间原版本、窗口或明确接受改变，未发送'); await beginSeasonalAdoptionOperation(intent, now.mutationBlocked); began = true; await postSeasonalAdoption(intent); setAccepted(false); setNotice('收到采纳响应；原请求仍待独立GET核对，未据此解除写门。'); }
    catch (e) { setError(`${errorMessage(e)}；保存的原请求保留同键，不自动重试或换键。`); } finally { if (began) endSeasonalAdoptionAttempt(); setBusy(false); }
  }
  const registered = preview?.scope?.suggestion_original.public_windows;
  const windows = Array.isArray(registered) ? registered.filter((row): row is Record<string, unknown> => object(row) && typeof row.window_id === 'string') : [];
  const ownReceipt = op.original_receipt?.original.policy_id === fullPolicy.policy_id && op.original_receipt.original.user_id === userId ? op.original_receipt : null;
  return <section className="card readonly-section" aria-label={`季节储备显式采纳 ${fullPolicy.policy_id}`}><h4>节日储备 · 明确采纳原建议</h4><p>未采纳时一律 ADVICE_ONLY。签名本地 USER 确认只登记固定保护金额，不证明真人身份，也不授银行权限。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{op.storage_error && <p role="alert">{op.storage_error}</p>}{mutationBlocked && <p>其他族尚待核对，新采纳暂停；原键GET仍可用。</p>}
    <button type="button" disabled={busy || op.busy} onClick={() => void readCurrent()}>只读刷新当前季节采纳</button><button type="button" disabled={busy || op.busy} onClick={() => void actor()}>只读检查采纳USER会话</button>{currentUser && <p>已读取本地签名 USER · {identity?.principal.session_id}。服务端逐次重新验证身份。</p>}
    {current && <section aria-label="当前季节采纳状态"><p>当前状态 {current.status} · 服务时点 {current.as_of} · 完整注册采纳分母 {current.actual_adoption_count}</p><p>原因 {current.reasons.join('、') || '服务未报告不足'}</p>{current.original && <p>原固定采纳 ¥{formatMoneyCents(current.original.scope.adopted_adjustment_cents)} · {current.original.command_id}；UNKNOWN 时不能当作有效保护金额。</p>}<details><summary>完整当前采纳证明</summary><pre className="readonly-raw">{seasonalOriginalText(current) ?? JSON.stringify(current, null, 2)}</pre></details></section>}
    <label>用户指定登记节日窗口 ID<input value={windowId} maxLength={160} disabled={busy || op.busy} onChange={(e) => { setWindowId(e.target.value); resetReview(); }} /></label>
    {windows.length > 0 && <label>选择本次服务器原件中的窗口<select value={windowId} disabled={busy || op.busy} onChange={(e) => { setWindowId(e.target.value); resetReview(); }}><option value={windowId}>{windowId}</option>{windows.filter((w) => w.window_id !== windowId).map((w) => <option key={String(w.window_id)} value={String(w.window_id)}>{String(w.window_id)} · {String(w.start)} → {String(w.end)}</option>)}</select></label>}
    <button type="button" disabled={writesBlocked || !epochId || !windowId.trim() || !policyCurrent} onClick={() => void prepareReview()}>只读预览原节日建议采纳</button>
    {preview?.status === 'UNKNOWN' && <p>来源 UNKNOWN：{preview.reasons.join('、')}。未提供固定金额，不能确认。</p>}
    {preview?.scope && <><Scope scope={preview.scope} /><p>复核摘要 {preview.reviewed_hash}</p>{!matches && <p>当前用户、周期或版本已改变，请重新读取原建议。</p>}<label>采纳理由<input value={reason} maxLength={500} disabled={busy || op.busy} onChange={(e) => { setReason(e.target.value); setAccepted(false); }} /></label><label><input type="checkbox" checked={accepted && matches} disabled={writesBlocked || !matches || !currentUser} onChange={(e) => setAccepted(e.target.checked)} />我已复核原周期、完整来源及服务器固定金额，明确采纳</label><button type="button" disabled={writesBlocked || !matches || !currentUser || !accepted || !reason.trim() || reason !== reason.trim()} onClick={() => void confirm()}>明确确认原季节储备采纳</button></>}
    {ownReceipt && <Original receipt={ownReceipt} />}{showOriginalRecovery && <SeasonalAdoptionOriginalRecoveryPanel />}
  </section>;
}
