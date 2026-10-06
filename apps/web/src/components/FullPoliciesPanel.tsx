import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fullTemplateNames, getFullPolicies, getFullPolicy, getFullPolicyCommands, getFullPolicyVersions, getFullTemplateCatalog, getFullTemplateSchema, getOriginalFullPolicyResponse, lookupFullPolicyCommand, previewFullPolicyChange, sendFullPolicyCommand, validateFullPolicyCandidate } from '../api/full-policies';
import type { FullPolicy, FullPolicyPreview, FullPolicyReceipt, TemplateCandidate, TemplateName } from '../api/full-policies';
import { errorMessage } from '../api/http';
import { beginFullPolicyOperation, clearFullPolicyOperationAfterLookup, endFullPolicyAttempt, prepareFullPolicyIntent, recoverFullPolicyOperation, useFullPolicyOperation } from '../features/full-policy-operation';
import type { FullPolicyIntent } from '../features/full-policy-operation';
import { object } from '../features/policy-form';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';
import { getAccounts } from '../api/goals';
import { getDemoState } from '../api/demo';
import FixedPaymentRelationPanel from './FixedPaymentRelationPanel';
import FixedPaymentOriginalRecoveryPanel from './FixedPaymentOriginalRecoveryPanel';
import FullPolicyCompilerPanel from './FullPolicyCompilerPanel';
import FullPolicyFinancialImpactHost from './FullPolicyFinancialImpactHost';
import FullPolicyDependencyPanel from './FullPolicyDependencyPanel';
import FullRecoveryExecutionHost from './FullRecoveryExecutionHost';
import { FullRecoveryOriginalRecoveryPanel } from './FullRecoveryExecutionPanel';
import type { FullCompiledDraft } from './FullPolicyCompilerPanel';
import SeasonalReserveAdoptionPanel from './SeasonalReserveAdoptionPanel';
import { releaseUUID } from '../api/goal-release-authorizations';

