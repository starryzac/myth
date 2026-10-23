import { Client } from './api';
import { parseTemplateVersion, parseTemplateView } from './template-api';
import type { TemplatePolicyView } from './template-api';
import { templateDate, templateID } from './template-schema';
import { nfClosed, nfHash, nfMoney, nfObject, nfSameOwner, nfText, nfTime, parseActionFunding, parseActionHeader, parseActionOperation, parseLiability, validateActionLocator, validateCashResources } from './native-financial-api';
import type { ActionFunding, ActionLocator, ActionPreview, ActionTransport, FinancialDebt, FinancialLiability, FinancialOptions } from './native-financial-api';

export interface PaymentRequest { policy_version_id: string; obligation_id: string; source_account_id: string; amount_cents: string; timezone: 'UTC' | 'Asia/Shanghai' }
export interface BillReview { statement_account_id: string; first_observed_at: string; issued_at: string }
export interface PaymentPreview extends ActionPreview { kind: 'PAY_RECURRING'; request: PaymentRequest; policy: TemplatePolicyView; debt: FinancialDebt; liability: FinancialLiability; funding: ActionFunding; bill?: BillReview; adopted_version?: number }
function proofOriginal(raw: unknown): Record<string, unknown> {
  const text = nfText(raw, 87384); if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)) throw new Error('原债务字节不完整');
  const bytes = Uint8Array.from(atob(text), (c) => c.charCodeAt(0)); if (!bytes.length || bytes.length > 65536) throw new Error('原债务超出完整读取范围');
  return nfObject(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)));
}
function adoptionReview(raw: unknown, base: ActionPreview, policy: TemplatePolicyView, debt: FinancialDebt, liability: FinancialLiability, client: Client): number {
  const p = nfObject(raw); nfClosed(p, ['obligation_original', 'obligation_hash', 'adopted_version']); nfHash(p.obligation_hash);
  const o = proofOriginal(p.obligation_original); nfClosed(o, ['id', 'policy_id', 'version_id', 'due_date', 'amount_cents', 'verified', 'source_hash']);
  const old = parseTemplateVersion(p.adopted_version, client); nfSameOwner(base.scope, old.scope);
  const due = templateDate(o.due_date), currentRule = policy.version.compiled.configuration, oldRule = old.compiled.configuration;
  if ((oldRule.type !== 'recurring_obligation' && oldRule.type !== 'periodic_transfer') || (currentRule.type !== 'recurring_obligation' && currentRule.type !== 'periodic_transfer')) throw new Error('原债务规则种类不受支持');
  const upper = oldRule.amount_rule.kind === 'exact' ? oldRule.amount_rule.amount_cents : oldRule.amount_rule.kind === 'range' ? oldRule.amount_rule.max_cents : undefined;
  const final = BigInt(liability.final_total_cents), paid = BigInt(liability.paid_cents), remaining = final - paid;
  const day = Math.min(oldRule.due_day, new Date(Date.UTC(Number(due.slice(0, 4)), Number(due.slice(5, 7)), 0)).getUTCDate());
  if (upper == null || old.compiled.template_name !== policy.version.compiled.template_name || old.policy_id !== debt.policy_id || old.version_id !== debt.version_id || old.compiled.configuration_hash !== debt.source_hash || o.id !== debt.id || o.id !== `${old.policy_id}:monthly:${due.slice(0, 7)}` || o.policy_id !== debt.policy_id || o.version_id !== debt.version_id || due !== debt.due_date || Number(due.slice(8)) !== day || nfMoney(o.amount_cents) !== upper || o.verified !== true || nfHash(o.source_hash) !== debt.source_hash || liability.kind !== 'OCCURRENCE' || liability.policy_id !== debt.policy_id || liability.period !== due.slice(0, 7) || liability.evidence_hash !== debt.source_hash || oldRule.payee_id !== liability.payee_id || currentRule.payee_id !== liability.payee_id || Date.parse(old.confirmed_at) > Date.parse(base.created_at) || BigInt(debt.amount_cents) < remaining || BigInt(debt.amount_cents) > BigInt(upper) - paid || [oldRule.amount_rule, currentRule.amount_rule].some((r) => r.kind === 'exact' ? final !== BigInt(r.amount_cents) : r.kind !== 'range' || final < BigInt(r.min_cents) || final > BigInt(r.max_cents))) throw new Error('原债务版本、原最终金额和当前支付许可不一致');
  return old.version;
}
function billReview(raw: unknown, base: ActionPreview, policy: TemplatePolicyView, debt: FinancialDebt, liability: FinancialLiability, client: Client): BillReview {
  const p = nfObject(raw); nfClosed(p, ['original', 'original_hash', 'bank_reference', 'bank_reference_hash', 'first_observed_at', 'proven_paid_cents', 'adopted_version']);
  const encoded = nfText(p.original, 87384); if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) throw new Error('原账单字节不完整');
  const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0)); if (!bytes.length || bytes.length > 65536) throw new Error('原账单超出完整读取范围');
  const bill = nfObject(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))); nfClosed(bill, ['protocol', 'id', 'scope', 'bank_namespace', 'statement_account_id', 'due_date', 'issued_at', 'final_total_cents', 'payee_id', 'external_key']);
  nfSameOwner(base.scope, bill.scope); const old = parseTemplateVersion(p.adopted_version, client); nfSameOwner(base.scope, old.scope);
  const currentRule = policy.version.compiled.configuration, oldRule = old.compiled.configuration;
  const account = templateID(bill.statement_account_id), first = nfTime(p.first_observed_at), issued = nfTime(bill.issued_at);
  if (bill.protocol !== 'zhiyu-external-bill-v1' || !/^zygo_[a-z][a-z0-9_]{0,63}$/.test(nfText(bill.bank_namespace)) || debt.id !== `bill:${templateID(bill.id)}` || templateDate(bill.due_date) !== debt.due_date || nfMoney(bill.final_total_cents, true) !== liability.final_total_cents || bill.payee_id !== liability.payee_id || !nfText(bill.external_key) || nfHash(p.original_hash) !== liability.evidence_hash || nfHash(p.bank_reference_hash) !== liability.original_hash || JSON.stringify(p.bank_reference) !== JSON.stringify(liability.original) || nfMoney(p.proven_paid_cents) !== liability.paid_cents || Date.parse(issued) > Date.parse(first) || Date.parse(first) > Date.parse(base.created_at) || Date.parse(old.confirmed_at) > Date.parse(base.created_at) || old.policy_id !== debt.policy_id || old.version_id !== debt.version_id || old.compiled.configuration_hash !== debt.source_hash || [currentRule, oldRule].some((r) => r.type !== 'recurring_obligation' || r.amount_rule.kind !== 'bill_balance' || r.amount_rule.account_id !== account || r.payee_id !== bill.payee_id) || BigInt(debt.amount_cents) + BigInt(liability.paid_cents) !== BigInt(liability.final_total_cents)) throw new Error('原账单、最早观察、采纳版本与当前支付许可不一致');
  return { statement_account_id: account, first_observed_at: first, issued_at: issued };
}
export function paymentCandidates(options: FinancialOptions) {
  return options.debts.flatMap((debt) => {
    const liability = options.liabilities.find((l) => l.id === debt.id && l.reserved_cents === '0' && BigInt(l.final_total_cents) > BigInt(l.paid_cents));
    if (!liability || debt.amount_cents === '0') return [];
    const rule = options.policies.find((p) => {
      if (p.version.policy_id !== debt.policy_id || p.effective_state !== 'ACTIVE' || p.goal_id) return false;
      const config = p.version.compiled.configuration;
      return liability.kind === 'BILL' ? debt.id.startsWith('bill:') && config.type === 'recurring_obligation' && config.amount_rule.kind === 'bill_balance' && config.payee_id === liability.payee_id : liability.policy_id === debt.policy_id && liability.evidence_hash === debt.source_hash && ['RecurringObligationPolicy', 'PeriodicTransferPolicy'].includes(p.version.compiled.template_name);
    });
    return rule ? [{ debt, rule, liability }] : [];
  });
}
function paymentRequest(raw: unknown): PaymentRequest { const r = nfObject(raw); nfClosed(r, ['policy_version_id', 'obligation_id', 'source_account_id', 'amount_cents', 'timezone']); if (r.timezone !== 'UTC' && r.timezone !== 'Asia/Shanghai') throw new Error('支付自然时区不受支持'); return { policy_version_id: templateID(r.policy_version_id), obligation_id: nfText(r.obligation_id, 128), source_account_id: templateID(r.source_account_id), amount_cents: nfMoney(r.amount_cents), timezone: r.timezone }; }
export function parsePaymentPreview(raw: unknown, client: Client): PaymentPreview {
  const base = parseActionHeader(raw, client, 'native-payment-preview-v1', ['PAY_RECURRING']), p = base.original;
  nfClosed(p, ['id', 'scope', 'session_id', 'kind', 'effect_hash', 'dependencies', 'source_hash', 'inventory_hash', 'created_at', 'expires_at', 'bank_authority', 'protocol', 'timezone', 'request', 'policy', 'debt', 'liability', 'bill_proof', 'adoption_proof', 'bank_effect', 'resources', 'ledger', 'inventory_original', 'forecast_input', 'forecast_before', 'forecast_after', 'reviewed_hash']);
  const request = paymentRequest(p.request), policy = parseTemplateView(p.policy, client), debt = nfObject(p.debt), liability = parseLiability(p.liability), effect = nfObject(p.bank_effect), funding = parseActionFunding(effect);
  nfSameOwner(base.scope, policy.version.scope);
  if (policy.version.version_id !== request.policy_version_id || policy.goal_id || !['RecurringObligationPolicy', 'PeriodicTransferPolicy'].includes(policy.version.compiled.template_name) || policy.effective_state !== 'ACTIVE' || debt.verified !== true || debt.id !== request.obligation_id || debt.policy_id !== policy.version.policy_id || liability.id !== debt.id || liability.reserved_cents !== '0' || BigInt(base.amount_cents) > BigInt(nfMoney(debt.amount_cents)) || BigInt(base.amount_cents) + BigInt(liability.paid_cents) > BigInt(liability.final_total_cents) || (request.amount_cents !== '0' && request.amount_cents !== base.amount_cents)) throw new Error('支付原规则、已采纳债务与独立最终账单不一致');
  if (effect.business_key !== base.id || effect.liability == null || JSON.stringify(effect.liability) !== JSON.stringify(liability.original) || base.fee_cents !== '0' || base.loss_cents !== '0' || base.net_cents !== '0' || effect.product !== null || ['destination_account_id', 'source_goal_id', 'destination_goal_id', 'position_id', 'position_opening_hash', 'return_account_id', 'quote_id'].some((key) => effect[key] !== '') || ['principal_available_by', 'planned_redeem_at'].some((key) => effect[key] !== '0001-01-01T00:00:00Z') || effect.latest_arrival_at !== base.expires_at || funding.cash_uses.length !== 1 || funding.cash_uses[0]!.account_id !== request.source_account_id || funding.cash_uses[0]!.amount_cents !== base.amount_cents || funding.income_uses.some((v) => v.bucket !== 'AVAILABLE')) throw new Error('支付包含未审阅的经济效果或已归属来源');
  validateCashResources(p.resources, funding, base.amount_cents);
  if (base.forecast_after.points.some((point, i) => point.id !== base.forecast_before.points[i]!.id || BigInt(point.margin_cents) < BigInt(base.forecast_before.points[i]!.margin_cents))) throw new Error('支付后保护余量低于原审阅');
  const parsedDebt: FinancialDebt = { id: request.obligation_id, policy_id: templateID(debt.policy_id), version_id: templateID(debt.version_id), due_date: templateDate(debt.due_date), amount_cents: nfMoney(debt.amount_cents), source_hash: nfHash(debt.source_hash) };
  const bill = liability.kind === 'BILL' ? billReview(p.bill_proof, base, policy, parsedDebt, liability, client) : undefined;
  const adopted = p.adoption_proof != null ? adoptionReview(p.adoption_proof, base, policy, parsedDebt, liability, client) : undefined;
  if (bill && adopted != null || !bill && p.bill_proof != null || !bill && adopted == null && (debt.version_id !== policy.version.version_id || liability.policy_id !== debt.policy_id || liability.evidence_hash !== debt.source_hash)) throw new Error('原周期债务和许可版本不一致');
  const rule = policy.version.compiled.configuration; if (rule.type === 'periodic_transfer' && rule.source_account_id !== request.source_account_id) throw new Error('本次付款账户不在当前转账许可内');
  return { ...base, kind: 'PAY_RECURRING', request, policy, debt: parsedDebt, liability, funding, ...(bill ? { bill } : {}), ...(adopted == null ? {} : { adopted_version: adopted }) };
}
export class PaymentClient implements ActionTransport<PaymentPreview> {
  readonly family = 'payments' as const;
  constructor(readonly client: Client) {}
  parsePreview(raw: unknown) { return parsePaymentPreview(raw, this.client); }
  async preview(body: PaymentRequest) { const value = this.parsePreview(await this.client.request('/payment-previews', { method: 'POST', body: JSON.stringify(paymentRequest(body)) })); if (JSON.stringify(value.request) !== JSON.stringify(paymentRequest(body))) throw new Error('服务端返回另一份支付预览'); return value; }
  async original(preview: PaymentPreview, signal?: AbortSignal) { return this.parsePreview(await this.client.request(`/payment-previews/${encodeURIComponent(preview.id)}`, { signal })); }
  async accept(original: ActionLocator<PaymentPreview>) { validateActionLocator(this, original); return parseActionOperation(await this.client.request('/payments', { method: 'POST', headers: { 'Idempotency-Key': original.key }, body: JSON.stringify(original.command) }), original); }
  async byKey(original: ActionLocator<PaymentPreview>, signal?: AbortSignal) { return parseActionOperation(await this.client.request(`/payment-commands/${encodeURIComponent(original.key)}`, { signal }), original); }
  async operation(id: string, original: ActionLocator<PaymentPreview>, signal?: AbortSignal) { return parseActionOperation(await this.client.request(`/payments/${encodeURIComponent(templateID(id))}`, { signal }), original); }
}
