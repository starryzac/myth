import { useEffect, useState } from 'react';
import { getAccounts } from '../api/goals';
import { getDemoState } from '../api/demo';
import { getFullPolicies } from '../api/full-policies';
import { errorMessage } from '../api/http';
import { getJointExecution, getOriginalJointExecutionResponse, lookupJointExecution, parseJointPrepare, postJointExecution, previewJointExecution } from '../api/full-joint-goal-execution';
import type { JointIntent, JointPlan, JointPreview, JointResponse } from '../api/full-joint-goal-execution';
import { acceptFullJointGoalRead, acceptFullJointGoalWorkspaceRead, beginFullJointGoalOperation, endFullJointGoalAttempt, prepareJointIntent, recoverFullJointGoalOperation, useFullJointGoalOperation } from '../features/full-joint-goal-operation';
import { useWriteInFlight } from '../features/write-flight';
import { formatMoneyCents } from '../features/money';

type Sources = { user_id: string; epoch_id: string; policies: Awaited<ReturnType<typeof getFullPolicies>> };
function Raw({ value, label }: { value: object; label: string }) {
  const raw = getOriginalJointExecutionResponse(value);
  function download() { if (raw === null) return; const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' })), link = document.createElement('a'); link.href = url; link.download = 'joint-original-http-envelope.json'; link.click(); URL.revokeObjectURL(url); }
  return <details><summary>{label}</summary><pre className="readonly-raw">{raw ?? JSON.stringify(value, null, 2)}</pre>{raw !== null && <button type="button" onClick={download}>下载完整原HTTP封套文本</button>}</details>;
}
function Plan({ value }: { value: JointPlan }) {
  return <section aria-label="联合完整固定计划"><h4>整组原金额 ¥{formatMoneyCents(value.total_allocation_cents)}</h4><p>父计划 {value.plan_id} · 周期 {value.epoch_id} · 整组 hash {value.plan_hash}</p><p>整组hash由原服务验证；前端未独立重算任意历史JSON的Python数字token，不以结构比较冒称审计验真。</p><p>实际目标分母 {value.allocation_input.goals.length}，原固定正额子动作 {value.children.length}，完整保护节点 {value.allocation_input.hard_protection_points.length}。原准备 {value.prepared_at} → {value.expires_at}。</p><p>仅当前月联合规划；整组未保留资金，子动作沿原资金占用。跨操作原子性与整体回滚不可用；未到账未来收入不计今日资金。</p><ol>{value.children.map((child) => <li key={child.action_id}><strong>固定子 {child.child_number} · ¥{formatMoneyCents(child.command.effect.amount_cents)}</strong><dl><dt>目标 / 当前原版本</dt><dd>{child.goal_id} / {child.command.effect.policy_version_id}</dd><dt>原 action / 银行键</dt><dd>{child.action_id} / {child.bank_idempotency_key}</dd><dt>经济效果 hash</dt><dd>{child.command.effect_hash}</dd><dt>原模型证据 / hash</dt><dd>{child.original_model_evidence_id} / {child.original_model_evidence_hash}</dd></dl><Raw value={child.command} label={`固定子${child.child_number}的完整现金/收入来源与命令`} /></li>)}</ol><Raw value={value} label="完整原计划、全部目标/1098保护/联合输入输出" /></section>;
}
function Original({ value }: { value: JointResponse }) {
  return <section aria-label="联合持久原状态"><h3>原服务状态 {value.state}</h3><Plan value={value.original_plan} /><p>原准备请求 hash {value.original_request_hash}。原整组确认 {value.original_consent?.current_evidence_status ?? 'NOT_RECORDED'}；确认只绑定原整组，不表示当前持续银行权限。</p>{value.original_consent && <><p>确认原键 {value.original_consent.original_request.idempotency_key} · 原确认请求 hash {value.original_consent.request_hash} · 原证据 {value.original_consent.evidence_id}</p><Raw value={value.original_consent} label="原整组body、Signed USER身份与保留确认证据" /></>}<ul>{value.children.map((child) => <li key={child.action_id}>固定子 {child.child_number} · {child.state} · 原行动 {child.original_action?.status ?? 'MISSING'} · 银行 {child.original_action?.bank_status ?? '未观察/未知'} · 银行原键 {child.bank_idempotency_key}{child.original_action?.receipt && <p>原回执 {child.original_action.receipt.receipt_id} · 已执行 ¥{formatMoneyCents(child.original_action.receipt.executed_cents)}</p>}</li>)}</ul>{value.state === 'UNRESOLVED' && <p>前子 UNKNOWN / SUBMITTED：保留原父计划、action和银行键，后子停止。先独立GET，再明确恢复同一固定子。</p>}<Raw value={value} label="联合所有子状态与原JSON响应" /></section>;
}
/** Thin GET-only recovery stays outside parent financial fieldsets. No USER
 * cookie, current list/version or POST is needed to read retained originals. */
export function FullJointGoalOriginalRecoveryPanel() {
  const operation = useFullJointGoalOperation(); const [id, setId] = useState(''), [value, setValue] = useState<JointResponse | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { void recoverFullJointGoalOperation(); }, []);
  async function readOriginal() { if (busy || operation.busy || operation.recovering || !id) return; setBusy(true); setError(''); try { const r = await getJointExecution(id, undefined, operation.workspace_reference?.plan_id === id ? operation.workspace_reference : null); if (!operation.pending && (!operation.workspace_reference || operation.workspace_reference.plan_id === r.original_plan.plan_id)) await acceptFullJointGoalWorkspaceRead(r); setValue(r); } catch (cause) { setValue(null); setError(errorMessage(cause)); } finally { setBusy(false); } }
  async function recover() { const intent = operation.pending; if (!intent || busy || operation.busy || operation.recovering) return; setBusy(true); setError(''); try { const r = intent.kind === 'EXECUTE' ? await getJointExecution(intent.plan_id!, intent.user_id, intent.reviewed_plan) : await lookupJointExecution(intent); const result = await acceptFullJointGoalRead(intent, r); setValue(result.original); if (result.original) setId(result.original.original_plan.plan_id); setNotice(result.complete ? '原请求与独立GET原结果完整匹配；保留原父计划，未自动推进。' : '原结果仍未决/未找到；保留同一body/父子身份/原键，只读查询未发送金融动作。'); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  return <section className="card" aria-label="联合原请求只读恢复"><h3>联合原件 · 只读恢复</h3>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <><Raw value={operation.pending} label="持久完整原联合请求与全部固定子定位" /><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void recover()}>独立读取原联合请求结果</button></>}
    {operation.workspace_reference && !operation.original && <section aria-label="恢复联合原定位"><p>已恢复原父计划 {operation.workspace_reference.plan_id} 的全部固定子定位，尚无本次服务器完整读取；小定位不表示已review或授权。</p><button type="button" disabled={busy || operation.busy} onClick={() => setId(operation.workspace_reference!.plan_id)}>填入原父计划读取ID</button></section>}
    <label>读取既存原父计划ID<input value={id} disabled={busy || operation.busy} onChange={(e) => setId(e.target.value)} /></label><button type="button" disabled={busy || operation.busy || operation.recovering || !id} onClick={() => void readOriginal()}>只读读取原联合计划</button>
    {value && <><p>只读原状态 {value.state} · 原父计划 {value.original_plan.plan_id} · 原owner {value.user_id} · 原周期 {value.epoch_id}。</p><Raw value={value} label="只读完整服务器原封套、所有子与1098保护" /></>}
    <p>本区域只GET，不确认/执行/重试POST。原receipt不是当前权限，NOT_FOUND_NOT_FINAL不释放原请求；未知子只能在完整核对后由主执行界面明确恢复同一原子。</p>
  </section>;
}
export default function FullJointGoalExecutionPanel({ userId, epochId, mutationBlocked = false, showOriginalRecovery = false }: { userId?: string; epochId?: string; mutationBlocked?: boolean; showOriginalRecovery?: boolean }) {
  const operation = useFullJointGoalOperation(), writing = useWriteInFlight();
  const [sources, setSources] = useState<Sources | null>(null), [policyId, setPolicyId] = useState(''), [preview, setPreview] = useState<JointPreview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [reviewed, setReviewed] = useState(false), [executeAccepted, setExecuteAccepted] = useState(false), [resumeAccepted, setResumeAccepted] = useState(false);
  useEffect(() => { void recoverFullJointGoalOperation(); }, []);
  const original = operation.original;
  const blocked = mutationBlocked || writing || busy || operation.busy || operation.recovering || !!operation.pending || !!operation.storage_error;
  const policy = sources?.policies.items.find((p) => p.policy_id === policyId);
  const sameEpoch = !!original && (!userId || userId === original.user_id) && (!epochId || epochId === original.epoch_id) && (!sources || sources.epoch_id === original.epoch_id);
  const next = original?.children.find((c) => c.state !== 'ORIGINAL_RECEIPT_VERIFIED');
  const ownRead = !!original && operation.original === original;
  const canConfirm = !blocked && ownRead && sameEpoch && original?.state === 'PREPARED_UNRESERVED' && original.original_consent === null;
  const canExecute = !blocked && ownRead && sameEpoch && original && next && (['UNKNOWN', 'SUBMITTED'].includes(next.state) || original.original_consent?.current_evidence_verified && ['PLANNED_UNRESERVED', 'AUTHORIZED'].includes(next.state)) && !['STOPPED', 'RETAINED_HISTORY', 'PARTIALLY_PREPARED'].includes(original.state);
  function invalidate() { setPreview(null); setReviewed(false); setExecuteAccepted(false); }
  async function readSources() {
    if (busy || operation.busy) return; setBusy(true); setError(''); invalidate(); setSources(null);
    try { const [accounts, demo, policies] = await Promise.all([getAccounts(), getDemoState(), getFullPolicies()]); if (!demo.available || !demo.epoch_id || userId && accounts.user_id !== userId || epochId && demo.epoch_id !== epochId) throw new Error('实际可用周期或当前owner缺失，不创建联合计划。'); setSources({ user_id: accounts.user_id, epoch_id: demo.epoch_id, policies }); setPolicyId(''); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readPreview() {
    if (blocked || !sources || !policy) return; setBusy(true); setError(''); invalidate();
    try { const body = parseJointPrepare({ full_policy_id: policy.policy_id, expected_full_policy_version_id: policy.current_version.version_id, expected_epoch_id: sources.epoch_id, idempotency_key: `joint-prepare-${crypto.randomUUID()}` }); setPreview(await previewJointExecution(body, sources.user_id)); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function send(intent: JointIntent) {
    await beginFullJointGoalOperation(intent, mutationBlocked); setResumeAccepted(false);
    try { await postJointExecution(intent); setNotice('原请求已返回，仍保留完整原件；请独立GET核对，不自动确认或推进后子。'); }
    finally { endFullJointGoalAttempt(); }
  }
  async function prepare() { if (blocked || preview?.status !== 'READY_TO_REVIEW' || !preview.plan) return; setBusy(true); setError(''); try { await send(await prepareJointIntent('PREPARE', preview.user_id, preview.request)); setPreview(null); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  async function finishPrepare() { if (blocked || !sameEpoch || !original || original.state !== 'PARTIALLY_PREPARED') return; setBusy(true); setError(''); try { await send(await prepareJointIntent('PREPARE', original.user_id, original.original_plan.inputs.request)); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  async function confirm() { if (!canConfirm || !reviewed || !original) return; setBusy(true); setError(''); try { await send(await prepareJointIntent('CONFIRM', original.user_id, { accepted: true, reviewed_plan_hash: original.original_plan.plan_hash, expected_epoch_id: original.epoch_id, idempotency_key: `joint-confirm-${crypto.randomUUID()}` }, original.original_plan)); setReviewed(false); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  async function execute() { if (!canExecute || !executeAccepted || !original || !next) return; setBusy(true); setError(''); try { await send(await prepareJointIntent('EXECUTE', original.user_id, { accepted: true, reviewed_plan_hash: original.original_plan.plan_hash, expected_epoch_id: original.epoch_id, expected_child_number: next.child_number, expected_action_id: next.action_id }, original.original_plan)); setExecuteAccepted(false); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  async function resume() { if (!operation.pending || !operation.pending_read_verified || !resumeAccepted || busy || mutationBlocked || writing || operation.busy || operation.storage_error) return; setBusy(true); setError(''); try { await send(operation.pending); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  return <section className="card" aria-label="联合目标固定批次明确执行"><h2>联合目标 · 固定整组与逐子模拟执行</h2><p>金额、目标范围、原收入来源及365天保护均由服务器重算。本页不输入金额、银行结果、权限或时钟。每次写入都独立读取当前 Signed USER 身份，服务端再次验证；显示身份不授资金权。</p>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="待核对原联合请求"><h3>原 {operation.pending.kind} 尚待核对</h3><Raw value={operation.pending} label="POST前保存完整body/hash/父计划与固定子" />{operation.pending_read_verified && <><label><input type="checkbox" checked={resumeAccepted} disabled={mutationBlocked || busy || writing || !!operation.storage_error} onChange={(e) => setResumeAccepted(e.target.checked)} />明确恢复同一完整原请求，不换键或推进后子</label><button type="button" disabled={!resumeAccepted || mutationBlocked || busy || writing || !!operation.storage_error} onClick={() => void resume()}>手动恢复同一原联合请求</button></>}<p>NOT_FOUND_NOT_FINAL不是未提交证明。成功、4xx、解析或网络错误均不盲清门；刷新不会自动POST。</p></section>}
    {showOriginalRecovery && <FullJointGoalOriginalRecoveryPanel />}
    <button type="button" disabled={busy || operation.busy} onClick={() => void readSources()}>只读加载现有联合策略与周期</button>{sources && <><p>实际用户 {sources.user_id} · 周期 {sources.epoch_id}；列表是独立快照，正式提交由服务器重核。</p><fieldset disabled={blocked || !!operation.workspace_reference}><label>现有联合目标规划策略<select value={policyId} onChange={(e) => { setPolicyId(e.target.value); invalidate(); }}><option value="">请选择现有策略</option>{sources.policies.items.filter((p) => p.template_name === 'GoalAllocationPolicy').map((p) => <option key={p.policy_id} value={p.policy_id}>{p.name ?? p.policy_id} · {p.effective_status} · v{p.current_version.version_number}</option>)}</select></label><button type="button" disabled={!policy} onClick={() => void readPreview()}>读取服务器联合固定计划预览</button></fieldset></>}
    {preview && <section aria-label="联合固定计划只读预览"><h3>预览 {preview.status}</h3><p>实际目标分母 {preview.registered_goal_ids.length}；未决原行动 {preview.unresolved_original_action_ids.length}。</p>{preview.plan && <Plan value={preview.plan} />}{preview.reasons.map((r) => <p key={r}>{r}</p>)}{preview.limitations.map((r) => <p key={r}>{r}</p>)}<Raw value={preview} label="完整联合预览原响应" /><button type="button" disabled={blocked || preview.status !== 'READY_TO_REVIEW' || !preview.plan} onClick={() => void prepare()}>明确保存原联合候选（尚未整组确认）</button></section>}
    {original && <><Original value={original} /><fieldset disabled={blocked}>{original.state === 'PARTIALLY_PREPARED' && <button type="button" disabled={!sameEpoch} onClick={() => void finishPrepare()}>手动以原准备body/key补齐原缺失子</button>}<label><input type="checkbox" checked={reviewed} disabled={!canConfirm} onChange={(e) => setReviewed(e.target.checked)} />我复核全部目标、原金额、资金来源、1098保护及整组hash，明确确认整组</label><button type="button" disabled={!canConfirm || !reviewed} onClick={() => void confirm()}>提交原联合整组明确确认</button><label><input type="checkbox" checked={executeAccepted} disabled={!canExecute} onChange={(e) => setExecuteAccepted(e.target.checked)} />我明确执行固定子 {next?.child_number ?? '无'}，使用原action/hash/银行键</label><button type="button" disabled={!canExecute || !executeAccepted} onClick={() => void execute()}>仅执行这个固定原子</button></fieldset></>}
    <p>ORIGINAL_SERVICE_RECEIPTS_VERIFIED只表示服务器核验原服务回执，economic_experiment_verified=false。周节奏、多期全局最优、跨子原子性、独立经济验证未证明；停止/封存未完成的原计划继续保留。</p>
  </section>;
}
