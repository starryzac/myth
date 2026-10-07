import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import type { PaymentReceipt, PaymentScope } from '../api/full-payment-relations';
import type { NextState } from './api';
import { businessText, money, userError } from './display';
import { same, type PolicyRecord } from './policies';
import { getPaymentDiscovery, previewNextPayment, type PaymentUpdate } from './payments';
import type { PaymentRecovery } from './payment-recovery';
import { dueInServerWindow } from './payment-calendar';

export type PaymentPerform = (path: string, body: Record<string, unknown>, recovery?: PaymentRecovery) => Promise<void>;
function scopeRows(scope: PaymentScope, environment: NextState, payeeName?: string, sourceName?: string) {
  const source = sourceName ?? environment.dashboard.account_facts.facts.accounts.find((row) => row.id === scope.source_account_id)?.name ?? '原来源账户';
  return <dl className="zy-facts"><div><dt>收款方</dt><dd>{businessText(payeeName ?? '已核实的固定收款方')}</dd></div><div><dt>来源账户</dt><dd>{businessText(source)}</dd></div><div><dt>每次金额</dt><dd>{scope.amount_rule.kind === 'exact' ? money(scope.amount_rule.amount_cents) : `${money(scope.amount_rule.min_cents)} 至 ${money(scope.amount_rule.max_cents)}`}</dd></div><div><dt>单次上限</dt><dd>{money(scope.single_action_cap_cents)}</dd></div><div><dt>应付日</dt><dd>每月 {scope.due_day} 日</dd></div><div><dt>同意方式</dt><dd>{scope.auto_execute ? '原明确自动付款范围' : '每次付款询问一次'}</dd></div><div><dt>有限期限</dt><dd>{scope.valid_from.slice(0, 10)} 至 {scope.valid_until.slice(0, 10)}</dd></div></dl>;
}
export default function PaymentPanel({ record, records, environment, session, blocked, update, perform, runRead, resumeOriginal, close }: { record: PolicyRecord; records: PolicyRecord[]; environment: NextState; session: LocalActorSession | null; blocked: boolean; update: PaymentUpdate | null; perform: PaymentPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; resumeOriginal?: () => Promise<void>; close: () => void }) {
  const [originalId, setOriginalId] = useState(''); const [receipt, setReceipt] = useState<PaymentReceipt | null>(null); const [reason, setReason] = useState(''); const [working, setWorking] = useState(false); const [error, setError] = useState('');
  const observed = useRef('');
  const discovery = useQuery({ queryKey: ['zhiyu-next-payments', environment.environment_id, environment.epoch_id], queryFn: () => getPaymentDiscovery(environment), enabled: !blocked, retry: false, staleTime: Infinity });
  const signed = !!session && session.principal.user_id === environment.dashboard.user_id && session.principal.role === 'USER' && Date.parse(session.principal.expires_at) > Date.now();
  const originals = records.filter((row) => row.source_kind === 'MVP_POLICY' && row.template_name === 'RecurringObligationPolicy' && row.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(row.effective_status) && ['payee_id', 'amount_rule', 'due_day', 'prepare_days_before', 'auto_execute'].every((key) => same(row.configuration[key], record.configuration[key])) && discovery.data?.pairs.some((pair) => pair.full_policy_id === record.policy_id && pair.full_version_id === record.current_version_id && pair.full_configuration_hash === record.configuration_hash && pair.original_policy_id === row.policy_id && pair.original_version_id === row.current_version_id && pair.original_configuration_hash === row.configuration_hash));
  const currentUpdate = update?.recovery.scope.full_policy_id === record.policy_id ? update : null;
  useEffect(() => { if (currentUpdate?.receipt) setReceipt(currentUpdate.receipt); }, [currentUpdate]);
  const discovered = discovery.data?.relations.find((row) => row.receipt.original.kind === 'CONFIRM' && row.receipt.original.scope.full_policy_id === record.policy_id && row.receipt.current_scope_status === 'CURRENT');
  const initiated = discovery.data?.relations.filter((row) => row.receipt.original.kind === 'START' && row.receipt.original.scope.full_policy_id === record.policy_id && row.receipt.current_scope_status === 'CURRENT').sort((a, b) => b.receipt.original.recorded_at.localeCompare(a.receipt.original.recorded_at))[0];
  const current = currentUpdate?.receipt ?? (receipt?.original.scope.full_policy_id === record.policy_id ? receipt : null) ?? discovered?.receipt ?? initiated?.receipt ?? null;
  const authorization = current?.original.kind === 'CONFIRM' ? current : discovered?.receipt ?? null;
  const prepared = currentUpdate?.prepared ?? discovery.data?.prepared.find((row) => row.original_binding?.authorization_id === authorization?.original.command_id) ?? null;
  const action = currentUpdate?.action ?? prepared?.original_action ?? null;
  const scope = currentUpdate?.recovery.scope ?? authorization?.original.scope ?? current?.original.scope ?? null;
  async function previewAndRegister() {
    const original = originals.find((row) => row.policy_id === originalId); if (!original || blocked || !signed || working) return;
    setWorking(true); setError('');
    try {
      const preview = await runRead(() => previewNextPayment({ expected_epoch_id: environment.epoch_id, full_policy_id: record.policy_id, expected_full_version_id: record.current_version_id, original_policy_id: original.policy_id, expected_original_version_id: original.current_version_id }, environment));
      const recovery: PaymentRecovery = { protocol: 'zhiyu-next-payment-recovery-v1', scope: preview.scope, start_command_id: null, authorization_id: null, action: null };
      setReceipt(null); await perform('/zhiyu-next/payments/relation/start', { expected_epoch_id: environment.epoch_id, full_policy_id: preview.scope.full_policy_id, expected_full_version_id: preview.scope.full_version_id, original_policy_id: preview.scope.original_policy_id, expected_original_version_id: preview.scope.original_version_id }, recovery);
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  function confirmRelation() { if (!current || current.original.kind !== 'START' || blocked || !signed || !reason.trim()) return; void perform(`/zhiyu-next/payments/relation/${current.original.command_id}/confirm`, { expected_epoch_id: environment.epoch_id, reviewed_scope_hash: current.original.scope_hash, accepted: true, reason: reason.trim() }, { protocol: 'zhiyu-next-payment-recovery-v1', scope: current.original.scope, start_command_id: current.original.command_id, authorization_id: null, action: null }); }
  // Only the server discovery contract opens observation. The original fact GET
  // must finish before preparation; neither body accepts an amount or browser clock.
  const observationConnected = discovery.data?.observation_available === true;
  const currentScope = !!scope && scope.full_version_id === record.current_version_id && scope.full_configuration_hash === record.configuration_hash && authorization?.current_scope_status === 'CURRENT';
  const period = discovery.data?.server_period;
  const calendarDue = dueInServerWindow(environment.dashboard.as_of, period, scope);
  function observeCurrent() { if (!authorization || !scope || blocked || !signed || !observationConnected || !currentScope || !calendarDue) return; void perform('/zhiyu-next/payments/observe-current-period', { expected_epoch_id: environment.epoch_id, original_policy_id: scope.original_policy_id }, { protocol: 'zhiyu-next-payment-recovery-v1', scope, start_command_id: null, authorization_id: authorization.original.command_id, action: null }); }
  useEffect(() => {
    const proof = currentUpdate?.phase === 'OBSERVE' ? currentUpdate.observation : null;
    if (!proof || !authorization || !scope || blocked || !signed || !currentScope || observed.current === proof.client_request_id) return;
    observed.current = proof.client_request_id;
    void perform(`/zhiyu-next/payments/authorizations/${authorization.original.command_id}/prepare`, { expected_epoch_id: environment.epoch_id }, { protocol: 'zhiyu-next-payment-recovery-v1', scope, start_command_id: null, authorization_id: authorization.original.command_id, action: null });
  }, [currentUpdate, authorization, scope, blocked, signed, currentScope, environment.epoch_id, perform]);
  const partial = currentUpdate?.partial_preparation;
  const actionCurrent = !partial && !!action && Date.parse(action.as_of) < Date.parse(action.effect.expires_at);
  function executeAction() { if (partial || !action || !scope || !authorization || blocked || !signed || !currentScope || !actionCurrent || !['PLANNED', 'AUTHORIZED'].includes(action.status) || action.bank_status || !['ASK_ONCE', 'AUTO_EXECUTE'].includes(action.autonomy_level)) return; const ask = action.autonomy_level === 'ASK_ONCE'; void perform(`/zhiyu-next/payments/actions/${action.action_id}/${ask ? 'confirm-and-execute' : 'execute-original'}`, { expected_epoch_id: environment.epoch_id, ...(ask ? { reviewed_effect_hash: action.effect_hash, accepted: true } : {}) }, { protocol: 'zhiyu-next-payment-recovery-v1', scope, start_command_id: null, authorization_id: authorization.original.command_id, action }); }
  const paid = !!action && ['SUCCEEDED', 'RECONCILED'].includes(action.status) && action.bank_status === 'SETTLED' && !!action.receipt;
  const unknown = !!action && (['UNKNOWN', 'SUBMITTED'].includes(action.status) || action.bank_status === 'UNKNOWN');
  return <section className="zy-card zyn-payment" aria-label="必要付款"><div className="zy-section-heading"><h2>{businessText(record.name)} · 必要付款</h2><button className="zy-link" onClick={close}>收起付款</button></div><p>规划规则本身不授予银行权限。先核对已有周期义务与固定付款关系，再按原经济后果处理本期付款。</p>
    {discovery.isPending && <p role="status">正在核实本轮付款关系与当前周期…</p>}{discovery.isError && <p role="status">{userError(discovery.error)}</p>}{error && <p role="alert">{error}</p>}
    {!signed && <p>请先在上方建立本轮本地用户身份；密钥不会保存在浏览器。</p>}
    {!current && !authorization && <><label className="zy-field" htmlFor="zyn-payment-original">关联已确认周期义务<select id="zyn-payment-original" value={originalId} disabled={blocked || working} onChange={(event) => setOriginalId(event.target.value)}><option value="">请选择相同收款与周期的原规则</option>{originals.map((row) => <option key={row.policy_id} value={row.policy_id}>{businessText(row.name)}</option>)}</select></label>{!originals.length && <p>本轮没有已确认且范围一致的原周期义务。不能只靠完整规划规则创建付款权限。</p>}<button className="zy-secondary" disabled={blocked || working || !signed || !originalId} onClick={() => void previewAndRegister()}>查看并登记付款范围</button><p className="zy-muted">登记只保存待确认范围，不转移资金。</p></>}
    {scope && scopeRows(scope, environment, discovered?.payee_name ?? initiated?.payee_name, discovered?.source_account_name ?? initiated?.source_account_name)}
    {current?.original.kind === 'START' && <><p>已登记的有限范围来自服务器原件。确认后仍须核实原规则、资金与实际应付事实。</p><label className="zy-field" htmlFor="zyn-payment-reason">付款范围用途<input id="zyn-payment-reason" value={reason} maxLength={1000} disabled={blocked} onChange={(event) => setReason(event.target.value)} /></label><button className="zy-primary" disabled={blocked || !signed || !reason.trim() || current.current_scope_status !== 'CURRENT'} onClick={confirmRelation}>确认固定付款范围</button></>}
    {authorization && <><p>固定付款范围已由本轮用户确认。此关系回执不代表资金已经付出。</p>{!action && <><p role="status">{!observationConnected ? '本期应付观察与原请求回读尚未接通，暂不能准备付款。' : !currentScope ? '固定付款范围已变化，不能从历史关系继续新增付款。' : !calendarDue ? '当前服务器日期尚未到本期应付窗口，或本期不在此范围内；请保留规则。' : '服务器将核实当前自然月应付事实，再读取原付款后果。'}</p><button className="zy-secondary" disabled={blocked || !signed || !observationConnected || !currentScope || !calendarDue} onClick={observeCurrent}>查看本期付款</button></>}</>}
    {action && !partial && <section aria-label="本期付款后果"><h3>{paid ? '付款已核实' : unknown ? '正在核实这笔付款' : '本期付款后果'}</h3><strong className="zy-amount zy-amount-small">{money(paid ? action.receipt!.executed_cents : action.effect.amount_cents)}</strong><dl className="zy-facts"><div><dt>实际周期</dt><dd>{action.effect.liability?.kind === 'occurrence' ? action.effect.liability.period : '尚未核实'}</dd></div><div><dt>费用</dt><dd>{money(action.effect.fee_cents)}</dd></div><div><dt>损失</dt><dd>{money(action.effect.loss_cents)}</dd></div></dl>{paid ? <p className="zy-result">实际回执与原付款金额已核实。新周期需重新读取实际应付事实。</p> : unknown ? <p>银行结果未决，保留同一原动作与银行键继续核对，不会重新准备另一笔。</p> : ['PLANNED', 'AUTHORIZED'].includes(action.status) && !action.bank_status && ['ASK_ONCE', 'AUTO_EXECUTE'].includes(action.autonomy_level) ? <><p>{!currentScope || !actionCurrent ? '当前范围或原经济后果有效期已变化，不能新增银行受理。' : action.autonomy_level === 'ASK_ONCE' ? '确认一次上方经济后果，系统会完成原付款的后续执行。' : '只能消费现行明确自动付款范围，执行前服务端仍会重检原义务与资金。'}</p><button className="zy-primary" disabled={blocked || !signed || !currentScope || !actionCurrent} onClick={executeAction}>{action.autonomy_level === 'ASK_ONCE' ? '确认并付款' : '执行已授权本期付款'}</button></> : <p>{businessText((action.prepared_validation.reasons ?? []).join('；')) || '当前动作不能执行，请保留原件。'}</p>}</section>}
    {partial && <p role="status">原付款动作已保存，范围关联尚未完整核实。只可续接同一原准备，不能确认付款或创建替代动作。</p>}
    {resumeOriginal && currentUpdate?.resume_original && <><p>{partial ? '仅完成原范围关联，保持同一原键与服务器周期。' : action?.bank_status === 'SETTLED' ? '原银行结算已核实，仍需完成同一原行动的回执恢复。' : '这笔付款的原同意已核实，银行尚未受理；可继续同一原行动，无需再次确认金额。'}</p><button className="zy-secondary" disabled={!signed} onClick={() => void resumeOriginal()}>{partial ? '续接同一原准备' : '继续这笔已确认付款'}</button></>}
  </section>;
}

/** Recovery needs only the independently read original. It must work even while
 * catalogue queries are suspended by the one shared write gate after reload. */
export function PaymentOriginalResult({ update, environment, session, resumeOriginal }: { update: PaymentUpdate; environment: NextState; session: LocalActorSession | null; resumeOriginal?: () => Promise<void> }) {
  const action = update.action; if (!action) return null;
  if (update.partial_preparation) return <section className="zy-card zyn-payment" aria-label="原必要付款恢复"><h2>原付款准备尚未完整</h2>{scopeRows(update.recovery.scope, environment)}<p>原动作与服务器周期已独立定位，范围关联尚未完整。只可续接相同原准备；不能替换、确认付款或新增银行受理。</p>{resumeOriginal && update.resume_original && <button className="zy-secondary" disabled={!session || session.principal.role !== 'USER' || session.principal.user_id !== environment.dashboard.user_id || Date.parse(session.principal.expires_at) <= Date.now()} onClick={() => void resumeOriginal()}>续接同一原准备</button>}</section>;
  const paid = ['SUCCEEDED', 'RECONCILED'].includes(action.status) && action.bank_status === 'SETTLED' && !!action.receipt;
  const signed = !!session && session.principal.user_id === environment.dashboard.user_id && session.principal.role === 'USER' && Date.parse(session.principal.expires_at) > Date.now();
  return <section className="zy-card zyn-payment" aria-label="原必要付款恢复"><h2>{paid ? '付款已核实' : '正在核实这笔付款'}</h2>{scopeRows(update.recovery.scope, environment)}<strong className="zy-amount zy-amount-small">{money(paid ? action.receipt!.executed_cents : action.effect.amount_cents)}</strong><p>费用 {money(action.effect.fee_cents)}，损失 {money(action.effect.loss_cents)}。{paid ? '原回执与原经济后果已独立核实。' : '保留同一原行动，不更换银行键或重新准备另一笔。'}</p>{resumeOriginal && update.resume_original && <><p>{action.bank_status === 'SETTLED' ? '银行结算已核实，继续恢复原回执。' : '原用户同意已经核实，可继续同一原行动的执行。'}</p><button className="zy-secondary" disabled={!signed} onClick={() => void resumeOriginal()}>继续这笔已确认付款</button></>}</section>;
}
