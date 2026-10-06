import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { compileFullPolicyCandidate, compilerTemplates, getFullCompilerGrammar, getOriginalFullCompilationResponse, requireCompilerJson } from '../api/full-policy-compilation';
import type { FullCompilation, FullCompilationRequest } from '../api/full-policy-compilation';
import { validateFullPolicyCandidate } from '../api/full-policies';
import type { TemplateCandidate, TemplateName } from '../api/full-policies';
import { errorMessage } from '../api/http';
import { object } from '../features/policy-form';
import { isWriteInFlight, useWriteInFlight } from '../features/write-flight';

export type FullCompiledDraft = { templateName: TemplateName; configuration: Record<string, unknown>; configurationHash: string };
type Props = { mutationBlocked?: boolean; userId?: string; onCandidate?: (candidate: FullCompiledDraft) => void };
function json(text: string): Record<string, unknown> { const value: unknown = JSON.parse(text); if (!object(value)) throw new Error('候选配置必须是完整JSON对象'); requireCompilerJson(value); return value; }
export default function FullPolicyCompilerPanel(props: Props) { return <Workspace key={props.userId ?? 'server-user'} {...props} />; }
function Workspace({ mutationBlocked = false, userId, onCandidate }: Props) {
  const [text, setText] = useState(''); const [data, setData] = useState<FullCompilation | null>(null); const [draft, setDraft] = useState(''); const [candidate, setCandidate] = useState<TemplateCandidate | null>(null); const [candidateInput, setCandidateInput] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [comparison, setComparison] = useState(false); const [comparisonTemplate, setComparisonTemplate] = useState<TemplateName>('DatedExpensePolicy'); const [comparisonJson, setComparisonJson] = useState('');
  const revision = useRef(0); const writing = useWriteInFlight();
  const grammar = useQuery({ queryKey: ['full-natural-policy-grammar'], queryFn: getFullCompilerGrammar, retry: false, structuralSharing: false });
  const blocked = mutationBlocked || busy || writing;
  function changeSource(value: string) { revision.current++; setText(value); setData(null); setDraft(''); setCandidate(null); setCandidateInput(''); setError(''); setNotice(''); }
  function changeComparison() { revision.current++; setData(null); setDraft(''); setCandidate(null); setCandidateInput(''); setError(''); setNotice(''); }
  async function compile() {
    if (mutationBlocked || busy || isWriteInFlight() || !text.length) return;
    const current = ++revision.current; setBusy(true); setData(null); setDraft(''); setCandidate(null); setCandidateInput(''); setError(''); setNotice('');
    try {
      const body: FullCompilationRequest = { text, engine: 'rules', ...(comparison ? { comparison_candidate: { template_name: comparisonTemplate, configuration: json(comparisonJson) } } : {}) };
      const result = await compileFullPolicyCandidate(body, userId);
      if (current !== revision.current) return;
      setData(result); setDraft(result.compilation.configuration === null ? '' : JSON.stringify(result.compilation.configuration, null, 2));
    } catch (e) { if (current === revision.current) setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function validate() {
    const template = data?.compilation.template_name;
    if (mutationBlocked || busy || isWriteInFlight() || !template || !data?.compilation.configuration || !draft.trim()) return;
    const current = ++revision.current; setBusy(true); setCandidate(null); setCandidateInput(''); setError(''); setNotice('');
    try {
      const result = await validateFullPolicyCandidate(template, json(draft));
      if (current !== revision.current) return;
      setCandidate(result); setCandidateInput(draft); setNotice('当前编辑已通过十二模板严格校验；引用仍待实际服务器事实复核，没有创建策略或授予权限。');
    } catch (e) { if (current === revision.current) setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  function apply() {
    if (mutationBlocked || busy || isWriteInFlight() || !onCandidate || !candidate || candidateInput !== draft || candidate.template_name !== data?.compilation.template_name) return;
    onCandidate({ templateName: candidate.template_name, configuration: structuredClone(candidate.normalized_configuration), configurationHash: candidate.configuration_hash });
    setNotice('已填入父工作区的待验证草稿；仍需其原校验、完整复核、理由和明确确认。');
  }
  const compilation = data?.compilation;
  return <section className="readonly-section" aria-label="完整自然策略候选编译"><h4>自然规则 → 待复核候选</h4><p>默认使用有限离线规则。明确金额、日期与引用才产生候选；不创建策略、不激活、不确认、不执行资金。</p>
    {mutationBlocked && <p className="notice">其他族有待核对原请求，禁止提交新候选请求；句式目录仍可只读查看。</p>}
    <details><summary>查看服务器十二模板受控句式</summary>{grammar.isPending && <p role="status">正在读取有限句式目录…</p>}{grammar.isError && <p role="alert">{errorMessage(grammar.error)}</p>}{grammar.data && <><p>{grammar.data.supported_scope}</p><p>以下为合成句式说明；示例账户、收款人、日期和产品类别没有当前事实或授权证明。</p><ul>{compilerTemplates.map((name) => <li key={name}><strong>{name}</strong><p>{grammar.data!.examples[name]}</p><button type="button" disabled={busy} onClick={() => { changeSource(grammar.data!.examples[name]); setNotice('已填入合成句式说明；请改为自己明确的金额、日期和引用。尚未编译。'); }}>填入{name}句式</button></li>)}</ul></>}</details>
    <label className="field">完整策略原句<textarea rows={4} maxLength={4000} value={text} onChange={(event) => changeSource(event.target.value)} /></label>
    <label className="full-policy-check"><input type="checkbox" disabled={busy} checked={comparison} onChange={(event) => { setComparison(event.target.checked); changeComparison(); }} />与我提供的另一个配置候选比较（不是当前策略版本）</label>
    {comparison && <><label className="field">比较候选模板<select disabled={busy} value={comparisonTemplate} onChange={(event) => { setComparisonTemplate(event.target.value as TemplateName); changeComparison(); }}>{compilerTemplates.map((name) => <option value={name} key={name}>{name}</option>)}</select></label><label className="field">比较候选完整JSON<textarea rows={5} disabled={busy} value={comparisonJson} onChange={(event) => { setComparisonJson(event.target.value); changeComparison(); }} /></label></>}
    <button type="button" disabled={blocked || !text.length || comparison && !comparisonJson.trim()} onClick={() => void compile()}>{busy ? '正在读取候选…' : '只读编译完整候选'}</button>
    {error && <p role="alert">{error}；未自动重试，旧候选已清除。</p>}{notice && <p role="status">{notice}</p>}
    {data && compilation && <section aria-label="完整自然策略编译结果"><h5>{compilation.status} · 仍需用户完整复核</h5><p>{compilation.summary}</p><p>服务端日期 {data.reference_date} · {data.timezone}；原文本摘要 <code>{compilation.original_text_sha256}</code>。</p><p>来源 {compilation.evidence_level}；引用 NOT_SERVER_VERIFIED；没有确认记录或银行权限。可选模型默认关闭。</p>
      <p>原schema默认字段：{compilation.defaulted_fields.join('、') || '无'}。默认值未伪装为原句明确声明。</p>
      {compilation.issues.length > 0 && <ul aria-label="候选具体不足">{compilation.issues.map((issue, index) => <li key={index}>{issue.code} · {issue.field} · {issue.message}{issue.source_fragment && <blockquote>{issue.source_fragment}</blockquote>}</li>)}</ul>}
      <details><summary>原句提取片段与位置</summary><ul>{compilation.source_fragments.map((fragment, index) => <li key={index}>{fragment.field} · 码点 {fragment.start}–{fragment.end}<blockquote>{fragment.redacted_text}</blockquote><code>{fragment.original_fragment_sha256}</code></li>)}</ul></details>
      <details><summary>候选差异解释</summary><p>{data.comparison_source === 'NONE' ? '没有提供比较候选。' : '比较来源为用户提供候选，不是读取当前策略。'}</p>{compilation.differences.length === 0 ? <p>未返回字段差异。</p> : <ul>{compilation.differences.map((difference, index) => <li key={index}><strong>{difference.field}</strong><p>{difference.explanation}</p><pre>{JSON.stringify({ before: difference.before, after: difference.after }, null, 2)}</pre></li>)}</ul>}</details>
      <details><summary>查看原编译响应JSON</summary><pre className="readonly-raw">{getOriginalFullCompilationResponse(data) ?? '未保留原响应文本'}</pre></details>
      {compilation.configuration === null ? <p className="notice">候选配置未形成；UNKNOWN、缺失或歧义不会被填为默认成功。请补充明确原值后重新编译。</p> : <section aria-label="完整自然候选编辑"><p>编译候选hash <code>{compilation.configuration_hash}</code>。可以编辑，但使用前必须重新严格校验。</p><label className="field">自然候选完整配置JSON（金额为整数分）<textarea rows={12} disabled={busy} value={draft} onChange={(event) => { revision.current++; setDraft(event.target.value); setCandidate(null); setCandidateInput(''); setNotice(''); setError(''); }} /></label><button type="button" disabled={blocked || !draft.trim()} onClick={() => void validate()}>仅校验当前编辑候选</button>
        {candidate && <><p>当前编辑校验hash <code>{candidate.configuration_hash}</code>；candidate_only=true，authority_granted=false，引用仍待验真。</p><details><summary>服务器规范化编辑配置</summary><pre>{JSON.stringify(candidate.normalized_configuration, null, 2)}</pre></details>{onCandidate ? <button type="button" disabled={blocked || candidateInput !== draft} onClick={apply}>作为待验证草稿使用</button> : <p>当前没有接入确认工作区；可以复核候选，尚未提交策略。</p>}</>}
      </section>}
    </section>}
  </section>;
}
