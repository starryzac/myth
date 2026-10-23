import { Client } from './api';
import { cents } from './money';
import { parseConfiguration, parseTemplateName, templateDate, templateID, templateMoney } from './template-schema';
import type { TemplateConfiguration, TemplateName } from './template-schema';

export interface TemplateScope { environment_id: string; user_id: string }
export interface CompiledTemplate { protocol: 'policy-templates-go-v1'; template_name: TemplateName; configuration: TemplateConfiguration; configuration_hash: string }
export interface TemplateVersion { policy_id: string; version_id: string; version: number; scope: TemplateScope; compiled: CompiledTemplate; state: string; confirmed_at: string; current: boolean; confirmation_verified: true; confirmed_configuration_hash: string }
export interface TemplatePolicyView { version: TemplateVersion; goal_id?: string; timezone: 'UTC' | 'Asia/Shanghai'; effective_state: string; confirmation_id: string; reviewed_bundle_hash: string; status_sequence: number; bank_authority: false }
export interface TemplateRequest { template_name: TemplateName; configuration: TemplateConfiguration; policy_id?: string; goal_id?: string; timezone: 'UTC' | 'Asia/Shanghai' }
export interface ForecastPoint { id: string; date: string; phase: 'BEFORE_PAYMENT' | 'AFTER_PAYMENT' | 'AFTER_PRINCIPAL'; at: string; cash_cents: string; protected_cents: string; margin_cents: string; future_income_cents: string; forecast_cash_cents: string }
export interface ForecastResult { algorithm: 'cash-protection-forecast-go-v1'; input_hash: string; as_of: string; timezone: 'UTC' | 'Asia/Shanghai'; horizon_days: number; state: 'READY' | 'PROTECTION_DEFICIT'; current_safe_cents: string; maximum_deficit_cents: string; points: ForecastPoint[]; obligations: { id: string; policy_id: string; version_id: string; due_date: string; amount_cents: string; verified: true; source_hash: string }[]; external_liabilities?: { id: string; due_date: string; remaining_cents: string; verified: true; source_hash: string }[]; reasons: string[]; bank_authority: false }
export interface ForecastView { protocol: 'template-forecast-view-v1'; source_hash: string; inventory_hash: string; funds_version: number; source_version: number; result: ForecastResult; protection_source_version?: number; bank_authority: false }
export interface TemplateFacts {
  current_cash_cents: string; current_safe_cents: string; already_reallocated_cents: string;
  managed_amount_cents: string; standing_authorization_covered: boolean; income_complete: boolean;
  history: { date: string; amount_cents: string; category: string; category_confirmed: boolean; one_off: boolean; direction: string; economic_role: string }[];
  coverage: { account_id: string; dates: string[] }[];
  settlements: { policy_id: string; period: string; paid_cents: string; final_total_cents?: string }[];
  accounts: { id: string; balance_cents: string }[];
  goals: { id: string; account_id: string; owned_cents: string; cash_owned_cents: string; month_contributed_cents: string; month_period: string }[];
  income: { id: string; available_cents: string; occurred_at: string }[];
  bills: { id: string; account_id: string; due_date: string; total_cents: string; paid_cents: string }[];
  products: { id: string; asset_class: string; minimum_cents: string; lock_days: number; redemption_delay_days: number; risk_level: number }[];
  positions: { id: string; principal_cents: string; recoverable_cents: string; fee_cents: string; loss_cents: string; state: string }[];
}
export interface TemplateImpact {
  state: string; amount_cents: string; minimum_shortfall_cents: string; guarantee_shortfall_cents: string;
  commitments: { id: string; kind: string; due_date: string; end_date?: string; amount_cents: string; account_id?: string; payee_id?: string }[];
  candidates: { kind: string; subject_id: string; target_id?: string; amount_cap_cents: string; requires_confirmation: boolean; reasons: string[] }[];
  goal_demands: { goal_id: string; minimum_cents: string; desired_cents: string; maximum_cents: string; importance: number; earliest_income_at: string }[];
  questions: string[]; reasons: string[]; bank_authority: false;
}
export interface TemplatePreview {
  id: string; policy_id: string; version_id: string; base_version: number; mode: 'IF_USER_CONFIRMED'; scope: TemplateScope; goal_id?: string;
  target_mutation?: { goal_id: string; before_cents: string; after_cents: string };
  timezone: 'UTC' | 'Asia/Shanghai'; compiled: CompiledTemplate; facts: TemplateFacts; preview: TemplateImpact; forecast: ForecastResult;
  reviewed_bundle_hash: string; created_at: string; expires_at: string; authority: 'POLICY_CONFIGURATION_ONLY_NO_BANK_GRANT'; bank_authority: false;
}
export interface PendingTemplateConfirmation { environment: string; user_id: string; key: string; policy_id: string; version_id: string; request: { preview_id: string; reviewed_bundle_hash: string } }

