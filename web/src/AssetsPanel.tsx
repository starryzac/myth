import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Client, Dashboard } from './api';
import { money, yuanToCents } from './money';
import { timestamp, userError } from './display';
import { actionKindLabel, financialOptions, productLabel } from './native-financial-api';
import type { AssetKind, FinancialOptions, FinancialPosition, ProductTerms } from './native-financial-api';
import { AssetClient } from './asset-api';
import type { AssetPreview } from './asset-api';
import NativeActionRecovery from './NativeActionRecovery';

function policyAssetClass(terms: ProductTerms): string { return terms.asset_class !== 'FIXED_DEPOSIT' ? terms.asset_class : [7, 30, 90].includes(terms.term_days) ? `FIXED_DEPOSIT_${terms.term_days}D` : 'LOW_RISK_TERM'; }
export default function AssetsPanel({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId(), api = useMemo(() => new AssetClient(client), [client]);
  const [options, setOptions] = useState<FinancialOptions | null>(null), [loading, setLoading] = useState(true), [readError, setReadError] = useState(''), [version, setVersion] = useState(0);
  const [kind, setKind] = useState<AssetKind>('PURCHASE_ASSET'), [ruleID, setRuleID] = useState(''), [objectID, setObjectID] = useState(''), [yuan, setYuan] = useState(''), [preview, setPreview] = useState<AssetPreview | null>(null);
  const [blocked, setBlocked] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''); const revision = useRef(0);
  function consume() { revision.current++; setPreview(null); }
  useEffect(() => {
    const controller = new AbortController(); let cancelled = false; setLoading(true); setReadError(''); consume();
    void financialOptions(client, controller.signal).then((value) => { if (!cancelled) setOptions(value); }).catch((cause) => { if (!cancelled) { setOptions(null); setReadError(userError(cause)); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [client, dashboard, version]);
  const rules = options?.policies.filter((p) => p.effective_state === 'ACTIVE' && (p.version.compiled.configuration.type === 'asset_authorization' || (kind !== 'PURCHASE_ASSET' && p.version.compiled.configuration.type === 'recovery'))) ?? [];
  const rule = rules.find((r) => r.version.version_id === ruleID), config = rule?.version.compiled.configuration;
  const products = options?.products.filter((p) => p.availability.status === 'AVAILABLE' && config?.type === 'asset_authorization' && (config.allowed_asset_classes as readonly string[]).includes(policyAssetClass(p.terms))) ?? [];
  const positions = options?.positions.filter((position) => {
    if (position.closed || position.closing_key || !config || (config.type !== 'asset_authorization' && config.type !== 'recovery')) return false;
    const policyID = config.type === 'recovery' ? config.asset_policy_id : rule?.version.policy_id;
    return position.source_policy_id === policyID && (config.scope === 'goal' ? position.goal_id === config.goal_id : !position.goal_id);
  }) ?? [];
  const nameGoal = (id?: string) => id ? dashboard.goals.find((g) => g.id === id)?.name ?? '原归属目标' : '本人未归属闲置资金';
  const nameAccount = (id: string) => dashboard.accounts.find((a) => a.id === id)?.name ?? '原购买回款账户';
  function positionLabel(position: FinancialPosition, index: number) { return `原持仓 ${index + 1} · ${productLabel(position.terms)} · 本金 ${money(position.principal_cents)} · ${nameGoal(position.goal_id)}`; }
  async function createPreview() {
    if (blocked || busy || loading || !rule || !objectID) return;
    consume(); const expected = revision.current; setBusy(true); setError('');
    try { const value = await api.preview({ kind, policy_version_id: rule.version.version_id, ...(kind === 'PURCHASE_ASSET' ? { product_id: objectID } : { position_id: objectID }), amount_cents: kind === 'PURCHASE_ASSET' ? yuanToCents(yuan) : '0', timezone: rule.timezone }); if (expected !== revision.current) throw new Error('当前规则或持仓已变化，请重新读取完整原事实'); setPreview(value); }
    catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  return <section className="zy-template-review" aria-label="资产购买与本金回收"><h3>资产购买与本金回收</h3><p>只使用当前本人已确认的资产范围与独立银行真实产品、原持仓。购买、赎回、到期回收分别审阅；本金返还保留原身份，未结清前不能当作新收入或可用现金。</p>
    <button className="zy-secondary" disabled={busy || loading} onClick={() => { consume(); setVersion((v) => v + 1); }}>重新读取产品与原持仓</button>
    {loading && <p role="status">正在读取实际产品条款与原持仓…</p>}{readError && <p className="zy-message zy-error" role="alert">{readError}</p>}
    {options && !loading && <p className="zy-muted">读取于 {timestamp(options.captured_at)}。产品标签来自实际类别和期限；提交前重新核对原条款、报价、当前规则与保护。</p>}
    {options && !loading && !rules.length && <p>当前没有匹配本次动作的有效资产规则。请先在规则页明确资产范围、类别、上限、期限和可接受的费用或损失。</p>}
    <form className="zy-template-grid" onSubmit={(event) => { event.preventDefault(); void createPreview(); }}>
      <label className="zy-field" htmlFor={`${formID}-kind`}>本次资产动作<select id={`${formID}-kind`} aria-label="本次资产动作" disabled={blocked || busy || loading} value={kind} onChange={(event) => { setKind(event.target.value as AssetKind); setRuleID(''); setObjectID(''); consume(); }}><option value="PURCHASE_ASSET">购买当前产品</option><option value="REDEEM_ASSET">赎回原持仓</option><option value="MATURE_ASSET">到期本金回收</option></select></label>
      <label className="zy-field" htmlFor={`${formID}-rule`}>本次资产范围规则<select id={`${formID}-rule`} aria-label="本次资产范围规则" value={ruleID} disabled={blocked || busy || loading} onChange={(event) => { setRuleID(event.target.value); setObjectID(''); consume(); }}><option value="">请选择本人已确认的范围</option>{rules.map((r, i) => <option key={r.version.version_id} value={r.version.version_id}>{r.version.compiled.configuration.name ?? `${r.version.compiled.configuration.type === 'recovery' ? '零损回收' : '资产安排'}规则 ${i + 1}`} · 第{r.version.version}版</option>)}</select></label>
      {kind === 'PURCHASE_ASSET' ? <><label className="zy-field" htmlFor={`${formID}-product`}>本次购买的实际产品<select id={`${formID}-product`} aria-label="本次购买的实际产品" value={objectID} disabled={blocked || busy || loading || !rule} onChange={(event) => { setObjectID(event.target.value); consume(); }}><option value="">请选择真实产品条款</option>{products.map((p, i) => <option value={p.terms.product_id} key={p.terms.product_id}>{productLabel(p.terms)} · 第{i + 1}项 · 条款第{p.terms.version}版 · 起购 {money(p.terms.minimum_purchase_cents)}</option>)}</select></label><label className="zy-field" htmlFor={`${formID}-amount`}>本次购买金额上限（元）<input id={`${formID}-amount`} aria-label="本次购买金额上限（元）" inputMode="decimal" value={yuan} disabled={blocked || busy || loading} required onChange={(event) => { setYuan(event.target.value); consume(); }} /><span className="zy-muted">实际候选金额由服务端按当前资金、保护与本人规则计算，可能低于此上限。</span></label>{rule && !products.length && <p>这份规则当前没有匹配且可购买的实际产品。</p>}</> : <><label className="zy-field" htmlFor={`${formID}-position`}>本次回收的原持仓<select id={`${formID}-position`} aria-label="本次回收的原持仓" value={objectID} disabled={blocked || busy || loading || !rule} onChange={(event) => { setObjectID(event.target.value); consume(); }}><option value="">请选择仍未关闭的原持仓</option>{positions.map((p, i) => <option value={p.id} key={p.id}>{positionLabel(p, i)}</option>)}</select></label><p>本入口按完整原持仓本金报价；是否已到期、锁定、费用和损失均以服务端独立原报价为准。</p>{rule && !positions.length && <p>这份规则当前没有匹配且尚未关闭的原持仓。</p>}</>}
      <button className="zy-secondary" type="submit" disabled={blocked || busy || loading || !rule || !objectID || (kind === 'PURCHASE_ASSET' && !yuan)}>预览本次{actionKindLabel[kind]}</button>
    </form>
    {preview && <div aria-label="资产安排完整审阅"><h4>核对本次{actionKindLabel[preview.kind]}</h4><p>{productLabel(preview.product.terms)} · 条款第{preview.product.terms.version}版；归属：{nameGoal(preview.goal_id)}。本金 {money(preview.amount_cents)}。</p>
      {preview.kind === 'PURCHASE_ASSET' ? <><p>购买后本金进入原持仓，现金减少 {money(preview.amount_cents)}。归还账户为“{nameAccount(preview.return_account_id)}”；本金计划最迟可用时间 {timestamp(preview.available_at)}{preview.planned_redeem_at ? `，计划赎回时间 ${timestamp(preview.planned_redeem_at)}` : ''}，这些未来时点不能提前扩大今日现金。</p><p>实际使用 {preview.funding.cash_uses.length}个账户的原现金；{preview.funding.income_uses.length}项原收入用途、{preview.funding.return_uses.length}项实际本金返还用途。</p></> : <><p>原本金 {money(preview.amount_cents)}，原报价费用 {money(preview.fee_cents)}、本金损失 {money(preview.loss_cents)}、净返本 {money(preview.net_cents)}。</p><p>报价预计实际可用时点 {timestamp(preview.available_at)}，返至“{nameAccount(preview.return_account_id)}”；有回执后才更新现金，本金不会成为新收入。</p></>}
      <p>当前产品锁定 {preview.product.terms.lock_days}天、赎回延迟 {preview.product.terms.redemption_delay_days}天；没有自动续期。</p><p>365日保护完整核对 {preview.forecast_after.points.length} 个阶段点；今日余量 {money(preview.forecast_before.current_safe_cents)} → {money(preview.forecast_after.current_safe_cents)}，最大缺口 {money(preview.forecast_before.maximum_deficit_cents)} → {money(preview.forecast_after.maximum_deficit_cents)}。</p>
      {preview.recovery_trigger && <p>当前零损回收触发已由服务端核实，仍需本人审阅这一笔原持仓动作。</p>}
    </div>}
    <NativeActionRecovery api={api} preview={preview} onConsumed={consume} onBlockedChange={setBlocked} onChanged={onChanged} />
    {busy && <p role="status">正在读取原报价并计算完整资产保护…</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
