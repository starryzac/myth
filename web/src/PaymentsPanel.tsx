import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Client, Dashboard } from './api';
import { money, yuanToCents } from './money';
import { timestamp, userError } from './display';
import { financialOptions } from './native-financial-api';
import type { FinancialOptions } from './native-financial-api';
import { PaymentClient, paymentCandidates } from './payment-api';
import type { PaymentPreview } from './payment-api';
import NativeActionRecovery from './NativeActionRecovery';

export default function PaymentsPanel({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId(), api = useMemo(() => new PaymentClient(client), [client]);
  const [options, setOptions] = useState<FinancialOptions | null>(null), [loading, setLoading] = useState(true), [readError, setReadError] = useState(''), [version, setVersion] = useState(0);
  const [debtID, setDebtID] = useState(''), [accountID, setAccountID] = useState(''), [yuan, setYuan] = useState(''), [preview, setPreview] = useState<PaymentPreview | null>(null);
  const [blocked, setBlocked] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''); const revision = useRef(0);
  function consume() { revision.current++; setPreview(null); }
  useEffect(() => {
    const controller = new AbortController(); let cancelled = false; setLoading(true); setReadError(''); consume();
    void financialOptions(client, controller.signal).then((value) => { if (!cancelled) setOptions(value); }).catch((cause) => { if (!cancelled) { setOptions(null); setReadError(userError(cause)); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [client, dashboard, version]);
  const payable = options ? paymentCandidates(options) : [];
  const selected = payable.find((item) => item.debt.id === debtID), config = selected?.rule.version.compiled.configuration;
  const accounts = dashboard.accounts.filter((a) => config?.type !== 'periodic_transfer' || config.source_account_id === a.id);
  const nameAccount = (id: string) => dashboard.accounts.find((a) => a.id === id)?.name ?? '原审阅付款账户';
  async function createPreview() {
    if (blocked || busy || loading || !selected || !accountID) return;
    consume(); const expected = revision.current; setBusy(true); setError('');
    try { const value = await api.preview({ policy_version_id: selected.rule.version.version_id, obligation_id: selected.debt.id, source_account_id: accountID, amount_cents: yuan.trim() ? yuanToCents(yuan) : '0', timezone: selected.rule.timezone }); if (expected !== revision.current) throw new Error('当前账单或规则已经变化，请重新核对'); setPreview(value); }
    catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  return <section className="zy-template-review" aria-label="已确认负债支付"><h3>已确认负债支付</h3><p>仅支付已采纳债务和独立银行最终账单均一致的原事项。输入金额是本次支付范围，不能新造账单、收款人或使用其他目标已归属的资金。</p>
    <button className="zy-secondary" disabled={busy || loading} onClick={() => { consume(); setVersion((v) => v + 1); }}>重新读取待付账单</button>
    {loading && <p role="status">正在读取真实已采纳债务与独立最终账单…</p>}{readError && <p className="zy-message zy-error" role="alert">{readError}</p>}
    {options && !loading && <p className="zy-muted">读取于 {timestamp(options.captured_at)}；提交前服务端会再次核对最终余额与保护。</p>}
    {options && !loading && !payable.length && <p>当前没有同时匹配有效规则、已采纳原债务与独立最终账单的可预览付款。未决、已付清或待核实的账单不会被当作新付款。</p>}
    {!!payable.length && <form className="zy-template-grid" onSubmit={(event) => { event.preventDefault(); void createPreview(); }}>
      <label className="zy-field" htmlFor={`${formID}-debt`}>本次待付原账单<select id={`${formID}-debt`} aria-label="本次待付原账单" value={debtID} disabled={blocked || busy || loading} onChange={(event) => { setDebtID(event.target.value); setAccountID(''); consume(); }}><option value="">请选择已核实的原事项</option>{payable.map((item, i) => <option key={item.debt.id} value={item.debt.id}>{item.rule.version.compiled.configuration.name ?? `已确认账单 ${i + 1}`} · {item.debt.due_date} · 待付 {money((BigInt(item.liability.final_total_cents) - BigInt(item.liability.paid_cents)).toString())}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-account`}>本次付款账户<select id={`${formID}-account`} aria-label="本次付款账户" value={accountID} disabled={blocked || busy || loading || !selected} onChange={(event) => { setAccountID(event.target.value); consume(); }}><option value="">请选择本人原账户</option>{accounts.map((account) => <option value={account.id} key={account.id}>{account.name}</option>)}</select></label>
      <label className="zy-field" htmlFor={`${formID}-amount`}>本次支付金额（元，可留空）<input id={`${formID}-amount`} aria-label="本次支付金额（元，可留空）" inputMode="decimal" value={yuan} disabled={blocked || busy || loading} onChange={(event) => { setYuan(event.target.value); consume(); }} /><span className="zy-muted">留空时由服务端按最终待付余额与已确认规则计算，仍须审阅实际金额后本人确认。</span></label>
      <button className="zy-secondary" type="submit" disabled={blocked || busy || loading || !selected || !accountID}>预览本次负债支付</button>
    </form>}
    {preview && <div aria-label="负债支付完整审阅"><h4>核对最终账单与本次支付</h4><p>{preview.policy.version.compiled.configuration.name ?? '本人已确认的账单规则'} · 到期日 {preview.debt.due_date}。从“{nameAccount(preview.request.source_account_id)}”支付 {money(preview.amount_cents)}。</p>
      <p>独立最终总额 {money(preview.liability.final_total_cents)}，原已付 {money(preview.liability.paid_cents)}，本笔结清后剩余 {money((BigInt(preview.liability.final_total_cents) - BigInt(preview.liability.paid_cents) - BigInt(preview.amount_cents)).toString())}。费用与损失均为 {money('0')}。</p>
      {preview.bill && <p>原账单账户“{nameAccount(preview.bill.statement_account_id)}”，出账于 {timestamp(preview.bill.issued_at)}，首次核实于 {timestamp(preview.bill.first_observed_at)}。本次使用当前有效许可；先前已采纳的原债务保持原身份。</p>}
      {preview.adopted_version != null && <p>原债务按第{preview.adopted_version}版规则采纳，本次按当前第{preview.policy.version.version}版有效许可核对。原到期日与已付记录保留。</p>}
      <p>只使用实际未归属现金：{preview.funding.income_uses.length}项原收入、{preview.funding.return_uses.length}项实际本金返还；其余为已独立核实的现金余量。返本不会成为新收入。</p>
      <p>365日保护完整核对 {preview.forecast_after.points.length} 个阶段点；今日保护余量 {money(preview.forecast_before.current_safe_cents)} → {money(preview.forecast_after.current_safe_cents)}，最大缺口 {money(preview.forecast_before.maximum_deficit_cents)} → {money(preview.forecast_after.maximum_deficit_cents)}。</p>
    </div>}
    <NativeActionRecovery api={api} preview={preview} onConsumed={consume} onBlockedChange={setBlocked} onChanged={onChanged} />
    {busy && <p role="status">正在核对本次付款的完整原事实…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
