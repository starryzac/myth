import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Goal } from '../api/goals';
import { getFullGoalConflicts, getOriginalGoalConflictResponse, previewFullGoalRepairs, validateGoalConflictGoals } from '../api/full-goal-conflicts';
import type { FullGoalConflicts, FullGoalRepairs, GoalRepairSelection } from '../api/full-goal-conflicts';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { recoverFullGoalOperation, useFullGoalOperation } from '../features/full-goal-operation';
import { useWriteInFlight } from '../features/write-flight';
import FullGoalModelPanel from './FullGoalModelPanel';

const money = (v: number | null | undefined) => v == null ? '未知 · 未提供' : `¥${formatMoneyCents(v)}`;
const names = { MINIMUM_GUARANTEE: '目标最低保障', DEADLINE_COMPLETION: '到期完成要求', MONTHLY_MAX: '当月最高贡献' };
type Range = { selected: boolean; minimum: string; maximum: string };
const emptyRange: Range = { selected: false, minimum: '', maximum: '' };
function amount(text: string): number { if (!/^(0|[1-9]\d*)$/.test(text) || !Number.isSafeInteger(Number(text))) throw new Error('调整范围必须为可精确表示的非负整数分'); return Number(text); }
function Conflict({ data, title }: { data: FullGoalConflicts; title: (id: string) => string }) {
  const e = data.explanation;
  return <section aria-label="原目标最小冲突与反事实"><p>原服务时点 {data.as_of} · 当前状态 {data.state} · 原登记 {data.registered_goal_count} 个 / 输入 {data.included_goal_ids.length} 个 / 未覆盖 {data.uncovered_goal_ids.length} 个。</p>
    <p>原许可修复：{data.current_permission_repair?.status ?? 'UNKNOWN · 未提供'}。原许可没有提高执行月max；用户选取新范围仅提出新策略候选。</p>
    {data.state === 'UNKNOWN' && <p className="notice">UNKNOWN · 原来源、银行、版本或完整分母未证明，冲突/修复未提供；不会补零或使用旧候选。</p>}
    <ul>{data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    {e && <><h4>原冲突状态 · {e.conflict.status}</h4><p>删除最小，不是最少基数。逐移除见证只证明这个最小集合去掉一项；不证明其他全部约束兼容。固定财务保护 {e.immutable_financial_point_count} 个时点，不调整硬保护。</p>
      {e.constraints.map((c) => <article key={c.constraint_id} aria-label={`原冲突约束 ${c.constraint_id}`}><h5>{title(c.goal_id)} · {names[c.kind]}</h5><p>原目标 <code>{c.goal_id}</code> · 原策略 <code>{c.policy_id}</code> · 原版本 <code>{c.current_version_id}</code></p><dl><dt>原参数</dt><dd>{money(c.original_parameter_cents)}</dd><dt>当前原已归属</dt><dd>{money(c.current_owned_cents)}</dd><dt>当月原已贡献</dt><dd>{money(c.current_month_contributed_cents)}</dd><dt>本次必要新增</dt><dd>{money(c.required_new_cents)}</dd><dt>当前原允许新增</dt><dd>{money(c.allowed_new_cents)}</dd></dl>{c.deadline && <p>原目标日期 {c.deadline}</p>}<ul>{c.source_refs.map((r) => <li key={r.evidence_id}>原来源 <a href={`#evidence/EVIDENCE/${r.evidence_id}`}>{r.evidence_id}</a> · hash <code>{r.content_hash}</code></li>)}</ul></article>)}
      <section aria-label="逐项移除原反事实见证"><h5>逐项移除原反事实见证</h5>{e.conflict.deletion_checks.map((check) => <article key={check.removed_constraint_id}><p>假设移除 <code>{check.removed_constraint_id}</code>：该最小集合的剩余约束可满足。</p><p>counterfactual_only=true；不改变原策略、确认或资金。</p><ul>{Object.entries(check.witness_amounts_cents).map(([id, value]) => <li key={id}>{title(id)} · <code>{id}</code>：{money(value)}</li>)}</ul></article>)}</section>
      {e.immutable_base_blocks.map((b) => <p className="notice" key={b.point_index}>原不可调整保护短缺：第{b.point_index}点 {b.date}，原现金 {money(b.cash_cents)}、保护 {money(b.protected_cents)}、短缺 {money(b.shortfall_cents)}。adjustable=false，不能用策略修复放松。</p>)}
      <p>不在这个最小集合中的目标：{e.goals_outside_this_minimal_set.map(title).join('、') || '本次没有'}；不因此称其全部约束已经兼容。</p></>}
    <details><summary>本次原来源、版本与摘要</summary><p>原epoch <code>{data.epoch_id ?? 'UNKNOWN'}</code> · 完整输入hash <code>{data.current_input_hash ?? 'UNKNOWN'}</code></p><p>读状态hash <code>{data.review_state_hash ?? 'UNKNOWN'}</code> · 真实规划来源摘要 <code>{data.planning_source_digest}</code></p><p>仅顶层读时点区别；真实经济事实、来源时点、有效窗口及保护变化必须重新读取。浏览器不独立重算求解与来源hash。</p><ul>{data.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
    <details><summary>完整冲突原HTTP响应</summary><pre className="readonly-raw">{getOriginalGoalConflictResponse(data) ?? '原响应文本未保留'}</pre></details>
  </section>;
}
function Repair({ data, title, goals, mutationBlocked }: { data: FullGoalRepairs; title: (id: string) => string; goals?: readonly Goal[]; mutationBlocked: boolean }) {
  const p = data.proposal, result = p?.repair;
  const [openedGoal, setOpenedGoal] = useState<string | null>(null);
  const family = useFullGoalOperation(), writing = useWriteInFlight();
  return <section aria-label="只读修复候选结果"><h4>只读修复候选 · {data.state}</h4><p>本次POST原时点 {data.as_of}；本次完整输入 {data.original_conflicts.current_input_hash ?? 'UNKNOWN'}。与上方GET是独立读取，不能拼成同一事务。</p><p className="notice">尚未确认；原执行上限、已归属资金及硬保护未改。没有金融动作或自动确认。</p>
    {data.state === 'UNKNOWN' && <p>UNKNOWN · 本次来源/求解未证明，没有成功候选或金额；请重新读取当前原事实。</p>}
    <ul>{data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
    {p && <><p>原输入 <code>{p.original_input_hash}</code> · 新规划搜索 <code>{p.search_input_hash}</code> · 用户范围 <code>{p.selection_hash}</code>。搜索登记不是已有资金权限。</p>{p.range_outcomes.map((range) => <p key={range.goal_id}>{title(range.goal_id)} · 原max {money(range.original_monthly_max_cents)} → 新候选 {money(range.proposed_monthly_max_cents)} · {range.reason}</p>)}
      {result && <><h5>原有限最小排序</h5><p>改变策略数 {result.changed_policy_count ?? '未知'} → 精确参数偏离 {result.parameter_deviation_numerator == null || result.parameter_deviation_denominator == null ? '未知' : `${result.parameter_deviation_numerator}/${result.parameter_deviation_denominator}`} → 重要性损失 {money(result.priority_loss_cents)}。</p><p>未受影响目标：{result.unaffected_goal_ids.map(title).join('、') || '本次没有'}。范围仅当前期、用户明确所选月max；不是所有模板/多期全局修复。</p></>}
    </>}
    {data.version_previews.map((row) => { const goal = goals?.find((current) => current.id === row.goal_id && current.policy_version_id === row.current_version_id && current.policy_id === row.actual_existing_preview.base_policy_impact.policy_id); return <article key={row.goal_id} aria-label={`待复核新目标版本 ${row.goal_id}`}><h5>{title(row.goal_id)} · 待复核新版本</h5><p>原当前版本 <code>{row.current_version_id}</code>；原max {money(row.original_monthly_max_cents)} / 新max {money(row.proposed_monthly_max_cents)}。</p><dl><dt>原完整配置hash</dt><dd><code>{row.original_full_configuration_hash}</code></dd><dt>新FULL待复核hash</dt><dd><code>{row.actual_existing_preview.full_configuration_hash}</code></dd><dt>新原执行策略待复核hash</dt><dd><code>{row.actual_existing_preview.base_configuration_hash}</code></dd></dl><details><summary>原配置与服务器规范化候选配置</summary><pre>{JSON.stringify({ original: row.original_full_configuration, candidate: row.actual_existing_preview.full_configuration }, null, 2)}</pre></details><details><summary>原现有财务影响完整预览</summary><pre>{JSON.stringify(row.actual_existing_preview, null, 2)}</pre></details>
      <label>原完整目标确认绑定（尚缺明确接受/理由/原键）<textarea readOnly rows={10} value={JSON.stringify(row.confirmation_bindings, null, 2)} /></label><p>尚未确认。请在该目标原完整模型工作区重新预览并明确复核双hash、填写理由；通过原完整请求/key持久恢复链确认新版本。展开与采用候选不会自动确认。</p><p>原接口 <code>{row.confirmation_endpoint}</code>；缺用户字段 {row.missing_explicit_user_fields.join(' / ')}。多目标未提供原子确认，每次确认后重新读取/规划。</p>
      <button type="button" disabled={!goal || mutationBlocked || family.busy || family.pending !== null || family.storage_error !== null || writing} onClick={() => setOpenedGoal(openedGoal === row.goal_id ? null : row.goal_id)}>{openedGoal === row.goal_id ? '收起原完整目标确认工作区' : '展开原完整目标确认工作区'}</button>{!goal && <p>当前Goal原件/版本/策略不匹配或未提供，不能打开确认流程；请刷新完整目标列表。</p>}
      {openedGoal === row.goal_id && goal && <FullGoalModelPanel goal={goal} blocked={mutationBlocked} reviewCandidate={row.actual_existing_preview} />}
    </article>; })}
    <details><summary>完整修复预览原HTTP响应</summary><pre className="readonly-raw">{getOriginalGoalConflictResponse(data) ?? '原响应文本未保留'}</pre></details><ul>{data.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul>
  </section>;
}
export default function GoalConflictRepairPanel({ goals, mutationBlocked = false }: { goals?: readonly Goal[]; mutationBlocked?: boolean }) {
  const query = useQuery({ queryKey: ['full-goal-conflicts'], queryFn: getFullGoalConflicts, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const family = useFullGoalOperation(), flight = useWriteInFlight();
  useEffect(() => { recoverFullGoalOperation(); }, []);
  const [ranges, setRanges] = useState<Record<string, Range>>({}); const [busy, setBusy] = useState(false); const busyRef = useRef(false); const generation = useRef(0);
  const [error, setError] = useState(''); const [preview, setPreview] = useState<{ value: FullGoalRepairs; reviewed: string | null | undefined; goalsKey: string } | null>(null);
  const goalsKey = goals === undefined ? 'UNPROVIDED' : goals.map((goal) => `${goal.id}:${goal.policy_id}:${goal.policy_version_id}`).sort().join('|');
  const title = (id: string) => goals?.find((g) => g.id === id)?.name ?? `原目标 ${id}`;
  let mismatch: string | null = null;
  if (query.data) try { validateGoalConflictGoals(query.data, goals); } catch (e) { mismatch = errorMessage(e); }
  const disabled = mutationBlocked || family.busy || family.pending !== null || family.storage_error !== null || flight || busy || query.isFetching || query.isError || !!mismatch || query.data?.state !== 'COMPUTED';
  const targets = query.data?.included_goal_ids.map((id) => ({ id, version: goals?.find((g) => g.id === id)?.policy_version_id ?? query.data?.explanation?.constraints.find((c) => c.goal_id === id)?.current_version_id })) ?? [];
  function edit(id: string, value: Partial<Range>) { generation.current++; setRanges((old) => ({ ...old, [id]: { ...(old[id] ?? emptyRange), ...value } })); setPreview(null); setError(''); }
  function refresh() { generation.current++; setPreview(null); setRanges({}); setError(''); void query.refetch(); }
  async function submit() {
    if (disabled || busyRef.current || !query.data) return;
    const original = query.data; const expectedGeneration = generation.current; setPreview(null); setError(''); busyRef.current = true; setBusy(true);
    try {
      const body: GoalRepairSelection = { expected_epoch_id: original.epoch_id!, reviewed_state_hash: original.review_state_hash!, adjustments: targets.filter((row) => ranges[row.id]?.selected).map((row) => { if (!row.version) throw new Error('当前原目标版本未提供'); const range = ranges[row.id]!; return { goal_id: row.id, expected_version_id: row.version, minimum_new_monthly_max_cents: amount(range.minimum), maximum_new_monthly_max_cents: amount(range.maximum) }; }) };
      const value = await previewFullGoalRepairs(original, body); if (generation.current === expectedGeneration) setPreview({ value, reviewed: original.review_state_hash, goalsKey });
    } catch (e) { if (generation.current === expectedGeneration) setError(`${errorMessage(e)}；未生成成功候选，请重新读取原版本和真实事实。`); }
    finally { busyRef.current = false; setBusy(false); }
  }
  return <section className="card readonly-section" aria-label="目标最小冲突与修复工作区"><h3>目标最小冲突与修复</h3><p>只读诊断与候选。财务硬保护、最低保障、已有归属、期限保持，不自动放松策略或执行资金。</p><button type="button" disabled={busy || query.isFetching} onClick={refresh}>只读刷新目标冲突</button>
    {query.isPending && <p role="status">正在读取当前真实完整目标、银行与保护来源…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}；旧报告不作为本次读取成功。</p>}{query.isFetching && query.data && <p role="status">重新读取中，下方仍是上次原报告，预览暂停。</p>}{mismatch && <p role="alert">{mismatch}；当前目标列表/版本不匹配，请刷新原目标与规划。</p>}{error && <p role="alert">{error}</p>}{family.storage_error && <p role="alert">{family.storage_error}</p>}
    {(mutationBlocked || family.pending) && <p className="notice">其他原命令正在处理或待核对，新的预览暂停；只读刷新仍可用。</p>}
    {!query.isError && !mismatch && query.data && <><Conflict data={query.data} title={title} />
      {query.data.state === 'COMPUTED' && <section aria-label="用户主动选择修复范围"><h4>用户主动提出新的月max范围</h4><p>选择不授予银行权限，也不改变当前原max。仅为只读新策略候选搜索；所有硬保护保持。</p>{goals === undefined && <p>未提供独立当前Goal列表；只有本次原冲突中带当前版本的目标可选，其他目标保持未提供。</p>}
        {targets.map((row) => { const range = ranges[row.id] ?? emptyRange; return <fieldset key={row.id} disabled={disabled || !row.version}><legend>{title(row.id)}</legend><p>原目标 <code>{row.id}</code>；当前版本 <code>{row.version ?? '未提供，不能选择'}</code></p><label><input type="checkbox" checked={range.selected} onChange={(e) => edit(row.id, { selected: e.target.checked })} />选择 {title(row.id)} 月max新范围</label>{range.selected && <><label>新月max下界（整数分）<input inputMode="numeric" value={range.minimum} onChange={(e) => edit(row.id, { minimum: e.target.value })} /></label><label>新月max上界（整数分）<input inputMode="numeric" value={range.maximum} onChange={(e) => edit(row.id, { maximum: e.target.value })} /></label></>}</fieldset>; })}
        <button type="button" disabled={disabled || !targets.some((row) => ranges[row.id]?.selected)} onClick={() => void submit()}>{busy ? '正在只读修复预览…' : '只读预览所选修复范围'}</button>
      </section>}
      {preview && preview.reviewed === query.data.review_state_hash && preview.goalsKey === goalsKey && !query.isFetching && <Repair data={preview.value} title={title} goals={goals} mutationBlocked={mutationBlocked} />}
    </>}
    <p className="caption">范围/原目标版本变化、刷新或预览失败会停止沿用旧候选。手动展开后须在原完整目标工作区重新预览并明确确认；诊断不自动确认，不放宽硬保护，资金执行另走原验证。</p><a href="#goals">前往原完整目标模型与确认流程</a>
  </section>;
}