type Props = { blocked?: boolean; paymentMutationBlocked?: boolean; recoveryMutationBlocked?: boolean; seasonalMutationBlocked?: boolean; maturityMutationBlocked?: boolean };
type Send = (intent: FullPolicyIntent) => Promise<void>;
const money = (value: number | null) => value === null ? 'UNKNOWN · 尚未证明' : `¥${formatMoneyCents(value)}`;
const key = () => `full-policy-ui:${crypto.randomUUID()}`;
function configuration(text: string): Record<string, unknown> { const value: unknown = JSON.parse(text); if (!object(value)) throw new Error('候选必须是完整JSON对象'); return value; }
function Raw({ value, title }: { value: object; title: string }) { return <details><summary>{title}</summary><pre className="readonly-raw">{getOriginalFullPolicyResponse(value) ?? '原响应文本未保留'}</pre></details>; }
function CreateEditor({ disabled, send }: { disabled: boolean; send: Send }) {
  const [template, setTemplate] = useState<TemplateName>('DatedExpensePolicy'); const [draft, setDraft] = useState(''); const [candidate, setCandidate] = useState<TemplateCandidate | null>(null); const [reason, setReason] = useState(''); const [accepted, setAccepted] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const catalog = useQuery({ queryKey: ['full-policy-template-catalog'], queryFn: getFullTemplateCatalog, retry: false, structuralSharing: false });
  const schema = useQuery({ queryKey: ['full-policy-template-schema', template], queryFn: () => getFullTemplateSchema(template), retry: false, structuralSharing: false });
  const full = fullTemplateNames.includes(template);
  const [compiling, setCompiling] = useState(false);
  function adoptCompiledDraft(value: FullCompiledDraft) {
    if (disabled || busy) return;
    setTemplate(value.templateName);
    setDraft(JSON.stringify(value.configuration, null, 2));
    setCandidate(null);
    setAccepted(false);
    setReason('');
    setError('');
  }
  async function validate() {
    if (disabled || busy) return; setBusy(true); setError(''); setCandidate(null); setAccepted(false);
    try { const result = await validateFullPolicyCandidate(template, configuration(draft)); setCandidate(result); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!candidate || !accepted || !reason.trim() || disabled || busy || !full) return;
    try { await send(prepareFullPolicyIntent({ kind: 'CREATE', policy_id: null, original_epoch_id: null, original_configuration_hash: candidate.configuration_hash, path: '/full-policies/confirm', body: { template_name: template, configuration: candidate.normalized_configuration, reviewed_hash: candidate.configuration_hash, accepted: true, reason, idempotency_key: key() } })); }
    catch (e) { setError(errorMessage(e)); }
  }
  return <section className="full-policy-editor" aria-label="完整版策略候选与首次确认"><h4>准备新策略 · 用户明确确认</h4><p>目录和Schema仅描述候选；服务器字段校验不产生授权。首次确认保存原配置、复核hash和用户理由。</p>
    <button type="button" disabled={disabled || busy} aria-expanded={compiling} onClick={() => setCompiling(!compiling)}>{compiling ? '收起自然策略候选编译' : '打开自然策略候选编译'}</button>
    {compiling && <FullPolicyCompilerPanel mutationBlocked={disabled || busy} onCandidate={adoptCompiledDraft} />}
    {catalog.isPending && <p role="status">正在读取12类模板目录…</p>}{catalog.isError && <p role="alert">{errorMessage(catalog.error)}</p>}
    {catalog.data && <label className="field">策略模板<select value={template} disabled={disabled || busy} onChange={(event) => { setTemplate(event.target.value as TemplateName); setCandidate(null); setAccepted(false); setDraft(''); setError(''); }}>{catalog.data.templates.map((item) => <option key={item.template_name} value={item.template_name}>{item.template_name} · {fullTemplateNames.includes(item.template_name) ? '完整版独立生命周期' : item.template_name === 'LongTermGoalPolicy' ? '目标双hash确认入口' : '原MVP确认入口'}</option>)}</select></label>}
    {!full && <p className="notice">此模板沿既存合同确认：<a href={template === 'LongTermGoalPolicy' ? '#goals' : '#policies'}>{template === 'LongTermGoalPolicy' ? '转目标中心复核双hash' : '转原策略生命周期'}</a>。本面板不把候选转为独立FULL策略或银行权限。</p>}
    {schema.isPending && <p role="status">正在读取当前模板Schema…</p>}{schema.isError && <p role="alert">{errorMessage(schema.error)}</p>}{schema.data && <><p>原Schema摘要 <code>{schema.data.schema_sha256}</code>；仍需服务器跨字段与引用条件校验。</p><Raw value={schema.data} title="查看服务器完整JSON Schema原响应" /></>}
    <label className="field">新策略完整配置JSON（金额为整数分）<textarea rows={12} disabled={disabled || busy} value={draft} onChange={(event) => { setDraft(event.target.value); setCandidate(null); setAccepted(false); setError(''); }} /></label>
    <button type="button" disabled={disabled || busy || !draft.trim() || catalog.isError || !catalog.data || schema.isError || !schema.data} onClick={() => void validate()}>{busy ? '正在校验候选…' : '仅校验候选配置'}</button>
    {error && <p role="alert">{error}</p>}{candidate && <section aria-label="服务器规范化策略候选"><p>候选配置摘要 <code>{candidate.configuration_hash}</code>；引用有效性仍待正式确认服务核对，未获银行授权。</p><Raw value={candidate} title="查看规范化候选原JSON响应" /></section>}
    <label className="field">首次确认理由<input maxLength={1000} value={reason} disabled={disabled || busy} onChange={(event) => setReason(event.target.value)} /></label>
    <label className="full-policy-check"><input type="checkbox" checked={accepted} disabled={disabled || busy || !candidate || !full} onChange={(event) => setAccepted(event.target.checked)} />我已复核规范化候选和配置hash，明确确认这份新策略</label>
    <button type="button" disabled={disabled || busy || !full || !candidate || !accepted || !reason.trim()} onClick={() => void confirm()}>明确确认新策略</button>
  </section>;
}
function Preview({ data }: { data: FullPolicyPreview }) { const boundary = data.current_financial_boundary;
  return <section className="full-policy-preview" aria-label="完整版策略只读修改预览"><h5>只读预览 · 未确认</h5><p>原预期版本 <code>{data.expected_version_id}</code>；待复核配置hash <code>{data.configuration_hash}</code>。</p><p>变更字段：{data.changed_fields.join('、') || '无字段差异'}。</p>
    <dl className="full-policy-fields"><div><dt>当前实际财务边界</dt><dd>{boundary.state} · {boundary.status} · {data.as_of}</dd></div><div><dt>当前安全闲置</dt><dd>{money(boundary.safe_idle_cents)}</dd></div><div><dt>当前最小余量 / 缺口</dt><dd>{money(boundary.minimum_margin_cents)} / {money(boundary.deficit_cents)}</dd></div><div><dt>候选安全闲置变化</dt><dd>UNKNOWN · NOT_IMPLEMENTED</dd></div><div><dt>候选目标归属变化</dt><dd>UNKNOWN · NOT_IMPLEMENTED</dd></div><div><dt>候选资产本金变化</dt><dd>UNKNOWN · NOT_IMPLEMENTED</dd></div></dl>
    <p>实际引用：目标{data.relevant_goal_ids.length}、资产{data.relevant_position_ids.length}、当前行动{data.relevant_current_action_ids.length}。未实现FULL执行适配器和未来行动依赖，不能把空变化当作零影响。</p><ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul><Raw value={data} title="查看完整修改预览原JSON响应与引用快照" /></section>;
}
function PolicyWorkspace({ policy, disabled, send }: { policy: FullPolicy; disabled: boolean; send: Send }) {
  const [draft, setDraft] = useState(JSON.stringify(policy.current_version.configuration, null, 2)); const [preview, setPreview] = useState<FullPolicyPreview | null>(null); const [reason, setReason] = useState(''); const [accepted, setAccepted] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [history, setHistory] = useState(false); const [versionId, setVersionId] = useState(policy.current_version.version_id);
  const versions = useQuery({ queryKey: ['full-policy-versions', policy.policy_id, policy.current_version.version_id], queryFn: () => getFullPolicyVersions(policy.policy_id), enabled: history, retry: false, structuralSharing: false });
  const commands = useQuery({ queryKey: ['full-policy-commands', policy.policy_id, policy.current_version.version_id, policy.updated_at], queryFn: () => getFullPolicyCommands(policy.policy_id), enabled: history, retry: false, structuralSharing: false });
  const version = versions.data?.items.find((item) => item.version_id === versionId); const historical = version?.version_id !== policy.current_version.version_id; const archived = policy.reference_validation === 'ARCHIVED' || policy.effective_status === 'ARCHIVED';
  const changeable = ['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(policy.status) && !archived; const canResume = policy.status === 'SUSPENDED' && !archived;
  async function readPreview() { if (disabled || busy) return; setBusy(true); setPreview(null); setAccepted(false); setError(''); try { setPreview(await previewFullPolicyChange(policy, configuration(draft))); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } }
  async function act(kind: Exclude<FullPolicyIntent['kind'], 'CREATE'>) {
    if (disabled || busy || archived || !accepted || !reason.trim() || (kind === 'CHANGE' && !preview) || (kind === 'RESUME' && !canResume)) return;
    const common = { expected_version_id: policy.current_version.version_id, reason, idempotency_key: key() };
    const hash = kind === 'CHANGE' ? preview!.configuration_hash : policy.current_version.content_hash;
    const body = kind === 'CHANGE' ? { ...common, configuration: preview!.after_configuration, reviewed_hash: hash, accepted: true as const } : kind === 'RESUME' ? { ...common, reviewed_hash: hash, accepted: true as const } : common;
    try { await send(prepareFullPolicyIntent({ kind, policy_id: policy.policy_id, original_epoch_id: policy.epoch_id, original_configuration_hash: hash, path: `/full-policies/${policy.policy_id}/${kind.toLowerCase()}`, body })); } catch (e) { setError(errorMessage(e)); }
  }
  return <section className="full-policy-workspace" aria-label={`完整版策略详情 ${policy.policy_id}`}><h4>{policy.name} · {policy.template_name}</h4><p>持久状态 {policy.status}；当前有效状态 {policy.effective_status}；引用 {policy.reference_validation}。</p><p>规划确认{policy.planning_confirmation_valid ? '仍通过原引用条件' : '当前未通过原引用条件'}；此配置确认不授予银行执行权限，资金执行需在对应工作区复核原效果。</p>
    <dl className="full-policy-fields"><div><dt>原策略 / 审计周期</dt><dd><code>{policy.policy_id}</code> / <code>{policy.epoch_id}</code></dd></div><div><dt>原当前版本</dt><dd><code>{policy.current_version.version_id}</code></dd></div><div><dt>原配置hash</dt><dd><code>{policy.current_version.content_hash}</code></dd></div><div><dt>有效期</dt><dd>{policy.current_version.valid_from} → {policy.current_version.valid_until ?? '未设结束时点'}</dd></div><div><dt>确认摘要</dt><dd>{policy.current_version.summary}</dd></div></dl>
    {archived && <p className="notice">这是历史周期原件；只读保留，不能变成当前权限或改写原版本。</p>}<Raw value={policy} title="查看完整版策略详情原JSON响应" />
    {!archived && <FullPolicyDependencyPanel policyId={policy.policy_id} currentVersionId={policy.current_version.version_id} onReviewCurrentVersion={() => { setVersionId(policy.current_version.version_id); setHistory(true); }} />}
    <button type="button" onClick={() => setHistory(!history)}>{history ? '收起版本与原命令历史' : '读取完整版本与原命令历史'}</button>
    {history && <section aria-label="完整版策略版本与命令历史"><h5>原版本历史与连续命令</h5>{(versions.isPending || commands.isPending) && <p role="status">正在读取原历史…</p>}{versions.isError && <p role="alert">{errorMessage(versions.error)}；版本链未显示为通过。</p>}{commands.isError && <p role="alert">{errorMessage(commands.error)}；命令链未显示为通过。</p>}
      {versions.data && !versions.isError && <><label className="field">选择原策略版本<select value={version ? versionId : ''} onChange={(event) => setVersionId(event.target.value)}>{!version && <option value="">UNKNOWN · 未取得所选原版本</option>}{versions.data.items.map((item) => <option value={item.version_id} key={item.version_id}>版本 {item.version_number} · {item.version_id}</option>)}</select></label>{version ? <><p>{historical ? '历史版本，仅原确认材料，不是当前授权。' : '当前原版本。'}{version.confirmation_evidence_status === 'RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING' ? '当前证据行缺失，原确认仅保留在版本中。' : '原服务确认当前证据行匹配。'}</p><p>{version.summary} · {version.change_reason}</p><code>{version.content_hash}</code><details><summary>所选原版本结构化配置与确认</summary><pre className="readonly-raw">{JSON.stringify(version, null, 2)}</pre></details></> : <p className="notice">UNKNOWN · 版本列表缺少所选原版本，未把当前详情或另一个原版本当作已取得的历史原件。</p>}<Raw value={versions.data} title="完整版本列表原JSON响应" /></>}
      {commands.data && !commands.isError && <><ol className="full-policy-commands">{commands.data.items.map((item) => <li key={item.command_id}><p>命令 {item.command_number} · {item.kind} · {item.previous_status ?? '首次'} → {item.resulting_status}</p><dl className="full-policy-fields"><div><dt>原命令 / 请求键</dt><dd><code>{item.command_id}</code> / <code>{item.idempotency_key}</code></dd></div><div><dt>原请求hash / 回执hash</dt><dd><code>{item.request_hash}</code> / <code>{item.result_hash}</code></dd></div><div><dt>前序回执hash</dt><dd><code>{item.previous_hash ?? '首条，无前序'}</code></dd></div></dl><p>原回执不是当前授权；dedicated_audit_event=false。</p></li>)}</ol><Raw value={commands.data} title="完整命令列表原JSON响应" /></>}
      <p className="caption">列表、详情、版本和命令分别读取；不是同一事务快照，也不是独立金融效果验真。</p>
    </section>}
    <section className="full-policy-editor" aria-label="修改或停止完整版策略"><h5>用户复核后提交原版本命令</h5><p>修改与恢复创建新版本；暂停和撤销保留原版本。每次命令需理由与原预期版本，旧材料完整保留。</p>
      <label className="field">修改候选完整配置JSON（金额为整数分）<textarea rows={12} value={draft} disabled={disabled || busy || !changeable} onChange={(event) => { setDraft(event.target.value); setPreview(null); setAccepted(false); setError(''); }} /></label><button type="button" disabled={disabled || busy || !changeable || !draft.trim()} onClick={() => void readPreview()}>只读预览完整版修改</button>
      {preview && <Preview data={preview} />}{error && <p role="alert">{error}</p>}
      <FullPolicyFinancialImpactHost policyId={policy.policy_id} expectedVersionId={policy.current_version.version_id} expectedVersionNumber={policy.current_version.version_number} expectedEpochId={policy.epoch_id} candidateText={draft} blocked={disabled || busy || !changeable} />
      <label className="field">完整版命令理由<input maxLength={1000} value={reason} disabled={disabled || busy || archived} onChange={(event) => setReason(event.target.value)} /></label>
      <label className="full-policy-check"><input type="checkbox" disabled={disabled || busy || archived} checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />我已复核原版本、配置hash与操作含义，明确提交所选命令</label>
      <div className="full-policy-actions"><button type="button" disabled={disabled || busy || !changeable || !preview || !accepted || !reason.trim()} onClick={() => void act('CHANGE')}>明确确认修改</button><button type="button" disabled={disabled || busy || !['ACTIVE', 'CONFIRMED'].includes(policy.status) || archived || !accepted || !reason.trim()} onClick={() => void act('SUSPEND')}>明确暂停策略</button><button type="button" disabled={disabled || busy || archived || policy.status === 'REVOKED' || !accepted || !reason.trim()} onClick={() => void act('REVOKE')}>明确撤销策略</button><button type="button" disabled={disabled || busy || !canResume || !accepted || !reason.trim()} onClick={() => void act('RESUME')}>明确重新确认并恢复</button></div>
      <p className="caption">恢复使用当前原配置hash；不是激活未确认候选。修改预览的金额变化未知，停止回执中的行动依赖列表不代表已覆盖未来执行。</p>
    </section>
  </section>;
}
function FixedPaymentWorkspace({ policy, mutationBlocked }: { policy: FullPolicy; mutationBlocked: boolean }) {
  const accounts = useQuery({ queryKey: ['fixed-payment-host-account-owner'], queryFn: getAccounts, retry: false, structuralSharing: false });
  const demo = useQuery({ queryKey: ['fixed-payment-host-open-epoch'], queryFn: getDemoState, retry: false, structuralSharing: false });
  return <section aria-label="固定付款当前来源"><button type="button" disabled={accounts.isFetching || demo.isFetching} onClick={() => { void accounts.refetch(); void demo.refetch(); }}>只读刷新付款用户与周期</button>
    {(accounts.isPending || demo.isPending) && <p role="status">正在读取付款当前用户与周期…</p>}
    {(accounts.isError || demo.isError) && <p role="alert">{errorMessage(accounts.error ?? demo.error)}；当前用户或周期未证明，不能提交新的付款关系。</p>}
    {accounts.data && demo.data && !accounts.isError && !demo.isError && <FixedPaymentRelationPanel fullPolicy={policy} userId={accounts.data.user_id} epochId={demo.data.available ? demo.data.epoch_id : null} mutationBlocked={mutationBlocked || accounts.isFetching || demo.isFetching} />}
  </section>;
}

function SeasonalAdoptionWorkspace({ policy, mutationBlocked }: { policy: FullPolicy; mutationBlocked: boolean }) {
  const accounts = useQuery({ queryKey: ['seasonal-adoption-host-account-owner'], queryFn: getAccounts, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const demo = useQuery({ queryKey: ['seasonal-adoption-host-open-epoch'], queryFn: getDemoState, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const matched = accounts.data && demo.data && !accounts.isError && !demo.isError && releaseUUID(accounts.data.user_id) && demo.data.available === true && releaseUUID(demo.data.epoch_id) && policy.epoch_id === demo.data.epoch_id && policy.current_version.confirmation.user_id === accounts.data.user_id;
  return <section aria-label="季节采纳当前来源"><button type="button" disabled={accounts.isFetching || demo.isFetching} onClick={() => { void accounts.refetch(); void demo.refetch(); }}>只读刷新采纳用户与周期</button>
    {(accounts.isPending || demo.isPending) && <p role="status">正在读取季节采纳当前用户与OPEN周期…</p>}
    {(accounts.isError || demo.isError) && <p role="alert">{errorMessage(accounts.error ?? demo.error)}；当前用户或周期未证明，不能建立采纳范围。</p>}
    {!accounts.isPending && !demo.isPending && !accounts.isError && !demo.isError && !matched && <p>未取得当前OPEN周期及匹配用户/策略原件，未建立采纳范围。</p>}
    {matched && accounts.data && demo.data && <SeasonalReserveAdoptionPanel fullPolicy={policy} userId={accounts.data.user_id} epochId={demo.data.epoch_id} mutationBlocked={mutationBlocked || accounts.isFetching || demo.isFetching} showOriginalRecovery={false} />}
  </section>;
}

export default function FullPoliciesPanel({ blocked = false, paymentMutationBlocked = blocked, recoveryMutationBlocked = blocked, seasonalMutationBlocked = blocked, maturityMutationBlocked = blocked }: Props) {
  const client = useQueryClient(); const operation = useFullPolicyOperation(); const writing = useWriteInFlight(); const [selected, setSelected] = useState<string | null>(null); const [creating, setCreating] = useState(false); const [error, setError] = useState(''); const [lookupMessage, setLookupMessage] = useState(''); const [lookupBusy, setLookupBusy] = useState(false); const [replayAccepted, setReplayAccepted] = useState(false); const [receipt, setReceipt] = useState<FullPolicyReceipt | null>(null);
  useEffect(() => { recoverFullPolicyOperation(); }, []);
  const list = useQuery({ queryKey: ['full-policies'], queryFn: getFullPolicies, retry: false, structuralSharing: false });
  const detail = useQuery({ queryKey: ['full-policy', selected], queryFn: () => getFullPolicy(selected!), enabled: selected !== null, retry: false, structuralSharing: false });
  const disabled = blocked || operation.busy || operation.pending !== null || operation.storage_error !== null || writing || lookupBusy;
  async function send(intent: FullPolicyIntent) {
    setError(''); setReceipt(null); setLookupMessage(''); setReplayAccepted(false);
    beginFullPolicyOperation(intent, blocked);
    try { const result = await sendFullPolicyCommand(intent); setReceipt(result); setLookupMessage('收到原服务回执；原请求仍待手动只读核对。回执不代表当前授权。'); }
    catch (e) { setError(`${errorMessage(e)}；原请求与键已保留，禁止换键重提。`); }
    finally { endFullPolicyAttempt(); }
  }
  async function lookup() {
    const original = operation.pending; if (!original || operation.busy || lookupBusy) return; setLookupBusy(true); setError(''); setLookupMessage('');
    try { const value = await lookupFullPolicyCommand(original); if (value.status === 'NOT_FOUND') setLookupMessage('当前只读快照未找到原键：NOT_FOUND不是最终未提交证明，原请求仍保留。');
      else { clearFullPolicyOperationAfterLookup(original, value); setReceipt(value.command!.result as FullPolicyReceipt); setLookupMessage('已核对服务原命令、原body、原键与回执身份；已解除本族待核对门。当前权限仍需读取当前策略。'); setSelected(value.command!.policy_id); setCreating(false); await client.invalidateQueries({ queryKey: ['full-policies'] }); await client.invalidateQueries({ queryKey: ['full-policy'] }); }
    } catch (e) { setError(`${errorMessage(e)}；原请求继续保留。`); } finally { setLookupBusy(false); }
  }
  return <section className="full-policies-panel readonly-section" aria-label="完整版策略生命周期"><h3>完整版策略 · 原配置与命令生命周期</h3><p>8类独立FULL策略已接原服务，12类目录保留既存确认入口。策略回执本身不提供银行权限；固定收款与资产组合另需原权限、明确确认和当前资金重验。</p>
    <FixedPaymentOriginalRecoveryPanel />
    <FullRecoveryOriginalRecoveryPanel />
    {blocked && <p className="notice">其他族原请求正在处理或待核对，禁止新的策略写入；本族只读原命令查询仍可用。</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{lookupMessage && <p role="status">{lookupMessage}</p>}
    {operation.pending && <section className="full-policy-pending" aria-label="待核对的完整版原请求"><h4>原请求待核对 · {operation.pending.kind}</h4><p>原键 <code>{operation.pending.body.idempotency_key}</code>；原策略 <code>{operation.pending.policy_id ?? '首次创建，身份由服务生成'}</code>。本地记录没有授权效力。</p><details><summary>查看不可改写的原请求body与恢复身份</summary><pre className="readonly-raw">{JSON.stringify(operation.pending, null, 2)}</pre></details>
      <button type="button" disabled={operation.busy || lookupBusy} onClick={() => void lookup()}>只读核对原命令</button><label className="full-policy-check"><input type="checkbox" disabled={operation.busy || lookupBusy || blocked || writing || operation.storage_error !== null} checked={replayAccepted} onChange={(event) => setReplayAccepted(event.target.checked)} />我已核对原请求，明确仅重放同一body和同一键</label><button type="button" disabled={!replayAccepted || operation.busy || lookupBusy || blocked || writing || operation.storage_error !== null} onClick={() => { const original = operation.pending; if (original) void send(original).catch((e) => setError(errorMessage(e))); }}>手动重放同一原请求</button>
      <p>不会自动重试或新增行动。即使服务器拒绝且查询未找到，仍不能用NOT_FOUND清除原请求；缺少最终拒绝证明时需保留并核查。</p></section>}
    {receipt && <section aria-label="完整版策略原服务回执"><h4>原服务回执 · {receipt.status}</h4><p>原命令 {receipt.command_number} · <code>{receipt.command_id}</code>；原版本 <code>{receipt.version_id}</code>；配置hash <code>{receipt.configuration_hash}</code>。</p><p>receipt_is_current_authority=false；需重新读取当前状态，不能据此执行资金。行动依赖尚未实现。</p><details><summary>查看原服务回执结构</summary><pre className="readonly-raw">{JSON.stringify(receipt, null, 2)}</pre></details></section>}
    <div className="full-policy-actions"><button type="button" disabled={list.isFetching || operation.busy || lookupBusy} onClick={() => { void list.refetch(); if (selected) void detail.refetch(); }}>只读刷新完整版策略</button><button type="button" disabled={disabled} onClick={() => setCreating(!creating)}>{creating ? '收起新策略候选' : '准备新完整版策略'}</button></div>
    {list.isPending && <p role="status">正在读取原完整版策略…</p>}{list.isError && <p role="alert">{errorMessage(list.error)}</p>}{list.data && !list.isError && <><ul className="full-policy-list">{list.data.items.map((item) => <li key={item.policy_id}><button type="button" aria-pressed={item.policy_id === selected} onClick={() => { setSelected(item.policy_id); setCreating(false); }}>{item.name} · {item.template_name} · {item.effective_status}</button><p>原版本 {item.current_version.version_number} · 引用 {item.reference_validation}</p></li>)}</ul>{list.data.items.length === 0 && <p>当前未登记独立FULL策略；目录候选不等于已确认策略。</p>}<Raw value={list.data} title="完整版策略列表原JSON响应" /></>}
    {creating && <CreateEditor disabled={disabled} send={send} />}{selected && detail.isPending && <p role="status">正在读取所选原策略…</p>}{selected && detail.isError && <p role="alert">{errorMessage(detail.error)}；不把旧详情作为当前成功。</p>}{selected && detail.data && !detail.isError && <PolicyWorkspace key={`${detail.data.policy_id}:${detail.data.current_version.version_id}:${detail.data.status}:${detail.data.updated_at}`} policy={detail.data} disabled={disabled || detail.isFetching} send={send} />}
    {selected && detail.data && !detail.isError && detail.data.template_name === 'RecoveryPolicy' && <FullRecoveryExecutionHost key={`${detail.data.policy_id}:${detail.data.current_version.version_id}`} policy={detail.data} mutationBlocked={recoveryMutationBlocked || detail.isFetching} maturityMutationBlocked={maturityMutationBlocked || detail.isFetching} />}
    {selected && detail.data && !detail.isError && detail.data.template_name === 'PeriodicTransferPolicy' && <FixedPaymentWorkspace key={detail.data.policy_id} policy={detail.data} mutationBlocked={paymentMutationBlocked || detail.isFetching} />}
    {selected && detail.data && !detail.isError && detail.data.template_name === 'SeasonalReservePolicy' && <SeasonalAdoptionWorkspace key={detail.data.policy_id} policy={detail.data} mutationBlocked={seasonalMutationBlocked || detail.isFetching} />}
  </section>;
}