function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('服务端规则结构不完整'); return value as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('服务端规则字段类型不符'); return value; }
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number { if (!Number.isSafeInteger(value) || (value as number) < min || (value as number) > max) throw new Error('服务端规则数值超出范围'); return value as number; }
function flag(value: unknown): boolean { if (typeof value !== 'boolean') throw new Error('服务端规则开关不完整'); return value; }
function verified(value: unknown): true { if (value !== true) throw new Error('服务端规则事实尚未通过核实'); return true; }
function noBankAuthority(value: unknown): false { if (value !== false) throw new Error('规则预览不符合配置确认范围，已停止'); return false; }
function hash(value: unknown): string { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) throw new Error('服务端原件引用不完整'); return value; }
function instant(value: unknown): string { const valueText = text(value); if (!Number.isFinite(Date.parse(valueText))) throw new Error('服务端规则时间不完整'); return valueText; }
function timezone(value: unknown): 'UTC' | 'Asia/Shanghai' { if (value !== 'UTC' && value !== 'Asia/Shanghai') throw new Error('规则自然时区不受支持'); return value; }
function list<T>(value: unknown, parse: (raw: unknown) => T, maximum = 10000): T[] { if (!Array.isArray(value) || value.length > maximum) throw new Error('规则列表超出完整范围'); return value.map(parse); }
function strings(value: unknown): string[] { return list(value, text, 1000); }
function optionalID(value: unknown): string | undefined { return value == null ? undefined : templateID(value); }
function scope(value: unknown, client?: Client): TemplateScope { const item = record(value); const result = { environment_id: templateID(item.environment_id), user_id: templateID(item.user_id) }; if (client?.session && result.user_id !== client.session.user_id) throw new Error('规则主体与当前会话不一致'); return result; }
function sameScope(left: TemplateScope, right: unknown): void { const other = scope(right); if (other.environment_id !== left.environment_id || other.user_id !== left.user_id) throw new Error('预览中的当前事实不属于同一主体'); }
function financialTree(value: unknown): void {
  if (Array.isArray(value)) { for (const child of value) financialTree(child); return; }
  if (value && typeof value === 'object') for (const [key, child] of Object.entries(value)) { if (key === 'bank_authority') noBankAuthority(child); if (key.includes('_cents') && child != null && typeof child !== 'object') { if (['cash_cents', 'margin_cents', 'forecast_cash_cents'].includes(key)) cents(child); else templateMoney(child); } financialTree(child); }
}
function compiled(value: unknown): CompiledTemplate { const item = record(value); if (item.protocol !== 'policy-templates-go-v1') throw new Error('规则版本协议不符'); const name = parseTemplateName(item.template_name); return { protocol: item.protocol, template_name: name, configuration: parseConfiguration(name, item.configuration), configuration_hash: hash(item.configuration_hash) }; }
export function parseTemplateVersion(value: unknown, client?: Client): TemplateVersion {
  financialTree(value); const raw = record(value), configuration = compiled(raw.compiled), confirmed = hash(raw.confirmed_configuration_hash);
  if (confirmed !== configuration.configuration_hash) throw new Error('规则配置与原确认不一致');
  const state = text(raw.state);
  if (!['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED', 'CONFIRMATION_IN_FUTURE'].includes(state)) throw new Error('规则状态尚待核实');
  return { policy_id: templateID(raw.policy_id), version_id: templateID(raw.version_id), version: integer(raw.version, 1), scope: scope(raw.scope, client), compiled: configuration, state, confirmed_at: instant(raw.confirmed_at), current: flag(raw.current), confirmation_verified: verified(raw.confirmation_verified), confirmed_configuration_hash: confirmed };
}
export function parseTemplateView(value: unknown, client?: Client): TemplatePolicyView {
  financialTree(value); const item = record(value), version = parseTemplateVersion(item.version, client), effective = text(item.effective_state);
  if (!['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED', 'CONFIRMATION_IN_FUTURE'].includes(effective)) throw new Error('规则状态尚待核实');
  return { version, goal_id: optionalID(item.goal_id), timezone: timezone(item.timezone), effective_state: effective, confirmation_id: templateID(item.confirmation_id), reviewed_bundle_hash: hash(item.reviewed_bundle_hash), status_sequence: integer(item.status_sequence, 1), bank_authority: noBankAuthority(item.bank_authority) };
}
export function parseForecastResult(value: unknown): ForecastResult {
  const item = record(value); if (item.algorithm !== 'cash-protection-forecast-go-v1') throw new Error('预测算法版本不符');
  const horizon = integer(item.horizon_days, 1, 365);
  const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const;
  const points = list(item.points, (raw): ForecastPoint => { const point = record(raw); const phase = text(point.phase); if (!(phases as readonly string[]).includes(phase)) throw new Error('预测阶段不完整'); const result = { id: text(point.id), date: templateDate(point.date), phase: phase as ForecastPoint['phase'], at: instant(point.at), cash_cents: cents(point.cash_cents), protected_cents: templateMoney(point.protected_cents), margin_cents: cents(point.margin_cents), future_income_cents: templateMoney(point.future_income_cents), forecast_cash_cents: cents(point.forecast_cash_cents) }; if (BigInt(result.cash_cents) - BigInt(result.protected_cents) !== BigInt(result.margin_cents) || BigInt(result.cash_cents) + BigInt(result.future_income_cents) !== BigInt(result.forecast_cash_cents)) throw new Error('预测现金与保护金额无法对应'); return result; }, 1098);
  if (points.length !== (horizon + 1) * 3 || points.some((point, index) => point.phase !== phases[index % 3] || point.id !== `${point.date}:${point.phase}` || (index % 3 && point.date !== points[index - 1]?.date) || (index >= 3 && Date.parse(point.date) - Date.parse(points[index - 3]!.date) !== 86400000))) throw new Error('年度预测日期或阶段范围不完整');
  const state = text(item.state); if (state !== 'READY' && state !== 'PROTECTION_DEFICIT') throw new Error('预测状态不符');
  const safe = templateMoney(item.current_safe_cents), deficit = templateMoney(item.maximum_deficit_cents); const minimum = points.reduce((result, point) => BigInt(point.margin_cents) < result ? BigInt(point.margin_cents) : result, BigInt(points[0]!.margin_cents));
  if (minimum < 0n ? state !== 'PROTECTION_DEFICIT' || safe !== '0' || BigInt(deficit) !== -minimum : state !== 'READY' || deficit !== '0' || BigInt(safe) !== minimum) throw new Error('今日保护额度与完整年度曲线不符');
  const obligations = list(item.obligations, (raw) => { const row = record(raw); return { id: text(row.id), policy_id: templateID(row.policy_id), version_id: templateID(row.version_id), due_date: templateDate(row.due_date), amount_cents: templateMoney(row.amount_cents), verified: verified(row.verified), source_hash: hash(row.source_hash) }; });
  const external = item.external_liabilities == null ? undefined : list(item.external_liabilities, (raw) => { const row = record(raw), id = text(row.id); if (id !== `bill:${templateID(id.slice(5))}` || !id.startsWith('bill:')) throw new Error('外部账单缺少真实原身份'); return { id, due_date: templateDate(row.due_date), remaining_cents: templateMoney(row.remaining_cents), verified: verified(row.verified), source_hash: hash(row.source_hash) }; });
  const debtIDs = [...obligations, ...(external ?? [])].map((debt) => debt.id); if (new Set(debtIDs).size !== debtIDs.length || debtIDs.length > 10000) throw new Error('预测账单重复或超出完整范围');
  return { algorithm: item.algorithm, input_hash: hash(item.input_hash), as_of: instant(item.as_of), timezone: timezone(item.timezone), horizon_days: horizon, state, current_safe_cents: safe, maximum_deficit_cents: deficit, points, obligations, external_liabilities: external, reasons: strings(item.reasons), bank_authority: noBankAuthority(item.bank_authority) };
}
export function parseForecastView(value: unknown): ForecastView { const item = record(value); if (item.protocol !== 'template-forecast-view-v1') throw new Error('当前预测协议不符'); return { protocol: item.protocol, source_hash: hash(item.source_hash), inventory_hash: hash(item.inventory_hash), funds_version: integer(item.funds_version, 1), source_version: integer(item.source_version, 1), result: parseForecastResult(item.result), protection_source_version: item.protection_source_version == null ? undefined : integer(item.protection_source_version, 1), bank_authority: noBankAuthority(item.bank_authority) }; }
function parseFacts(value: unknown, owner: TemplateScope): TemplateFacts {
  const item = record(value); financialTree(item); verified(item.complete); verified(item.current_safe_verified); sameScope(owner, item.scope); hash(item.source_hash);
  return { current_cash_cents: templateMoney(item.current_cash_cents), current_safe_cents: templateMoney(item.current_safe_cents), already_reallocated_cents: templateMoney(item.already_reallocated_cents), managed_amount_cents: templateMoney(item.managed_amount_cents), standing_authorization_covered: flag(item.standing_authorization_covered), income_complete: flag(item.income_complete),
    history: list(item.history, (raw) => { const row = record(raw); return { date: templateDate(row.date), amount_cents: templateMoney(row.amount_cents), category: text(row.category), category_confirmed: flag(row.category_confirmed), one_off: flag(row.one_off), direction: text(row.direction), economic_role: text(row.economic_role) }; }, 100000),
    coverage: list(item.coverage, (raw) => { const row = record(raw); return { account_id: templateID(row.account_id), dates: list(row.dates, templateDate, 3650) }; }, 100),
    settlements: list(item.settlements, (raw) => { const row = record(raw); verified(row.verified); return { policy_id: templateID(row.policy_id), period: text(row.period), paid_cents: templateMoney(row.paid_cents), final_total_cents: row.final_total_cents == null ? undefined : templateMoney(row.final_total_cents) }; }),
    accounts: list(item.accounts, (raw) => { const row = record(raw); verified(row.verified); return { id: templateID(row.id), balance_cents: templateMoney(row.balance_cents) }; }, 100),
    goals: list(item.goals, (raw) => { const row = record(raw); verified(row.verified); return { id: templateID(row.id), account_id: templateID(row.account_id), owned_cents: templateMoney(row.owned_cents), cash_owned_cents: templateMoney(row.cash_owned_cents), month_contributed_cents: templateMoney(row.month_contributed_cents), month_period: text(row.month_period) }; }, 100),
    income: list(item.income, (raw) => { const row = record(raw); verified(row.verified); return { id: templateID(row.id), available_cents: templateMoney(row.available_cents), occurred_at: instant(row.occurred_at) }; }, 100000),
    bills: list(item.bills, (raw) => { const row = record(raw); verified(row.verified); return { id: templateID(row.id), account_id: templateID(row.account_id), due_date: templateDate(row.due_date), total_cents: templateMoney(row.total_cents), paid_cents: templateMoney(row.paid_cents) }; }),
    products: list(item.products, (raw) => { const row = record(raw); return { id: templateID(row.id), asset_class: text(row.asset_class), minimum_cents: templateMoney(row.minimum_cents), lock_days: integer(row.lock_days, 0, 3660), redemption_delay_days: integer(row.redemption_delay_days, 0, 3660), risk_level: integer(row.risk_level, 0, 5) }; }, 100),
    positions: list(item.positions, (raw) => { const row = record(raw); return { id: templateID(row.id), principal_cents: templateMoney(row.principal_cents), recoverable_cents: templateMoney(row.recoverable_cents), fee_cents: templateMoney(row.fee_cents), loss_cents: templateMoney(row.loss_cents), state: text(row.state) }; }),
  };
}
export function parseTemplatePreview(value: unknown, client?: Client): TemplatePreview {
  const item = record(value); financialTree(item); if (item.mode !== 'IF_USER_CONFIRMED' || item.authority !== 'POLICY_CONFIGURATION_ONLY_NO_BANK_GRANT') throw new Error('预览不属于本次规则配置确认');
  const owner = scope(item.scope, client), configuration = compiled(item.compiled), impact = record(item.preview), target = item.target_mutation == null ? undefined : record(item.target_mutation);
  if (target && (configuration.configuration.type !== 'long_term_goal' || target.goal_id !== item.goal_id || target.after_cents !== configuration.configuration.target_cents)) throw new Error('目标金额变更与本次长期规则不一致');
  if (impact.protocol !== 'policy-templates-go-v1' || impact.template_name !== configuration.template_name || impact.configuration_hash !== configuration.configuration_hash || impact.future_income_included_cents !== '0') throw new Error('规则预览与原配置或今日资金范围不一致');
  hash(item.facts_hash); hash(item.source_hash); hash(impact.input_hash);
  return { id: templateID(item.id), policy_id: templateID(item.policy_id), version_id: templateID(item.version_id), base_version: integer(item.base_version), mode: item.mode, scope: owner, goal_id: optionalID(item.goal_id), target_mutation: target ? { goal_id: templateID(target.goal_id), before_cents: templateMoney(target.before_cents), after_cents: templateMoney(target.after_cents) } : undefined, timezone: timezone(item.timezone), compiled: configuration, facts: parseFacts(item.facts, owner), forecast: parseForecastResult(item.forecast), reviewed_bundle_hash: hash(item.reviewed_bundle_hash), created_at: instant(item.created_at), expires_at: instant(item.expires_at), authority: item.authority, bank_authority: noBankAuthority(item.bank_authority), preview: { state: text(impact.state), amount_cents: templateMoney(impact.amount_cents), minimum_shortfall_cents: templateMoney(impact.minimum_shortfall_cents), guarantee_shortfall_cents: templateMoney(impact.guarantee_shortfall_cents), bank_authority: noBankAuthority(impact.bank_authority), questions: strings(impact.questions), reasons: strings(impact.reasons),
      commitments: list(impact.commitments, (raw) => { const row = record(raw); return { id: text(row.id), kind: text(row.kind), due_date: templateDate(row.due_date), end_date: row.end_date == null ? undefined : templateDate(row.end_date), amount_cents: templateMoney(row.amount_cents), account_id: optionalID(row.account_id), payee_id: row.payee_id == null ? undefined : text(row.payee_id) }; }),
      candidates: list(impact.candidates, (raw) => { const row = record(raw); return { kind: text(row.kind), subject_id: templateID(row.subject_id), target_id: optionalID(row.target_id), amount_cap_cents: templateMoney(row.amount_cap_cents), requires_confirmation: flag(row.requires_confirmation), reasons: strings(row.reasons) }; }),
      goal_demands: list(impact.goal_demands, (raw) => { const row = record(raw); return { goal_id: templateID(row.goal_id), minimum_cents: templateMoney(row.minimum_cents), desired_cents: templateMoney(row.desired_cents), maximum_cents: templateMoney(row.maximum_cents), importance: integer(row.importance, 0, 100), earliest_income_at: instant(row.earliest_income_at) }; }, 8),
    } };
}

