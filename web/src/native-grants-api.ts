import { Client } from './api';
import { templateID, parseTemplateName } from './template-schema';
import type { TemplateName } from './template-schema';
import type { TemplateScope } from './template-api';
import { nfObject as object, nfClosed as closed, nfHash as hash, nfTime as instant, nfInt as integer, nfList as list, nfMoney as amount, nfOwner as owner, nfSameOwner as sameOwner, nfText as text, nfUnique as unique } from './native-financial-api';

export const nativeFamilies = ['SINGLE_GOAL', 'JOINT_GOALS', 'ASSET_ALLOCATION', 'MATURITY_REINVEST', 'PERIODIC_PAYMENT'] as const;
export type NativeFamily = typeof nativeFamilies[number];
export const nativeFamilyLabels: Record<NativeFamily, string> = { SINGLE_GOAL: '单目标新增收入安排', JOINT_GOALS: '多目标联合新增收入安排', ASSET_ALLOCATION: '新增收入资产安排', MATURITY_REINVEST: '已到账期满返本再投', PERIODIC_PAYMENT: '周期账单与转账支付' };
export interface NativeCapability { family: NativeFamily; qualified: boolean; reason: string }
export interface NativeGrantRequest { family: NativeFamily; policy_version_ids: string[]; account_ids: string[]; product_ids: string[]; single_cap_cents: string; lifetime_cap_cents: string; monthly_cap_cents: string; timezone: 'UTC' | 'Asia/Shanghai'; expires_at: string }
export interface NativePolicyRef { policy_id: string; version_id: string; version: number; template_name: TemplateName; configuration_hash: string; goal_id?: string }
export interface NativeGrantTerms { protocol: 'native-standing-authority-v1'; scope: TemplateScope; family: NativeFamily; policies: NativePolicyRef[]; account_ids: string[]; goal_ids: string[]; product_ids: string[]; payee_ids: string[]; single_cap_cents: string; lifetime_cap_cents: string; monthly_cap_cents: string; timezone: 'UTC' | 'Asia/Shanghai'; effective_from: string; expires_at: string }
export interface NativeGrantPreview { protocol: 'native-automation-v1/grant-preview'; id: string; scope: TemplateScope; session_id: string; terms: NativeGrantTerms; reviewed_hash: string; created_at: string; expires_at: string }
export interface NativeGrantView { grant: { protocol: 'native-automation-v1/grant'; id: string; version: number; scope: TemplateScope; terms: NativeGrantTerms; reviewed_terms: NativeGrantTerms; preview_id: string; session_id: string; reviewed_hash: string; principal_kind: 'USER'; confirmed_at: string }; state: 'ACTIVE' | 'PAUSED' | 'REVOKED'; state_sequence: number; state_at: string }
export interface NativeGrantCommand { preview_id: string; reviewed_hash: string }
export interface NativeGrantLocator { environment: string; user_id: string; key: string; command: NativeGrantCommand; preview: NativeGrantPreview }
export type NativeGrantAction = 'PAUSE' | 'RESUME' | 'REVOKE';
export interface NativeGrantStateIntent { environment: string; user_id: string; grant_id: string; expected_sequence: number; action: NativeGrantAction }

