import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { FullPolicy } from '../api/full-policies';
import { getPolicies } from '../api/policies';
import { readLocalActorSession } from '../api/local-actor';
import type { LocalActorSession } from '../api/local-actor';
import { errorMessage } from '../api/http';
import { lookupPaymentIntent, paymentOriginalText, postPaymentIntent, preparePaymentIntent, previewPaymentRelation, readPaymentAction, scopeRequest } from '../api/full-payment-relations';
import type { PaymentIntent, PaymentPreview, PaymentReceipt, PaymentScope } from '../api/full-payment-relations';
import { acceptFixedPaymentRead, beginFixedPaymentOperation, beginFixedPaymentSettlementResume, endFixedPaymentAttempt, recoverFixedPaymentOperation, retainFixedPaymentActionRead, useFixedPaymentOperation } from '../features/fixed-payment-operation';
import { formatMoneyCents } from '../features/money';
import ActionEffectReview from './ActionEffectReview';

export type FixedPaymentRelationPanelProps = { fullPolicy: FullPolicy; userId: string; epochId: string | null; mutationBlocked?: boolean };
function Scope({ scope }: { scope: PaymentScope }) {
  return <section aria-label="固定付款原范围"><h5>明确复核原固定关系</h5><dl><dt>银行收款身份</dt><dd>{scope.payee_id}</dd><dt>原银行身份凭证</dt><dd>{scope.payee_evidence_id} · {scope.payee_evidence_hash}</dd><dt>现金来源账户</dt><dd>{scope.source_account_id} · {scope.source_account_identity_hash}</dd><dt>金额规则</dt><dd>{scope.amount_rule.kind === 'exact' ? `固定 ¥${formatMoneyCents(scope.amount_rule.amount_cents)}` : `¥${formatMoneyCents(scope.amount_rule.min_cents)}—¥${formatMoneyCents(scope.amount_rule.max_cents)}`}</dd><dt>单次上限</dt><dd>¥{formatMoneyCents(scope.single_action_cap_cents)}</dd><dt>自然月到期日</dt><dd>{scope.due_day} 日 · {scope.timezone}</dd><dt>周期自主</dt><dd>{scope.auto_execute ? '已有周期自主范围，实际动作等级仍由服务端决定' : '每次原经济后果需要用户明确同意'}</dd><dt>有效窗口</dt><dd>{scope.valid_from} 至 {scope.valid_until}（不含）</dd></dl><p>Full版本 {scope.full_version_id} · {scope.full_configuration_hash}；原MVP版本 {scope.original_version_id} · {scope.original_configuration_hash}。本界面不创建原MVP权限，不把规划确认当银行授权。</p></section>;
}
function Receipt({ value }: { value: PaymentReceipt }) {
  return <section aria-label={`原关系 ${value.original.kind}`}><p>原 {value.original.kind} 命令 {value.original.command_id} · 原记录捕获时服务读取范围 {value.current_scope_status}。历史记录不是当前授权缓存。</p><p>原证据 {value.evidence_id} / {value.evidence_hash}；trace {value.trace_hash}。</p><details><summary>完整原关系记录</summary><pre className="readonly-raw">{paymentOriginalText(value) ?? JSON.stringify(value, null, 2)}</pre></details></section>;
}
export default function FixedPaymentRelationPanel({ fullPolicy, userId, epochId, mutationBlocked = false }: FixedPaymentRelationPanelProps) {
  const operation = useFixedPaymentOperation();
  useEffect(() => { void recoverFixedPaymentOperation(); }, []);
  const policies = useQuery({ queryKey: ['fixed-payment-original-policies', userId, fullPolicy.policy_id, fullPolicy.current_version.version_id], queryFn: getPolicies, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const [originalId, setOriginalId] = useState(''), [preview, setPreview] = useState<PaymentPreview | null>(null), [identity, setIdentity] = useState<LocalActorSession | null>(null);
  const [acceptedStart, setAcceptedStart] = useState(false), [acceptedRelation, setAcceptedRelation] = useState(false), [acceptedAction, setAcceptedAction] = useState(false), [reason, setReason] = useState(''), [period, setPeriod] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null), [postReceipt, setPostReceipt] = useState<PaymentReceipt | null>(null), [actionRead, setActionRead] = useState(false);
  const selected = policies.data?.items.find((p) => p.id === originalId), workflow = operation.workflow;
  const owned = operation.pending?.user_id === userId && operation.pending.full_policy_id === fullPolicy.policy_id;
  const ownWorkflow = workflow.start?.original.user_id === userId && workflow.start.original.scope.full_policy_id === fullPolicy.policy_id;
  const w = ownWorkflow ? workflow : null;
  const scope = w?.authorization?.original.scope ?? w?.start?.original.scope ?? preview?.scope;
  const sameCurrent = !!(scope && epochId === scope.epoch_id && scope.full_version_id === fullPolicy.current_version.version_id && fullPolicy.epoch_id === epochId);
  const samePreview = !!(preview && preview.scope.epoch_id === epochId && preview.scope.full_version_id === fullPolicy.current_version.version_id && preview.scope.full_policy_id === fullPolicy.policy_id && selected?.current_version?.id === preview.scope.original_version_id && selected.id === preview.scope.original_policy_id && preview.scope.user_id === userId);
  const userSession = identity?.principal.user_id === userId && identity.principal.role === 'USER';
  const writeBlocked = mutationBlocked || busy || operation.busy || operation.recovering || !!operation.pending || !!operation.storage_error || !userSession;
  const latest = useRef({ fullPolicy, userId, epochId, mutationBlocked, acceptedStart, acceptedRelation, acceptedAction, preview, originalId }); latest.current = { fullPolicy, userId, epochId, mutationBlocked, acceptedStart, acceptedRelation, acceptedAction, preview, originalId };
  function resetReview() { setPreview(null); setAcceptedStart(false); setAcceptedRelation(false); setAcceptedAction(false); setPostReceipt(null); setActionRead(false); }
  async function readIdentity() { if (busy || operation.busy) return; setBusy(true); setError(null); setIdentity(null); try { const value = await readLocalActorSession(); if (value.principal.user_id !== userId || value.principal.role !== 'USER') throw new Error('当前会话不是本用户的USER身份'); setIdentity(value); setNotice('读取了本地签名USER会话；身份显示不是资金授权，也不证明真人身份。'); } catch (e) { setError(`${errorMessage(e)}；请先在本地身份入口登录。凭证不由本面板保存。`); } finally { setBusy(false); } }
  async function previewScope() {
    if (!epochId || !selected?.current_version || busy || operation.busy || operation.pending || operation.storage_error || policies.isFetching) return;
    setBusy(true); resetReview(); setError(null); setNotice(null); const binding = latest.current;
    try { const value = await previewPaymentRelation(userId, { expected_epoch_id: epochId, full_policy_id: fullPolicy.policy_id, expected_full_version_id: fullPolicy.current_version.version_id, original_policy_id: selected.id, expected_original_version_id: selected.current_version.id }); if (latest.current.fullPolicy.current_version.version_id !== binding.fullPolicy.current_version.version_id || latest.current.userId !== binding.userId || latest.current.epochId !== binding.epochId || latest.current.originalId !== binding.originalId) throw new Error('复核期间原策略或周期改变'); setPreview(value); }
    catch (e) { setError(`${errorMessage(e)}；没有建立关系，未猜测银行收款或当月已付金额。`); } finally { setBusy(false); }
  }
  async function send(kind: PaymentIntent['kind']) {
    const scope = kind === 'START' ? preview?.scope : w?.authorization?.original.scope ?? w?.start?.original.scope;
    if (writeBlocked || !scope || (kind === 'START' ? !samePreview : !sameCurrent)) return;
    if (kind === 'START' && (!preview || !samePreview || !acceptedStart) || kind === 'CONFIRM' && (!w?.start || !acceptedRelation || !reason.trim()) || kind === 'PREPARE' && !w?.authorization || kind === 'ACTION_CONFIRM' && (!w?.action || !acceptedAction || !actionRead) || kind === 'EXECUTE' && (!w?.action || !actionRead)) return;
    setBusy(true); setError(null); setNotice(null); const binding = latest.current; let began = false;
    try {
      const key = () => { if (!crypto.randomUUID) throw new Error('原键生成能力缺失，未发送'); return `fixed-payment:${crypto.randomUUID()}`; };
      const intent = kind === 'START' ? await preparePaymentIntent(kind, scope, { ...scopeRequest(scope), idempotency_key: key() }) : kind === 'CONFIRM' ? await preparePaymentIntent(kind, scope, { expected_epoch_id: scope.epoch_id, reviewed_scope_hash: w!.start!.original.scope_hash, accepted: true, reason, idempotency_key: key() }, { start_command_id: w!.start!.original.command_id }) : kind === 'PREPARE' ? await preparePaymentIntent(kind, scope, { expected_epoch_id: scope.epoch_id, period, idempotency_key: key() }, { authorization_id: w!.authorization!.original.command_id }) : await preparePaymentIntent(kind, scope, kind === 'ACTION_CONFIRM' ? { expected_epoch_id: scope.epoch_id, reviewed_effect_hash: w!.action!.effect_hash, accepted: true } : { expected_epoch_id: scope.epoch_id }, { authorization_id: w!.authorization!.original.command_id, action: w!.action! });
      const current = latest.current; if (current.mutationBlocked || current.userId !== binding.userId || current.epochId !== binding.epochId || current.fullPolicy.current_version.version_id !== binding.fullPolicy.current_version.version_id || kind === 'START' && (!current.acceptedStart || current.preview !== binding.preview) || kind === 'CONFIRM' && !current.acceptedRelation || kind === 'ACTION_CONFIRM' && !current.acceptedAction) throw new Error('复核期间用户、版本或明确接受改变，未发送');
      await beginFixedPaymentOperation(intent, current.mutationBlocked); began = true;
      const value = await postPaymentIntent(intent); if ('original' in value) setPostReceipt(value);
      setAcceptedStart(false); setAcceptedRelation(false); setAcceptedAction(false); setActionRead(false); setNotice('收到服务响应；原请求仍待独立GET核对，未据此解除写门。');
    } catch (e) { setError(`${errorMessage(e)}；已保存的原请求必须原键核对，不自动重试或换键。`); }
    finally { if (began) endFixedPaymentAttempt(); setBusy(false); }
  }
  async function lookup() {
    const intent = operation.pending; if (!intent || !owned || busy || operation.busy) return; setBusy(true); setError(null); setNotice(null);
    try { const result = await lookupPaymentIntent(intent); const cleared = await acceptFixedPaymentRead(intent, result); setActionRead(false); setAcceptedAction(false); setNotice(cleared ? '完整原GET已匹配原请求/范围/经济后果；本族门已解除。历史原件不缓存当前授权。' : '原键未终局，或原银行结果仍待核对；保留原身份，不创建替代付款。'); }
    catch (e) { setError(`${errorMessage(e)}；原请求继续保留。`); } finally { setBusy(false); }
  }
  async function readAction() {
    if (!w?.action || !scope || busy || operation.busy) return; setBusy(true); setError(null); setAcceptedAction(false); setActionRead(false);
    try { const value = await readPaymentAction(w.action, scope); await retainFixedPaymentActionRead(value); setActionRead(true); setNotice('独立GET已核对原动作；必须重新逐项复核，没有自动确认或执行。'); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function resumeSettlement() {
    if (!owned || busy || operation.busy || mutationBlocked || !userSession) return; setBusy(true); setError(null); let began = false;
    try { const intent = await beginFixedPaymentSettlementResume(latest.current.mutationBlocked); began = true; await postPaymentIntent(intent); setNotice('仅恢复原已SETTLED银行身份的应用投影；仍须独立GET原回执。'); } catch (e) { setError(errorMessage(e)); } finally { if (began) endFixedPaymentAttempt(); setBusy(false); }
  }
  return <section className="card readonly-section" aria-label={`固定收款关系 ${fullPolicy.policy_id}`}><h4>固定收款关系 · 用户发起并确认</h4><p>仅支持服务器已唯一证明的银行收款身份。未知或歧义收款人不能由 Agent 自生外付；这里不创建外部银行账户。</p><p>本地 USER 会话使用 HttpOnly cookie；面板不存 token、角色权限、金额或银行结果作为授权。未到账收入不会扩大付款能力。</p>
    {operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{mutationBlocked && <p>其他族待核对，禁止新写；自身原件GET仍可用。</p>}
    <button type="button" disabled={busy || operation.busy} onClick={() => void readIdentity()}>只读检查本地USER会话</button>{identity && userSession && <p>本地签名 USER · {identity.principal.user_id} · 会话结束 {identity.principal.expires_at}；human_identity_verified=false。</p>}
    {operation.pending && (owned ? <section aria-label="待核对固定付款原请求"><h5>待核对 {operation.pending.kind}</h5><p>原路径 {operation.pending.path}，所有HTTP结果均保留原请求。</p><button type="button" disabled={busy || operation.busy} onClick={() => void lookup()}>只读核对原固定付款请求</button><details><summary>POST前保存的完整原请求</summary><pre className="readonly-raw">{JSON.stringify(operation.pending, null, 2)}</pre></details>{operation.pending.kind === 'EXECUTE' && w?.action?.bank_status === 'SETTLED' && !w.action.receipt && <button type="button" disabled={busy || operation.busy || mutationBlocked || !userSession || !actionRead} onClick={() => void resumeSettlement()}>显式恢复原已结算付款投影</button>}</section> : <p>另一用户或固定关系的原请求待核对；请返回原关系，不能在这里换键。</p>)}
    {!operation.pending && <><label>选择实际已授权的原MVP周期策略<select value={originalId} disabled={busy || policies.isFetching} onChange={(e) => { setOriginalId(e.target.value); resetReview(); }}><option value="">请选择原周期策略</option>{policies.data?.items.filter((p) => p.policy_type === 'recurring_obligation').map((p) => <option key={p.id} value={p.id} disabled={!p.current_version || !p.version_authorized || !['ACTIVE', 'CONFIRMED'].includes(p.effective_status)}>{p.name} · {p.effective_status}</option>)}</select></label>{policies.isError && <p role="alert">{errorMessage(policies.error)}</p>}<button type="button" disabled={busy || policies.isFetching} onClick={() => { resetReview(); void policies.refetch(); }}>只读刷新原周期来源</button><button type="button" disabled={!epochId || !selected?.current_version || busy || operation.busy || !!operation.storage_error || policies.isFetching} onClick={() => void previewScope()}>只读复核固定关系范围</button></>}
    {preview && samePreview && !operation.pending && <section aria-label="固定关系发起复核"><Scope scope={preview.scope} /><p>复核摘要 {preview.scope_hash}，preview_only=true，未授银行权限。</p><label><input type="checkbox" checked={acceptedStart} disabled={writeBlocked} onChange={(e) => setAcceptedStart(e.target.checked)} />由我发起与此已验证银行收款身份的新固定关系</label><button type="button" disabled={writeBlocked || !acceptedStart} onClick={() => void send('START')}>用户明确发起固定关系</button></section>}
    {w?.start && <Receipt value={w.start} />}{w?.authorization && <Receipt value={w.authorization} />}{postReceipt && <p>刚收到的 {postReceipt.original.kind} POST仅供查看，不能替代原键GET。</p>}
    {w?.start && !w.authorization && !operation.pending && <section aria-label="固定关系确认复核"><Scope scope={w.start.original.scope} /><label>关系确认理由<input value={reason} maxLength={1000} onChange={(e) => { setReason(e.target.value); setAcceptedRelation(false); }} /></label><label><input type="checkbox" checked={acceptedRelation} disabled={writeBlocked || !sameCurrent} onChange={(e) => setAcceptedRelation(e.target.checked)} />我已复核原身份、账户、额度、周期自主与有效期，明确确认这项固定关系</label><button type="button" disabled={writeBlocked || !sameCurrent || !acceptedRelation || !reason.trim()} onClick={() => void send('CONFIRM')}>明确确认原固定关系</button></section>}
    {w?.authorization && !operation.pending && (!w.action || ['SUCCEEDED', 'RECONCILED'].includes(w.action.status)) && <section aria-label="固定周期付款准备"><label>实际付款自然月<input placeholder="YYYY-MM" value={period} onChange={(e) => setPeriod(e.target.value)} /></label><p>服务器按原银行当期付款观察核验金额；本界面不填金额、不推定已付为零。</p><button type="button" disabled={writeBlocked || !sameCurrent || !/^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(period)} onClick={() => void send('PREPARE')}>准备原周期付款</button></section>}
    {w?.action && <section aria-label="原固定付款动作"><ActionEffectReview action={w.action} /><button type="button" disabled={busy || operation.busy} onClick={() => void readAction()}>只读刷新原固定付款动作</button>{w.action.receipt && <p>原回执 {w.action.receipt.receipt_id} · 银行 {w.action.bank_status} · 原执行金额 ¥{formatMoneyCents(w.action.receipt.executed_cents)}。</p>}{['ADVISE_ONLY', 'BLOCKED'].includes(w.action.autonomy_level) && <p>原等级仅建议或阻挡，不能用用户同意升级为可执行付款。</p>}{!operation.pending && ['PLANNED', 'AUTHORIZED'].includes(w.action.status) && ['AUTO_EXECUTE', 'ASK_ONCE'].includes(w.action.autonomy_level) && <>{w.action.autonomy_level === 'ASK_ONCE' && !w.consent && <><label><input type="checkbox" checked={acceptedAction} disabled={writeBlocked || !sameCurrent || !actionRead} onChange={(e) => setAcceptedAction(e.target.checked)} />我已复核此原付款金额、收款身份、来源和原effect hash，明确同意本次ASK付款</label><button type="button" disabled={writeBlocked || !sameCurrent || !actionRead || !acceptedAction} onClick={() => void send('ACTION_CONFIRM')}>明确同意本次原付款</button></>}{w.consent?.status === 'RECORDED' && <p>原签名USER单次同意已记录；尚未证明付款结算。</p>}<button type="button" disabled={writeBlocked || !sameCurrent || !actionRead || w.action.autonomy_level !== 'AUTO_EXECUTE' && w.consent?.status !== 'RECORDED'} onClick={() => void send('EXECUTE')}>执行已复核原周期付款</button></>}</section>}
    {scope && !sameCurrent && <p className="notice">用户、周期或Full版本已变化；历史原件可读取，新写暂停，旧确认不会继承到新版本。</p>}
  </section>;
}