export class TemplateClient {
  constructor(readonly client: Client) {}
  async list(signal?: AbortSignal): Promise<TemplatePolicyView[]> { return list(await this.client.request('/template-policies', { signal }), (raw) => parseTemplateView(raw, this.client), 100); }
  async policy(id: string): Promise<TemplatePolicyView> { return parseTemplateView(await this.client.request(`/template-policies/${encodeURIComponent(templateID(id))}`), this.client); }
  async version(id: string): Promise<TemplatePolicyView> { return parseTemplateView(await this.client.request(`/template-policy-versions/${encodeURIComponent(templateID(id))}`), this.client); }
  async preview(request: TemplateRequest): Promise<TemplatePreview> { parseConfiguration(request.template_name, request.configuration); return parseTemplatePreview(await this.client.request('/template-policy-previews', { method: 'POST', body: JSON.stringify(request) }), this.client); }
  async confirm(original: PendingTemplateConfirmation): Promise<TemplatePolicyView> { const value = parseTemplateView(await this.client.request('/template-policies', { method: 'POST', headers: { 'Idempotency-Key': original.key }, body: JSON.stringify(original.request) }), this.client); if (value.version.policy_id !== original.policy_id || value.version.version_id !== original.version_id || value.reviewed_bundle_hash !== original.request.reviewed_bundle_hash) throw new Error('确认结果与原规则不一致，请保留原请求'); return value; }
  async state(policy: TemplatePolicyView, action: 'PAUSE' | 'RESUME' | 'REVOKE'): Promise<TemplatePolicyView> { return parseTemplateView(await this.client.request(`/template-policies/${encodeURIComponent(policy.version.policy_id)}/state`, { method: 'POST', body: JSON.stringify({ expected_version_id: policy.version.version_id, action }) }), this.client); }
  async forecast(days: number, zone: 'UTC' | 'Asia/Shanghai', signal?: AbortSignal): Promise<ForecastView> { integer(days, 1, 365); const value = parseForecastView(await this.client.request(`/forecast?horizon_days=${days}&timezone=${encodeURIComponent(zone)}`, { signal })); if (value.result.horizon_days !== days || value.result.timezone !== zone) throw new Error('服务端预测范围与本次选择不一致'); return value; }
  async refresh(): Promise<ForecastView> { return parseForecastView(await this.client.request('/forecast/refresh-protection', { method: 'POST', body: '{}' })); }
}
function storageKey(client: Client): string { if (!client.session) throw new Error('请先登录当前模拟环境'); return `zhiyu-go:template-confirmation:v1:${client.session.environment}:${client.session.user_id}`; }
export function saveTemplateConfirmation(client: Client, original: PendingTemplateConfirmation): void { const session=client.session; if (!session || original.environment !== session.environment || original.user_id !== session.user_id) throw new Error('原规则确认不属于当前会话'); localStorage.setItem(storageKey(client), JSON.stringify(original)); }
export function loadTemplateConfirmation(client: Client): PendingTemplateConfirmation | null {
	const session=client.session;if(!session)throw new Error('请先登录当前模拟环境');
  const raw = localStorage.getItem(storageKey(client)); if (!raw) return null;
  try { const item = record(JSON.parse(raw)), request = record(item.request); if (Object.keys(item).sort().join(',') !== 'environment,key,policy_id,request,user_id,version_id' || Object.keys(request).sort().join(',') !== 'preview_id,reviewed_bundle_hash' || item.environment !== session.environment || item.user_id !== session.user_id) throw new Error(); return { environment: text(item.environment), user_id: templateID(item.user_id), key: templateID(item.key), policy_id: templateID(item.policy_id), version_id: templateID(item.version_id), request: { preview_id: templateID(request.preview_id), reviewed_bundle_hash: hash(request.reviewed_bundle_hash) } }; } catch { throw new Error('原规则确认记录不完整，已保留；请先核对原记录'); }
}
export function clearTemplateConfirmation(client: Client): void { localStorage.removeItem(storageKey(client)); }