function family(raw: unknown): NativeFamily { if (!nativeFamilies.includes(raw as NativeFamily)) throw new Error('持续授权执行族尚未识别'); return raw as NativeFamily; }
function ids(raw: unknown, max: number, parse = templateID): string[] { const values = list(raw == null ? [] : raw, parse, max); unique(values); return values; }
function sorted(values: string[]) { if (values.some((v, i) => i > 0 && v <= values[i - 1]!)) throw new Error('持续授权原范围未稳定排序'); }
function zone(raw: unknown): 'UTC' | 'Asia/Shanghai' { if (raw !== 'UTC' && raw !== 'Asia/Shanghai') throw new Error('持续授权预算时区不受支持'); return raw; }
function caps(v: Record<string, unknown>) { const single = amount(v.single_cap_cents, true), life = amount(v.lifetime_cap_cents, true), month = amount(v.monthly_cap_cents, true); if (BigInt(single) > BigInt(month) || BigInt(month) > BigInt(life)) throw new Error('须满足单次上限 ≤ 每月上限 ≤ 累计上限'); return { single_cap_cents: single, lifetime_cap_cents: life, monthly_cap_cents: month }; }
export function parseNativeCapabilities(raw: unknown): NativeCapability[] {
  const values = list(raw, (row): NativeCapability => { const v = object(row); closed(v, ['family', 'qualified', 'reason']); if (typeof v.qualified !== 'boolean') throw new Error('持续执行资格尚待核实'); return { family: family(v.family), qualified: v.qualified, reason: text(v.reason) }; }, 5);
  unique(values.map((v) => v.family)); if (values.length !== nativeFamilies.length) throw new Error('持续执行资格列表不完整'); return values;
}
export function parseNativeGrantTerms(raw: unknown, client?: Client): NativeGrantTerms {
  const v = object(raw); closed(v, ['protocol', 'scope', 'family', 'policies', 'account_ids', 'goal_ids', 'product_ids', 'payee_ids', 'single_cap_cents', 'lifetime_cap_cents', 'monthly_cap_cents', 'timezone', 'effective_from', 'expires_at']);
  if (v.protocol !== 'native-standing-authority-v1') throw new Error('持续授权原协议不符');
  const scope = owner(v.scope, client), f = family(v.family), policies = list(v.policies, (row): NativePolicyRef => { const r = object(row); closed(r, ['policy_id', 'version_id', 'version', 'template_name', 'configuration_hash', 'goal_id']); return { policy_id: templateID(r.policy_id), version_id: templateID(r.version_id), version: integer(r.version, 1), template_name: parseTemplateName(r.template_name), configuration_hash: hash(r.configuration_hash), ...(r.goal_id == null ? {} : { goal_id: templateID(r.goal_id) }) }; }, 16);
  const accounts = ids(v.account_ids, 16), goals = ids(v.goal_ids, 8), products = ids(v.product_ids, 100, (v) => text(v, 128)), payees = ids(v.payee_ids, 1, (v) => text(v, 128));
  [policies.map((p) => p.policy_id), accounts, goals, products, payees].forEach(sorted); unique(policies.map((p) => p.version_id));
  const from = instant(v.effective_from), until = instant(v.expires_at), timespan = Date.parse(until) - Date.parse(from);
  if (!policies.length || !accounts.length || timespan <= 0 || timespan > 366 * 86400000 || policies.some((p) => p.goal_id && !goals.includes(p.goal_id))) throw new Error('持续授权期限或原规则范围不完整');
  if (f === 'SINGLE_GOAL' || f === 'JOINT_GOALS') {
    if (products.length || payees.length || policies.some((p) => p.template_name !== 'LongTermGoalPolicy' || !p.goal_id) || goals.length !== policies.length || new Set(policies.map((p) => p.goal_id)).size !== goals.length || (f === 'SINGLE_GOAL' ? goals.length !== 1 : goals.length < 2)) throw new Error('目标持续授权范围不一致');
  } else if (f === 'PERIODIC_PAYMENT') {
    if (policies.length !== 1 || accounts.length !== 1 || payees.length !== 1 || products.length || goals.length || !['RecurringObligationPolicy', 'PeriodicTransferPolicy'].includes(policies[0]!.template_name)) throw new Error('支付持续授权范围不一致');
  } else if (!products.length || payees.length || !policies.some((p) => p.template_name === 'AssetAuthorizationPolicy') || policies.some((p) => p.template_name !== 'AssetAuthorizationPolicy' && !(f === 'MATURITY_REINVEST' && p.template_name === 'RecoveryPolicy'))) throw new Error('资产持续授权范围不一致');
  return { protocol: v.protocol, scope, family: f, policies, account_ids: accounts, goal_ids: goals, product_ids: products, payee_ids: payees, ...caps(v), timezone: zone(v.timezone), effective_from: from, expires_at: until };
}
export function parseNativeGrantPreview(raw: unknown, client?: Client): NativeGrantPreview {
  const v = object(raw); closed(v, ['protocol', 'id', 'scope', 'session_id', 'terms', 'reviewed_hash', 'created_at', 'expires_at']);
  if (v.protocol !== 'native-automation-v1/grant-preview') throw new Error('持续授权审阅协议不符');
  const scope = owner(v.scope, client), terms = parseNativeGrantTerms(v.terms, client); sameOwner(scope, terms.scope);
  const created = instant(v.created_at), expires = instant(v.expires_at);
  if (Date.parse(created) < Date.parse(terms.effective_from) || Date.parse(expires) <= Date.parse(created) || Date.parse(expires) - Date.parse(created) > 121000) throw new Error('持续授权原审阅期限不符');
  return { protocol: v.protocol, id: templateID(v.id), scope, session_id: templateID(v.session_id), terms, reviewed_hash: hash(v.reviewed_hash), created_at: created, expires_at: expires };
}
export function parseNativeGrantView(raw: unknown, client?: Client, original?: NativeGrantLocator): NativeGrantView {
  const v = object(raw), g = object(v.grant); closed(v, ['grant', 'state', 'state_sequence', 'state_at']); closed(g, ['protocol', 'id', 'version', 'scope', 'terms', 'reviewed_terms', 'preview_id', 'session_id', 'reviewed_hash', 'principal_kind', 'confirmed_at']);
  if (g.protocol !== 'native-automation-v1/grant' || g.principal_kind !== 'USER' || !['ACTIVE', 'PAUSED', 'REVOKED'].includes(String(v.state))) throw new Error('持续授权缺少原本人确认或当前状态');
  const scope = owner(g.scope, client), terms = parseNativeGrantTerms(g.terms, client), reviewed = parseNativeGrantTerms(g.reviewed_terms, client), confirmed = instant(g.confirmed_at);
  sameOwner(scope, terms.scope); sameOwner(scope, reviewed.scope);
  if (terms.effective_from !== confirmed || Date.parse(confirmed) < Date.parse(reviewed.effective_from) || JSON.stringify({ ...terms, effective_from: reviewed.effective_from }) !== JSON.stringify(reviewed)) throw new Error('持续授权生效范围超出原审阅');
  const result: NativeGrantView = { grant: { protocol: g.protocol, id: templateID(g.id), version: integer(g.version, 1), scope, terms, reviewed_terms: reviewed, preview_id: templateID(g.preview_id), session_id: templateID(g.session_id), reviewed_hash: hash(g.reviewed_hash), principal_kind: 'USER', confirmed_at: confirmed }, state: v.state as NativeGrantView['state'], state_sequence: integer(v.state_sequence, 1), state_at: instant(v.state_at) };
  if (original && (result.grant.preview_id !== original.command.preview_id || result.grant.reviewed_hash !== original.command.reviewed_hash || result.grant.session_id !== original.preview.session_id || JSON.stringify(reviewed) !== JSON.stringify(original.preview.terms))) throw new Error('原键持续授权与本人原审阅不一致');
  return result;
}
export function validateNativeGrantRequest(raw: NativeGrantRequest): NativeGrantRequest {
  const v = object(raw); closed(v, ['family', 'policy_version_ids', 'account_ids', 'product_ids', 'single_cap_cents', 'lifetime_cap_cents', 'monthly_cap_cents', 'timezone', 'expires_at']);
  const result = { family: family(v.family), policy_version_ids: ids(v.policy_version_ids, 16), account_ids: ids(v.account_ids, 16), product_ids: ids(v.product_ids, 100, (v) => text(v, 128)), ...caps(v), timezone: zone(v.timezone), expires_at: instant(v.expires_at) };
  if (!result.policy_version_ids.length || !result.account_ids.length) throw new Error('请明确选择本人原规则与实际账户'); return result;
}
export class NativeGrantsClient {
  constructor(readonly client: Client) {}
  async capabilities(signal?: AbortSignal) { return parseNativeCapabilities(await this.client.request('/automation-capabilities', { signal })); }
  async list(signal?: AbortSignal) { const values = list(await this.client.request('/standing-grants', { signal }), (v) => parseNativeGrantView(v, this.client), 100); unique(values.map((v) => v.grant.id)); return values; }
  async preview(request: NativeGrantRequest) { const input = validateNativeGrantRequest(request), result = parseNativeGrantPreview(await this.client.request('/standing-grant-previews', { method: 'POST', body: JSON.stringify(input) }), this.client), t = result.terms; if (t.family !== input.family || t.single_cap_cents !== input.single_cap_cents || t.monthly_cap_cents !== input.monthly_cap_cents || t.lifetime_cap_cents !== input.lifetime_cap_cents || t.timezone !== input.timezone || Date.parse(t.expires_at) !== Date.parse(input.expires_at) || [t.policies.map((p) => p.version_id), t.account_ids, t.product_ids].some((values, i) => JSON.stringify([...values].sort()) !== JSON.stringify([input.policy_version_ids, input.account_ids, input.product_ids][i]!.slice().sort()))) throw new Error('服务端持续授权范围与本人输入不一致'); return result; }
  async original(record: NativeGrantLocator, signal?: AbortSignal) { const result = parseNativeGrantPreview(await this.client.request(`/standing-grant-previews/${record.command.preview_id}`, { signal }), this.client); if (JSON.stringify(result) !== JSON.stringify(record.preview)) throw new Error('独立原审阅与本机持续授权资料不一致'); return result; }
  async confirm(record: NativeGrantLocator) { const value = validateNativeGrantLocator(this.client, record); return parseNativeGrantView(await this.client.request('/standing-grants', { method: 'POST', headers: { 'Idempotency-Key': value.key }, body: JSON.stringify(value.command) }), this.client, value); }
  async byKey(record: NativeGrantLocator, signal?: AbortSignal) { return parseNativeGrantView(await this.client.request(`/standing-grant-commands/${encodeURIComponent(record.key)}`, { signal }), this.client, record); }
  async grant(id: string, signal?: AbortSignal) { return parseNativeGrantView(await this.client.request(`/standing-grants/${templateID(id)}`, { signal }), this.client); }
  async state(intent: NativeGrantStateIntent) { validateScope(this.client, intent); const result = parseNativeGrantView(await this.client.request(`/standing-grants/${templateID(intent.grant_id)}/state`, { method: 'POST', body: JSON.stringify({ expected_sequence: integer(intent.expected_sequence, 1), action: action(intent.action) }) }), this.client); if (result.grant.id !== intent.grant_id) throw new Error('状态结果不是这一份原授权'); return result; }
}
function action(value: unknown): NativeGrantAction { if (!['PAUSE', 'RESUME', 'REVOKE'].includes(String(value))) throw new Error('持续授权状态动作不完整'); return value as NativeGrantAction; }
function validateScope(client: Client, value: { environment: string; user_id: string }) { if (!client.session || value.environment !== client.session.environment || value.user_id !== client.session.user_id) throw new Error('持续授权恢复资料不属于当前本人环境'); }
function storage(client: Client, suffix = 'confirmations') { if (!client.session) throw new Error('请先登录当前本人会话'); return `zhiyu:v2:native-grants:${suffix}:${client.session.environment}:${client.session.user_id}`; }
export function validateNativeGrantLocator(client: Client, raw: unknown): NativeGrantLocator {
  const v = object(raw); closed(v, ['environment', 'user_id', 'key', 'command', 'preview']); const environment = text(v.environment), user = templateID(v.user_id); validateScope(client, { environment, user_id: user }); const command = object(v.command); closed(command, ['preview_id', 'reviewed_hash']); const preview = parseNativeGrantPreview(v.preview, client), key = templateID(v.key);
  if (templateID(command.preview_id) !== preview.id || hash(command.reviewed_hash) !== preview.reviewed_hash) throw new Error('原持续授权命令与完整审阅不一致');
  return { environment, user_id: user, key, command: { preview_id: preview.id, reviewed_hash: preview.reviewed_hash }, preview };
}
export function loadNativeGrantLocators(client: Client): NativeGrantLocator[] { const raw = localStorage.getItem(storage(client)); if (!raw) return []; const values = list(JSON.parse(raw), (v) => validateNativeGrantLocator(client, v), 100); unique(values.map((v) => v.key)); return values; }
export function saveNativeGrantLocator(client: Client, record: NativeGrantLocator) { const value = validateNativeGrantLocator(client, record), records = loadNativeGrantLocators(client), old = records.find((r) => r.key === value.key); if (old && JSON.stringify(old) !== JSON.stringify(value)) throw new Error('不能更换原键的完整持续授权命令'); const result = old ? records : [...records, value]; if (result.length > 100) throw new Error('本机持续授权恢复记录已满，请保留原件后处理'); const raw = JSON.stringify(result); localStorage.setItem(storage(client), raw); if (localStorage.getItem(storage(client)) !== raw) throw new Error('本机持续授权原键未可靠保存，已停止提交'); return result; }
export function loadNativeGrantStateIntent(client: Client): NativeGrantStateIntent | null { const raw = localStorage.getItem(storage(client, 'state')); if (!raw) return null; const v = object(JSON.parse(raw)); closed(v, ['environment', 'user_id', 'grant_id', 'expected_sequence', 'action']); const result = { environment: text(v.environment), user_id: templateID(v.user_id), grant_id: templateID(v.grant_id), expected_sequence: integer(v.expected_sequence, 1), action: action(v.action) }; validateScope(client, result); return result; }
export function saveNativeGrantStateIntent(client: Client, value: NativeGrantStateIntent) { validateScope(client, value); const raw = JSON.stringify(value); localStorage.setItem(storage(client, 'state'), raw); if (localStorage.getItem(storage(client, 'state')) !== raw) throw new Error('本机原状态请求未可靠保存，已停止提交'); }
export function clearNativeGrantStateIntent(client: Client) { localStorage.removeItem(storage(client, 'state')); }
