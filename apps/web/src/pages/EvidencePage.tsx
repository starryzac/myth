import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { evidenceKinds, getEvidenceGraph, getFacts, getOriginalEvidenceResponse, isEvidenceIdentity, isEvidenceKind } from '../api/evidence';
import type { EvidenceIssue, EvidenceKind, FactQuery, FactsResponse, GraphResponse } from '../api/evidence';
import { errorMessage } from '../api/http';
import { isFullGraphKind } from '../api/full-evidence-graph';
import FullEvidenceGraphPanel from '../components/FullEvidenceGraphPanel';

const kindLabels: Record<EvidenceKind, string> = { EVIDENCE: '事实证据', PROPOSAL: '策略候选', POLICY: '策略', POLICY_VERSION: '策略版本', DECISION: '决策', ACTION: '动作', RECEIPT: '回执' };
const stateLabels: Record<string, string> = { VALID: '当前查询范围有效', CONFLICTED: '存在冲突', UNKNOWN: '未知或未证明', SUPERSEDED: '原件标记已被替代', ACTIVE: '原件标记有效',
  REFERENCES_RESOLVED: '引用已解析（未证明资金或银行验真）' };
const relationLabels: Record<string, string> = { SUPPORTED_BY: '依据证据', USES_VERSION: '使用版本', SUPERSEDES: '替代原证据', CONFIRMED_AS: '确认为策略', DERIVED_FROM: '派生自', GENERATED_BY: '由决策产生', EXECUTION_OF: '对应动作', HAS_VERSION: '拥有版本' };
const uncoveredLabels: Record<string, string> = { 'Public ingestion of new fact revisions': '新事实修订的公开录入尚未支持', 'External bank anchor verification in this view': '此视图未验证外部银行锚点', 'Exact historical mutable action/policy status': '未重建动作与策略可变状态的完整历史' };
function unsafeNumber(value: unknown, depth = 0): boolean {
  if (depth > 64) return true;
  if (typeof value === 'number') return !Number.isFinite(value) || Number.isInteger(value) && !Number.isSafeInteger(value);
  if (Array.isArray(value)) return value.some((item) => unsafeNumber(item, depth + 1));
  return typeof value === 'object' && value !== null && Object.entries(value).some(([key, item]) => key.endsWith('_cents') && typeof item === 'number' && !Number.isSafeInteger(item) || unsafeNumber(item, depth + 1));
}
function OriginalCopy({ value }: { value: Record<string, unknown> }) {
  return unsafeNumber(value) ? <p className="notice">此原件含不能精确展示的数字或过深结构，解析副本不展示其内容。请查看下方完整原JSON响应。</p>
    : <details><summary>查看此原件解析字段</summary><p className="caption">这是字段解析副本，不作金融计算；原JSON响应另行保留。</p><pre className="readonly-raw">{JSON.stringify(value, null, 2)}</pre></details>;
}
function Issues({ issues }: { issues: EvidenceIssue[] }) {
  return issues.length > 0 ? <ul className="issues">{issues.map((issue, index) => <li key={index}><strong>{issue.code}</strong><pre className="readonly-raw">{JSON.stringify(issue, null, 2)}</pre></li>)}</ul> : <p className="caption">服务端本次查询未列出问题；这不代表获得执行权限或外部锚点已验真。</p>;
}
function Facts({ data, open }: { data: FactsResponse; open: (id: string, knownAt: string) => void }) {
  return <section className="readonly-section" aria-label="事实查询结果"><h3>事实查询结果 · {stateLabels[data.state]}</h3><p>有效时 {data.valid_at}；知悉时 {data.known_at}</p>
    <p className="caption">有效开始包含，结束不包含。{data.complete_within_registered_capacity ? '在服务端已登记容量内完整' : '超过服务端已登记容量，结果不完整'}；可变历史状态未重建，不授予执行权限。</p>
    <Issues issues={data.issues} />{data.groups.length === 0 && <p className="notice">没有覆盖本次时点的事实，不能推定为零或成功。</p>}
    <div className="evidence-fact-groups">{data.groups.map((group) => <article className="card" key={JSON.stringify([group.source_type, group.source_ref])}><h4>{group.source_type} · {group.source_ref}</h4><p>{stateLabels[group.state]}</p>
      {group.originals.map((original) => <section className="evidence-fact-original" key={original.id}><h5>证据 <code>{original.id}</code></h5><dl className="readonly-fields">
        <div><dt>证据等级</dt><dd>{original.evidence_level}</dd></div><div><dt>原状态标记</dt><dd>{stateLabels[original.status] ?? original.status} · {original.status}</dd></div>
        <div><dt>有效区间</dt><dd>{original.valid_from} 至 {original.valid_to ?? '未设置结束'}（结束不包含）</dd></div><div><dt>观察时点</dt><dd>{original.observed_at}</dd></div>
        <div><dt>替代的原证据</dt><dd>{original.supersedes_id ?? '没有原引用'}</dd></div><div><dt>内容摘要</dt><dd>{original.content_hash}</dd></div></dl>
        <OriginalCopy value={original.content} />
        <button type="button" onClick={() => open(original.id, data.known_at)}>追溯此证据关系</button>
      </section>)}</article>)}</div><details><summary>事实原JSON响应</summary><pre className="readonly-raw">{getOriginalEvidenceResponse(data) ?? '原JSON字节未保留'}</pre></details>
  </section>;
}
function Graph({ data }: { data: GraphResponse }) {
  const [nodeKey, setNodeKey] = useState(data.root); const [filter, setFilter] = useState<EvidenceKind | 'ALL'>('ALL');
  const selected = data.nodes.find((node) => node.key === nodeKey); const byKey = new Map(data.nodes.map((node) => [node.key, node]));
  return <section className="readonly-section" aria-label="证据关系结果"><h3>实际原件关系 · {stateLabels[data.state]}</h3><p>知悉时 {data.known_at}；根 <code>{data.root}</code></p>
    <p className="notice">图只展示已保存的引用。回执节点存在、内容摘要存在或引用已解析，都不能替代原银行核验与审计链验证。</p><Issues issues={data.issues} />
    <label className="field">查看节点种类<select value={filter} onChange={(e) => setFilter(e.target.value as EvidenceKind | 'ALL')}><option value="ALL">全部原节点</option>{evidenceKinds.map((kind) => <option value={kind} key={kind}>{kindLabels[kind]}</option>)}</select></label>
    <div className="evidence-graph-layout"><ul className="evidence-graph-nodes" aria-label="实际证据节点">{data.nodes.filter((node) => filter === 'ALL' || node.kind === filter).map((node) => <li key={node.key}>
      <button type="button" aria-pressed={node.key === nodeKey} onClick={() => setNodeKey(node.key)}><strong>{kindLabels[node.kind]}</strong><code>{node.id}</code><span>{node.key === data.root ? '查询根原件' : '关联原件'}</span></button>
    </li>)}</ul><div className="evidence-node-detail">{selected ? <><h4>所选原件 · {kindLabels[selected.kind]}</h4><p><code>{selected.key}</code></p><p className="caption">字段是原响应的解析副本，不作金额计算；完整原JSON在下方。</p>
      <dl className="readonly-fields">{Object.entries(selected.original).filter(([key, value]) => typeof value === 'string' && !key.endsWith('_cents')).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl><OriginalCopy value={selected.original} /></> : <p>所选原件缺失，未知。</p>}</div></div>
    <h4>原有向关系</h4><ul className="evidence-graph-edges" aria-label="实际证据边">{data.edges.map((edge) => <li key={JSON.stringify([edge.from, edge.to, edge.relation])}>
      <p><code>{edge.from}</code><span aria-hidden="true"> → </span><span>{relationLabels[edge.relation]} · {edge.relation}</span><span aria-hidden="true"> → </span><code>{edge.to}</code></p>
      {byKey.has(edge.to) ? <button type="button" onClick={() => setNodeKey(edge.to)}>查看关系目标</button> : <p className="notice">目标原件未提供，保留断链或容量问题，不能补造节点。</p>}
    </li>)}</ul>{data.edges.length === 0 && <p>服务端未返回有向引用，不补画推测关系。</p>}
    <h4>此视图未覆盖</h4><ul>{data.uncovered.map((item) => <li key={item}>{uncoveredLabels[item] ?? item}</li>)}</ul>
    <details><summary>证据图原JSON响应</summary><pre className="readonly-raw">{getOriginalEvidenceResponse(data) ?? '原JSON字节未保留'}</pre></details>
  </section>;
}
function EvidenceWorkspace({ kind, identity }: { kind?: string; identity?: string }) {
  const initialValid = isEvidenceKind(kind) && isEvidenceIdentity(identity);
  const [validAt, setValidAt] = useState(''); const [knownAt, setKnownAt] = useState(''); const [sourceType, setSourceType] = useState(''); const [sourceRef, setSourceRef] = useState('');
  const [factRequest, setFactRequest] = useState<FactQuery>({});
  const [graphKind, setGraphKind] = useState<EvidenceKind>(initialValid ? kind : 'EVIDENCE'); const [graphIdentity, setGraphIdentity] = useState(identity ?? '');
  const [graphKnown, setGraphKnown] = useState(''); const [graphError, setGraphError] = useState(!initialValid && (kind !== undefined || identity !== undefined) ? '证据深链种类或UUID无效，请核对原编号。' : '');
  const [graphRequest, setGraphRequest] = useState<{ kind: EvidenceKind; identity: string; knownAt?: string } | null>(initialValid ? { kind, identity } : null);
  const graphRef = useRef<HTMLElement>(null);
  const facts = useQuery({ queryKey: ['evidence-facts', factRequest], queryFn: () => getFacts(factRequest), retry: false });
  const graph = useQuery({ queryKey: ['evidence-graph', graphRequest], queryFn: () => getEvidenceGraph(graphRequest!.kind, graphRequest!.identity, graphRequest!.knownAt), enabled: graphRequest !== null, retry: false });
  function openEvidence(id: string, known: string) { setGraphKind('EVIDENCE'); setGraphIdentity(id); setGraphKnown(known); setGraphError(''); setGraphRequest({ kind: 'EVIDENCE', identity: id, knownAt: known }); graphRef.current?.focus(); }
  function readGraph() {
    if (!isEvidenceIdentity(graphIdentity.trim())) { setGraphError('需要原件UUID；不能将名称或其他类型编号猜成证据身份。'); return; }
    setGraphError(''); setGraphRequest({ kind: graphKind, identity: graphIdentity.trim(), knownAt: graphKnown.trim() || undefined });
  }
  return <div className="evidence-page"><section className="page-intro"><div><p className="eyebrow">分开有效时、知悉时和执行权限</p><h2>事实与证据</h2></div></section>
    <p className="simulation-note">只读事实与原件引用；不录入修订、不确认策略、不提交资金动作。未来知悉时由服务端可信时钟拒绝。</p>
    <section className="card readonly-section" aria-label="双时态事实查询"><h3>按两个时点查询事实</h3><form onSubmit={(event) => { event.preventDefault(); setFactRequest({ valid_at: validAt.trim() || undefined, known_at: knownAt.trim() || undefined, source_type: sourceType.trim() || undefined, source_ref: sourceRef.trim() || undefined }); }}>
      <div className="readonly-query-grid"><label className="field">事实有效时（含时区；空白用服务端当前时点）<input value={validAt} onChange={(e) => setValidAt(e.target.value)} placeholder="2026-10-05T12:00:00+08:00" /></label>
        <label className="field">系统知悉时（含时区；空白用服务端当前时点）<input value={knownAt} onChange={(e) => setKnownAt(e.target.value)} placeholder="2026-10-05T12:00:00+08:00" /></label>
        <label className="field">来源类型（可选）<input maxLength={48} value={sourceType} onChange={(e) => setSourceType(e.target.value)} /></label><label className="field">来源原引用（可选）<input maxLength={160} value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} /></label></div>
      <button type="submit" disabled={facts.isFetching}>查询事实</button><button type="button" disabled={facts.isFetching} onClick={() => void facts.refetch()}>重读当前查询</button></form>
      {facts.isPending && <p role="status">正在读取事实…</p>}{facts.isError && <p role="alert">{errorMessage(facts.error)}</p>}
      {!facts.isError && facts.data && <Facts data={facts.data} open={openEvidence} />}</section>
    <section className="card readonly-section evidence-query-focus" ref={graphRef} tabIndex={-1} aria-label="原件关系查询"><h3>从原件追溯实际引用</h3><form onSubmit={(event) => { event.preventDefault(); readGraph(); }}>
      <div className="readonly-query-grid"><label className="field">根原件种类<select value={graphKind} onChange={(e) => setGraphKind(e.target.value as EvidenceKind)}>{evidenceKinds.map((value) => <option value={value} key={value}>{kindLabels[value]}</option>)}</select></label>
        <label className="field">根原件UUID<input value={graphIdentity} onChange={(e) => setGraphIdentity(e.target.value)} /></label><label className="field">关系知悉时（含时区，可选）<input value={graphKnown} onChange={(e) => setGraphKnown(e.target.value)} placeholder="2026-10-05T12:00:00+08:00" /></label></div>
      <button type="submit" disabled={graph.isFetching}>查询原件关系</button>{graphRequest && <button type="button" disabled={graph.isFetching} onClick={() => void graph.refetch()}>重读原关系</button>}</form>
      {graphError && <p role="alert">{graphError}</p>}{graphRequest && graph.isPending && <p role="status">正在读取证据图…</p>}{graph.isError && <p role="alert">{errorMessage(graph.error)}</p>}
      {!graph.isError && graph.data && <Graph key={graph.data.root + graph.data.known_at} data={graph.data} />}</section>
  </div>;
}
export default function EvidencePage({ kind, identity }: { kind?: string; identity?: string }) {
  return <><EvidenceWorkspace key={`original:${kind ?? ''}/${identity ?? ''}`} kind={kind} identity={identity} />
    <FullEvidenceGraphPanel key={`full:${kind ?? ''}/${identity ?? ''}`} initialKind={isFullGraphKind(kind) ? kind : undefined} initialIdentity={isEvidenceIdentity(identity) ? identity : undefined} /></>;
}
