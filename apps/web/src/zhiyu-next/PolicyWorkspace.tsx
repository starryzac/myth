import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NextState } from './api';
import { businessText, money, userError } from './display';
import { getPolicyCatalog, getPolicyRecords, getPolicySchema, same, type PolicyCandidate, type PolicyRecord } from './policies';
import { buildFromSchema, configurationSummary, initialForm, lifecycleText, templateTitles, type FormValues, type TemplateName } from './policy-schema';
import PolicySchemaForm from './PolicySchemaForm';
import type { LocalActorSession } from '../api/local-actor';
import type { ChangeReview, ChangeCommit } from './policy-change';
import LocalUserPanel from './LocalUserPanel';
import PolicyChangePanel from './PolicyChangePanel';
import PaymentPanel, { PaymentOriginalResult, type PaymentPerform } from './PaymentPanel';
import type { PaymentUpdate } from './payments';
import DiscoveryPanel from './DiscoveryPanel';
import type { DiscoveryUpdate } from './discovery';
import GoalAssetLinkPanel from './GoalAssetLinkPanel';
import type { GoalAssetLinkReview } from './goal-asset-link';
import { prepareGoalAssetLink } from './goal-asset-link';

export default function PolicyWorkspace({ environment, blocked, candidate, completed, perform, editCandidate, runRead, review, commit, editChange, retryOriginal, paymentUpdate = null, resumePaymentOriginal, discoveryUpdate = null, openGoalLink = false }: { environment: NextState; blocked: boolean; candidate: PolicyCandidate | null; completed: string | null; perform: PaymentPerform; editCandidate: () => void; runRead: <T>(work: () => Promise<T>) => Promise<T>; review: ChangeReview | null; commit: ChangeCommit | null; editChange: () => void; retryOriginal?: () => Promise<void>; paymentUpdate?: PaymentUpdate | null; resumePaymentOriginal?: () => Promise<void>; discoveryUpdate?: DiscoveryUpdate | null; openGoalLink?: boolean }) {
  const [selected, setSelected] = useState<TemplateName | null>(null); const [values, setValues] = useState<FormValues>({}); const [goal, setGoal] = useState(''); const [error, setError] = useState('');
  const initialized = useRef<string>('');
  const [session, setSession] = useState<LocalActorSession | null>(null); const [editing, setEditing] = useState<PolicyRecord | null>(null);
  const needsSignIn = !!retryOriginal;
  useEffect(() => { if (needsSignIn) setSession(null); }, [needsSignIn]);
  const [paying, setPaying] = useState<PolicyRecord | null>(null);
  const [goalLinkReview, setGoalLinkReview] = useState<GoalAssetLinkReview | null>(null);
  const identity = [environment.environment_id, environment.epoch_id];
  const catalog = useQuery({ queryKey: ['zhiyu-next-policy-templates', ...identity], queryFn: () => getPolicyCatalog(environment), enabled: !blocked, retry: false, staleTime: Infinity });
  const schema = useQuery({ queryKey: ['zhiyu-next-policy-schema', ...identity, selected], queryFn: () => getPolicySchema(selected!, environment), enabled: !!selected && !blocked, retry: false, staleTime: Infinity });
  const records = useQuery({ queryKey: ['zhiyu-next-policy-records', ...identity], queryFn: () => getPolicyRecords(environment), enabled: !blocked, retry: false, staleTime: Infinity });
  useEffect(() => { if (candidate) setSelected(candidate.template_name); }, [candidate]);
  useEffect(() => { if (review && records.data) { const item = records.data.items.find((row) => row.policy_id === review.policy_id && row.source_kind === review.source_kind); if (item) setEditing(item); } }, [review, records.data]);
  useEffect(() => { if (paymentUpdate && records.data) { const item = records.data.items.find((row) => row.source_kind === 'FULL_POLICY' && row.policy_id === paymentUpdate.recovery.scope.full_policy_id && row.template_name === 'PeriodicTransferPolicy'); if (item) setPaying(item); } }, [paymentUpdate, records.data]);
  useEffect(() => { if (!editing || !records.data) return; const current = records.data.items.find((row) => row.policy_id === editing.policy_id && row.source_kind === editing.source_kind); if (!current || current.current_version_id !== editing.current_version_id || current.configuration_hash !== editing.configuration_hash || !current.planning_confirmed) setEditing(null); }, [records.data, editing]);
  useEffect(() => {
    if (!schema.data) return;
    const key = `${schema.data.template_name}:${schema.data.schema_sha256}`;
    if (initialized.current !== key) { initialized.current = key; setValues(initialForm(schema.data.json_schema)); setGoal(''); }
  }, [schema.data]);
  const current = catalog.data?.templates.find((item) => item.template_name === selected);
  async function preview() {
    if (!schema.data || !selected || blocked) return;
    setError(''); editCandidate();
    try {
      const configuration = buildFromSchema(schema.data.json_schema, values, schema.data.reference_choices);
      if (selected === 'LongTermGoalPolicy' && !environment.goals.some((item) => item.id === goal)) throw new Error('请选择本轮已存在的目标。');
      await perform('/zhiyu-next/policy-candidates', { template_name: selected, dsl_version: 'FULL_V1', configuration, expected_epoch_id: environment.epoch_id, ...(selected === 'LongTermGoalPolicy' ? { goal_id: goal, expected_version_id: environment.goals.find((item) => item.id === goal)!.policy_version_id } : {}) });
    } catch (cause) { setError(userError(cause)); }
  }
  function choose(name: TemplateName) { if (blocked) return; setGoalLinkReview(null); setSelected(name); setError(''); initialized.current = ''; editCandidate(); }
  function update(path: string, value: string | string[]) { setGoalLinkReview(null); setValues((existing) => ({ ...existing, [path]: value })); editCandidate(); }
  function lifecycle(item: PolicyRecord, command: 'SUSPEND' | 'RESUME' | 'REVOKE') {
    if (blocked || !item.lifecycle_available) return;
    editCandidate();
    void perform('/zhiyu-next/policy-commands/lifecycle', { source_kind: item.source_kind, policy_id: item.policy_id, expected_version_id: item.current_version_id, reviewed_hash: item.configuration_hash, command, reason: `用户在当前规则范围页面明确${command === 'SUSPEND' ? '暂停' : command === 'RESUME' ? '按原范围恢复' : '撤销'}此规则`, accepted: true, expected_epoch_id: environment.epoch_id, ...(item.source_kind === 'GOAL_BRIDGE' ? { goal_id: item.goal_id } : {}) });
  }
  async function confirmShown() {
    if (!candidate || blocked || candidate.template_name === 'LongTermGoalPolicy' && (!session || session.principal.role !== 'USER')) return; setError('');
    try {
      if (goalLinkReview) {
        const fresh = await runRead(() => prepareGoalAssetLink(goalLinkReview.goal, goalLinkReview.permission.policy_id, environment));
        if (fresh.original_model.full_configuration_hash !== goalLinkReview.original_model.full_configuration_hash || fresh.permission.current_version_id !== goalLinkReview.permission.current_version_id || fresh.permission.configuration_hash !== goalLinkReview.permission.configuration_hash || fresh.configuration_hash !== candidate.configuration_hash || !same(candidate.canonical_configuration, fresh.configuration) || !session || session.principal.role !== 'USER') throw new Error('原目标或购买权限已经变化，请重新审阅同一目标的关联。');
      }
      await perform('/zhiyu-next/policy-commands/confirm', { candidate_id: candidate.candidate_id, reviewed_hash: candidate.configuration_hash, accepted: true, expected_epoch_id: environment.epoch_id });
    } catch (cause) { setError(userError(cause)); }
  }
  const shown = candidate && candidate.template_name === selected ? candidate : null;
  const status = { PROJECTED: '已计算的影响', PARTIAL: '部分影响可核实', UNKNOWN: '影响尚未核实' };
  const lifecycleStatus: Record<string, string> = { ACTIVE: '生效', CONFIRMED: '已确认，等待生效', SUSPENDED: '已暂停', REVOKED: '已撤销', EXPIRED: '已到期', INVALIDATED: '已失效', PROPOSED: '等待确认' };
  return <section className="zyn-policy-workspace" aria-label="十二类策略">
    <section className="zy-card"><h2>十二类规则</h2><p>填写业务范围，核对候选影响，再一次确认。规划确认只保存规则；实际消费与执行按每类能力分别开放。</p>
      {catalog.isPending && <p role="status">正在读取策略目录…</p>}{catalog.isError && <p role="alert">{userError(catalog.error)}</p>}
      <div className="zyn-policy-directory">{catalog.data?.templates.map((item) => <button className={`zyn-template ${selected === item.template_name ? 'is-selected' : ''}`} aria-pressed={selected === item.template_name} key={item.template_name} disabled={blocked || !item.candidate_available} onClick={() => choose(item.template_name)}><strong>{templateTitles[item.template_name]}</strong><span>{lifecycleText[item.lifecycle_backend]}</span><small>{item.execution_status === 'VERIFIED' ? '本轮消费已验证' : '消费范围尚未在本轮完整验证'}</small>{item.reason && <small>{businessText(item.reason)}</small>}</button>)}</div>
    </section>
    <GoalAssetLinkPanel environment={environment} session={session} blocked={blocked} perform={perform} runRead={runRead} candidateEdited={() => { editCandidate(); setGoalLinkReview(null); setError(''); }} openRequested={openGoalLink} reviewing={(source) => { setGoalLinkReview(source); setSelected('LongTermGoalPolicy'); }} />
    {error && goalLinkReview && <p role="alert">{error}</p>}
    {selected && !goalLinkReview && <section className="zy-card"><div className="zy-section-heading"><h2>设置{templateTitles[selected]}</h2><button className="zy-link" disabled={blocked} onClick={() => { setSelected(null); editCandidate(); }}>收起表单</button></div>
      {schema.isPending && <p role="status">正在读取业务字段与可选关联…</p>}{schema.isError && <p role="alert">{userError(schema.error)}</p>}
      {schema.data && <form onSubmit={(event) => { event.preventDefault(); void preview(); }}><PolicySchemaForm schema={schema.data.json_schema} values={values} choices={schema.data.reference_choices} disabled={blocked} update={update} />
        {selected === 'LongTermGoalPolicy' && <label className="zy-field" htmlFor="zyn-policy-existing-goal">扩展已有目标<select id="zyn-policy-existing-goal" value={goal} disabled={blocked} onChange={(event) => { setGoal(event.target.value); editCandidate(); }}><option value="">请选择本轮目标</option>{environment.goals.map((item) => <option key={item.id} value={item.id}>{businessText(item.name)}</option>)}</select><small>此入口扩展现有目标，不创建另一套通用规划策略。</small></label>}
        <p className="zy-muted">金额以元填写，服务器重新核实字段、关联与约束。表单不会补造账户事实、历史消费、权限或回执。</p>{error && <p role="alert">{error}</p>}<button className="zy-secondary" type="submit" disabled={blocked || !current?.candidate_available}>预览规则与影响</button>
      </form>}
    </section>}
    {shown && <section className="zy-card zyn-candidate" aria-label="策略候选影响"><span className="zy-kicker">{lifecycleText[shown.lifecycle_backend]}</span><h2>确认{templateTitles[shown.template_name]}</h2><p>{businessText(shown.summary)}</p><details className="zyn-policy-details"><summary>核对服务端保存的候选范围</summary><ul>{configurationSummary(shown.canonical_configuration, schema.data?.reference_choices).map((row, index) => <li key={index}>{businessText(row)}</li>)}</ul></details><h3>{status[shown.impact.status]}</h3><p>{businessText(shown.impact.summary)}</p>
      {shown.impact.changes.length > 0 && <ul>{shown.impact.changes.map((change, index) => <li key={index}>{businessText(change.label)}{change.before_cents !== undefined && <>：原先 {money(change.before_cents)}</>}{change.after_cents !== undefined && <>，候选 {money(change.after_cents)}</>}</li>)}</ul>}
      {shown.impact.uncovered.length > 0 && <><h3>尚未覆盖</h3><ul>{shown.impact.uncovered.map((item, index) => <li key={index}>{businessText(item)}</li>)}</ul></>}
      <p>本次只确认规划规则，不授予银行付款或资产购买权限。后续必要的执行权限与逐笔确认仍需核实。</p>
      <div className="zy-buttons"><button className="zy-primary" disabled={blocked || !shown.can_confirm || !current?.confirm_available || shown.template_name === 'LongTermGoalPolicy' && (!session || session.principal.role !== 'USER') || !!goalLinkReview && (!session || session.principal.role !== 'USER' || shown.configuration_hash !== goalLinkReview.configuration_hash)} onClick={() => void confirmShown()}>确认这条规则</button><button className="zy-link" disabled={blocked} onClick={editCandidate}>取消候选</button></div>
      {!shown.can_confirm && <p role="status">当前条件尚不允许确认，请保留已填写字段并核对上方限制。</p>}
    </section>}
    {completed && <p className="zy-message" role="status">规划确认已独立核实，当前版本以服务端记录为准。</p>}
    <LocalUserPanel environment={environment} session={session} update={setSession} />
    <DiscoveryPanel environment={environment} session={session} blocked={blocked} update={discoveryUpdate} perform={perform} retryOriginal={retryOriginal} />
    {retryOriginal && <section className="zy-card"><p>先前请求的确认会话未通过。重新建立本轮身份后，仍使用保存的同一原请求继续；系统会先保留原键核对。</p><button className="zy-secondary" disabled={!session || Date.parse(session.principal.expires_at) <= Date.now()} onClick={() => void retryOriginal()}>以同一原请求继续</button></section>}
    {editing && <PolicyChangePanel record={editing} environment={environment} blocked={blocked} session={session} review={review} perform={perform} runRead={runRead} edited={editChange} close={() => { setEditing(null); editChange(); }} />}
    {paying && <PaymentPanel record={paying} records={records.data?.items ?? []} environment={environment} session={session} blocked={blocked} update={paymentUpdate} perform={perform} runRead={runRead} resumeOriginal={resumePaymentOriginal} close={() => setPaying(null)} />}
    {!paying && paymentUpdate?.action && <PaymentOriginalResult update={paymentUpdate} environment={environment} session={session} resumeOriginal={resumePaymentOriginal} />}
    {commit && <section className="zy-card" aria-label="修改实际回读"><h2>{commit.status === 'COMMITTED_BUT_FINANCIAL_UNKNOWN' ? '规则版本已提交，资金影响待核实' : '已审阅范围完成核对'}</h2><p>原修改命令、当前实际版本与候选摘要已经独立定位，不会重新提交。此结果不授予银行权限，也不证明全部财务后果。</p>{commit.uncovered_items.length > 0 && <p>仍有 {commit.uncovered_items.length} 项未覆盖条件；后续实际执行须重新核对。</p>}</section>}
    <section className="zy-rule-list" aria-label="扩展现行策略">{records.isPending && <p role="status">正在读取扩展现行版本…</p>}{records.isError && <p role="alert">{userError(records.error)}</p>}{records.data?.items.map((item) => <article key={`${item.source_kind}:${item.policy_id}`} className="zy-card"><div className="zy-section-heading"><h2>{businessText(item.name) || templateTitles[item.template_name]}</h2><span className="zy-status zy-status-plain">{lifecycleStatus[item.effective_status] ?? '状态尚未核实'}</span></div><p>{lifecycleText[item.lifecycle_backend]} · {templateTitles[item.template_name]}</p><details className="zyn-policy-details"><summary>查看当前版本范围</summary><ul>{configurationSummary(item.configuration).map((row, index) => <li key={index}>{businessText(row)}</li>)}</ul></details><p>{item.planning_confirmed ? '当前规划版本已确认。' : '当前规划版本尚无有效确认。'}{item.execution_status === 'VERIFIED' ? '本轮支持的消费路径已验证。' : '该类完整消费路径尚未在本轮验证。'}</p>{item.reason && <p>{businessText(item.reason)}</p>}
      {item.lifecycle_available && !['REVOKED', 'EXPIRED', 'INVALIDATED'].includes(item.effective_status) && <><p className="zy-muted">暂停阻止此规则继续生效；恢复会重审当前范围并创建新确认版本。撤销后不能用恢复按钮重新开启。</p><div className="zy-buttons">{item.effective_status === 'SUSPENDED' ? <button className="zy-secondary" disabled={blocked} onClick={() => lifecycle(item, 'RESUME')}>按当前范围恢复规则</button> : ['ACTIVE', 'CONFIRMED'].includes(item.effective_status) ? <button className="zy-secondary" disabled={blocked} onClick={() => lifecycle(item, 'SUSPEND')}>暂停规则</button> : null}<button className="zy-link" disabled={blocked} onClick={() => lifecycle(item, 'REVOKE')}>撤销这条规则</button></div></>}
      {item.lifecycle_available && item.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(item.effective_status) && <button className="zy-secondary" disabled={blocked} onClick={() => { setEditing(item); editChange(); }}>预览修改影响</button>}
      {item.source_kind === 'FULL_POLICY' && item.template_name === 'PeriodicTransferPolicy' && item.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(item.effective_status) && <button className="zy-secondary" disabled={blocked} onClick={() => setPaying(item)}>必要付款</button>}
      <p className="zy-muted">规划记录不代表银行执行授权。真实资金结果仍以执行页面的回执为准。</p></article>)}</section>
  </section>;
}
