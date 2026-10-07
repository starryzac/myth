import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NextState } from './api';
import type { AssetsState } from './assets-state';
import { assetSourceOpen } from './assets-state';
import type { AssetPerform } from './AssetsPanel';
import { getNextLoss, type LossBinding, type LossUpdate } from './loss';
import { getRevisionView, readRevisionQuote, readRevisionUpdate, revisionSourceAvailable, type LossRevisionUpdate } from './loss-revisions';
import { revisionBase as base, type LossRevisionRecovery } from './loss-revision-recovery';
import { spendingHash as hash } from '../api/spending-evidence';
import { businessText, money, userError } from './display';

export default function LossRevisionPanel({ environment, state, blocked, signed, old, oldQuote, update, recovering, perform, runRead, resumeOriginal, retryOriginal, onPresence }: { environment: NextState; state?: AssetsState; blocked: boolean; signed: boolean; old?: LossUpdate | null; oldQuote?: LossBinding['quote'] | null; update: LossRevisionUpdate | null; recovering: boolean; perform: AssetPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; resumeOriginal?: () => Promise<void>; retryOriginal?: () => Promise<void>; onPresence: () => void }) {
  const [oldFresh, setOldFresh] = useState<LossUpdate | null>(null); const [error, setError] = useState(''); const [working, setWorking] = useState(false); const [fresh, setFresh] = useState<LossRevisionUpdate | null>(null);
  const identity = [environment.environment_id, environment.epoch_id];
  const previous = useQuery({ queryKey: ['zhiyu-next-quote-revision-view', ...identity], queryFn: () => readRevisionUpdate(environment), enabled: !blocked && !update, retry: false, staleTime: Infinity, refetchInterval: (q) => !blocked && !update && ['UNKNOWN', 'SUBMITTED'].includes(q.state.data?.view?.action.status ?? '') ? 15000 : false });
  const source = useQuery({ queryKey: ['zhiyu-next-quote-revision-source', ...identity], queryFn: () => revisionSourceAvailable(environment), enabled: !blocked && assetSourceOpen(state?.source_validation_status), retry: false, staleTime: Infinity });
  useEffect(() => { if (update) setFresh(null); }, [update]);
  const legacy = oldFresh ?? old; const current = fresh ?? update ?? previous.data; const view = current?.view; const quoted = current?.quote; const originalCost = legacy?.view?.binding.quote ?? oldQuote;
  const available = assetSourceOpen(state?.source_validation_status) && (source.data === true || !!current?.quote || !!current?.view || current?.partial === true);
  const baseCost = quoted?.revision.quote ?? originalCost;
  const baseAction = view?.action ?? (!current ? legacy?.view?.action : null);
  const serverAsOf = view?.server_as_of ?? quoted?.server_as_of ?? legacy?.view?.action.as_of ?? state?.server_as_of;
  const expired = !!baseCost && !!serverAsOf && Date.parse(serverAsOf) >= Date.parse(baseCost.expires_at);
  useEffect(() => { if (current || expired) onPresence(); }, [current, expired, onPresence]);
  const unresolved = !!baseAction && (baseAction.bank_status !== null || baseAction.receipt !== null || ['UNKNOWN', 'SUBMITTED', 'SUCCEEDED', 'RECONCILED'].includes(baseAction.status));
  const permission = (goal: string | null | undefined) => state?.policies.some((p) => p.source_kind === 'MVP_POLICY' && p.template_name === 'AssetAuthorizationPolicy' && p.planning_confirmation_valid && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status) && p.configuration.allow_early_withdrawal_with_penalty === true && (goal === null ? p.configuration.scope === 'general_idle_funds' && p.configuration.goal_id === null : p.configuration.scope === 'goal' && p.configuration.goal_id === goal)) ?? false;
  const goal = quoted?.revision.goal_id ?? baseAction?.effect.goal_id ?? state?.positions.find((p) => p.position_id === baseCost?.position_id)?.goal_id;
  const canRenew = available && (!previous.isError || !!update) && expired && !unresolved && !current?.partial && !!baseCost && permission(goal);
  const canPrepare = available && quoted?.quote_validity.status === 'VALID' && !view && !current?.partial && current?.recovery.prepare_request === null && permission(quoted.revision.goal_id);
  const canConfirm = available && !!view && view.action.status === 'PLANNED' && view.action.bank_status === null && view.action.receipt === null && view.signed === null && view.quote_validity.status === 'VALID' && Date.parse(view.server_as_of) < Date.parse(view.action.effect.expires_at) && permission(view.action.effect.goal_id);
  async function reload() {
    if (blocked || working) return; setWorking(true); setError('');
    try {
      const result = await runRead(() => readRevisionUpdate(environment));
      if (result) setFresh(result);
      else if (old?.view) { const original = await runRead(() => getNextLoss(old.recovery, environment)); setOldFresh({ ...old, view: original }); setFresh(null); if (original.action.bank_status !== null || original.action.receipt !== null || ['UNKNOWN', 'SUBMITTED'].includes(original.action.status)) throw new Error('原支取正在核实或已受理，请继续原动作；不会续报。'); if (Date.parse(original.action.as_of) < Date.parse(original.binding.quote.expires_at)) throw new Error('服务器核实原报价仍有效，请审阅原报价。'); }
      await source.refetch();
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function renew() {
    if (blocked || working || !signed || !canRenew || !baseCost) return; setWorking(true); setError('');
    try {
      if (current?.view) {
        const latest = await runRead(() => getRevisionView(current.recovery, environment));
        if (latest.view.binding.quote_hash !== view?.binding.quote_hash || latest.view.quote_validity.status !== 'EXPIRED' || latest.view.action.bank_status !== null || latest.view.action.receipt !== null || ['UNKNOWN', 'SUBMITTED'].includes(latest.view.action.status)) throw new Error('原报价或银行状态已变化，请核实原动作；不会替换报价。');
      } else if (current?.quote) {
        const latest = await runRead(() => readRevisionQuote(current.recovery.renewal_request, baseCost.quote_id, environment)); if (latest.quote_validity.status !== 'EXPIRED' || latest.quote_hash !== current.quote.quote_hash) throw new Error('服务器尚未确认原报价过期；不会续报。');
      } else if (old?.view) {
        const latest = await runRead(() => getNextLoss(old.recovery, environment)); if (latest.binding.quote_hash !== old.view.binding.quote_hash || latest.action.bank_status !== null || latest.action.receipt !== null || ['UNKNOWN', 'SUBMITTED'].includes(latest.action.status) || Date.parse(latest.action.as_of) < Date.parse(latest.binding.quote.expires_at)) throw new Error('原报价或银行状态已变化，请核实原动作；不会续报。');
      }
      const body = { expected_epoch_id: environment.epoch_id, position_id: baseCost.position_id, previous_quote_id: baseCost.quote_id, reviewed_previous_quote_hash: await hash(baseCost), client_request_id: crypto.randomUUID() };
      const recovery: LossRevisionRecovery = { protocol: 'zhiyu-next-loss-revision-recovery-v2', user_id: environment.dashboard.user_id, renewal_request: body, prepare_request: null, action_id: null, reviewed_quote_hash: null, reviewed_effect_hash: null };
      await perform(`${base}/renew`, body, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, recovery);
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function prepare() {
    if (blocked || working || !signed || !canPrepare || !current || !quoted) return; setWorking(true); setError('');
    try {
      const latest = await runRead(() => readRevisionQuote(current.recovery.renewal_request, quoted.revision.quote.quote_id, environment)); if (latest.quote_validity.status !== 'VALID' || latest.quote_hash !== quoted.quote_hash) throw new Error('新报价已变化或过期，请重新核实；不会默换已审报价。');
      const body = { expected_epoch_id: environment.epoch_id, position_id: quoted.revision.position_id, quote_id: quoted.revision.quote.quote_id, client_request_id: crypto.randomUUID() };
      await perform(`${base}/prepare`, body, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, { ...current.recovery, prepare_request: body, action_id: null, reviewed_quote_hash: null, reviewed_effect_hash: null });
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function confirm() {
    if (blocked || working || !signed || !canConfirm || !current || !view) return; setWorking(true); setError('');
    try {
      const latest = await runRead(() => getRevisionView(current.recovery, environment));
      if (latest.view.action.status !== 'PLANNED' || latest.view.action.bank_status !== null || latest.view.action.receipt !== null || latest.view.signed !== null || latest.view.binding.quote_hash !== view.binding.quote_hash || latest.view.binding.effect_hash !== view.binding.effect_hash || latest.view.quote_validity.status !== 'VALID' || Date.parse(latest.view.server_as_of) >= Date.parse(view.action.effect.expires_at)) throw new Error('新报价原件或动作状态已变化，请保留已审原件核实。');
      await perform(`${base}/actions/${view.binding.action_id}/confirm-and-execute`, { expected_epoch_id: environment.epoch_id, reviewed_quote_hash: view.binding.quote_hash, reviewed_effect_hash: view.binding.effect_hash, accepted: true, client_request_id: crypto.randomUUID() }, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, current.recovery);
    } catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  return <section aria-label="过期报价续报"><h4>过期报价续报</h4><p>服务器确认原报价过期且原持仓尚未受理支取后，可取得新报价。旧报价与原动作保留，新报价需要重新审阅并确认。</p>
    {(error || source.isError || previous.isError) && <p role="alert" className="zy-message zy-error">{error || (source.isError ? '续报入口尚未接通或无法核实，当前不会发送续报。' : userError(previous.error))}</p>}
    <div className="zy-buttons"><button className="zy-secondary" disabled={blocked || working} onClick={() => void reload()}>核实报价有效期与原状态</button><button className="zy-secondary" disabled={blocked || working || !signed || !canRenew} onClick={() => void renew()}>取得新报价</button></div>
    {!available && <p className="zy-muted">续报尚待本轮真实验收或入口核实，当前关闭新请求。</p>}
    {unresolved && <p>原支取已受理或结果正在核实，继续核实原动作；不会换报价。</p>}
    {recovering && !current && <p role="status">续报原请求正在独立核实，未找到原件仍保留同一请求。</p>}
    {quoted && <section aria-label="新报价必要后果"><h4>{view ? '已准备的新报价完整后果' : '独立核实的新报价'}</h4><dl className="zy-facts"><div><dt>本金</dt><dd>{money(quoted.revision.quote.principal_cents)}</dd></div><div><dt>费用</dt><dd>{money(quoted.revision.quote.fee_cents)}</dd></div><div><dt>明确本金损失</dt><dd>{money(quoted.revision.quote.loss_cents)}</dd></div><div><dt>净到账</dt><dd>{money(quoted.revision.quote.net_cents)}</dd></div></dl><p>本金可用时间 {quoted.revision.quote.principal_available_at.replace('T', ' ').slice(0, 19)}；新报价有效至 {quoted.revision.quote.expires_at.replace('T', ' ').slice(0, 19)}。服务器最近核实：{(view?.quote_validity ?? quoted.quote_validity).status === 'VALID' ? '报价有效' : '报价已过期'}。</p><p>到账账户：{businessText(environment.dashboard.account_facts.facts.accounts.find((a) => a.id === quoted.revision.destination_account_id)?.name ?? '原兑付账户')}。本金损失向上取整到分；不支付已计提利息，未来放弃收益尚未估算。</p>{canPrepare && <button className="zy-secondary" disabled={blocked || working || !signed} onClick={() => void prepare()}>准备新报价支取原件</button>}</section>}
    {current?.partial && <p role="status">同一新报价准备尚未完整，只能续接原准备；不能确认、执行或再次续报。</p>}
    {view?.action.receipt ? <p className="zy-result">新报价原银行与回执已核实：本金 {money(view.action.receipt.executed_cents)}，费用 {money(view.action.receipt.fee_cents)}，损失 {money(view.action.receipt.loss_cents)}，净到账 {money(view.action.effect.net_cents)}。</p> : view && <p>{['UNKNOWN', 'SUBMITTED'].includes(view.action.status) ? '同一新报价支取正在核实，保留原动作和银行请求。' : view.signed ? '本次新报价的明确同意已核实，尚无完成回执。' : '新报价仅已准备，尚未同意或执行支取。'}</p>}
    {canConfirm && <button className="zy-primary" disabled={blocked || working || !signed} onClick={() => void confirm()}>接受本次新报价损失并支取</button>}
    {resumeOriginal && current?.resume_original && signed && available && <button className="zy-secondary" disabled={working} onClick={() => void resumeOriginal()}>{current.partial ? '续接同一新报价准备' : '继续核实同一新报价支取'}</button>}
    {retryOriginal && recovering && signed && <button className="zy-secondary" disabled={working} onClick={() => void retryOriginal()}>恢复会话后续接原续报请求</button>}
  </section>;
}
