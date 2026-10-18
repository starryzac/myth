import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import type { ReleasePreview, ReleaseResponse } from '../api/goal-release-authorizations';
import type { CashCandidate } from '../api/goal-cash-releases';
import type { NextState } from './api';
import { businessText, money, userError } from './display';
import { getPolicyRecords } from './policies';
import LocalUserPanel from './LocalUserPanel';
import { isTransferConfiguration } from './goal-transfers';
import { authorizationCommand, executeReleaseCommand, getGoalCashPreview, getGoalReleaseContext, getGoalReleasePreview, prepareReleaseCommand } from './goal-releases';
import type { GoalReleaseCommand, GoalReleaseContext, GoalReleasePerform, GoalReleaseUpdate } from './goal-releases';

type Props = { environment: NextState; blocked: boolean; update: GoalReleaseUpdate | null; recovering: boolean; perform: GoalReleasePerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; retryOriginal?: () => Promise<void>; resumeOriginal?: () => Promise<void> };
export default function GoalReleasePanel(props: Props) { return <OwnedGoalReleasePanel key={`${props.environment.environment_id}:${props.environment.dashboard.user_id}:${props.environment.epoch_id}`} {...props} />; }
function OwnedGoalReleasePanel({ environment, blocked, update, recovering, perform, runRead, retryOriginal, resumeOriginal }: Props) {
  const [open, setOpen] = useState(false), [policyId, setPolicyId] = useState(''), [goalId, setGoalId] = useState(''), [destination, setDestination] = useState('');
  const [entry, setEntry] = useState<GoalReleaseContext | null>(null), [preview, setPreview] = useState<ReleasePreview | null>(null), [draft, setDraft] = useState<GoalReleaseCommand | null>(null), [candidate, setCandidate] = useState<CashCandidate | null>(null);
  const [session, setSession] = useState<LocalActorSession | null>(null), [working, setWorking] = useState(false), [error, setError] = useState('');
  const identity = `${environment.environment_id}:${environment.dashboard.user_id}:${environment.epoch_id}`, mounted = useRef(true), selection = useRef('');
  selection.current = `${identity}:${policyId}:${goalId}:${destination}`;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (recovering || update) setOpen(true); }, [recovering, update]);
  const records = useQuery({ queryKey: ['zhiyu-next-policy-records', environment.environment_id, environment.epoch_id], queryFn: () => getPolicyRecords(environment), enabled: open && !blocked, retry: false });
  const policies = records.data?.items.filter((row) => row.source_kind === 'FULL_POLICY' && row.template_name === 'CrossGoalReallocationPolicy' && !isTransferConfiguration(row.configuration) && row.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(row.effective_status)) ?? [];
  const signed = !!session && session.principal.user_id === environment.dashboard.user_id && session.principal.role === 'USER' && Date.parse(session.principal.expires_at) > Date.now();
  const selectedGoal = entry?.sources.find((row) => row.goal.id === goalId)?.goal;
  const authorizations = new Map<string, ReleaseResponse>();
  for (const row of entry?.authorizations ?? []) authorizations.set(row.original_authorization.authorization_id, row);
  if (update?.authorization && update.authorization.original_authorization.policy_id === entry?.policy.policy_id) authorizations.set(update.authorization.original_authorization.authorization_id, update.authorization);
  const authorization = [...authorizations.values()].reverse().find((row) => row.current_scope_status === 'CURRENT' && row.original_authorization.scope.source_goals.some((goal) => goal.goal_id === goalId) && Date.parse(row.original_authorization.valid_until) > Date.now());
  const actions = new Map((entry?.actions ?? []).map((row) => [row.action.action_id, row]));
  if (update?.action && update.recovery.cash) actions.set(update.action.action_id, { action: update.action, intent: update.recovery.cash });
  const unresolved = !!entry?.any_unresolved || [...actions.values()].some((row) => row.action.unresolved);
  const unavailable = blocked || working || unresolved;
  function reset() { setPreview(null); setDraft(null); setCandidate(null); setError(''); }
  async function work<T>(action: () => Promise<T>, apply?: (result: T) => void) {
    if (working) return; const expected = selection.current;
    setWorking(true); setError('');
    try { const result = await action(); if (mounted.current && selection.current === expected) apply?.(result); }
    catch (cause) { if (mounted.current && selection.current === expected) setError(userError(cause)); }
    finally { if (mounted.current) setWorking(false); }
  }
  function inspect() { if (blocked || !policyId) return; void work<GoalReleaseContext>(() => runRead(() => getGoalReleaseContext(policyId, environment)), (value) => { setEntry(value); setGoalId(value.sources[0]?.goal.id ?? ''); setDestination(value.accounts[0]?.id ?? ''); setPreview(null); setDraft(null); setCandidate(null); }); }
  function review() { if (unavailable || !entry || !selectedGoal) return; void work(() => runRead(() => getGoalReleasePreview(entry, selectedGoal, environment)), setPreview); }
  function confirmScope() { if (unavailable || !signed || !preview || !selectedGoal) return; void work(async () => { const command = await authorizationCommand(preview, selectedGoal.id, environment); await perform(command.path, command.body, command.recovery); }); }
  function calculate() {
    if (unavailable || !entry || !selectedGoal || !authorization || !destination) return;
    void work(async () => { const command = await prepareReleaseCommand(selectedGoal, authorization, destination, environment); const value = await runRead(() => getGoalCashPreview(command, environment)); return { command, value }; }, ({ command, value }) => { setDraft(command); setCandidate(value); });
  }
  function prepare() { if (unavailable || !signed || !draft || candidate?.state !== 'READY') return; void work(() => perform(draft.path, draft.body, draft.recovery)); }
  return <section aria-label="目标现金应急回拨"><section className="zy-card"><div className="zy-section-heading"><h2>目标现金应急回拨</h2><button className="zy-secondary" disabled={working} onClick={() => setOpen((value) => !value)}>{open ? '收起应急回拨' : '查看应急回拨'}</button></div><p>只在列明的应急条件下释放原目标现金到本人受保护现金账户。不会释放资产本金、增加收入或授权普通目标间自由转账。</p></section>
    {open && <><LocalUserPanel environment={environment} session={session} update={setSession} inputId="zyn-goal-release-secret" />
      <section className="zy-card"><h3>核对现行规则与原目标</h3><label className="zy-field" htmlFor="zyn-goal-release-policy">应急回拨规则<select id="zyn-goal-release-policy" value={policyId} disabled={blocked || working} onChange={(event) => { setPolicyId(event.target.value); setEntry(null); reset(); }}><option value="">请选择</option>{policies.map((row) => <option key={row.policy_id} value={row.policy_id}>{businessText(row.name)}</option>)}</select></label><button className="zy-secondary" disabled={blocked || working || !policyId} onClick={inspect}>核对规则与本轮原件</button>{records.isError && <p role="alert">{userError(records.error)}</p>}{records.isSuccess && policies.length === 0 && <p>本轮没有已确认的应急回拨规则，请先在“我的规则”建立明确的来源与上限。</p>}
      {entry && <><label className="zy-field" htmlFor="zyn-goal-release-source">原目标<select id="zyn-goal-release-source" value={goalId} disabled={blocked || working} onChange={(event) => { setGoalId(event.target.value); reset(); }}>{entry.sources.map((row) => <option key={row.goal.id} value={row.goal.id}>{businessText(row.goal.name)}</option>)}</select></label><button className="zy-secondary" disabled={unavailable || !selectedGoal} onClick={review}>核对专用应急范围</button></>}
      {preview && <section aria-label="应急释放范围"><p>单次最多 {money(preview.scope.single_action_cap_cents)}，此规则所有版本合计最多 {money(preview.scope.total_cap_cents)}。原目标最低保证仍受保护；有效至 {preview.scope.valid_until.slice(0, 10)}。</p><p>这里只确认专用应急范围，不代表已有可释放金额或银行动作。</p><button className="zy-primary" disabled={unavailable || !signed || Date.parse(preview.scope.valid_until) <= Date.now()} onClick={confirmScope}>确认这项专用应急范围</button></section>}
      {entry && <><label className="zy-field" htmlFor="zyn-goal-release-destination">本人受保护现金账户<select id="zyn-goal-release-destination" value={destination} disabled={blocked || working} onChange={(event) => { setDestination(event.target.value); reset(); }}>{entry.accounts.map((row) => <option key={row.id} value={row.id}>{businessText(row.name)}</option>)}</select></label><button className="zy-secondary" disabled={unavailable || !authorization || !selectedGoal || !destination} onClick={calculate}>核对本次必要回拨</button>{!authorization && <p>尚无适用于该原目标的当前专用应急授权，不能沿用普通目标规划或旧版本确认。</p>}</>}
      {candidate && <section aria-label="本次应急回拨影响"><h4>{candidate.state === 'READY' ? '当前必要金额已核实' : '当前还不能安排回拨'}</h4>{candidate.amount_cents !== null && <strong>{money(candidate.amount_cents)}</strong>}<p>原现金归属、所有版本已用额度及全年保护均由服务器重检；准备方案不代表已经释放资金。</p>{candidate.reasons.map((reason) => <p key={reason}>{businessText(reason)}</p>)}{candidate.state === 'READY' && <button className="zy-secondary" disabled={unavailable || !signed} onClick={prepare}>建立可核对的回拨方案</button>}</section>}
      {unresolved && <p role="status">已有原回拨结果未决。保留原目标、金额和银行键，核实前不建立另一笔回拨。</p>}
      </section>
      {[...actions.values()].map(({ action, intent }) => <section className="zy-card" key={action.action_id} aria-label="原应急回拨"><h3>{action.service_receipt_verified ? '原回拨回执已独立核实' : action.unresolved ? '原回拨仍在核实' : '原回拨等待明确确认'}</h3><strong>{money(action.original_command.effect.amount_cents)}</strong><p>由 {businessText(intent.owner_goal.name)} 回到本人受保护现金。费用 {money(action.original_command.effect.fee_cents)}，损失 {money(action.original_command.effect.loss_cents)}。</p><p>只改变本次已核实的目标现金归属，不改原收入或其他目标。若之后安排到另一个目标，仍需其现行接收授权。</p>{!action.unresolved && !action.service_receipt_verified && ['PLANNED', 'AUTHORIZED'].includes(action.original_action_status) && <button className="zy-primary" disabled={unavailable || !signed || Date.parse(action.original_command.effect.expires_at) <= Date.now()} onClick={() => void work(async () => { const command = await executeReleaseCommand(intent, action, environment); await perform(command.path, command.body, command.recovery); })}>确认并执行这笔回拨</button>}{action.unresolved && resumeOriginal && <button className="zy-secondary" disabled={working || !signed} onClick={() => void work(resumeOriginal)}>继续核对同一原回拨</button>}</section>)}
      {retryOriginal && <section className="zy-card"><p>恢复当前本人会话后，只能重试保留的同一原请求。</p><button className="zy-secondary" disabled={working || !signed} onClick={() => void work(retryOriginal)}>重试同一原请求</button></section>}{error && <p role="alert">{error}</p>}
    </>}
  </section>;
}
