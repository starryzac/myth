import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import PolicyConfigForm, { ConfigurationReview } from '../components/PolicyConfigForm';
import { changePolicy, changeState, compilePolicy, confirmProposal, discoverPolicies, getCompilation, getPolicies, getProposals, getVersions, previewChange, reviseCompilation } from '../api/policies';
import type { ChangeCommand, ChangePreview, Compilation, Configuration, Lifecycle, Policy, Proposal } from '../api/policies';
import { ApiError, errorMessage } from '../api/http';
import { lifecycleLabels, policyTypes } from '../features/policy-form';
import { formatMoneyCents } from '../features/money';

function Result({ result }: { result: Lifecycle }) {
  return <div className="command-result" role="status"><p>服务端已返回此命令的处理记录，当前状态以重新读取的策略为准。</p>
    <p>待执行原项失效 {result.invalidated_action_ids.length} 项；在途原项待核对 {result.inflight_action_ids.length} 项。</p>
    <p className="caption">在途、UNKNOWN 或已受理操作保留原身份，仍需核对；已有归属资金与持仓不会因此转给其他目标。</p>
    {(result.invalidated_action_ids.length > 0 || result.inflight_action_ids.length > 0) && <details><summary>查看原项编号</summary><p>已失效：{result.invalidated_action_ids.join('、') || '无'}</p><p>待核对：{result.inflight_action_ids.join('、') || '无'}</p></details>}</div>;
}
function BoundaryComparison({ preview }: { preview: ChangePreview }) {
  const money = (value: number | null | undefined) => value == null ? '待核验' : `¥${formatMoneyCents(value)}`;
  return <section className="change-preview" aria-label="修改前后资金边界"><h4>修改前后资金边界</h4>
    <p className="caption">服务端查询时点 {preview.as_of} · {preview.timezone}；这是假设确认配置后的财务比较，未授予执行权限，也未锁定跨请求资金。</p>
    <table><thead><tr><th>91日口径</th><th>修改前</th><th>假设修改后</th></tr></thead><tbody>
      <tr><th>核验状态</th><td>{preview.before.state}</td><td>{preview.after.state}</td></tr>
      <tr><th>安全闲置</th><td>{money(preview.before.safe_idle_cents)}</td><td>{money(preview.after.safe_idle_cents)}</td></tr>
      <tr><th>最小余量（含负值）</th><td>{money(preview.before.minimum_margin_cents)}</td><td>{money(preview.after.minimum_margin_cents)}</td></tr>
      <tr><th>资金缺口</th><td>{money(preview.before.deficit_cents)}</td><td>{money(preview.after.deficit_cents)}</td></tr>
    </tbody></table><p>安全闲置变化：{money(preview.delta_safe_idle_cents)}；最小余量变化：{money(preview.delta_minimum_margin_cents)}</p>
    <p>假设状态：{lifecycleLabels[preview.assumed_status] ?? preview.assumed_status}；有效期 {preview.assumed_valid_from ?? '未限制'} 至 {preview.assumed_valid_until ?? '未限制'}（结束时点不包含）。</p>
    {preview.notes.map((note) => <p className="caption" key={note}>{note}</p>)}
    <details><summary>核验问题与比较依据</summary>{[...preview.before.issues ?? [], ...preview.after.issues ?? []].map((issue, i) => <p key={i}>{issue.code} · {issue.message}</p>)}
      <p className="caption">当前事实摘要 {preview.current_fact_input_digest}</p><p className="caption">假设配置摘要 {preview.assumption_digest}</p><p className="caption">假设输入摘要 {preview.hypothetical_input_digest}</p></details>
  </section>;
}
function PolicyEditor({ policy, done }: { policy: Policy; done: (result: Lifecycle) => Promise<void> }) {
  const [basis, setBasis] = useState(policy);
  const [draft, setDraft] = useState<Configuration | null>(policy.current_version!.configuration);
  const [preview, setPreview] = useState<ChangePreview | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [command, setCommand] = useState<ChangeCommand | null>(null);
  function clearReview() { setPreview(null); setAccepted(false); setCommand(null); }
  async function compare() {
    if (!draft || stale) return;
    setBusy(true); setError(''); clearReview();
    try { setPreview(await previewChange(basis.id, basis.current_version!.id, draft)); }
    catch (e) { setError(errorMessage(e)); if (e instanceof ApiError && e.status === 409) setStale(true); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (!preview || !accepted || !reason.trim()) return;
    const frozen = command ?? { accepted: true, reviewed_hash: preview.configuration_hash,
      expected_version_id: basis.current_version!.id, configuration: preview.configuration, reason: reason.trim(), idempotency_key: crypto.randomUUID() };
    setCommand(frozen); setBusy(true); setError('');
    try { await done(await changePolicy(basis.id, frozen)); }
    catch (e) {
      setError(errorMessage(e));
      if (!(e instanceof ApiError) || e.status !== 0) { setCommand(null); setAccepted(false); setPreview(null); }
      if (e instanceof ApiError && e.status === 409) setStale(true);
    } finally { setBusy(false); }
  }
  async function reloadBasis() {
    setBusy(true); setError('');
    try {
      const current = (await getPolicies()).items.find((item) => item.id === basis.id);
      if (!current?.current_version || !['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(current.effective_status)) throw new Error('当前策略已不可修改，请关闭本次编辑查看现状');
      setBasis(current); setStale(false); clearReview();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <section className="editor-panel" aria-label="修改策略"><h3>修改 {policy.name}</h3>
    <p className="caption">当前复核版本 {basis.current_version!.version_number}。暂停中的策略修改后仍保持暂停。</p>
    <details><summary>查看原版本全部字段</summary><ConfigurationReview configuration={basis.current_version!.configuration} /></details>
    <fieldset disabled={busy || command !== null}><PolicyConfigForm initial={policy.current_version!.configuration} onChange={(value) => { setDraft(value); clearReview(); setError(''); }} /></fieldset>
    <button type="button" disabled={!draft || busy || stale || command !== null} onClick={() => void compare()}>预览修改影响</button>
    {preview && <><BoundaryComparison preview={preview} /><details open><summary>复核服务端规范化后的配置</summary><ConfigurationReview configuration={preview.configuration} /></details>
      <fieldset disabled={busy || command !== null}><label className="field">修改原因<input maxLength={1000} value={reason} onChange={(e) => { setReason(e.target.value); setAccepted(false); }} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />我已复核完整配置与本次服务端边界比较，明确接受此次修改</label></fieldset>
      <button type="button" disabled={busy || !accepted || !reason.trim() || stale} onClick={() => void submit()}>{command ? '重试原修改请求' : '确认修改'}</button></>}
    {stale && <div className="notice"><p>版本已变化；草稿保留，旧预览已废弃。请先查看最新版本，再重新预览。</p><button disabled={busy} onClick={() => void reloadBasis()}>加载最新版本并保留草稿</button></div>}
    {command && !busy && <p className="notice">连接中断的请求结果尚待核对。编辑已冻结；重试继续使用原键与原内容。</p>}
    {error && <p role="alert" className="form-issues">{error}</p>}
  </section>;
}
function Versions({ id }: { id: string }) {
  const versions = useQuery({ queryKey: ['policy-versions', id], queryFn: () => getVersions(id), retry: false });
  if (versions.isPending) return <p role="status">正在读取版本…</p>;
  if (versions.isError) return <p role="alert">{errorMessage(versions.error)}</p>;
  return <div className="version-list">{versions.data.items.map((version) => <details key={version.id}><summary>版本 {version.version_number} · {version.change_reason}</summary>
    <p>确认时间 {version.confirmed_at ?? '未确认'} · 有效期 {version.valid_from ?? '未限制'} 至 {version.valid_until ?? '未限制'}</p><ConfigurationReview configuration={version.configuration} />
    <p className="caption">内容摘要 {version.content_hash}；前版本摘要 {version.previous_hash ?? '无'}。这些是版本追溯字段。</p></details>)}</div>;
}
function PolicyCard({ policy, edit, done }: { policy: Policy; edit: () => void; done: (result: Lifecycle) => Promise<void> }) {
  const [history, setHistory] = useState(false);
  const [operation, setOperation] = useState<'suspend' | 'revoke' | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const canChange = !!policy.current_version && ['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(policy.effective_status);
  async function execute() {
    if (!operation || !accepted || !policy.current_version) return;
    setBusy(true); setError('');
    try { await done(await changeState(policy.id, policy.current_version.id, operation)); setOperation(null); setAccepted(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <article className="policy-card" aria-label={`策略 ${policy.name}`}><div className="card-heading"><h3>{policy.name}</h3><span className="badge">{lifecycleLabels[policy.effective_status] ?? policy.effective_status}</span></div>
    <p>{policyTypes[policy.policy_type] ?? policy.policy_type} · {policy.current_version ? `版本 ${policy.current_version.version_number}` : '当前版本待核验'}</p>
    {policy.current_version && <><p className="caption">有效期 {policy.current_version.valid_from ?? '未限制'} 至 {policy.current_version.valid_until ?? '未限制'}（结束时点不包含）</p><details><summary>查看完整配置</summary><ConfigurationReview configuration={policy.current_version.configuration} /></details></>}
    <p className="caption">{policy.version_authorized ? '当前确认版本与生命周期证据有效' : '当前版本未满足有效授权条件'}；具体执行仍需核验资金、动作约束与权限。</p>
    <div className="button-row"><button disabled={!canChange} onClick={edit}>修改策略</button><button disabled={!canChange || policy.effective_status === 'SUSPENDED'} onClick={() => { setOperation('suspend'); setAccepted(false); }}>暂停</button>
      <button disabled={!canChange} onClick={() => { setOperation('revoke'); setAccepted(false); }}>撤销</button><button onClick={() => setHistory(!history)}>{history ? '收起版本历史' : '查看版本历史'}</button></div>
    {operation && <section className="state-review" aria-label={operation === 'suspend' ? '暂停复核' : '撤销复核'}><h4>{operation === 'suspend' ? '暂停' : '撤销'} {policy.name} · 版本 {policy.current_version?.version_number}</h4>
      <p>已有归属现金和持仓保留；原在途、UNKNOWN 或已受理操作继续核对，不能借此次变更重付或转移归属。</p>
      <label className="checkbox-field"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />我已复核此策略和版本，明确接受此次{operation === 'suspend' ? '暂停' : '撤销'}</label>
      <div className="button-row"><button disabled={!accepted || busy} onClick={() => void execute()}>确认{operation === 'suspend' ? '暂停' : '撤销'}</button><button disabled={busy} onClick={() => setOperation(null)}>取消</button></div></section>}
    {error && <p role="alert">{error}</p>}{history && <Versions id={policy.id} />}
  </article>;
}
function CandidateReview({ proposal, done }: { proposal: Proposal; done: (result: Lifecycle) => Promise<void> }) {
  const [accepted, setAccepted] = useState(false); const [compilation, setCompilation] = useState<Compilation | null>(null);
  const [dirty, setDirty] = useState(false);
  const [draft, setDraft] = useState<Configuration | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const currentId = compilation?.proposal_id ?? proposal.id;
  const hash = compilation?.configuration_hash ?? proposal.configuration_hash;
  const configuration = compilation?.configuration ?? proposal.configuration;
  const ready = compilation ? !!compilation.configuration && !!compilation.proposal_id : proposal.validation_ready;
  const candidateStatus = compilation?.proposal_status ?? proposal.status;
  async function confirm() {
    setBusy(true); setError('');
    try { await done(await confirmProposal(currentId, hash!)); setAccepted(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function resume() {
    if (!proposal.compilation_id) return;
    setBusy(true); setError('');
    try { const response = await getCompilation(proposal.compilation_id); setCompilation(response); setDraft(response.configuration); setAccepted(false); setDirty(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function revise() {
    if (!compilation || !draft) return;
    setBusy(true); setError('');
    try { const response = await reviseCompilation(compilation.compilation_id, draft); setCompilation(response); setDraft(response.configuration); setAccepted(false); setDirty(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <article className="policy-card" aria-label="候选复核"><h3>{String(configuration.name ?? '候选策略')}</h3><p className="caption">来源 {proposal.source_type} · {candidateStatus} · {proposal.compiler_version}</p><p>{proposal.source_text}</p>
    <ConfigurationReview configuration={configuration} />
    {!ready && <p className="notice">结构尚未完整，请先补齐；当前不能确认。</p>}
    {proposal.compilation_id && !compilation && <button disabled={busy} onClick={() => void resume()}>读取原编译并修订</button>}
    {compilation && <><CompilationNotes compilation={compilation} /><PolicyConfigForm key={compilation.proposal_id ?? compilation.compilation_id} initial={compilation.configuration ?? compilation.compilation.draft} onChange={(value) => { setDraft(value); setAccepted(false); setDirty(true); }} />
      <button disabled={!draft || busy || candidateStatus !== 'PROPOSED'} onClick={() => void revise()}>保存修订候选</button><p className="caption">修改表单后先保存修订，再复核最新规范化配置。</p></>}
    {dirty && <p className="notice">表单已改变，先保存修订，再复核和接受最新配置。</p>}
    {candidateStatus === 'PROPOSED' && <><label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={dirty || busy || !ready} onChange={(e) => setAccepted(e.target.checked)} />我已逐项复核完整候选配置，明确接受此策略</label>
      <button disabled={!ready || !accepted || busy || dirty} onClick={() => void confirm()}>确认候选策略</button></>}
    {error && <p role="alert">{error}</p>}
  </article>;
}
function CompilationNotes({ compilation }: { compilation: Compilation }) {
  return <div className="compile-notes"><p>原编译日期锚点 {compilation.compilation.reference_date} · {compilation.compilation.timezone}</p>
    {compilation.compilation.issues?.map((issue, index) => <p key={index}>{issue.code} · {issue.message}</p>)}
    {compilation.compilation.assumptions?.map((item) => <p className="caption" key={item}>原默认假设：{item}</p>)}</div>;
}
export default function PolicyCenterPage() {
  const client = useQueryClient();
  const policies = useQuery({ queryKey: ['policies'], queryFn: getPolicies, retry: false });
  const proposals = useQuery({ queryKey: ['proposals'], queryFn: getProposals, retry: false });
  const [text, setText] = useState(''); const [compilation, setCompilation] = useState<Compilation | null>(null);
  const [draft, setDraft] = useState<Configuration | null>(null); const [accepted, setAccepted] = useState(false);
  const [dirty, setDirty] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [editing, setEditing] = useState<Policy | null>(null); const [result, setResult] = useState<Lifecycle | null>(null);
  async function refresh() {
    await Promise.all(['policies', 'proposals', 'goals', 'dashboard', 'policy-versions'].map((key) => client.invalidateQueries({ queryKey: [key] })));
  }
  async function done(receipt: Lifecycle) { setResult(receipt); setEditing(null); await refresh(); }
  async function compile() {
    setBusy(true); setError(''); setAccepted(false);
    try { const response = await compilePolicy(text); setCompilation(response); setDraft(response.configuration); setDirty(false); await refresh(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function revise() {
    if (!compilation || !draft) return;
    setBusy(true); setError(''); setAccepted(false);
    try { const response = await reviseCompilation(compilation.compilation_id, draft); setCompilation(response); setDraft(response.configuration); setDirty(false); await refresh(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!compilation?.proposal_id || !compilation.configuration_hash || !accepted || dirty) return;
    setBusy(true); setError('');
    try { await done(await confirmProposal(compilation.proposal_id, compilation.configuration_hash)); setCompilation(null); setAccepted(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function discover() {
    setBusy(true); setError('');
    try { await discoverPolicies(); await refresh(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <><section className="page-intro"><div><p className="eyebrow">把自主安排写清楚，再逐项确认</p><h2>策略中心</h2></div><button onClick={() => void refresh()}>刷新策略</button></section>
    <p className="caption">策略状态以服务端可信时钟与当前版本为准；确认策略不等于直接执行资金动作。</p>
    {result && <Result result={result} />}
    <section className="policy-section" aria-label="自然语言候选"><h3>用一句话起草策略</h3><p className="caption">规则编译支持目标储蓄与应急金。请先查看完整字段、问题和默认假设，再明确确认。</p>
      <label className="field">策略描述<textarea maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：保留2000元应急金" /></label>
      <div className="button-row"><button disabled={!text.trim() || busy} onClick={() => void compile()}>编译候选</button><button disabled={busy} onClick={() => void discover()}>从真实模拟历史发现候选</button></div>
      {compilation && <div className="editor-panel"><CompilationNotes compilation={compilation} /><PolicyConfigForm key={compilation.proposal_id ?? compilation.compilation_id} initial={compilation.configuration ?? compilation.compilation.draft}
        onChange={(value) => { setDraft(value); setDirty(true); setAccepted(false); }} />
        <button disabled={!draft || !dirty || busy} onClick={() => void revise()}>保存修订候选</button>
        {compilation.configuration && <><h4>当前服务端规范化配置</h4><ConfigurationReview configuration={compilation.configuration} /></>}
        {dirty && <p className="notice">表单已改变，先保存修订，再接受最新配置。</p>}
        <label className="checkbox-field"><input type="checkbox" checked={accepted} disabled={dirty || !compilation.configuration} onChange={(e) => setAccepted(e.target.checked)} />我已逐项复核完整配置与原编译说明，明确接受此策略</label>
        <button disabled={!accepted || dirty || !compilation.proposal_id || busy} onClick={() => void confirm()}>确认编译策略</button></div>}
      {error && <p className="form-issues" role="alert">{error}</p>}</section>
    {editing && <><PolicyEditor key={editing.id} policy={editing} done={done} /><button onClick={() => setEditing(null)}>关闭编辑</button></>}
    <section className="policy-section" aria-label="当前策略"><h3>当前策略</h3>
      {policies.isPending && <p role="status">正在读取策略…</p>}{policies.isError && <p role="alert">{errorMessage(policies.error)}</p>}
      {!policies.isError && policies.data?.items.length === 0 && <p className="empty">还没有已确认的策略。</p>}
      {!policies.isError && policies.data?.items.map((policy) => <PolicyCard key={`${policy.id}-${policy.current_version?.id}-${policy.effective_status}`} policy={policy} edit={() => setEditing(policy)} done={done} />)}</section>
    <section className="policy-section" aria-label="候选策略"><h3>候选与原始说明</h3>
      {proposals.isError && <p role="alert">{errorMessage(proposals.error)}</p>}{proposals.isPending && <p role="status">正在读取候选…</p>}
      {!proposals.isError && proposals.data?.items.filter((proposal) => proposal.id !== compilation?.proposal_id).map((proposal) => <CandidateReview key={`${proposal.id}-${proposal.status}-${proposal.configuration_hash}`} proposal={proposal} done={done} />)}
    </section></>;
}
