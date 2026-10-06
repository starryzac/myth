import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { CashAction, CashCandidate, CashGoal, CashIntent } from '../api/goal-cash-releases';
import { cashKey, cashOriginalJson, createCashIntent, executeCashIntent, getCashAccounts, lookupCashAuthorization, parseCashAuthorization, prepareCashIntent, previewCashIntent, readCashIntent } from '../api/goal-cash-releases';
import type { ReleaseResponse } from '../api/goal-release-authorizations';
import { errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { acceptCashRead, beginCashExecute, beginCashPrepare, endCashAttempt, recoverGoalCashReleaseOperation, retainCashPostResponse, useGoalCashReleaseOperation } from '../features/goal-cash-release-operation';

export type GoalCashReleaseExecutionPanelProps = { goal: CashGoal; userId: string; epochId: string | null; mutationBlocked?: boolean; authorization?: ReleaseResponse | null };
function OriginalEffect({ action }: { action: CashAction }) {
  const effect = action.original_command.effect;
  return <section aria-label="原回拨经济后果"><h5>原效果 · {action.original_action_status}</h5><p>模拟回拨 ¥{formatMoneyCents(effect.amount_cents)}；最低保障 ¥{formatMoneyCents(effect.minimum_guarantee_cents)}。</p><p>源 Goal <code>{effect.source_goal_id}</code>，现金账户 <code>{effect.source_account_id}</code> → 普通保护 CASH <code>{effect.destination_account_id}</code>。</p><p>原三腿：源 CASH −¥{formatMoneyCents(effect.amount_cents)}；目的 CASH +¥{formatMoneyCents(effect.amount_cents)}；GOAL_CASH 归属 −¥{formatMoneyCents(effect.amount_cents)}。</p><p>本金、其他Goal、新收入、原ASSIGNED改动、费用与损失均为 0。银行结算与应用投影分开；准备和SUBMITTED均未预留资金。</p><ul>{effect.release_uses.map((u) => <li key={`${u.allocation_action_id}:${u.fragment_id}`}>原分配 <code>{u.allocation_action_id}</code> / 片段 <code>{u.fragment_id}</code> · ¥{formatMoneyCents(u.amount_cents)}；原收入 <code>{u.origin_transaction_id}</code>；原用途摘要 {u.allocation_effect_hash} / {u.allocation_bank_request_hash} / {u.allocation_action_request_hash}</li>)}</ul><p>有效窗口 {effect.valid_from} 至 {effect.expires_at}（上界不含）；只修复 {effect.emergency_conditions.join('、')}，不转给另一Goal。</p><p>原效果摘要 <code>{action.original_command.effect_hash}</code>；服务端完整封套摘要 <code>{action.original_request_hash}</code>（不以客户端body重算）。</p><p>原明确确认 {action.action_confirmation_evidence_id ?? '尚未确认'}；原银行 {action.original_bank_status ?? '尚无原银行行'}；服务回执 {action.receipt_id ?? '缺失，未当成成功'}。</p><details><summary>完整原command、全部hash与服务响应</summary><pre className="readonly-raw">{cashOriginalJson(action) ?? JSON.stringify(action, null, 2)}</pre></details><p>银行三腿/回执核验由服务端完成。浏览器核原身份和预期三腿/交易ID，不独立审核银行账本或宣称金融实验效果。</p></section>;
}
export default function GoalCashReleaseExecutionPanel({ goal, userId, epochId, mutationBlocked = false, authorization = null }: GoalCashReleaseExecutionPanelProps) {
  const operation = useGoalCashReleaseOperation();
  useEffect(() => { void recoverGoalCashReleaseOperation(); }, []);
  const accounts = useQuery({ queryKey: ['goal-cash-release-accounts', userId], queryFn: () => getCashAccounts(userId), retry: false, structuralSharing: false, refetchOnWindowFocus: false });
  const [authEpoch, setAuthEpoch] = useState(''), [authKey, setAuthKey] = useState(''), [destination, setDestination] = useState('');
  const [scope, setScope] = useState<ReleaseResponse | null>(null), [review, setReview] = useState<{ intent: CashIntent; candidate: CashCandidate } | null>(null), [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null), [recorded, setRecorded] = useState<CashAction | null>(null);
  const pending = operation.pending; const own = !!pending && pending.intent.user_id === userId && pending.intent.owner_goal.id === goal.id;
  const actual = own ? pending!.original_action : null;
  const historical = actual?.original_bank_status === 'SETTLED' && actual.bank_settlement_legs_verified && !actual.service_receipt_verified;
  const context = `${userId}:${epochId}:${goal.id}:${goal.policy_version_id}`;
  const latest = useRef({ context, mutationBlocked, accepted }); latest.current = { context, mutationBlocked, accepted };
  const currentScope = scope?.current_scope_status === 'CURRENT' && scope.original_authorization.user_id === userId && scope.original_authorization.epoch_id === epochId && scope.original_authorization.scope.source_goals.some((g) => g.goal_id === goal.id && g.original_policy_id === goal.policy_id && g.original_policy_version_id === goal.policy_version_id);
  const newBlocked = busy || operation.busy || operation.recovering || !!operation.storage_error || !!pending || mutationBlocked || !epochId || accounts.isFetching || accounts.isError;
  const reviewMatches = review?.intent.user_id === userId && review.intent.owner_goal.id === goal.id && review.intent.body.expected_goal_policy_version_id === goal.policy_version_id && review.intent.body.expected_epoch_id === epochId;
  async function loadScope(useExisting = false) {
    if (busy) return; setBusy(true); setError(null); setScope(null); setReview(null); setAccepted(false); const start = context;
    try { let original: ReleaseResponse | null;
      if (useExisting) { if (!authorization) return; original = await parseCashAuthorization(authorization, goal, userId); }
      else { const result = await lookupCashAuthorization(goal, userId, authEpoch, authKey); original = result.original; if (!original) setNotice('原授权NOT_FOUND_NOT_FINAL；尚不能形成新回拨请求。'); }
      if (latest.current.context !== start) throw new Error('读取期间原用户/目标/版本或epoch变化'); setScope(original);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function preview() {
    if (newBlocked || !currentScope || !scope || !epochId || !destination) return; setBusy(true); setError(null); setReview(null); const start = context;
    try { if (!crypto.randomUUID) throw new Error('无法生成和保存原键'); const intent = await createCashIntent({ goal, userId, epochId }, scope, destination, `goal-cash:${crypto.randomUUID()}`); const candidate = await previewCashIntent(intent); if (latest.current.context !== start) throw new Error('预览期间原上下文变化'); setReview({ intent, candidate }); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function prepare() {
    if (newBlocked || !review || !reviewMatches || review.candidate.state !== 'READY') return; const original = review; const start = context; setBusy(true); setError(null); let began = false;
    try { if (latest.current.context !== start || latest.current.mutationBlocked) throw new Error('上下文或写门变化'); await beginCashPrepare(original.intent, latest.current.mutationBlocked); began = true; const response = await prepareCashIntent(original.intent); await retainCashPostResponse(original.intent, response); setAccepted(false); setNotice('收到原prepare；行动与原键继续保留，尚未执行资金。'); }
    catch (e) { setError(`${errorMessage(e)}；已开始的原body/key必须保留，只GET原记录，不自动重发prepare。`); } finally { if (began) endCashAttempt(); setBusy(false); }
  }
  async function read() {
    if (!pending || !own || busy || operation.busy) return; setBusy(true); setError(null); setAccepted(false);
    try { const result = await readCashIntent(pending.intent, pending.original_action); const cleared = await acceptCashRead(pending.intent, result); setRecorded(result.original); setNotice(cleared ? '独立GET已核完整原效果、原回执和预期三腿/交易身份，解除本族待核对门。历史回执不授予当前权限。' : result.status === 'NOT_FOUND_NOT_FINAL' ? 'NOT_FOUND_NOT_FINAL不是未提交证明；保留原body/key，禁止新prepare。' : '原PLANNED/SUBMITTED/UNKNOWN仍保留；只有明确操作原行动，不换键。'); }
    catch (e) { setError(`${errorMessage(e)}；原请求未解除。`); } finally { setBusy(false); }
  }
  async function execute() {
    if (!own || !actual || !accepted || busy || operation.busy || (!historical && (mutationBlocked || actual.original_command.effect.epoch_id !== epochId || actual.original_command.effect.original_goal_policy_version_id !== goal.policy_version_id))) return;
    setBusy(true); setError(null); const start = context; let began = false;
    try { const body = { accepted: true as const, reviewed_effect_hash: actual.original_command.effect_hash, expected_epoch_id: actual.original_command.effect.epoch_id }; if (!latest.current.accepted || latest.current.context !== start) throw new Error('明确接受或上下文已变化'); const saved = await beginCashExecute(body, latest.current.mutationBlocked); began = true; const response = await executeCashIntent(saved.intent, saved.original_action!, body); await retainCashPostResponse(saved.intent, response); setAccepted(false); setNotice('收到原execute响应；仍须主动独立GET原回执，POST不清除待核对门。'); }
    catch (e) { setError(`${errorMessage(e)}；原效果/hash/epoch/行动保留，按原GET核对银行事实，不换键。`); } finally { if (began) endCashAttempt(); setBusy(false); }
  }
  return <section className="card readonly-section" aria-label={`目标现金回拨执行 ${goal.id}`}><h4>目标现金紧急回拨 · 模拟原行动</h4><p>{goal.name} · 只修复受保护现金缺口。授权范围与逐行动效果需要分别明确确认；不接真实资金。</p>{operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{busy && <p role="status">正在处理原请求…</p>}{mutationBlocked && <p>其他族待核对；新prepare和新银行execute停止，自身GET可用。已结算原银行的明确恢复不创建新效果。</p>}
    {!pending && <><label>原专用授权epoch<input value={authEpoch} disabled={busy} onChange={(e) => { setAuthEpoch(e.target.value); setScope(null); setReview(null); }} /></label><label>原专用授权key<input value={authKey} disabled={busy} onChange={(e) => { setAuthKey(e.target.value); setScope(null); setReview(null); }} /></label><button type="button" disabled={busy || !authEpoch || !cashKey(authKey)} onClick={() => void loadScope()}>只读查询原专用授权</button>{authorization && <button type="button" disabled={busy} onClick={() => void loadScope(true)}>读取已提供的原授权范围</button>}
      {scope && <section aria-label="原授权与回拨上限"><p>服务读取报告 {scope.current_scope_status}；不缓存当前权限，新prepare会由服务fresh重核。</p><p>单次 ¥{formatMoneyCents(scope.original_authorization.scope.single_action_cap_cents)}，全策略跨版本累计 ¥{formatMoneyCents(scope.original_authorization.scope.total_cap_cents)}；所有源Goal最低保障与有限日期如下。</p><pre className="readonly-raw">{JSON.stringify(scope.original_authorization.scope, null, 2)}</pre></section>}
      {accounts.isError && <p role="alert">{errorMessage(accounts.error)}；账户未知不补为零。</p>}<label>保护现金目的账户<select value={destination} disabled={newBlocked} onChange={(e) => { setDestination(e.target.value); setReview(null); }}><option value="">选择当前原owned CASH账户</option>{accounts.data?.accounts.filter((a) => a.account_type === 'CASH').map((a) => <option key={a.id} value={a.id}>{a.name} · {a.id}</option>)}</select></label><button type="button" disabled={newBlocked || !currentScope || !destination} onClick={() => void preview()}>手动只读预览最低修复</button>
      {review && reviewMatches && <section aria-label="回拨实际当前预览"><h5>{review.candidate.state} · {review.candidate.amount_cents === null ? 'UNKNOWN · 金额未证明' : `¥${formatMoneyCents(review.candidate.amount_cents)}`}</h5><p>{review.candidate.reasons.join('、')}；保护点 {review.candidate.protection?.compared_point_count ?? 'UNKNOWN'} / 1098，授权确认不是资金事实。</p><details><summary>完整原预览JSON与source哈希</summary><pre className="readonly-raw">{cashOriginalJson(review.candidate) ?? JSON.stringify(review.candidate, null, 2)}</pre></details><button type="button" disabled={newBlocked || !reviewMatches || review.candidate.state !== 'READY'} onClick={() => void prepare()}>手动准备原回拨行动</button></section>}</>}
    {pending && (own ? <section aria-label="待核对原回拨行动"><p>原客户端意图摘要 <code>{pending.intent.client_intent_hash}</code>；原键 <code>{pending.intent.body.idempotency_key}</code>。新客户端仅恢复原GET，绝不自动POST。</p><button type="button" disabled={busy || operation.busy} onClick={() => void read()}>只读核对原回拨行动与回执</button><details><summary>POST前保存的完整原prepare与execute</summary><pre className="readonly-raw">{JSON.stringify(pending, null, 2)}</pre></details>{actual && <><OriginalEffect action={actual} /><label><input type="checkbox" checked={accepted} disabled={busy || operation.busy || !!operation.storage_error || (!historical && mutationBlocked)} onChange={(e) => setAccepted(e.target.checked)} />我已复核原金额、源Goal与保护账户、全部来源片段、零费用损失和原效果hash，明确接受并只执行或恢复此原行动</label><button type="button" disabled={!accepted || busy || operation.busy || !!operation.storage_error || (!historical && (mutationBlocked || !['PLANNED', 'AUTHORIZED', 'SUBMITTED'].includes(actual.original_action_status) || actual.original_command.effect.epoch_id !== epochId || actual.original_command.effect.original_goal_policy_version_id !== goal.policy_version_id))} onClick={() => void execute()}>{historical ? '明确按原银行键恢复应用投影' : '明确确认原效果并执行'}</button></>}</section> : <p>另一原用户或Goal的回拨尚未解决；请回原目标核对，不能换键或清除。</p>)}
    {!pending && recorded && recorded.user_id === userId && recorded.original_command.effect.source_goal_id === goal.id && <OriginalEffect action={recorded} />}
  </section>;
}
