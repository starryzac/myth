import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { confirmFullGoalModel, getFullGoalModel, getOriginalFullGoalResponse, lookupFullGoalCommand, parseFullGoalConfiguration, parseFullGoalPreview, previewFullGoalModel } from '../api/full-goals';
import type { FullGoalConfiguration, FullGoalLookup, FullGoalModel, FullGoalPreview, FullGoalReceipt, GoalModelBinding } from '../api/full-goals';
import type { Goal } from '../api/goals';
import { errorMessage } from '../api/http';
import { object } from '../features/policy-form';
import { formatMoneyCents } from '../features/money';
import { useWriteInFlight } from '../features/write-flight';
import { beginFullGoalOperation, clearFullGoalOperationAfterLookup, endFullGoalAttempt, fullGoalIntentHashMatches, prepareFullGoalIntent, recoverFullGoalOperation, useFullGoalOperation } from '../features/full-goal-operation';
import type { FullGoalIntent } from '../features/full-goal-operation';

type ModelGoal = Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'>;
const money = (amount: number | null) => amount === null ? 'UNKNOWN · 尚未证明' : `¥${formatMoneyCents(amount)}`;
function Configuration({ config }: { config: FullGoalConfiguration }) {
  return <dl className="full-goal-fields"><div><dt>目标金额</dt><dd>{money(config.target_cents)}</dd></div><div><dt>目标日期</dt><dd>{config.deadline}</dd></div>
    <div><dt>月度最低 / 建议 / 最高</dt><dd>{money(config.monthly_contribution.min_cents)} / {money(config.monthly_contribution.target_cents)} / {money(config.monthly_contribution.max_cents)}</dd></div><div><dt>重要程度</dt><dd>{config.importance}</dd></div><div><dt>最低保障额</dt><dd>{money(config.minimum_guarantee_cents)}</dd></div>
    <div><dt>允许部分满足</dt><dd>{config.allow_partial ? '允许' : '不允许'}</dd></div><div><dt>允许延期</dt><dd>{config.allow_deferral ? '允许' : '不允许'}</dd></div><div><dt>延期成本 / 日</dt><dd>{money(config.deferral_cost_cents_per_day)}</dd></div>
    <div><dt>资产策略原引用</dt><dd>{config.asset_policy_id ? <code>{config.asset_policy_id}</code> : '未设置，不推定资产权限'}</dd></div><div><dt>跨目标回拨</dt><dd>不允许；独立确认服务尚未实现</dd></div><div><dt>有效期</dt><dd>{config.valid_from ?? '未设置起始日'} → {config.valid_until ?? '未设置结束日'}</dd></div></dl>;
}
function Preview({ data }: { data: FullGoalPreview }) {
  const impact = data.base_policy_impact;
  return <section className="full-goal-preview" aria-label="完整目标只读预览结果"><h4>只读预览，不是确认或生效</h4><Configuration config={parseFullGoalConfiguration(data.full_configuration)} />
    <dl className="full-goal-fields"><div><dt>待复核FULL配置摘要</dt><dd><code>{data.full_configuration_hash}</code></dd></div><div><dt>待复核原执行策略摘要</dt><dd><code>{data.base_configuration_hash}</code></dd></div><div><dt>原预期版本</dt><dd><code>{data.expected_version_id}</code></dd></div><div><dt>原预览时点</dt><dd>{impact.as_of}</dd></div></dl>
    <h4>原执行策略的财务影响</h4><div className="full-goal-impact">{[impact.before, impact.after].map((card, index) => <section key={index} aria-label={index === 0 ? '原策略预览前' : '原策略预览后'}><h5>{index === 0 ? '预览前' : '预览后（条件）'}</h5><p>{card.state} · {card.status}</p><dl className="full-goal-fields"><div><dt>安全闲置</dt><dd>{money(card.safe_idle_cents)}</dd></div><div><dt>最小余量</dt><dd>{money(card.minimum_margin_cents)}</dd></div><div><dt>缺口</dt><dd>{money(card.deficit_cents)}</dd></div></dl></section>)}</div>
    <p>安全闲置变化 {money(impact.delta_safe_idle_cents)}；最小余量变化 {money(impact.delta_minimum_margin_cents)}。</p><p className="notice">原财务影响仅由goal_saving执行策略计算；部分满足、延期成本等FULL额外字段未计入此影响。完整联合规划另读，不能把本次预览视为365日全局重算或新银行授权。</p>
    <ul>{data.notes.map((note) => <li key={note}>{note}</li>)}</ul><details><summary>完整预览原JSON响应（含财务约束与额外字段）</summary><pre className="readonly-raw">{getOriginalFullGoalResponse(data) ?? '原响应文本未保留'}</pre></details></section>;
}
function Model({ data }: { data: FullGoalModel }) {
  return <><dl className="full-goal-fields"><div><dt>原执行策略版本</dt><dd><code>{data.base_policy_version_id}</code></dd></div><div><dt>原模型审计周期</dt><dd><code>{data.epoch_id}</code></dd></div></dl>
    {data.status === 'MODEL_MISSING' ? <p className="notice">UNKNOWN · MODEL_MISSING：当前原版本缺少已确认FULL模型。额外属性与联合规划不可用，不能回落旧模型、填零或当作已完成配置。</p> : <><p>原服务报告模型 VERIFIED · 原策略 {data.policy_effective_status}。此模型不提供银行授权。</p><Configuration config={parseFullGoalConfiguration(data.full_configuration)} />
      <dl className="full-goal-fields"><div><dt>原完整配置hash</dt><dd><code>{data.full_configuration_hash}</code></dd></div><div><dt>原执行配置hash</dt><dd><code>{data.base_configuration_hash}</code></dd></div><div><dt>原确认时点</dt><dd>{data.confirmed_at}</dd></div><div><dt>原确认模型证据</dt><dd><a href={`#evidence/EVIDENCE/${data.evidence_id}`}>{data.evidence_id}</a></dd></div><div><dt>原模型证据hash</dt><dd><code>{data.evidence_hash}</code></dd></div></dl></>}
    <p className="caption">bank_authority=false；专用模型审计事件尚未实现。服务报告校验不等于独立银行效果验真。列表、归属与模型分开读取，不能组成一次事务快照。</p>
    <details><summary>查看完整目标模型原JSON响应</summary><pre className="readonly-raw">{getOriginalFullGoalResponse(data) ?? '原响应文本未保留'}</pre></details></>;
}
function Workspace({ goal, disabled, send, reviewCandidate }: { goal: ModelGoal; disabled: boolean; send: (intent: FullGoalIntent) => Promise<void>; reviewCandidate: FullGoalPreview | null }) {
  const binding: GoalModelBinding = goal; const [draft, setDraft] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [preview, setPreview] = useState<FullGoalPreview | null>(null); const [reason, setReason] = useState(''); const [accepted, setAccepted] = useState(false); const writeBusy = useWriteInFlight();
  const query = useQuery({ queryKey: ['full-goal-model', goal.id, goal.policy_id, goal.policy_version_id], queryFn: () => getFullGoalModel(binding), retry: false, structuralSharing: false });
  const generation = useRef(0); const busyRef = useRef(false); const firstRead = useRef<number | null>(null); const [candidateDismissed, setCandidateDismissed] = useState(false); const [previewReadAt, setPreviewReadAt] = useState<number | null>(null); const [editorOpen, setEditorOpen] = useState(false);
  useEffect(() => {
    if (firstRead.current !== null && (query.isFetching || query.dataUpdatedAt !== firstRead.current)) { generation.current++; setCandidateDismissed(true); setPreview(null); setAccepted(false); setReason(''); }
    if (!query.isFetching && !query.isPending && query.data) firstRead.current = query.dataUpdatedAt;
  }, [query.isFetching, query.isPending, query.data, query.dataUpdatedAt]);
  let candidateError = ''; let candidate: FullGoalPreview | null = null;
  if (reviewCandidate && !candidateDismissed) try {
    candidate = parseFullGoalPreview(reviewCandidate, binding);
    if (query.data && candidate.epoch_id !== query.data.epoch_id) throw new Error('修复候选的原周期与当前完整目标不一致');
  } catch (e) { candidateError = errorMessage(e); candidate = null; }
  const freshPreview = preview && previewReadAt === query.dataUpdatedAt && !query.isError && !query.isFetching ? preview : null;
  async function readPreview(configuration: Record<string, unknown>) {
    if (busyRef.current || writeBusy || disabled || query.isError || query.isFetching || query.isPending) return;
    const expectedGeneration = ++generation.current; const readAt = query.dataUpdatedAt; busyRef.current = true; setError(''); setPreview(null); setAccepted(false); setReason(''); setBusy(true);
    try { const value = await previewFullGoalModel(binding, configuration); if (query.data && value.epoch_id !== query.data.epoch_id) throw new Error('新预览的原周期与当前完整目标不一致，请重新读取'); if (generation.current === expectedGeneration) { setPreview(value); setPreviewReadAt(readAt); } }
    catch (e) { if (generation.current === expectedGeneration) setError(errorMessage(e)); } finally { busyRef.current = false; setBusy(false); }
  }
  async function submit() {
    if (busyRef.current || writeBusy || disabled) return;
    setPreview(null); setAccepted(false); setReason(''); setError('');
    try { const configuration: unknown = JSON.parse(draft); if (!object(configuration)) throw new Error('候选必须是完整JSON对象'); await readPreview(configuration); }
    catch (e) { setError(errorMessage(e)); }
  }
  async function applyRepairCandidate() {
    if (!candidate || disabled || query.isError || query.isFetching || query.data?.status !== 'VERIFIED') return;
    setDraft(JSON.stringify(candidate.full_configuration, null, 2)); setPreview(null); setAccepted(false); setReason(''); setEditorOpen(true);
    await readPreview(candidate.full_configuration);
  }
  function refreshModel() {
    generation.current++; setCandidateDismissed(true); setDraft(''); setPreview(null); setAccepted(false); setReason(''); void query.refetch();
  }
  async function confirm() {
    if (!freshPreview || !accepted || !reason.trim() || busy || writeBusy || disabled || query.isError || query.isFetching) return; setBusy(true); setError('');
    try { const original = await prepareFullGoalIntent({ user_id: freshPreview.base_policy_impact.user_id, goal_id: goal.id, policy_id: goal.policy_id, body: { expected_version_id: freshPreview.expected_version_id, expected_epoch_id: freshPreview.epoch_id, configuration: freshPreview.full_configuration, reviewed_full_hash: freshPreview.full_configuration_hash, reviewed_base_hash: freshPreview.base_configuration_hash, accepted: true, reason, idempotency_key: `full-goal:${crypto.randomUUID()}` } }); await send(original); setAccepted(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <section className="full-goal-model readonly-section" aria-label={`完整目标模型 ${goal.id}`}><h4>完整目标属性 · {goal.name}</h4><button type="button" disabled={query.isFetching || busy} onClick={refreshModel}>只读刷新完整模型</button>
    {query.isPending && <p role="status">正在读取原版本完整目标模型…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}；需刷新原目标和当前版本，未把旧模型作为当前成功。</p>}
    {query.isFetching && query.data && <p role="status">正在重新读取，下方仍为上次原模型。</p>}
    {!query.isError && query.data && <Model data={query.data} />}
    {reviewCandidate && <section aria-label="修复候选进入原完整目标确认"><h4>来自只读修复预览的完整候选</h4><p>尚未采用或确认。原目标、策略、版本和周期必须与当前读取一致；使用后重新服务器预览，不沿用旧候选的经济影响或确认。</p>{candidateError && <p role="alert">{candidateError}；旧候选不能使用。</p>}{candidateDismissed && <p role="status">读取已刷新，旧修复候选已清除；请重新读取冲突与修复。</p>}<button type="button" disabled={!candidate || busy || writeBusy || disabled || query.isFetching || query.isError || query.data?.status !== 'VERIFIED'} onClick={() => void applyRepairCandidate()}>使用修复候选并重新只读预览</button></section>}
    <details className="full-goal-preview-editor" open={editorOpen || undefined}><summary>准备完整目标的只读影响预览</summary><p>输入候选long_term_goal配置，先调用服务器只读预览。预览不保存模型、不确认双hash、不执行资金；之后需复核完整规范化配置、双hash及理由，独立明确确认。</p>
      {query.data?.status === 'VERIFIED' && !query.isError && <button type="button" disabled={busy || writeBusy || disabled} onClick={() => { setDraft(JSON.stringify(query.data!.full_configuration, null, 2)); setPreview(null); setAccepted(false); setError(''); }}>使用当前原模型作为候选</button>}
      <label className="field">候选完整模型JSON（金额为整数分）<textarea rows={12} value={draft} disabled={busy || disabled} onChange={(event) => { generation.current++; setDraft(event.target.value); setPreview(null); setAccepted(false); setReason(''); setError(''); }} placeholder="填写完整long_term_goal候选；缺模型时不自动猜测属性" /></label>
      <button type="button" disabled={busy || writeBusy || disabled || !draft.trim() || query.isError || query.isPending} onClick={() => void submit()}>{busy ? '正在只读预览…' : '只读预览候选影响'}</button>
      {error && <p role="alert">{error}</p>}{freshPreview && <><Preview data={freshPreview} /><section className="full-goal-confirmation" aria-label="完整目标双hash明确确认"><h4>独立明确确认完整目标</h4><p>确认保留原模型/证据并创建原策略新版本。只发送上方服务器规范化完整配置、原周期/版本和双hash；这不是执行资金或独立银行授权。</p><label className="field">完整目标确认理由<input maxLength={1000} value={reason} disabled={busy || disabled} onChange={(event) => { setReason(event.target.value); setAccepted(false); }} /></label><label className="full-goal-check"><input type="checkbox" checked={accepted} disabled={busy || disabled || !reason.trim()} onChange={(event) => setAccepted(event.target.checked)} />我已复核规范化完整配置、FULL与原执行策略两份hash，明确确认此目标</label><button type="button" disabled={busy || disabled || writeBusy || !accepted || !reason.trim() || query.isError || query.isFetching} onClick={() => void confirm()}>明确确认完整目标双hash</button><p>编辑JSON、重新预览、刷新或切换原版本会清除本次复核；跨目标回拨仍默认不允许。</p></section></>}
    </details></section>;
}
export default function FullGoalModelPanel({ goal, blocked = false, reviewCandidate = null }: { goal: ModelGoal; blocked?: boolean; reviewCandidate?: FullGoalPreview | null }) {
  const client = useQueryClient(); const operation = useFullGoalOperation(); const writing = useWriteInFlight(); const [error, setError] = useState(''); const [note, setNote] = useState(''); const [lookupBusy, setLookupBusy] = useState(false); const [replayAccepted, setReplayAccepted] = useState(false); const [receipt, setReceipt] = useState<FullGoalReceipt | null>(null); const [lookup, setLookup] = useState<FullGoalLookup | null>(null);
  useEffect(() => { recoverFullGoalOperation(); }, []);
  const original = operation.pending; const own = original?.goal_id === goal.id;
  const disabled = blocked || operation.busy || original !== null || operation.storage_error !== null || writing || lookupBusy;
  async function refresh() { await Promise.all(['goals', 'policies', 'dashboard', 'policy-versions', 'full-goal-model', 'full-goal-conflicts', 'goal-allocation', 'joint-current-goal-allocation', 'full-current-goal-allocation', 'dynamic-goal-reserve', 'annual-planning', 'full-annual-protection', 'onboarding-current-goals', 'onboarding-current-policies', 'onboarding-context'].map((key) => client.invalidateQueries({ queryKey: [key] }))); }
  async function send(intent: FullGoalIntent) {
    setError(''); setNote(''); setLookup(null); setReplayAccepted(false);
    if (!await fullGoalIntentHashMatches(intent)) throw new Error('原请求摘要不匹配，未开始发送'); beginFullGoalOperation(intent, blocked);
    try { const result = await confirmFullGoalModel(intent); setReceipt(result); setNote('收到原服务回执；原请求继续保留，须独立只读按原键核对。历史回执不是当前授权。'); await refresh(); }
    catch (e) { setError(`${errorMessage(e)}；完整原body/key/双hash保留，不能换键提交。`); }
    finally { endFullGoalAttempt(); }
  }
  async function readOriginal() {
    const pending = operation.pending; if (!pending || pending.goal_id !== goal.id || operation.busy || lookupBusy) return; setLookupBusy(true); setError(''); setNote(''); setLookup(null); setReplayAccepted(false);
    try { const result = await lookupFullGoalCommand(pending); setLookup(result); if (result.status === 'NOT_FOUND') { setNote('NOT_FOUND不是最终未提交证明；原body/key继续保留，新确认和其他资金写入仍被阻挡。'); return; } await clearFullGoalOperationAfterLookup(pending, result); setReceipt(result.record!.receipt); setNote('原完整目标请求与历史回执已核对，已解除本族待核对门；当前版本和权限仍重新读取。'); await refresh(); }
    catch (e) { setError(`${errorMessage(e)}；未解除原完整目标请求。`); } finally { setLookupBusy(false); }
  }
  return <div className="full-goal-confirmation-workspace">{operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{note && <p role="status">{note}</p>}
    {original && (own ? <section className="full-goal-pending" aria-label={`待核对完整目标原请求 ${original.goal_id}`}><h4>待核对原双hash确认请求</h4><p>原目标 <code>{original.goal_id}</code>；原策略 <code>{original.policy_id}</code>；原用户 <code>{original.user_id}</code>。仅恢复元数据，没有授权效力。</p><pre className="readonly-raw">{JSON.stringify(original, null, 2)}</pre><button type="button" disabled={operation.busy || lookupBusy} onClick={() => void readOriginal()}>只读核对原完整目标确认</button><label className="full-goal-check"><input type="checkbox" checked={replayAccepted} disabled={blocked || operation.busy || lookupBusy || writing || !!operation.storage_error} onChange={(event) => setReplayAccepted(event.target.checked)} />我已核对原请求，明确只重放同一目标、原body和原键</label><button type="button" disabled={!replayAccepted || blocked || operation.busy || lookupBusy || writing || !!operation.storage_error} onClick={() => void send(original).catch((e) => setError(errorMessage(e)))}>手动重放原完整目标确认</button><p>HTTP成功、超时、解析错误或4xx都不能清除原请求。NOT_FOUND非终局；不改原配置/版本/周期/键，不自动重试。</p></section> : <p className="notice">另一原目标 <code>{original.goal_id}</code> 的确认待核对；请在该原目标展开完整模型恢复，新确认暂停。</p>)}
    {receipt && <section className="full-goal-receipt" aria-label="完整目标原确认历史回执"><h4>原确认回执 · 仅原命令结果</h4><p>原目标 <code>{receipt.goal_id}</code>；原确认版本 <code>{receipt.lifecycle.current_version_id}</code>；原预期前版本 <code>{receipt.lifecycle.previous_version_id}</code>。此current_version_id是该命令首次形成版本，不能当作现在最新版本。</p><p>原持久状态 {receipt.lifecycle.status}；原有效状态 {receipt.lifecycle.effective_status}；原确认 {receipt.confirmed_at}；幂等重放 {receipt.idempotent_replay ? '是' : '否'}。</p><p>receipt_is_current_authority=false，bank_authority=false，dedicated_audit_event=false；具体金融动作仍沿原即时执行验证。原受影响行动{receipt.lifecycle.invalidated_action_ids.length}、在途{receipt.lifecycle.inflight_action_ids.length}；需要重新计算。</p><details><summary>{getOriginalFullGoalResponse(receipt) ? '完整原确认回执响应' : '从原键查询提取的回执结构（派生展示）'}</summary><pre className="readonly-raw">{getOriginalFullGoalResponse(receipt) ?? JSON.stringify(receipt, null, 2)}</pre></details></section>}
    {lookup && <details><summary>原键查询原JSON响应（非归档/银行效果验真）</summary><pre className="readonly-raw">{getOriginalFullGoalResponse(lookup) ?? '原响应文本未保留'}</pre></details>}
    <Workspace key={`${goal.id}:${goal.policy_id}:${goal.policy_version_id}:${JSON.stringify(reviewCandidate)}`} goal={goal} disabled={disabled} send={send} reviewCandidate={reviewCandidate} />
  </div>;
}
