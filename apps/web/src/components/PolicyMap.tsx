import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPolicyMapVersions } from '../api/policies';
import type { Policy } from '../api/policies';
import { errorMessage } from '../api/http';
import { lifecycleLabels, policyTypes } from '../features/policy-form';
import { ConfigurationReview } from './PolicyConfigForm';
import { buildPolicyRelations, canEditPolicy, protectionFacts, relationStateLabels } from './policy-map-model';

export default function PolicyMap({ policies, onEdit }: { policies: Policy[]; onEdit: (policy: Policy) => void }) {
  const [query, setQuery] = useState(''); const [status, setStatus] = useState('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null); const [versionId, setVersionId] = useState<string | null>(null);
  const [relationId, setRelationId] = useState<string | null>(null); const [confirmedOnly, setConfirmedOnly] = useState(false);
  const detailRef = useRef<HTMLElement>(null);
  const selected = policies.find((policy) => policy.id === selectedId);
  const versions = useQuery({ queryKey: ['policy-versions', selected?.id, 'map-identity-checked'], queryFn: () => getPolicyMapVersions(selected!.id), enabled: !!selected, retry: false });
  const historical = versionId ? versions.data?.items.find((version) => version.id === versionId) : undefined;
  const candidateVersion = versionId ? historical : selected?.current_version;
  const displayVersion = candidateVersion?.policy_id === selected?.id ? candidateVersion : undefined;
  const graph = buildPolicyRelations(policies, selected && versionId ? { policyId: selected.id, version: historical ?? null } : undefined);
  const byId = new Map(policies.map((policy) => [policy.id, policy]));
  const visible = policies.filter((policy) => (status === 'ALL' || (status === 'UNAUTHORIZED' ? !policy.version_authorized : policy.effective_status === status)) &&
    `${policy.name} ${policy.id} ${policy.policy_type}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const linked = selected ? graph.relations.filter((edge) => edge.sourceId === selected.id || edge.targetKind === 'policy' && edge.targetId === selected.id) : [];
  const chosenRelation = linked.find((edge) => edge.id === relationId);
  function select(id: string) { setSelectedId(id); setVersionId(null); setRelationId(null); detailRef.current?.focus(); }
  return <section className="policy-map policy-section" aria-label="策略地图"><div className="card-heading"><h3>策略地图</h3><span className="caption">{policies.length} 项当前策略</span></div>
    <p className="caption">状态来自服务端当前读取；连线来自配置中的明确引用，不代表资金动作已获授权。没有引用字段时不会补画依赖。</p>
    <div className="policy-map-controls"><label className="field">查找策略<input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="名称、类型或原编号" /></label>
      <label className="field">策略状态<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="ALL">全部状态</option>
        {['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED', 'INVALIDATED'].map((state) => <option value={state} key={state}>只看{lifecycleLabels[state]}</option>)}
        <option value="UNAUTHORIZED">授权条件未满足</option></select></label></div>
    <div className="policy-map-layout"><ul className="policy-map-nodes" aria-label="地图策略节点">{visible.map((policy) => <li key={policy.id}>
      <button type="button" className="policy-map-node" aria-pressed={selectedId === policy.id} onClick={() => select(policy.id)}><strong>{policy.name}</strong>
        <span>{policyTypes[policy.policy_type] ?? policy.policy_type} · {lifecycleLabels[policy.effective_status] ?? policy.effective_status}</span>
        <span>{policy.current_version ? `当前版本 ${policy.current_version.version_number}` : '当前版本缺失，关系未知'} · {policy.version_authorized ? '版本授权条件有效' : '授权条件未满足'}</span>
        <span>到期时点 {policy.current_version?.valid_until ?? '未设置'}</span></button>
    </li>)}</ul>{visible.length === 0 && <p className="empty" role="status">没有匹配的策略；已选关系仍按原列表显示。</p>}
    <section ref={detailRef} className="policy-map-detail" aria-label="地图所选策略" tabIndex={-1}>
      {!selected ? <p className="empty">选择策略查看版本时间线、出入关系与修改入口。</p> : <><h4>{selected.name}</h4>
        <p>当前生命周期：{lifecycleLabels[selected.effective_status] ?? selected.effective_status}；当前授权条件{selected.version_authorized ? '有效' : '未满足'}。</p>
        <p className="caption">策略原编号 <code>{selected.id}</code></p>
        {versionId && <p className="notice">正在查看历史配置。关联策略展示其当前读取版本；历史查看不会恢复或替换授权。</p>}
        {versions.isPending && <p role="status">正在读取原版本时间线…</p>}
        {versions.isError && <div role="alert"><p>版本读取失败，历史关系未知：{errorMessage(versions.error)}</p><button type="button" onClick={() => void versions.refetch()}>重读所选版本</button></div>}
        {versions.data && <><label className="field">查看配置版本<select value={versionId ?? 'CURRENT'} onChange={(e) => { setVersionId(e.target.value === 'CURRENT' ? null : e.target.value); setRelationId(null); }}>
          <option value="CURRENT">当前版本 {selected.current_version?.version_number ?? '缺失'}</option>{versions.data.items.map((version) => <option key={version.id} value={version.id}>历史原件版本 {version.version_number} · {version.change_reason}</option>)}</select></label>
          {!versions.data.items.some((version) => version.id === selected.current_version?.id) && <p className="notice">版本列表未包含当前版本原件，时间线不完整。当前卡片保留原响应，不拼造缺项。</p>}
          <label className="checkbox-field"><input type="checkbox" checked={confirmedOnly} onChange={(e) => setConfirmedOnly(e.target.checked)} />时间线仅显示有确认时点的原件</label>
          <ol className="policy-version-timeline" aria-label="原版本时间线">{[...versions.data.items].sort((a, b) => b.version_number - a.version_number).filter((version) => !confirmedOnly || !!version.confirmed_at).map((version) => <li key={version.id}>
            <button type="button" aria-pressed={version.id === displayVersion?.id} onClick={() => { setVersionId(version.id === selected.current_version?.id ? null : version.id); setRelationId(null); }}>版本 {version.version_number} · {version.change_reason}</button>
            <p>确认 {version.confirmed_at ?? '未提供确认时点'}；生效 {version.valid_from ?? '未限制'}；到期 {version.valid_until ?? '未限制'}（结束时点不包含）</p>
            <p className="caption">{version.id === selected.current_version?.id ? '当前版本' : '历史只读原件'} · 创建 {version.created_at}</p>
          </li>)}</ol></>}
        {displayVersion ? <><details><summary>查看所选版本完整配置与保护约束</summary><ConfigurationReview configuration={displayVersion.configuration} />
          {protectionFacts(displayVersion.configuration).map((fact) => <p key={fact}>{fact}</p>)}<pre className="policy-map-raw">{JSON.stringify(displayVersion.configuration, null, 2)}</pre>
          <p className="caption">内容摘要 <code>{displayVersion.content_hash}</code>；前版本摘要 <code>{displayVersion.previous_hash ?? '无'}</code></p></details></>
          : <p className="notice">所选版本原件不可用，配置与关系待核验。</p>}
        <h4>明确配置关系</h4><ul className="policy-map-relations" aria-label="所选策略的有向关系">{linked.map((edge) => <li key={edge.id}><button type="button" aria-pressed={relationId === edge.id} onClick={() => setRelationId(edge.id)}>
          <span>{byId.get(edge.sourceId)?.name ?? edge.sourceId}</span><span className="policy-map-arrow" aria-hidden="true">→</span><span>{edge.label}：{edge.targetKind === 'policy' ? byId.get(edge.targetId)?.name ?? edge.targetId : `目标归属 ${edge.targetId}`}</span>
          <span className="caption">{relationStateLabels[edge.state]}</span></button></li>)}</ul>
        {linked.length === 0 && <p>当前配置未提供可展示的出入引用。实际动作依赖清单尚未由此接口提供。</p>}
        {chosenRelation && <div className="policy-map-relation-detail" role="status"><p>方向：来源策略 → 引用对象；原字段 <code>{chosenRelation.field}</code></p>
          <p>来源版本 <code>{chosenRelation.sourceVersionId}</code>；引用原编号 <code>{chosenRelation.targetId}</code></p><p>{relationStateLabels[chosenRelation.state]}</p>
          {chosenRelation.targetKind === 'policy' && byId.has(chosenRelation.targetId) && <button type="button" onClick={() => select(chosenRelation.targetId)}>查看引用策略</button>}</div>}
        {graph.issues.filter((issue) => issue.policyId === selected.id).map((issue, index) => <p className="notice" key={index}>{issue.message}</p>)}
        <button type="button" disabled={!canEditPolicy(selected)} onClick={() => onEdit(selected)}>修改当前策略并预览影响</button>
        <p className="caption">修改入口始终基于当前版本，历史原件不授予权限。已有预览提供91日资金边界；动作依赖、持仓和目标的完整影响待后端合同补齐。暂停、撤销与原历史入口在下方当前策略卡片。</p>
      </>}
    </section></div>
  </section>;
}
