import { useRef, useState } from 'react';
import { type DecisionSearchQuery, type DecisionSearchResponse, getDecisionSearch, getOriginalDecisionSearch } from '../api/decision-search';
import { errorMessage } from '../api/http';
import { isRunId } from '../api/decisions';

export default function DecisionSearchPanel({ ownerUserId }: { ownerUserId: string | undefined }) {
  const [mode, setMode] = useState<'action_id' | 'action_key'>('action_id');
  const [action, setAction] = useState(''); const [version, setVersion] = useState(''); const [epoch, setEpoch] = useState('');
  const [result, setResult] = useState<DecisionSearchResponse | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  const serial = useRef(0);
  function change(update: () => void) { serial.current += 1; update(); setResult(null); setError(''); setLoading(false); }
  async function search(offset = 0) {
    const requestSerial = ++serial.current; setError(''); setLoading(true); setResult(null);
    const body: DecisionSearchQuery = { action_id: mode === 'action_id' && action ? action : null, action_key: mode === 'action_key' && action ? action : null, policy_version_id: version || null, epoch_id: epoch || null, limit: 20, offset };
    const priorSource = result?.source_hash;
    try {
      if (!isRunId(ownerUserId)) throw new Error('当前拥有者尚未核对，没有发出请求');
      const data = await getDecisionSearch(body, ownerUserId);
      if (requestSerial !== serial.current) return;
      if (offset && priorSource !== data.source_hash) throw new Error('原来源集合已变化，请重新从第一页查询');
      setResult(data);
    } catch (failure) { if (requestSerial === serial.current) setError(errorMessage(failure)); }
    finally { if (requestSerial === serial.current) setLoading(false); }
  }
  const bound = result?.user_id === ownerUserId ? result : null;
  return <section className="card" aria-label="动作与版本搜索">
    <h3>按原动作与策略版本检索</h3><p>只读检索当前保存记录。精确原键、UUID和版本可相交；未找到不能证明没有发生动作，记录完整也不代表资金已执行。</p>
    <label>动作查询方式<select value={mode} onChange={(event) => change(() => setMode(event.target.value as typeof mode))}><option value="action_id">动作 UUID</option><option value="action_key">原动作键</option></select></label>
    <label>{mode === 'action_id' ? '动作 UUID（可选）' : '完整原动作键（可选）'}<input value={action} onChange={(event) => change(() => setAction(event.target.value))} /></label>
    <label>策略版本 UUID（可选）<input value={version} onChange={(event) => change(() => setVersion(event.target.value))} /></label>
    <label>审计轮次 UUID（可选）<input value={epoch} onChange={(event) => change(() => setEpoch(event.target.value))} /></label>
    <button disabled={loading} onClick={() => void search()}>只读搜索原记录</button>
    {loading && <p role="status">正在读取完整来源分母…</p>}{error && <p role="alert">{error}</p>}
    {bound && <div role="region" aria-label="审计搜索结果">
      <h4>{bound.state === 'SEARCHED' ? '当前限定来源已检索 · 无资金授权' : 'UNKNOWN · 来源或检索范围未完整'}</h4>
      <p>读取 {bound.read_at}；业务知悉时点 {bound.business_known_at}；仅当前持久记录，归档与完整审计链未在搜索中验证。</p>
      <dl><dt>拥有者全部原决策</dt><dd>{bound.inventory.actual_owned_decision_count}</dd><dt>本时点可见</dt><dd>{bound.inventory.known_decision_count}</dd><dt>本查询来源已捕获 / 应有</dt><dd>{bound.inventory.captured_scope_count} / {bound.inventory.selected_scope_count}</dd><dt>类型化已核 / 未核</dt><dd>{bound.inventory.verified_typed_count} / {bound.inventory.unverifiable_count}</dd><dt>已核匹配 / 全部匹配</dt><dd>{bound.verified_match_count} / {bound.total_match_count === null ? '未知' : bound.total_match_count}</dd></dl>
      {(bound.issues.length > 0 || bound.unsupported_families.length > 0) && <div role="note"><p>具体未覆盖项</p>{[...bound.issues, ...bound.unsupported_families].map((issue, index) => <p key={`${issue}:${index}`}>{issue}</p>)}</div>}
      {!bound.items.length && <p>本页没有已核匹配；这不是原请求未提交或无资金动作的证明。</p>}
      {bound.items.map((item) => <article className="trace-item" key={item.run_id}><h4>{item.phase ?? '原阶段未记录'} · {item.match_state}</h4><p>{item.as_of} · 原记录状态 {item.record_status} · {item.completeness}</p><a href={`#decisions/${item.run_id}`}>查看原决策 {item.run_id}</a>{item.action_ids.map((id) => <p key={id}>原动作 {id}</p>)}<details><summary>原关联指针与摘要</summary><p>快照 {item.snapshot_hash}</p><p>轨迹 {item.trace_hash ?? '未取得'}</p>{item.references.map((ref, index) => <p key={index}>{ref.kind} {ref.identity} · {ref.pointer} · {ref.relation}</p>)}{item.issues.map((issue) => <p key={issue}>{issue}</p>)}</details></article>)}
      {bound.next_offset !== null && <button disabled={loading} onClick={() => void search(bound.next_offset!)}>读取下一页原来源</button>}
      <details><summary>完整原响应文本</summary><pre>{getOriginalDecisionSearch(bound) ?? '原响应未保留'}</pre></details>
    </div>}
  </section>;
}
