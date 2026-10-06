import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFullPolicies } from '../api/full-policies';
import type { FullPolicy } from '../api/full-policies';
import type { Goal } from '../api/goals';
import { confirmReleaseIntent, getOriginalReleaseResponse, loadReleaseReview, lookupReleaseIntent } from '../api/goal-release-authorizations';
import type { ReleaseResponse, ReleaseReview, ReleaseScope } from '../api/goal-release-authorizations';
import { parseCrossGoalConfiguration } from '../api/goal-reallocation';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { beginGoalReleaseAuthorizationOperation, clearGoalReleaseAuthorizationAfterLookup, endGoalReleaseAuthorizationAttempt, prepareGoalReleaseIntent, recoverGoalReleaseAuthorizationOperation, useGoalReleaseAuthorizationOperation } from '../features/goal-release-authorization-operation';

type SourceGoal = Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'>;
export type GoalReleaseAuthorizationPanelProps = { goal: SourceGoal; userId: string; epochId: string | null; mutationBlocked?: boolean };
const conditionNames = { HARD_OBLIGATION_SHORTFALL: '硬性义务短缺', LIVING_RESERVE_SHORTFALL: '生活费留存短缺', EMERGENCY_BUFFER_SHORTFALL: '应急缓冲短缺' };
function Scope({ scope, review }: { scope: ReleaseScope; review?: ReleaseReview }) {
  return <section className="readonly-section" aria-label="专用授权完整范围"><h5>全部源目标 · {scope.source_goals.length} 个</h5><p>原账户用户 <code>{scope.user_id}</code> · 原epoch <code>{scope.epoch_id}</code>。授权对象是以下全部来源目标，不只当前卡片目标。</p>
    <ul>{scope.source_goals.map((g) => <li key={g.goal_id}><h6>{review?.sources.find((s) => s.goal.id === g.goal_id)?.goal.name ?? '原来源目标'} · <code>{g.goal_id}</code></h6><p>原目标策略 <code>{g.original_policy_id}</code>；原执行版本 <code>{g.original_policy_version_id}</code>。</p><p>最低保障 ¥{formatMoneyCents(g.minimum_guarantee_cents)}；FULL模型证据 <code>{g.full_model_evidence_id}</code>。</p><details><summary>该目标完整模型来源摘要</summary><p>证据 <code>{g.full_model_evidence_hash}</code>；FULL配置 <code>{g.full_configuration_hash}</code>。</p>{review && <pre className="readonly-raw">{JSON.stringify(review.sources.find((s) => s.goal.id === g.goal_id)?.model, null, 2)}</pre>}</details></li>)}</ul>
    <h5>明确限定的紧急范围</h5><dl><dt>目的范围</dt><dd>PROTECTED_CASH · 受保护现金，不分配给其他普通目标</dd><dt>紧急条件</dt><dd>{scope.emergency_conditions.map((c) => conditionNames[c]).join('、')}</dd><dt>单次上限</dt><dd>¥{formatMoneyCents(scope.single_action_cap_cents)}</dd><dt>同策略生命周期累计上限</dt><dd>¥{formatMoneyCents(scope.total_cap_cents)} · POLICY_ID_ALL_VERSIONS，改版本不重置</dd><dt>生效下界</dt><dd>{scope.valid_from}</dd><dt>失效上界（不包含）</dt><dd>{scope.valid_until}</dd></dl>
    <p>仅列出的紧急条件可例外解除默认现金归属锁；本金释放、普通跨目标再分配、新增收入和改写原已归属收入均不允许。</p><p>原回拨规则 <code>{scope.policy_id}</code> · 原版本 <code>{scope.policy_version_id}</code> · 配置摘要 <code>{scope.policy_configuration_hash}</code>。</p>
  </section>;
}
function Recorded({ response }: { response: ReleaseResponse }) {
  return <section className="card readonly-section" aria-label="专用授权原记录"><h5>专用授权原记录 · {response.current_scope_status}</h5><p>原确认 <code>{response.original_authorization.authorization_id}</code> · {response.original_authorization.confirmed_at}。{response.current_scope_status === 'CURRENT' ? '该次服务端读取报告范围匹配；这份原记录不缓存当前有效授权，仍须另行核验实际资金、紧急条件与累计用量。' : '原记录只是历史确认；STALE、UNKNOWN或ARCHIVED不能当当前有效授权。'}</p><p className="notice">实际金额未验真（current_financial_amount_verified=false）；执行 NOT_IMPLEMENTED，未提交银行操作。明确确认范围不是资金已回拨。</p><Scope scope={response.original_authorization.scope} /><details><summary>{getOriginalReleaseResponse(response) ? '完整原授权HTTP记录' : '原响应的JSON展示（非HTTP原字节）'}</summary><pre className="readonly-raw">{getOriginalReleaseResponse(response) ?? JSON.stringify(response, null, 2)}</pre></details><p>原证据 <code>{response.evidence_id}</code> / <code>{response.evidence_hash}</code>；原trace <code>{response.original_trace_hash}</code>。浏览器核原内容/请求绑定，不独立重算整条审计或银行账本。</p></section>;
}
export default function GoalReleaseAuthorizationPanel({ goal, userId, epochId, mutationBlocked = false }: GoalReleaseAuthorizationPanelProps) {
  const operation = useGoalReleaseAuthorizationOperation();
  useEffect(() => { recoverGoalReleaseAuthorizationOperation(); }, []);
  const query = useQuery({ queryKey: ['full-policies', 'goal-release-authorization', userId, epochId], queryFn: getFullPolicies, retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const [selectedId, setSelectedId] = useState(''), [review, setReview] = useState<ReleaseReview | null>(null), [accepted, setAccepted] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null), [recorded, setRecorded] = useState<ReleaseResponse | null>(null);
  const selected = query.data?.items.find((p) => p.policy_id === selectedId);
  const policies = query.data?.items.filter((p) => p.template_name === 'CrossGoalReallocationPolicy') ?? [];
  function eligible(policy: FullPolicy) { try { const config = parseCrossGoalConfiguration(policy.current_version.configuration); return config.enabled === true && (config.source_goal_ids as string[]).includes(goal.id) && policy.epoch_id === epochId && policy.current_version.confirmation.user_id === userId && policy.planning_confirmation_valid && policy.reference_validation === 'CURRENT'; } catch { return false; } }
  const scope = review?.preview.scope;
  const matches = !!(review && scope && selected && eligible(selected) && scope.user_id === userId && scope.epoch_id === epochId && scope.policy_id === selected.policy_id && scope.policy_version_id === selected.current_version.version_id && scope.source_goals.some((g) => g.goal_id === goal.id && g.original_policy_id === goal.policy_id && g.original_policy_version_id === goal.policy_version_id));
  const latest = useRef({ goal, userId, epochId, mutationBlocked, selected, accepted, matches, review }); latest.current = { goal, userId, epochId, mutationBlocked, selected, accepted, matches, review };
  const pending = operation.pending, ownPending = pending?.user_id === userId && pending.owner_goal_id === goal.id;
  async function preview() {
    if (!selected || !epochId || !eligible(selected) || busy || operation.pending || operation.busy || operation.storage_error || query.isFetching || query.isError) return;
    setBusy(true); setAccepted(false); setReview(null); setNotice(null); setError(null);
    try { setReview(await loadReleaseReview({ goal, userId, epochId }, selected)); }
    catch (e) { setError(`${errorMessage(e)}；未记录专用授权。请刷新原规则、目标版本与周期后重新复核。`); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!review || !matches || !accepted || mutationBlocked || busy || operation.busy || operation.pending || operation.storage_error || query.isError || query.isFetching) return;
    setBusy(true); setError(null); setNotice(null); let began = false;
    try {
      if (!crypto.randomUUID) throw new Error('原键生成能力缺失，未开始确认'); const snapshot = review.preview;
      const intent = await prepareGoalReleaseIntent(userId, goal.id, snapshot.scope, { expected_epoch_id: snapshot.scope.epoch_id, expected_policy_version_id: snapshot.scope.policy_version_id, reviewed_scope_hash: snapshot.scope_hash, accepted: true, idempotency_key: `release-consent:${crypto.randomUUID()}` });
      const current = latest.current;
      if (!current.matches || !current.accepted || current.mutationBlocked || current.goal.id !== goal.id || current.userId !== userId || current.epochId !== epochId || current.review !== review) throw new Error('复核期间目标、周期、规则或明确接受已变化，未发送');
      await beginGoalReleaseAuthorizationOperation(intent, current.mutationBlocked); began = true;
      const response = await confirmReleaseIntent(intent); setRecorded(response); setAccepted(false); setNotice('收到原服务确认记录；原请求仍待独立GET核对，没有执行资金回拨。');
    } catch (e) { setError(`${errorMessage(e)}；已开始的原请求必须保留，只读查询原epoch/key，不换键重发。`); }
    finally { if (began) endGoalReleaseAuthorizationAttempt(); setBusy(false); }
  }
  async function lookup() {
    if (!pending || !ownPending || busy || operation.busy) return; setBusy(true); setError(null); setNotice(null);
    try { const result = await lookupReleaseIntent(pending); if (result.status === 'NOT_FOUND_NOT_FINAL') { setNotice('NOT_FOUND_NOT_FINAL不是最终未提交证明；原请求仍保留，不能换键确认。'); return; }
      await clearGoalReleaseAuthorizationAfterLookup(pending, result); setRecorded(result.original); setReview(null); setAccepted(false); setNotice('完整原body、范围hash、原证据和请求摘要已匹配，已解除本族待核对门；原历史记录不等于当前资金执行。');
    } catch (e) { setError(`${errorMessage(e)}；未解除原专用授权请求。`); }
    finally { setBusy(false); }
  }
  return <section className="card readonly-section" aria-label={`专用目标现金回拨授权 ${goal.id}`}><h4>专用目标现金回拨授权 · 明确确认范围</h4><p>当前来源目标 {goal.name} · <code>{goal.id}</code>。普通策略规划确认不能代替本专用确认；此界面只记录有界紧急现金权限，不执行回拨。</p><p className="notice">实际金融金额未验真；银行执行 NOT_IMPLEMENTED。本金和普通目标间再分配仍关闭。</p>
    {operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{busy && <p role="status">正在核对原来源或原记录…</p>}{mutationBlocked && <p>其他族原请求待核对或写入被阻挡；专用确认暂停，自身原键GET仍可用。</p>}
    {pending && (ownPending ? <section aria-label="待核对专用授权原请求"><h5>待核对原确认</h5><p>只读取原epoch <code>{pending.body.expected_epoch_id}</code> / key <code>{pending.body.idempotency_key}</code>，即使当前版本已变化也不替换原body。</p><button type="button" disabled={busy || operation.busy} onClick={() => void lookup()}>只读核对原专用授权</button><details><summary>POST前保存的完整原请求与范围</summary><pre className="readonly-raw">{JSON.stringify(pending, null, 2)}</pre></details><p>网络、解析错误、HTTP4xx及成功响应均不能单独清除原请求；未找到不是终局，不自动重发。</p></section> : <p className="notice">另一原目标或用户的专用授权尚待核对；请回到原来源目标，不能在这里换键或清除。</p>)}
    <button type="button" disabled={busy || query.isFetching} onClick={() => { setSelectedId(''); setReview(null); setAccepted(false); setError(null); void query.refetch(); }}>只读刷新专用授权来源</button>{query.isPending && <p role="status">正在读取当前回拨规划规则…</p>}{query.isError && <p role="alert">{errorMessage(query.error)}；不沿用旧来源作为当前成功。</p>}{!epochId && <p>当前epoch未知；不能准备或确认新范围。</p>}
    {query.data && !query.isError && <><label>选择已存在的当前回拨规则 <select value={selectedId} disabled={busy || query.isFetching || !!pending} onChange={(e) => { setSelectedId(e.target.value); setReview(null); setAccepted(false); setError(null); }}><option value="">先选择当前有明确源目标的规则</option>{policies.map((p) => <option key={p.policy_id} value={p.policy_id} disabled={!eligible(p)}>{p.name} · {p.effective_status} · v{p.current_version.version_number}{!eligible(p) ? ' · 当前来源不可确认' : ''}</option>)}</select></label>{policies.length === 0 && <p>尚无显式回拨规则。请先在策略中心用户确认规划规则，并确认全部来源目标完整模型；本页不自动创建。</p>}
      <button type="button" disabled={!selected || !eligible(selected) || !epochId || busy || operation.busy || !!pending || !!operation.storage_error || query.isFetching} onClick={() => void preview()}>只读准备完整专用授权范围</button></>}
    {matches && review && !query.isFetching && !query.isError && <section aria-label="专用授权明确复核"><Scope scope={review.preview.scope} review={review} /><p>复核范围摘要 <code>{review.preview.scope_hash}</code>；此预览尚未记录权限。</p><details><summary>原规则、全部源目标和模型HTTP原文</summary><pre className="readonly-raw">{review.originals.policy}</pre><pre className="readonly-raw">{review.originals.goals}</pre>{review.originals.models.map((raw, i) => <pre className="readonly-raw" key={i}>{raw}</pre>)}<pre className="readonly-raw">{getOriginalReleaseResponse(review.preview)}</pre></details><label><input type="checkbox" checked={accepted} disabled={busy || operation.busy || !!pending || mutationBlocked || !!operation.storage_error} onChange={(e) => setAccepted(e.target.checked)} />我已逐个复核全部源目标、原版本与最低保障、受保护现金目的、紧急条件、有效期和单次/累计上限，明确确认此专用范围</label><button type="button" disabled={!accepted || busy || operation.busy || !!pending || mutationBlocked || !!operation.storage_error} onClick={() => void confirm()}>明确确认专用回拨授权范围</button></section>}
    {recorded && recorded.original_authorization.user_id === userId && recorded.original_authorization.scope.source_goals.some((g) => g.goal_id === goal.id) && <Recorded response={recorded} />}
  </section>;
}
