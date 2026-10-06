import { useRef, useState } from 'react';
import type { Goal } from '../api/goals';
import type { FullGoalPreview } from '../api/full-goals';
import { errorMessage } from '../api/http';
import { getGoalAdjustmentOriginals, getOriginalGoalAdjustment, previewGoalAdjustments, type GoalAdjustmentRead, type GoalAdjustmentPreview, type GoalAdjustmentSelection } from '../api/goal-adjustments';

export default function GoalAdjustmentsPanel({ ownerUserId, goals, blocked = false, onReviewCandidate }: { ownerUserId: string | undefined; goals: readonly Goal[] | undefined; blocked?: boolean; onReviewCandidate?: (goalId: string, preview: FullGoalPreview) => void }) {
  const [read, setRead] = useState<GoalAdjustmentRead | null>(null); const [result, setResult] = useState<GoalAdjustmentPreview | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [goalId, setGoalId] = useState(''); const [field, setField] = useState<'monthly_min_cents' | 'deadline'>('monthly_min_cents'); const [lower, setLower] = useState(''); const [upper, setUpper] = useState(''); const generation = useRef(0);
  const scopeMatches = read !== null && read.user_id === ownerUserId && goals !== undefined && read.original_conflicts.registered_goal_count === goals.length && read.goals.every((row) => goals.some((goal) => goal.id === row.goal_id && goal.policy_id === row.policy_id && goal.policy_version_id === row.current_version_id));
  const current = scopeMatches ? read : null; const preview = current && result !== null && result.user_id === ownerUserId && result.original.original_conflicts.review_state_hash === current.original_conflicts.review_state_hash ? result : null;
  const selected = current?.goals.find((row) => row.goal_id === goalId);
  function edit() { generation.current++; setResult(null); setError(''); }
  function choose(id: string) { edit(); setGoalId(id); setLower(''); setUpper(''); }
  async function refresh() {
    if (!ownerUserId || goals === undefined || busy) return;
    const turn = ++generation.current; setBusy(true); setRead(null); setResult(null); setError(''); setGoalId(''); setLower(''); setUpper('');
    try { const value = await getGoalAdjustmentOriginals(ownerUserId, goals); if (turn === generation.current) setRead(value); }
    catch (e) { if (turn === generation.current) setError(errorMessage(e)); }
    finally { if (turn === generation.current) setBusy(false); }
  }
  async function submit() {
    if (!current || !selected || blocked || busy || current.state !== 'COMPUTED' || current.original_conflicts.epoch_id === null || current.original_conflicts.review_state_hash === null) return;
    setResult(null); setError('');
    try {
      let option: GoalAdjustmentSelection['adjustments'][number];
      if (field === 'monthly_min_cents') { if (!/^(0|[1-9][0-9]*)$/.test(lower) || !/^(0|[1-9][0-9]*)$/.test(upper)) throw new Error('月最低贡献范围使用非负整数分'); option = { field, goal_id: selected.goal_id, expected_version_id: selected.current_version_id, lower_cents: Number(lower), upper_cents: Number(upper) }; }
      else option = { field, goal_id: selected.goal_id, expected_version_id: selected.current_version_id, lower_date: lower, upper_date: upper };
      const body: GoalAdjustmentSelection = { expected_epoch_id: current.original_conflicts.epoch_id, reviewed_state_hash: current.original_conflicts.review_state_hash, adjustments: [option] };
      const turn = ++generation.current; setBusy(true);
      try { const value = await previewGoalAdjustments(body, current, goals); if (turn === generation.current) setResult(value); }
      finally { if (turn === generation.current) setBusy(false); }
    } catch (e) { setError(errorMessage(e)); }
  }
  return <section className="readonly-section" aria-label="明确月最低额或期限调整"><h3>明确选择目标调整范围</h3><p>月最低贡献是当前期偏好，不能靠降低它修复硬冲突。期限候选只计算当前期条件可行性，最低保证、归属、收入和全部保护保持；不承诺未来达标，不授资金权限。</p><button type="button" disabled={!ownerUserId || goals === undefined || busy} onClick={() => void refresh()}>只读读取当前调整依据</button>
    {busy && <p role="status">正在读取或只读预览…</p>}{error && <p role="alert">{error}</p>}{read && !scopeMatches && <p role="status">用户或目标版本已变化，旧依据已隐藏；请重新读取。</p>}
    {current && <><p>当前原件 {current.state}；原目标分母 {current.original_conflicts.registered_goal_count}；已覆盖 {current.goals.length}。未知不能填零。</p>{current.reasons.map((reason) => <p key={reason}>{reason}</p>)}<details><summary>完整读取原JSON</summary><pre className="readonly-raw">{getOriginalGoalAdjustment(current) ?? '原响应文本未保留'}</pre></details>
      {current.state === 'COMPUTED' && <><label className="field">调整目标<select aria-label="调整目标" value={goalId} disabled={busy} onChange={(event) => choose(event.target.value)}><option value="">明确选择一个目标</option>{current.goals.map((row) => <option key={row.goal_id} value={row.goal_id}>{goals?.find((g) => g.id === row.goal_id)?.name ?? row.goal_id}</option>)}</select></label>
        {selected && <><dl><dt>原版本</dt><dd>{selected.current_version_id}</dd><dt>月最低/目标/最高（分）</dt><dd>{selected.monthly_min_cents} / {selected.monthly_target_cents} / {selected.monthly_max_cents}</dd><dt>不可削最低保证（分）</dt><dd>{selected.minimum_guarantee_cents}</dd><dt>原期限</dt><dd>{selected.deadline}</dd><dt>已归属/本月已贡献（分）</dt><dd>{selected.current_owned_cents} / {selected.current_month_contributed_cents}</dd></dl>
          <label className="field">调整字段<select aria-label="调整字段" value={field} disabled={busy} onChange={(event) => { edit(); setField(event.target.value as 'monthly_min_cents' | 'deadline'); setLower(''); setUpper(''); }}><option value="monthly_min_cents">月最低贡献 · 仅偏好</option><option value="deadline">期限延长 · 条件规划</option></select></label><label className="field">明确范围下界<input aria-label="明确范围下界" type={field === 'deadline' ? 'date' : 'text'} value={lower} disabled={busy} onChange={(event) => { edit(); setLower(event.target.value); }} /></label><label className="field">明确范围上界<input aria-label="明确范围上界" type={field === 'deadline' ? 'date' : 'text'} value={upper} disabled={busy} onChange={(event) => { edit(); setUpper(event.target.value); }} /></label><button type="button" disabled={busy || blocked || !lower || !upper} onClick={() => void submit()}>仅预览所选范围</button></>}
      </>}
    </>}
    {preview && <section aria-label="目标范围调整原候选"><h4>{preview.state}</h4><p>实际子集 {preview.proposal?.evaluated_subset_count ?? '未知'} / 最多256；硬修复政策数 {preview.proposal?.hard_repair.changed_policy_count ?? '未知'}。月最低贡献候选不计入硬修复。</p>{preview.reasons.map((reason) => <p key={reason}>{reason}</p>)}{preview.proposal?.outcomes.map((row) => <p key={row.goal_id}>{row.field}：{row.original_value} → {row.proposed_value ?? '无候选'}；{row.reason}；偏好效果 {row.hypothetical_allocation?.status ?? '未单独计算'}</p>)}
      <p>不受硬修复影响的目标：{preview.proposal?.hard_repair.unaffected_goal_ids.join('、') ?? '未知'}</p>{preview.version_previews.map((row) => <article key={row.goal_id}><h5>{row.scope === 'SOFT_PREFERENCE_ONLY' ? '仅软偏好候选' : '当前期硬期限候选'}</h5><p>{row.original_value} → {row.proposed_value}；原目标 {row.goal_id}</p><p>FULL hash {row.actual_existing_preview.full_configuration_hash}</p><p>原执行配置 hash {row.actual_existing_preview.base_configuration_hash}</p><p>尚未确认；采用后须原模型页重新服务器预览、完整复核和双hash明确确认。</p>{onReviewCandidate && <button type="button" disabled={blocked || busy || !goals?.some((goal) => goal.id === row.goal_id && goal.policy_version_id === row.current_version_id)} onClick={() => onReviewCandidate(row.goal_id, row.actual_existing_preview)}>采用候选进入原模型复核</button>}</article>)}{preview.limitations.map((limit) => <p key={limit}>{limit}</p>)}<details><summary>完整候选原JSON</summary><pre className="readonly-raw">{getOriginalGoalAdjustment(preview) ?? '原响应文本未保留'}</pre></details></section>}
  </section>;
}
