import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fullGraphKinds, getFullEvidenceGraph, getOriginalFullGraphResponse, isFullGraphKind, isFullGraphTime } from '../api/full-evidence-graph';
import type { FullGraph, FullGraphNode, FullGraphRootKind } from '../api/full-evidence-graph';
import { isEvidenceIdentity } from '../api/evidence';
import { errorMessage } from '../api/http';

export type FullEvidenceGraphPanelProps = { initialKind?: FullGraphRootKind; initialIdentity?: string; ownerUserId?: string };
type Query = { kind: FullGraphRootKind; identity: string; knownAt?: string; attempt: number };
const proofLabel = (state: string) => state === 'VERIFIED' ? '原服务报告 VERIFIED' : state === 'UNKNOWN' ? 'UNKNOWN · 未证明' : state === 'NOT_CHECKED' ? '仅引用导航 · 未验真' : '不适用';
function OriginalDetail({ node, graph, select }: { node: FullGraphNode; graph: FullGraph; select: (key: string) => void }) {
  const links = graph.edges.filter((edge) => edge.from_key === node.key || edge.to_key === node.key);
  const proofRefs = node.proof.original_refs ?? [];
  return <section className="evidence-node-detail card" aria-label="完整图节点原件与引用">
    <h4>{node.kind} · {node.id}</h4>
    <dl className="readonly-fields"><dt>所有权范围</dt><dd>{node.owner_scope === 'CURRENT_USER' ? '当前用户' : '共享产品目录'}</dd><dt>知悉时间</dt><dd>{node.known_at}</dd><dt>原行摘要</dt><dd>{node.row_hash ?? '不可用 · null'}</dd><dt>来源分类</dt><dd>{node.source_classification ?? '未提供'}</dd><dt>验真范围</dt><dd>{proofLabel(node.proof.state)} · {node.proof.check}</dd><dt>具体说明</dt><dd>{node.proof.detail}</dd></dl>
    {node.original === null ? <p className="notice">历史原件内容不可用（null）。没有重建过去的余额、状态或权限。</p> : <p className="caption">原件内容保留在下方完整 HTTP 原文本；这里只展示身份和来源，不把 JSON 中的金额转成计算结果。</p>}
    {proofRefs.length > 0 && <details><summary>原服务验真引用</summary><ul>{proofRefs.map((ref, index) => <li key={index}>{ref}</li>)}</ul></details>}
    <h5>双向引用导航（保留原有方向）</h5>
    {links.length === 0 ? <p>本次显示范围内没有引用边。</p> : <ul>{links.map((edge) => { const target = edge.from_key === node.key ? edge.to_key : edge.from_key; return <li key={JSON.stringify(edge)}><span>{edge.from_key === node.key ? '指向' : '来自'} · {edge.relation} · {edge.pointer} </span><button type="button" onClick={() => select(target)}>{target}</button></li>; })}</ul>}
  </section>;
}
function Result({ graph }: { graph: FullGraph }) {
  const [selectedKey, setSelected] = useState(graph.nodes.find((node) => node.key === graph.root)?.key ?? graph.nodes[0]?.key ?? '');
  const [filter, setFilter] = useState('ALL');
  const selected = graph.nodes.find((node) => node.key === selectedKey);
  return <section className="readonly-section" aria-label="完整证据图结果">
    <h3>{graph.state === 'REFERENCES_RESOLVED' ? '引用已解析 · 不代表金融成功' : 'UNKNOWN · 证据图未完整证明'}</h3>
    <p className="simulation-note">只读模拟来源；不授予权限、不修账、不执行资金动作。</p>
    <dl className="readonly-fields"><dt>原用户</dt><dd>{graph.user_id}</dd><dt>本次读取时点</dt><dd>{graph.as_of}</dd><dt>知识时点</dt><dd>{graph.known_at}</dd><dt>实际节点分母 / 展示</dt><dd>{graph.expected_node_count} / {graph.displayed_node_count}</dd><dt>实际边分母 / 展示</dt><dd>{graph.expected_edge_count} / {graph.edges.length}</dd><dt>审计</dt><dd>{proofLabel(graph.audit_proof.state)} · {graph.audit_proof.detail}</dd><dt>银行</dt><dd>{proofLabel(graph.bank_proof.state)} · {graph.bank_proof.detail}</dd><dt>原输入摘要</dt><dd>{graph.input_hash}</dd></dl>
    <details><summary>35 类来源完整分母 · {graph.complete_registered_inventory ? '注册来源已完整捕获' : '来源捕获不完整'}</summary>
      <table><caption>同 owner 的实际行数、该知识时点可知行数与捕获数；不是仅当前连通节点数。</caption><thead><tr><th>类型 / 表</th><th>范围</th><th>实际</th><th>可知</th><th>捕获</th><th>完整</th></tr></thead><tbody>{graph.inventory.map((row) => <tr key={row.kind}><th>{row.kind} / {row.table}</th><td>{row.owner_scope === 'CURRENT_USER' ? '当前用户' : '共享目录'}</td><td>{row.actual_owned_count}</td><td>{row.known_count}</td><td>{row.captured_count}</td><td>{row.complete ? '是' : '否'}</td></tr>)}</tbody></table>
    </details>
    {graph.issues.length > 0 && <div role="note" className="notice"><h4>具体不足</h4><ul>{graph.issues.map((issue, index) => <li key={index}>{issue.code} · {issue.reference} · {issue.detail}</li>)}</ul></div>}
    <label className="field">完整图节点类型筛选<select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="ALL">全部展示节点</option>{[...new Set(graph.nodes.map((node) => node.kind))].map((kind) => <option key={kind}>{kind}</option>)}</select></label>
    <div className="evidence-graph-layout"><div className="evidence-graph-nodes" aria-label="完整图节点列表">{graph.nodes.filter((node) => filter === 'ALL' || node.kind === filter).map((node) => <button type="button" className="card" aria-pressed={selectedKey === node.key} key={node.key} onClick={() => setSelected(node.key)}>{node.kind} · {node.id}{node.original === null ? ' · 历史内容不可用' : ''}</button>)}</div>
      {selected ? <OriginalDetail node={selected} graph={graph} select={setSelected} /> : <p className="notice">图根未在当前展示预算内。请保留实际分母与容量不足信息。</p>}
    </div>
    <details><summary>完整证据图 HTTP 原文本（整数分保持原字符）</summary><pre className="readonly-raw">{getOriginalFullGraphResponse(graph) ?? '原 HTTP 文本未提供；不以解析值替代原件'}</pre></details>
    <details><summary>当前覆盖限制</summary><ul>{graph.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul></details>
  </section>;
}
export default function FullEvidenceGraphPanel({ initialKind = 'ACCOUNT', initialIdentity = '', ownerUserId }: FullEvidenceGraphPanelProps) {
  const [kind, setKind] = useState<FullGraphRootKind>(initialKind); const [identity, setIdentity] = useState(initialIdentity);
  const [known, setKnown] = useState(''); const [query, setQuery] = useState<Query | null>(null); const [validation, setValidation] = useState<string | null>(null);
  const attempt = useRef(0);
  const result = useQuery({ queryKey: ['full-evidence-graph-v2', ownerUserId, query], enabled: query !== null, retry: false, queryFn: () => { if (!query) throw new Error('未选择原件'); return getFullEvidenceGraph(query.kind, query.identity, query.knownAt, ownerUserId); } });
  const clear = () => { setQuery(null); setValidation(null); };
  const submit = () => {
    if (!isFullGraphKind(kind) || !isEvidenceIdentity(identity) || known !== '' && !isFullGraphTime(known)) { clear(); setValidation('请选择原件类型、完整 UUID 和带时区的知识时点；没有发出请求。'); return; }
    setValidation(null); attempt.current += 1; setQuery({ kind, identity: identity.toLowerCase(), ...(known === '' ? {} : { knownAt: known }), attempt: attempt.current });
  };
  return <section className="readonly-section" aria-label="完整证据图只读工作区">
    <h2>完整证据图</h2><p>从真实原件查双向引用。旧七类图与本视图各保留自己的协议和摘要。</p>
    <div className="readonly-query-grid"><label className="field">完整图根类型<select value={kind} onChange={(event) => { if (isFullGraphKind(event.target.value)) setKind(event.target.value); clear(); }}>{fullGraphKinds.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label className="field">完整图根 UUID<input value={identity} onChange={(event) => { setIdentity(event.target.value); clear(); }} /></label>
      <label className="field">完整图知识时点（可选，需含时区）<input placeholder="2026-10-05T12:00:00+08:00" value={known} onChange={(event) => { setKnown(event.target.value); clear(); }} /></label>
      <button type="button" disabled={result.isFetching} onClick={submit}>{result.isFetching ? '只读查询中…' : '只读查询完整证据图'}</button>
    </div>
    {(validation || result.error) && <p role="alert">{validation ?? errorMessage(result.error)}</p>}
    {query && result.data && <Result key={JSON.stringify(query)} graph={result.data} />}
  </section>;
}
