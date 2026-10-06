import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getDemoAction, getDemoCommand, getDemoPresets, getDemoState, prepareDemoTemplate, sendDemoEvent, resetDemo, confirmDemoAction, executeDemoAction } from '../api/demo';
import type { DemoAction, DemoCommand, DemoEventKind, DemoTemplate } from '../api/demo';
import { changePolicy, confirmProposal, previewChange } from '../api/policies';
import type { ChangePreview } from '../api/policies';
import { errorMessage } from '../api/http';
import { ConfigurationReview } from '../components/PolicyConfigForm';
import ActionEffectReview from '../components/ActionEffectReview';
import { formatMoneyCents } from '../features/money';
import { beginDemoOperation, canStartDemoOperation, endDemoOperation, getDemoOperation, markDemoExecutionStage, recoverDemoOperation, recoverServerDemoAction, renewDemoReadContext, retainServerDemoCommand, useDemoOperation } from '../features/demo-operation';
import type { DemoOperationIdentity } from '../features/demo-operation';
import { useWriteInFlight } from '../features/write-flight';

const statusLabels: Record<string, string> = { WAITING_TEMPLATE: '等待明确确认模板', WAITING_ACTION_CONFIRMATION: '等待原动作具体确认', WAITING_POLICY_CHANGE: '等待复核原策略修改', UNKNOWN: '原结果待核对', COMPLETED: '本事件原流程已完成', BLOCKED: '本事件被阻止', PENDING: '原流程处理中' };
const terminalActions = ['SUCCEEDED', 'RECONCILED', 'INVALIDATED', 'REJECTED', 'FAILED'];
const money = (value: number | null | undefined) => value == null ? '待核验' : `¥${formatMoneyCents(value)}`;
type Perform = (identity: DemoOperationIdentity, operation: () => Promise<boolean>) => Promise<void>;
function TemplateCard({ template, epoch, perform }: { template: DemoTemplate; epoch: string; perform: Perform }) {
  const [accepted, setAccepted] = useState(false);
  const operation = useDemoOperation(); const writing = useWriteInFlight();
  const prepare: DemoOperationIdentity = { kind: 'template', epoch_id: epoch, event_kind: template.kind };
  const confirm: DemoOperationIdentity = { kind: 'policy-confirm', epoch_id: epoch, resource_id: template.proposal_id ?? undefined, reviewed_hash: template.configuration_hash };
  return <article className="policy-card" aria-label={`演示模板 ${template.title}`}><div className="card-heading"><h4>{template.title}</h4><span className="badge">{template.status}</span></div>
    <ConfigurationReview configuration={template.configuration} /><p className="trace-hash caption">完整配置摘要 {template.configuration_hash}</p>
    <p className="caption">这是预定义演示声明。生成候选不构成授权；只有本轮明确确认后才能使用，具体动作仍由真实引擎核验。</p>
    {template.confirmed_policy_id ? <p role="status">本轮已确认策略 {template.confirmed_policy_id}</p> : template.proposal_id ? <>
      <p className="caption">原候选 {template.proposal_id} · 原声明 {template.evidence_id ?? '未记录'}</p>
      <label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={operation.busy || writing} onChange={(event) => setAccepted(event.target.checked)} />我已复核此模板完整字段与配置摘要，明确确认本轮授权</label>
      <button disabled={!accepted || !canStartDemoOperation(confirm)} onClick={() => void perform(confirm, async () => { await confirmProposal(template.proposal_id!, template.configuration_hash); return true; })}>确认模板：{template.title}</button>
    </> : <button disabled={!canStartDemoOperation(prepare)} onClick={() => void perform(prepare, async () => { await prepareDemoTemplate(template.kind, epoch); return true; })}>生成候选：{template.title}</button>}
  </article>;
}
function RentReview({ command, perform }: { command: DemoCommand; perform: Perform }) {
  const change = command.policy_change!;
  const [preview, setPreview] = useState<ChangePreview | null>(null); const [accepted, setAccepted] = useState(false);
  const [reading, setReading] = useState(false); const [error, setError] = useState('');
  const identity: DemoOperationIdentity = { kind: 'rent-change', epoch_id: command.epoch_id, resource_id: change.policy_id, expected_version_id: change.expected_version_id, reviewed_hash: change.reviewed_hash, idempotency_key: change.idempotency_key };
  async function compare() {
    setReading(true); setError(''); setPreview(null); setAccepted(false);
    try {
      const result = await previewChange(change.policy_id, change.expected_version_id, change.configuration);
      if (result.configuration_hash !== change.reviewed_hash) throw new Error('预览配置与原修改摘要不一致，请核对原命令');
      setPreview(result);
    } catch (value) { setError(errorMessage(value)); } finally { setReading(false); }
  }
  return <section className="state-review" aria-label="房租原修改复核"><h4>复核本次房租保护修改</h4><ConfigurationReview configuration={change.configuration} />
    <p className="caption">原策略 {change.policy_id} · 原版本 {change.expected_version_id} · 原修改键 {change.idempotency_key}</p><p className="trace-hash caption">原修改摘要 {change.reviewed_hash}</p><p>修改原因：{change.reason}</p>
    <button disabled={reading} onClick={() => void compare()}>读取房租修改边界预览</button>
    {preview && <><p className="caption">服务端假设比较 {preview.as_of} · {preview.timezone}；仅财务预览，不构成执行授权。</p>
      <table><thead><tr><th>91日口径</th><th>修改前</th><th>假设修改后</th></tr></thead><tbody><tr><th>核验状态</th><td>{preview.before.state}</td><td>{preview.after.state}</td></tr>
        <tr><th>安全闲置</th><td>{money(preview.before.safe_idle_cents)}</td><td>{money(preview.after.safe_idle_cents)}</td></tr><tr><th>最小余量（含负值）</th><td>{money(preview.before.minimum_margin_cents)}</td><td>{money(preview.after.minimum_margin_cents)}</td></tr></tbody></table>
      <p>安全闲置变化 {money(preview.delta_safe_idle_cents)}；最小余量变化 {money(preview.delta_minimum_margin_cents)}</p>
      <details><summary>服务端比较说明与原依据</summary>{preview.notes.map((note, index) => <p key={index}>{note}</p>)}<p className="caption">当前事实摘要 {preview.current_fact_input_digest}</p><p className="caption">假设输入摘要 {preview.hypothetical_input_digest}</p></details>
      <label className="checkbox-field"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />我已复核房租完整配置、原版本与服务端比较，明确接受此原修改</label>
      <button disabled={!accepted || !canStartDemoOperation(identity)} onClick={() => void perform(identity, async () => {
        await changePolicy(change.policy_id, { ...change, accepted: true });
        return !['UNKNOWN', 'PENDING'].includes((await sendDemoEvent(command.event_kind, command.epoch_id)).status);
      })}>确认房租原修改</button></>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
function ActionReview({ action, command, perform }: { action: DemoAction; command: DemoCommand; perform: Perform }) {
  const [accepted, setAccepted] = useState(false);
  const identity: DemoOperationIdentity = { kind: action.status === 'PLANNED' ? 'action-confirm' : 'action-execute', epoch_id: command.epoch_id, resource_id: action.action_id, effect_hash: action.effect_hash, event_kind: command.event_kind };
  const isConfirmable = action.autonomy_level === 'ASK_ONCE' && action.status === 'PLANNED';
  const isOriginalExecutable = action.autonomy_level === 'ASK_ONCE' && ['AUTHORIZED', 'UNKNOWN', 'SUBMITTED'].includes(action.status);
  async function original() {
    const current = await getDemoAction(action.action_id);
    if (current.effect_hash !== action.effect_hash) throw new Error('原动作经济后果摘要变化，请核对原命令');
    let confirmed = current;
    if (isConfirmable) { confirmed = await confirmDemoAction(current); markDemoExecutionStage({ ...identity, kind: 'action-execute' }); }
    const result = await executeDemoAction(confirmed);
    if (['UNKNOWN', 'SUBMITTED'].includes(result.status)) return false;
    const resumed = await sendDemoEvent(command.event_kind, command.epoch_id);
    return !['UNKNOWN', 'PENDING'].includes(resumed.status);
  }
  return <article className="demo-action"><ActionEffectReview action={action} />
    {action.receipt && <p className="caption">当前原回执 {action.receipt.receipt_id} · 原银行操作 {action.receipt.bank_operation_id} · {action.receipt.status}</p>}
    {isConfirmable && <><label className="checkbox-field"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />我已复核此原动作金额、费用、损失、净到账、到账时间及经济后果摘要，明确确认本次动作</label>
      <button disabled={!accepted || !canStartDemoOperation(identity)} onClick={() => void perform(identity, original)}>具体确认并执行原动作</button></>}
    {isOriginalExecutable && <button disabled={!canStartDemoOperation(identity)} onClick={() => void perform(identity, original)}>{action.status === 'AUTHORIZED' ? '继续执行已确认的原动作' : '核对并恢复原动作'}</button>}
    {['UNKNOWN', 'SUBMITTED'].includes(action.status) && <p className="notice">原操作结果尚未核定。恢复只使用此 action_id 与 effect_hash；不能创建新扣款或新报价。</p>}
  </article>;
}
function CommandResult({ command, perform, read }: { command: DemoCommand; perform: Perform; read: () => Promise<void> }) {
  const recovery = command.recovery;
  return <section className="command-result" aria-label={`原事件结果 ${command.event_kind}`}><h4>{statusLabels[command.status] ?? command.status}</h4><p>{command.message}</p>
    <p className="caption">原命令 {command.command_id} · 轮次 {command.epoch_id} · 受理 {command.admitted_at}</p><button onClick={() => void read()}>读取原命令</button>
    {command.goal_id && <p>实际目标原件 {command.goal_id} · <a href="#goals">查看目标归属与配置</a></p>}
    {command.fact && <><p>外部事实原件 {command.fact.external_fact_id}：银行状态 {command.fact.bank_status}；投影状态 {command.fact.projection_status}</p>
      <p className="caption">实际账务交易 {command.fact.transaction_id ?? '尚未投影'}；经济分录 {(command.fact.economic_posting_ids ?? []).join('、') || '未返回'}{command.fact.projection_error ? `；${command.fact.projection_error}` : ''}</p></>}
    {recovery && <section aria-label="实际恢复结果"><p>恢复原记录 {recovery.run_id} · {recovery.status} · 原计划 {recovery.plan.status}</p>
      <p>实际安全闲置 {money(recovery.actual_boundary.safe_idle_cents)}；实际最小余量 {money(recovery.actual_boundary.minimum_margin_cents)}</p>
      <p className="caption">候选的预计边界与当前实际边界分别记录；ASK 或 ADVISE 不表示已自动完成。</p>
      {(recovery.plan.candidates ?? []).map((candidate) => <p key={candidate.position_id}>原持仓 {candidate.position_id} · {candidate.decision} · {(candidate.reasons ?? []).join('、') || '未记录原因'}</p>)}
      {recovery.notifications.map((note, index) => <p key={index}>{note.code} · {note.message}</p>)}<a href={`#decisions/${recovery.run_id}`}>查看恢复决策轨迹</a></section>}
    {(command.actions ?? []).map((action) => <ActionReview key={`${action.action_id}:${action.effect_hash}`} action={action} command={command} perform={perform} />)}
    {command.policy_change && command.status === 'WAITING_POLICY_CHANGE' && <RentReview key={`${command.policy_change.expected_version_id}:${command.policy_change.reviewed_hash}`} command={command} perform={perform} />}
  </section>;
}
export default function DemoConsolePage() {
  const client = useQueryClient(); const operation = useDemoOperation(); const writing = useWriteInFlight();
  const [error, setError] = useState(''); const [resetOpen, setResetOpen] = useState(false); const [resetAccepted, setResetAccepted] = useState(false);
  const presets = useQuery({ queryKey: ['demo-presets'], queryFn: getDemoPresets, retry: false });
  const actual = useQuery({ queryKey: ['demo-state'], queryFn: getDemoState, retry: false, structuralSharing: false });
  useEffect(() => { recoverDemoOperation(); }, []);
  const data = actual.isSuccess && !actual.isError ? actual.data : undefined;
  useEffect(() => {
    if (!data || getDemoOperation().busy) return;
    const pending = getDemoOperation().pending;
    if (pending && pending.epoch_id === data.epoch_id) {
      const command = data.commands.find((item) => item.event_kind === pending.event_kind);
      const template = data.templates.find((item) => item.kind === pending.event_kind || item.proposal_id === pending.resource_id);
      const action = data.commands.flatMap((item) => item.actions ?? []).find((item) => item.action_id === pending.resource_id && item.effect_hash === pending.effect_hash);
      const known = pending.kind === 'event' ? command && !['UNKNOWN', 'PENDING'].includes(command.status)
        : pending.kind === 'template' ? template?.proposal_id
          : pending.kind === 'policy-confirm' ? template?.confirmed_policy_id
            : pending.kind === 'action-confirm' ? action && action.status === 'AUTHORIZED'
              : pending.kind === 'action-execute' ? action && terminalActions.includes(action.status)
                : pending.kind === 'rent-change' ? data.commands.some((item) => item.event_kind === 'CHANGE_RENT' && item.status === 'COMPLETED') : false;
      if (known) endDemoOperation(true);
    }
    const unknown = data.commands.find((item) => ['UNKNOWN', 'PENDING'].includes(item.status));
    if (unknown) { recoverServerDemoAction(unknown); retainServerDemoCommand(unknown); }
  }, [data, operation.busy]);
  async function refresh() { setError(''); await client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'demo-presets' }); }
  const perform: Perform = async (identity, task) => {
    setError(''); let started = false;
    try { beginDemoOperation(identity); started = true; const resolved = await task(); endDemoOperation(resolved); await refresh(); }
    catch (value) { if (started) endDemoOperation(false); setError(errorMessage(value)); }
  };
  async function readCommand(command: DemoCommand) {
    setError('');
    try { await getDemoCommand(command.command_id, command.epoch_id); await actual.refetch(); }
    catch (value) { setError(errorMessage(value)); }
  }
  async function reset() {
    if (!data || !resetAccepted) return;
    const pending = getDemoOperation().pending;
    const identity: DemoOperationIdentity = pending?.kind === 'reset' ? pending : { kind: 'reset', epoch_id: data.epoch_id, reset_key: crypto.randomUUID() };
    setError(''); let started = false;
    try {
      beginDemoOperation(identity); started = true;
      const result = await resetDemo({ reset_key: identity.reset_key!, expected_epoch_id: identity.epoch_id, accepted: true });
      await client.cancelQueries(); client.clear(); renewDemoReadContext(result); setResetOpen(false); setResetAccepted(false);
    } catch (value) { if (started) endDemoOperation(false); setError(errorMessage(value)); }
  }
  const stale = !!data && !!operation.pending && operation.pending.kind !== 'reset' && operation.pending.epoch_id !== data.epoch_id;
  return <section className="page-content"><div className="page-heading"><div><h2>演示控制台</h2><p>只注入预定义合成事件，资金、授权与恢复结果均来自实际引擎。</p></div><button disabled={actual.isFetching} onClick={() => void actual.refetch()}>读取实际演示状态</button></div>
    <p className="caption">每轮每个事件固定使用同一原命令身份。推荐先确认买车与无损配置模板，建立零归属目标，再走工资、消费和恢复；房租修改放在资金链最后。</p>
    {(operation.busy || writing) && <p role="status">原请求正在处理，资金操作全局串行；可继续只读核对。</p>}
    {operation.pending && !operation.busy && <p className="notice">原命令结果尚待核对：{operation.pending.kind} · {operation.pending.resource_id ?? operation.pending.event_kind ?? operation.pending.reset_key}。请读取原件或重试同一身份。</p>}
    {operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {stale && <p className="notice">实际轮次已变化，旧请求不适用于当前轮次。<button disabled={operation.busy || writing} onClick={() => endDemoOperation(true)}>已核对当前轮次，释放旧浏览器记录</button></p>}
    {operation.storage_error && data && <button disabled={operation.busy || writing} onClick={() => { endDemoOperation(true); const unknown = data.commands.find((item) => ['UNKNOWN', 'PENDING'].includes(item.status)); if (unknown) retainServerDemoCommand(unknown); }}>已读取服务端原件，恢复浏览器记录</button>}
    {operation.reset_receipt && <section className="notice" aria-label="原重置回执"><p>原重置键 {operation.reset_receipt.reset_key} · 该键原轮次 {operation.reset_receipt.reset_epoch_id} · 响应时当前轮次 {operation.reset_receipt.epoch_id}</p>
      <p className="caption">种子摘要是此原键的历史回执；当前资金与授权以重新读取的实际状态为准。重试旧键不会重置后来的轮次。</p></section>}
    {presets.isError && <p role="alert">预设读取失败：{errorMessage(presets.error)}</p>}{actual.isError && <p role="alert">实际状态读取失败：{errorMessage(actual.error)}</p>}
    {(presets.isPending || actual.isPending) && <p role="status">正在读取预定义输入与实际原件…</p>}
    {data && <p className="caption">实际轮次 {data.epoch_id ?? '尚未初始化'}{data.reason ? ` · ${data.reason}` : ''}</p>}
    {presets.isSuccess && data && <><section aria-label="七个预定义演示事件"><h3>预定义事件</h3><div className="demo-events">{presets.data.events.map((preset) => {
      const command = data.commands.find((item) => item.event_kind === preset.event_kind);
      const identity: DemoOperationIdentity = { kind: 'event', epoch_id: data.epoch_id, event_kind: preset.event_kind };
      const isReset = preset.event_kind === 'RESET';
      return <article className="demo-event" key={preset.event_kind}><h4>{preset.title}</h4><p>{preset.description}</p>{preset.amount_cents != null && <p>预设金额 {money(preset.amount_cents)}</p>}
        {(preset.required_templates ?? []).length > 0 && <p className="caption">需要本轮确认：{preset.required_templates!.map((kind) => presets.data.templates.find((item) => item.kind === kind)?.title ?? kind).join('、')}</p>}
        {isReset ? <button disabled={operation.busy || writing || (!!operation.pending && operation.pending.kind !== 'reset') || !!operation.storage_error} onClick={() => { setResetOpen(true); setResetAccepted(false); }}>恢复演示初始状态</button>
          : <button disabled={!data.available || !data.epoch_id || !canStartDemoOperation(identity)} onClick={() => void perform(identity, async () => !['UNKNOWN', 'PENDING'].includes((await sendDemoEvent(preset.event_kind as DemoEventKind, data.epoch_id!)).status))}>{command ? '恢复原事件：' : '注入事件：'}{preset.title}</button>}
        {command && <CommandResult command={command} perform={perform} read={() => readCommand(command)} />}
      </article>;
    })}</div></section>
    {resetOpen && <section className="state-review" aria-label="重置明确确认"><h3>复核重置范围</h3><p>此操作销毁当前演示金融投影并封存旧审计纪元，创建新的演示轮次。旧审计原件保留；已有模板授权不会转入新轮次，需要重新逐项明确确认。</p>
      <label className="checkbox-field"><input type="checkbox" checked={resetAccepted} onChange={(event) => setResetAccepted(event.target.checked)} />我明确接受销毁当前演示金融投影、封存旧审计并初始化新轮次，且重置不授予自动理财权限</label>
      <button disabled={!resetAccepted || operation.busy || writing || (!!operation.pending && operation.pending.kind !== 'reset') || !!operation.storage_error} onClick={() => void reset()}>{operation.pending?.kind === 'reset' ? '重试原重置请求' : '明确确认重置'}</button><button disabled={operation.busy || writing} onClick={() => setResetOpen(false)}>取消本次复核</button></section>}
    {data.available && data.epoch_id && <section aria-label="本轮完整授权模板"><h3>逐项确认本轮模板</h3><div className="policy-grid">{data.templates.map((template) => <TemplateCard key={`${template.kind}:${template.configuration_hash}:${template.status}`} template={template} epoch={data.epoch_id!} perform={perform} />)}</div></section>}
    </>}
    {error && <p className="form-issues" role="alert">{error}</p>}
  </section>;
}
