import { useEffect, useState } from 'react';
import { getAccounts, getGoals } from '../api/goals';
import { getDemoState } from '../api/demo';
import { getFullPolicies } from '../api/full-policies';
import { getPolicies } from '../api/policies';
import { errorMessage } from '../api/http';
import { getAssetExecution, getOriginalAssetExecutionResponse, lookupAssetExecution, parseAssetPrepare, postAssetExecution, previewAssetExecution } from '../api/full-asset-execution';
import type { AssetIntent, AssetPortfolio, AssetPreview, AssetResponse } from '../api/full-asset-execution';
import { acceptFullAssetExecutionRead, beginFullAssetExecutionOperation, endFullAssetExecutionAttempt, prepareAssetIntent, recoverFullAssetExecutionOperation, useFullAssetExecutionOperation } from '../features/full-asset-execution-operation';
import { useWriteInFlight } from '../features/write-flight';
import { formatMoneyCents } from '../features/money';

type Sources = { user_id: string; epoch_id: string; full: Awaited<ReturnType<typeof getFullPolicies>>; mvp: Awaited<ReturnType<typeof getPolicies>>; goals: Awaited<ReturnType<typeof getGoals>> };
function Raw({ value, label }: { value: object; label: string }) { return <details><summary>{label}</summary><pre className="readonly-raw">{getOriginalAssetExecutionResponse(value) ?? JSON.stringify(value, null, 2)}</pre></details>; }
function Portfolio({ value }: { value: AssetPortfolio }) {
  return <section aria-label="完整原资产组合"><h4>完整原组合 · ¥{formatMoneyCents(value.total_purchase_cents)}</h4><p>原组合 {value.portfolio_id} · 周期 {value.epoch_id}</p><p>整组 hash {value.portfolio_hash} · 原请求 hash {value.client_request_hash}</p><p>原准备 {value.prepared_at} → {value.expires_at}；整组未保留资金，跨银行操作整体回滚不可用。</p><p>完整批次数 {value.batches.length}。服务器提供哪些原期限就展示哪些，不补造7/30/90/180天产品。</p><ol>{value.batches.map((batch) => <li key={batch.action_id}><strong>原批 {batch.batch_number} · ¥{formatMoneyCents(batch.command.effect.amount_cents)}</strong><dl><dt>原行动</dt><dd>{batch.action_id}</dd><dt>银行原键</dt><dd>{batch.bank_idempotency_key}</dd><dt>原产品 / 目录版本</dt><dd>{batch.catalogue.product_id} / {batch.catalogue.catalogue_version_id}</dd><dt>条款 digest</dt><dd>{batch.catalogue.terms_digest}</dd><dt>原经济效果 hash</dt><dd>{batch.command.effect_hash}</dd><dt>原退出计划</dt><dd>{batch.command.effect.purchase_exit?.kind ?? 'UNKNOWN'} · 条件本金可用 {batch.command.effect.purchase_exit?.principal_available_at ?? 'UNKNOWN'}</dd></dl></li>)}</ol><Raw value={value} label="完整原组合、策略版本、cash/income来源与经济命令" /></section>;
}
function Original({ value }: { value: AssetResponse }) {
  return <section aria-label="资产组合持久原状态"><h3>原服务状态 {value.state}</h3><Portfolio value={value.original_portfolio} /><p>原周期 {value.current_epoch_open ? 'OPEN' : '历史封存'} · 资金整体保留 false · 当前银行权限未评估</p><p>整组确认证据 {value.original_consent_evidence_status}；服务报告当前证据{value.original_consent_verified ? '匹配' : '未匹配/未记录'}，原确认不等于持续当前权限。</p>{value.original_consent && <><p>原确认键 {value.original_consent.idempotency_key} · 原确认请求 hash {value.original_consent.request_hash} · 原证据 {value.original_consent.evidence_id}</p><Raw value={value.original_consent} label="原整组明确确认body与保留证据" /></>}<ul>{value.batches.map((batch) => <li key={batch.action_id}>原批 {batch.batch_number} · {batch.original_action?.status ?? 'UNKNOWN · 当前action缺失'} · 银行 {batch.original_action?.bank_status ?? '未观察/未知'} · 原键 {batch.bank_idempotency_key}{batch.original_action?.receipt && <p>原回执 {batch.original_action.receipt.receipt_id} · 原银行操作 {batch.original_action.receipt.bank_operation_id} · 已执行 ¥{formatMoneyCents(batch.original_action.receipt.executed_cents)}；服务回执不作为当前银行授权。</p>}</li>)}</ul>{value.state === 'UNRESOLVED' && <p>前批 UNKNOWN / 未决：保留原组合、action和银行键；后批停止。只能先独立读取，再由用户明确恢复同一固定原批。</p>}<Raw value={value} label="持久组合与所有批次原JSON响应" /></section>;
}
export default function FullAssetExecutionPanel({ userId, epochId, mutationBlocked = false }: { userId?: string; epochId?: string; mutationBlocked?: boolean }) {
  const operation = useFullAssetExecutionOperation(); const writing = useWriteInFlight();
  const [sources, setSources] = useState<Sources | null>(null); const [fullId, setFullId] = useState(''); const [mvpId, setMvpId] = useState(''); const [goalId, setGoalId] = useState(''); const [mode, setMode] = useState<'PORTFOLIO' | 'FIXED_LADDER'>('PORTFOLIO');
  const [preview, setPreview] = useState<AssetPreview | null>(null); const [original, setOriginal] = useState<AssetResponse | null>(null); const [portfolioId, setPortfolioId] = useState('');
  const [reviewed, setReviewed] = useState(false); const [executeAccepted, setExecuteAccepted] = useState(false); const [resumeAccepted, setResumeAccepted] = useState(false); const [pendingRead, setPendingRead] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  useEffect(() => { void recoverFullAssetExecutionOperation(); }, []);
  const blocked = mutationBlocked || writing || busy || operation.busy || operation.recovering || !!operation.storage_error || !!operation.pending;
  const full = sources?.full.items.find((value) => value.policy_id === fullId); const mvp = sources?.mvp.items.find((value) => value.id === mvpId); const goal = sources?.goals.items.find((value) => value.id === goalId);
  const sameEpoch = !!original && (!epochId || epochId === original.epoch_id) && (!sources || sources.epoch_id === original.epoch_id) && (!userId || userId === original.user_id);
  const next = original?.batches.find((batch) => !batch.original_action?.receipt || !['SUCCEEDED', 'RECONCILED'].includes(batch.original_action.status) || batch.original_action.bank_status !== 'SETTLED' || !batch.original_trace_verified);
  const canConfirm = !blocked && sameEpoch && original?.current_epoch_open && original.original_consent === null && original.state === 'PREPARED_UNRESERVED';
  const canExecute = !blocked && sameEpoch && original?.current_epoch_open && original.original_consent_verified && original.original_consent_evidence_status === 'CURRENT_EVIDENCE_MATCHED' && next?.original_action && !['FAILED', 'INVALIDATED', 'CANCELLED'].includes(next.original_action.status) && !['STOPPED', 'RETAINED_HISTORY'].includes(original.state);
  function invalidate() { setPreview(null); setReviewed(false); setExecuteAccepted(false); }
  async function readSources() {
    if (busy || operation.busy) return; setBusy(true); setError(''); invalidate(); setSources(null);
    try { const [accounts, demo, fullPolicies, policies, goals] = await Promise.all([getAccounts(), getDemoState(), getFullPolicies(), getPolicies(), getGoals()]); if (!demo.available || !demo.epoch_id || userId && accounts.user_id !== userId || epochId && demo.epoch_id !== epochId) throw new Error('缺实际可用OPEN周期或用户与调用来源不同，未准备组合。'); setSources({ user_id: accounts.user_id, epoch_id: demo.epoch_id, full: fullPolicies, mvp: policies, goals }); setFullId(''); setMvpId(''); setGoalId(''); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readPreview() {
    if (blocked || !sources || !full || !mvp?.current_version) return; setBusy(true); setError(''); invalidate();
    try { const body = parseAssetPrepare({ full_policy_id: full.policy_id, expected_full_policy_version_id: full.current_version.version_id, mvp_asset_policy_id: mvp.id, expected_mvp_policy_version_id: mvp.current_version.id, goal_id: goal?.id ?? null, expected_goal_policy_version_id: goal?.policy_version_id ?? null, expected_epoch_id: sources.epoch_id, idempotency_key: `asset-prepare-${crypto.randomUUID()}`, planning_mode: mode }); setPreview(await previewAssetExecution(body, sources.user_id)); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function send(intent: AssetIntent) {
    await beginFullAssetExecutionOperation(intent, mutationBlocked); setPendingRead(false); setResumeAccepted(false);
    try { await postAssetExecution(intent); setNotice('原请求已返回，仍保留完整原件；请独立GET核对，不自动推进下一批。'); }
    finally { endFullAssetExecutionAttempt(); }
  }
  async function prepare() {
    if (blocked || !preview?.portfolio || preview.state !== 'READY_TO_REVIEW') return; setBusy(true); setError('');
    try { await send(await prepareAssetIntent('PREPARE', preview.user_id, preview.original_request)); setPreview(null); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!canConfirm || !reviewed || !original) return; setBusy(true); setError('');
    try { await send(await prepareAssetIntent('CONFIRM', original.user_id, { accepted: true, expected_epoch_id: original.epoch_id, reviewed_portfolio_hash: original.original_portfolio.portfolio_hash, idempotency_key: `asset-confirm-${crypto.randomUUID()}` }, original.original_portfolio)); setReviewed(false); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function execute() {
    if (!canExecute || !executeAccepted || !original || !next) return; setBusy(true); setError('');
    try { await send(await prepareAssetIntent('EXECUTE', original.user_id, { accepted: true, expected_epoch_id: original.epoch_id, reviewed_portfolio_hash: original.original_portfolio.portfolio_hash, expected_batch_number: next.batch_number, expected_action_id: next.action_id }, original.original_portfolio)); setExecuteAccepted(false); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readOriginal() {
    if (busy || operation.busy || !portfolioId) return; setBusy(true); setError(''); setReviewed(false); setExecuteAccepted(false);
    try { setOriginal(await getAssetExecution(portfolioId, userId ?? sources?.user_id)); }
    catch (cause) { setOriginal(null); setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function recover() {
    const intent = operation.pending; if (!intent || busy || operation.busy) return; setBusy(true); setError(''); setPendingRead(false); setResumeAccepted(false); setReviewed(false); setExecuteAccepted(false);
    try { const value = intent.kind === 'EXECUTE' ? await getAssetExecution(intent.portfolio_id!, intent.user_id, intent.reviewed_portfolio!) : await lookupAssetExecution(intent); const result = await acceptFullAssetExecutionRead(intent, value); setOriginal(result.original); if (result.original) setPortfolioId(result.original.original_portfolio.portfolio_id); setPendingRead(!result.complete); setNotice(result.complete ? '原完整请求及原结果已只读核对；历史回执不提供当前银行权限。' : '原结果仍未决/未找到，保留同一原body、组合、批次和键，后批不会启动。'); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function resume() {
    if (!operation.pending || !pendingRead || !resumeAccepted || busy || mutationBlocked || writing || operation.busy || operation.storage_error) return; setBusy(true); setError('');
    try { await send(operation.pending); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <section className="card full-asset-execution-panel" aria-label="原组合明确执行"><h2>资产组合 · 原件确认与逐批模拟执行</h2><p>金额、产品目录、现金/收入来源和组合由服务器重算；本页不输入金额、bank结果、时钟或授权。每批需原MVP权限与当前整组重验，不承诺整体原子性或回滚。</p>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="待核对原资产请求"><h3>原 {operation.pending.kind} 尚待核对</h3><Raw value={operation.pending} label="POST前保存的完整body/hash/组合与固定批次" /><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void recover()}>独立读取原资产请求结果</button>{pendingRead && <><label><input type="checkbox" checked={resumeAccepted} disabled={mutationBlocked || busy || writing || !!operation.storage_error} onChange={(event) => setResumeAccepted(event.target.checked)} />我明确恢复同一完整原请求，不换键或推进下一批</label><button type="button" disabled={!resumeAccepted || mutationBlocked || busy || writing || !!operation.storage_error} onClick={() => void resume()}>手动恢复同一原资产请求</button></>}<p>NOT_FOUND不是最终未提交证明。网络、解析或4xx均不会释放原请求；页面刷新不会自动POST。</p></section>}
    <button type="button" disabled={busy || operation.busy} onClick={() => void readSources()}>只读加载现有资产权限与周期</button>{sources && <><p>实际来源用户 {sources.user_id} · 原周期 {sources.epoch_id}。各只读接口不是同一事务快照，提交前由服务器重核。</p><fieldset disabled={blocked}><label>完整资产策略<select value={fullId} onChange={(event) => { setFullId(event.target.value); invalidate(); }}><option value="">选择现有原策略</option>{sources.full.items.filter((value) => value.template_name === 'AssetAuthorizationPolicy').map((value) => <option key={value.policy_id} value={value.policy_id}>{value.name ?? value.policy_id} · {value.effective_status} · v{value.current_version.version_number}</option>)}</select></label><label>原MVP资产权限<select value={mvpId} onChange={(event) => { setMvpId(event.target.value); invalidate(); }}><option value="">选择现有原权限</option>{sources.mvp.items.filter((value) => value.policy_type === 'asset_authorization' && value.current_version).map((value) => <option key={value.id} value={value.id}>{value.name} · {value.effective_status}</option>)}</select></label><label>资金目标范围<select value={goalId} onChange={(event) => { setGoalId(event.target.value); invalidate(); }}><option value="">一般闲置资金</option>{sources.goals.items.map((value) => <option key={value.id} value={value.id}>{value.name} · 原目标版本 {value.policy_version_id}</option>)}</select></label><label>原规划模式<select value={mode} onChange={(event) => { setMode(event.target.value as typeof mode); invalidate(); }}><option value="PORTFOLIO">今日多资产组合</option><option value="FIXED_LADDER">原目录定存梯度</option></select></label><button type="button" disabled={!full || !mvp?.current_version} onClick={() => void readPreview()}>读取服务器整组预览</button></fieldset></>}
    {preview && <section aria-label="资产整组只读预览"><h3>原预览 {preview.state}</h3>{preview.portfolio && <Portfolio value={preview.portfolio} />}{preview.reasons.map((reason) => <p key={reason}>{reason}</p>)}{preview.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}<Raw value={preview} label="当前规划、365保护及完整预览原响应" /><button type="button" disabled={blocked || !preview.portfolio} onClick={() => void prepare()}>明确保存原组合候选（尚未确认执行）</button></section>}
    <label>读取既存原组合ID<input value={portfolioId} disabled={busy || operation.busy} onChange={(event) => setPortfolioId(event.target.value)} /></label><button type="button" disabled={busy || operation.busy || !portfolioId} onClick={() => void readOriginal()}>只读读取原组合</button>
    {original && <><Original value={original} /><fieldset disabled={blocked}><label><input type="checkbox" checked={reviewed} disabled={!canConfirm} onChange={(event) => setReviewed(event.target.checked)} />我复核全部原批次、金额、条款与整组hash，明确确认整组</label><button type="button" disabled={!canConfirm || !reviewed} onClick={() => void confirm()}>提交原整组明确确认</button><label><input type="checkbox" checked={executeAccepted} disabled={!canExecute} onChange={(event) => setExecuteAccepted(event.target.checked)} />我明确执行/恢复原批 {next?.batch_number ?? '无待执行批'}，使用原action与银行键</label><button type="button" disabled={!canExecute || !executeAccepted} onClick={() => void execute()}>仅执行这个固定原批</button></fieldset></>}
    <p>SERVICE_RECEIPTS_VERIFIED只表示原服务回执核验，不是独立资金实验成功；未来收入为0，未决前批阻断后批，到期后不会自动滚存。</p>
  </section>;
}
