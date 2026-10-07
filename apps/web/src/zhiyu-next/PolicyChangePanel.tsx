import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import type { MultiPreview } from '../api/full-policy-change-multi';
import { getPolicySchema, type PolicyRecord } from './policies';
import { previewPolicyChange, scopeTitles, type ChangeReview } from './policy-change';
import { buildFromSchema, formFromConfiguration, configurationSummary, type FormValues } from './policy-schema';
import PolicySchemaForm from './PolicySchemaForm';
import { businessText, money, userError } from './display';
import type { NextState } from './api';

export default function PolicyChangePanel({ record, environment, blocked, session, review, perform, runRead, edited, close }: { record: PolicyRecord; environment: NextState; blocked: boolean; session: LocalActorSession | null; review: ChangeReview | null; perform: (path: string, body: Record<string, unknown>) => Promise<void>; runRead: (work: () => Promise<MultiPreview>) => Promise<MultiPreview>; edited: () => void; close: () => void }) {
  const schema = useQuery({ queryKey: ['zhiyu-next-policy-schema', environment.environment_id, environment.epoch_id, record.template_name, record.dsl_version], queryFn: () => getPolicySchema(record.template_name, environment, record.dsl_version), enabled: !blocked, staleTime: Infinity, retry: false });
  const [values, setValues] = useState<FormValues>({}); const [preview, setPreview] = useState<MultiPreview | null>(null); const [error, setError] = useState(''); const [reason, setReason] = useState('');
  const initialized = useRef(''); const boundReview = review?.policy_id === record.policy_id && review.source_kind === record.source_kind && review.request.expected_version_id === record.current_version_id ? review : null;
  const displayed = boundReview?.preview ?? preview;
  const actorValid = !!session && Date.parse(session.principal.expires_at) > Date.now();
  const formal = record.source_kind !== 'GOAL_BRIDGE' && !(record.source_kind === 'MVP_POLICY' && ['LongTermGoalPolicy', 'AssetAuthorizationPolicy'].includes(record.template_name));
  useEffect(() => { if (schema.data && initialized.current !== `${record.current_version_id}:${schema.data.schema_sha256}`) { initialized.current = `${record.current_version_id}:${schema.data.schema_sha256}`; setValues(formFromConfiguration(schema.data.json_schema, record.configuration)); setPreview(null); setReason(''); } }, [schema.data, record.current_version_id, record.configuration]);
  function update(path: string, value: string | string[]) { setValues((existing) => ({ ...existing, [path]: value })); setPreview(null); edited(); }
  async function calculate() {
    if (!schema.data || blocked) return; setError(''); setPreview(null); edited();
    try {
      const configuration = buildFromSchema(schema.data.json_schema, values, schema.data.reference_choices);
      const result = await runRead(() => previewPolicyChange(record, configuration, environment)); setPreview(result);
      if (formal && actorValid && result.financial_impact.status !== 'UNKNOWN') await perform(`/zhiyu-next/policies/${record.source_kind}/${record.policy_id}/change-review`, { expected_version_id: record.current_version_id, expected_epoch_id: environment.epoch_id, configuration: result.after_configuration });
    } catch (cause) { setError(userError(cause)); }
  }
  function confirm() {
    if (!boundReview?.confirmation_eligible || !actorValid || blocked || !reason.trim() || boundReview.covered_scopes.length !== 1 || Date.parse(boundReview.expires_at) <= Date.now()) return;
    void perform(`/zhiyu-next/policies/${record.source_kind}/${record.policy_id}/change-confirm`, { expected_version_id: record.current_version_id, expected_epoch_id: environment.epoch_id, configuration: boundReview.preview.after_configuration, review_id: boundReview.review_id, accepted: true, reviewed_configuration_hash: boundReview.preview.candidate_configuration_hash, reviewed_review_hash: boundReview.review_hash, reviewed_scope: boundReview.covered_scopes[0], reason: reason.trim() });
  }
  const impact = displayed?.financial_impact;
  return <section className="zy-card zyn-policy-workspace" aria-label="现行规则修改"><div className="zy-section-heading"><h2>修改{businessText(record.name)}</h2><button className="zy-link" disabled={blocked} onClick={close}>取消修改</button></div><p>先核对当前版本与候选影响。实际修改只能确认已经覆盖的范围，未覆盖部分继续保留限制。</p>
    {schema.isPending && <p role="status">正在读取当前模板业务字段…</p>}{schema.isError && <p role="alert">{userError(schema.error)}</p>}
    {schema.data && <form onSubmit={(event) => { event.preventDefault(); void calculate(); }}><PolicySchemaForm schema={schema.data.json_schema} values={values} choices={schema.data.reference_choices} disabled={blocked} update={update} /><button className="zy-secondary" disabled={blocked} type="submit">计算修改影响</button></form>}
    {error && <p role="alert">{error}</p>}
    {impact && <section aria-label="修改影响"><h3>{impact.status === 'UNKNOWN' ? '修改影响尚未核实' : impact.status === 'PARTIAL' ? '修改影响部分可核实' : '修改影响已计算'}</h3><p>{impact.scope === 'UNSUPPORTED' ? '当前影响范围尚未支持。' : scopeTitles[impact.scope]}</p>
      <dl className="zy-facts"><div><dt>安全闲置资金</dt><dd>{money(impact.before?.safe_idle_cents)} → {money(impact.after?.safe_idle_cents)}</dd></div><div><dt>最低日内余量</dt><dd>{money(impact.before?.minimum_margin_cents)} → {money(impact.after?.minimum_margin_cents)}</dd></div></dl>
      <p>未来收入尚未到账，不计入当前现金；候选回收本金与产品容量不代表当前可执行资金。</p>
      <details className="zyn-policy-details"><summary>核对候选范围</summary><ul>{configurationSummary(displayed!.after_configuration, schema.data?.reference_choices).map((row, index) => <li key={index}>{businessText(row)}</li>)}</ul></details>
      {impact.product_capacities.length > 0 && <p>已覆盖 {impact.product_capacities.length} 个产品的独立容量；各产品容量不能相加为组合安排。</p>}
      {impact.recovery_candidates.length > 0 && <p>已覆盖 {impact.recovery_candidates.length} 笔持仓回收候选；尚未生成多仓执行。</p>}
      {impact.goal_allocation_after && <p>本次计算仅覆盖当前期 {impact.goal_allocation_after.goals.length} 个目标，不证明多期最优安排。</p>}
      <details className="zyn-policy-details" open={impact.status === 'UNKNOWN'}><summary>尚未覆盖与当前限制</summary><ul>{[...new Set([...displayed!.limitations, ...impact.limitations, ...impact.reasons, ...(boundReview?.uncovered_items ?? [])])].map((item, index) => <li key={index}>{businessText(item)}</li>)}</ul></details>
      {!formal && <p>完整目标修改需要同时重绑目标模型，此入口只提供基础资金影响预览，不确认改版。</p>}
      {formal && impact.status !== 'UNKNOWN' && !actorValid && <p>建立本轮确认身份后，重新计算影响以准备一次确认。</p>}
      {impact.status === 'UNKNOWN' && <p>影响尚未核实，当前不能确认财务修改。已有版本保持现状。</p>}
      {boundReview && <><p>审阅原件已独立核实。此次仅确认 {boundReview.covered_scopes.map((scope) => scopeTitles[scope]).join('、') || '当前尚无可确认范围'}；该确认不授予银行执行权限。</p><label className="zy-field" htmlFor="zyn-change-reason">修改原因<input id="zyn-change-reason" maxLength={1000} value={reason} disabled={blocked} onChange={(event) => setReason(event.target.value)} /></label><button className="zy-primary" disabled={blocked || !actorValid || !boundReview.confirmation_eligible || !reason.trim() || boundReview.covered_scopes.length !== 1 || Date.parse(boundReview.expires_at) <= Date.now()} onClick={confirm}>确认此范围内修改</button></>}
    </section>}
  </section>;
}
