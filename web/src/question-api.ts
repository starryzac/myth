import type { Client } from './api';
import { templateID, templateMoney } from './template-schema';

export type PreferenceDomain = { goal_id: string; priorities: number[]; contribution_targets_cents?: never } | { goal_id: string; contribution_targets_cents: string[]; priorities?: never };
export interface PreferenceRequest { plan_id: string; mode: 'DECLARED_FINITE_CHOICES'; domains: PreferenceDomain[] }
export type PreferenceChoice = { key: string; label: string; kind: 'money'; amount_cents: string } | { key: string; label: string; kind: 'priority'; priority: number };
export interface PreferenceRef { scope: { environment_id: string; user_id: string }; kind: string; id: string; version: number; hash: string; current: true; verified: true }
export interface PreferenceQuestion { original_question_id: string; variable_id: string; scope: PreferenceRef['scope']; prompt: string; source_ref: PreferenceRef; choices: PreferenceChoice[]; asked_at: string; expires_at: string; dependency_hash: string; object_refs_hash: string; domain_hash: string; question_hash: string; worst_residual_signatures: number; bank_authority: false }
export interface PreferenceAnalysis { id: string; request: PreferenceRequest; plan_hash: string; scope: PreferenceRef['scope']; as_of: string; expires_at: string; dependency_hash: string; result: { status: 'STABLE' | 'QUESTION_REQUIRED' | 'UNKNOWN' | 'CAPACITY_EXCEEDED' | 'ALL_WORLDS_BLOCKED' | 'DIVERGENT_NO_SINGLE_DISCRIMINATING_QUESTION'; complete_within_domain: boolean; expected_worlds: number; evaluated_worlds: number; stable: boolean | null; should_ask: boolean | null; signatures: string[]; question?: PreferenceQuestion; bank_authority: false }; bank_authority: false }
export interface PreferenceAnswerRequest { analysis_id: string; original_question_id: string; question_hash: string; choice_key: string }
export interface PreferenceAnswer { id: string; analysis_id: string; request: PreferenceAnswerRequest; answered_at: string; preference: { answer_id: string; original_question_id: string; variable_id: string; choice: PreferenceChoice; source_ref: PreferenceRef; dependency_hash: string; reevaluate_required: true; bank_authority: false }; bank_authority: false }
export type PreferenceLocator = { environment: string; user_id: string; key: string } & ({ stage: 'ANALYSIS'; request: PreferenceRequest; analysis_id?: string } | { stage: 'ANSWER'; request: PreferenceAnswerRequest });

function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('必要追问原件结构不完整'); return value as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('必要追问字段类型不符'); return value; }
function integer(value: unknown, min: number, max: number): number { if (!Number.isSafeInteger(value) || (value as number) < min || (value as number) > max) throw new Error('必要追问数值范围不完整'); return value as number; }
function flag(value: unknown): boolean { if (typeof value !== 'boolean') throw new Error('必要追问状态不完整'); return value; }
function truth(value: unknown): true { if (value !== true) throw new Error('必要追问原件尚未核实'); return true; }
function noAuthority(value: unknown): false { if (value !== false) throw new Error('偏好回答不能生成规则或金融权限'); return false; }
function hash(value: unknown): string { const result = text(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error('原问题引用不完整'); return result; }
function instant(value: unknown): string { const result = text(value); if (!Number.isFinite(Date.parse(result))) throw new Error('原问题时间不完整'); return result; }
function array<T>(value: unknown, parse: (raw: unknown) => T, min: number, max: number): T[] { if (!Array.isArray(value) || value.length < min || value.length > max) throw new Error('声明的有限比较范围不完整'); return value.map(parse); }
function unique(values: (string | number)[]): void { if (new Set(values).size !== values.length) throw new Error('有限选项或原件不能重复'); }
function closed(value: Record<string, unknown>, keys: string[]): void { if (Object.keys(value).some((key) => !keys.includes(key))) throw new Error('必要追问请求包含范围外字段'); }
function scope(value: unknown, client: Client): PreferenceRef['scope'] { const raw = object(value), result = { environment_id: templateID(raw.environment_id), user_id: templateID(raw.user_id) }; if (!client.session || result.user_id !== client.session.user_id) throw new Error('必要追问不属于当前本人会话'); return result; }
function sameScope(left: PreferenceRef['scope'], right: PreferenceRef['scope']): void { if (left.environment_id !== right.environment_id || left.user_id !== right.user_id) throw new Error('原问题及当前对象范围不一致'); }
function ref(value: unknown, client: Client): PreferenceRef { const raw = object(value); return { scope: scope(raw.scope, client), kind: text(raw.kind), id: templateID(raw.id), version: integer(raw.version, 1, Number.MAX_SAFE_INTEGER), hash: hash(raw.hash), current: truth(raw.current), verified: truth(raw.verified) }; }
function key(value: unknown): string { const result = text(value); if (!/^[A-Za-z0-9_.-]{1,80}$/.test(result)) throw new Error('原问题选项不受支持'); return result; }
function choice(value: unknown): PreferenceChoice {
  const raw = object(value), base = { key: key(raw.key), label: text(raw.label) };
  if (raw.kind === 'money') { closed(raw, ['key', 'label', 'kind', 'amount_cents']); const amount = templateMoney(raw.amount_cents); if (base.key !== `target-${amount}`) throw new Error('原金额选项与选择键不一致'); return { ...base, kind: 'money', amount_cents: amount }; }
  if (raw.kind === 'priority') { closed(raw, ['key', 'label', 'kind', 'priority']); const priority = integer(raw.priority, 0, 100); if (base.key !== `priority-${priority}`) throw new Error('原优先级选项与选择键不一致'); return { ...base, kind: 'priority', priority }; }
  throw new Error('当前入口仅支持声明的金额或优先级选项');
}
export function parsePreferenceRequest(value: unknown): PreferenceRequest {
  const raw = object(value); closed(raw, ['plan_id', 'mode', 'domains']); if (raw.mode !== 'DECLARED_FINITE_CHOICES') throw new Error('须明确声明本次有限比较范围');
  const domains = array(raw.domains, (value): PreferenceDomain => {
    const domain = object(value); closed(domain, ['goal_id', 'priorities', 'contribution_targets_cents']); const id = templateID(domain.goal_id);
    if ((domain.priorities == null) === (domain.contribution_targets_cents == null)) throw new Error('一项目标的金额和优先级选项不能混用');
    if (domain.priorities != null) { const values = array(domain.priorities, (value) => integer(value, 0, 100), 2, 8); unique(values); return { goal_id: id, priorities: values }; }
    const values = array(domain.contribution_targets_cents, templateMoney, 2, 8); unique(values); if (values.includes('0')) throw new Error('本次声明的期望金额须大于零'); return { goal_id: id, contribution_targets_cents: values };
  }, 1, 8);
  unique(domains.map((domain) => domain.goal_id));
  if (domains.reduce((product, domain) => product * (domain.priorities?.length ?? domain.contribution_targets_cents!.length), 1) > 16) throw new Error('本入口最多完整比较16个声明世界，请缩小本次范围');
  return { plan_id: templateID(raw.plan_id), mode: raw.mode, domains };
}
export function parsePreferenceAnswerRequest(value: unknown): PreferenceAnswerRequest { const raw = object(value); closed(raw, ['analysis_id', 'original_question_id', 'question_hash', 'choice_key']); return { analysis_id: templateID(raw.analysis_id), original_question_id: templateID(raw.original_question_id), question_hash: hash(raw.question_hash), choice_key: key(raw.choice_key) }; }

export function parsePreferenceAnalysis(value: unknown, client: Client): PreferenceAnalysis {
  const raw = object(value), input = object(raw.input), result = object(raw.result);
  if (raw.protocol !== 'native-priority-analysis-v1' || result.protocol !== 'finite-economic-question-go-v1' || raw.domain_scope !== 'ONLY_EXPLICIT_USER_DECLARED_AMOUNT_OR_PRIORITY_CHOICES; CONFIRMED_HARD_LIMITS_PRESERVED; COUNTERFACTUAL_NOT_POLICY_OR_FINANCIAL_CONFIRMATION') throw new Error('有限比较协议或范围声明不符');
  const owner = scope(input.Scope, client), request = parsePreferenceRequest(raw.request), refs = array(input.ObjectRefs, (raw) => ref(raw, client), 1, 512);
  refs.forEach((item) => sameScope(owner, item.scope));
  const complete = flag(result.complete_within_domain), expected = integer(result.expected_worlds, 1, 128), evaluated = integer(result.evaluated_worlds, 0, 128), signatures = array(result.signatures, hash, 0, 128);
  const status = text(result.status) as PreferenceAnalysis['result']['status'];
  if (!['STABLE', 'QUESTION_REQUIRED', 'UNKNOWN', 'CAPACITY_EXCEEDED', 'ALL_WORLDS_BLOCKED', 'DIVERGENT_NO_SINGLE_DISCRIMINATING_QUESTION'].includes(status)) throw new Error('有限比较结果状态不受支持');
  const stable = result.stable == null ? null : flag(result.stable), ask = result.should_ask == null ? null : flag(result.should_ask);
  const worlds = array(result.worlds, (value) => { const world = object(value); hash(world.id); const status = text(world.status); if (!['KNOWN', 'BLOCKED', 'UNKNOWN'].includes(status)) throw new Error('比较世界状态不完整'); return { status, signature: world.signature == null ? undefined : hash(world.signature) }; }, 0, 128);
  unique(signatures);
  if (evaluated !== worlds.length || complete && (expected !== evaluated || worlds.some((world) => world.status === 'UNKNOWN')) || evaluated > expected) throw new Error('比较范围尚未完整求解');
  if (status === 'STABLE' && (!complete || stable !== true || ask !== false || signatures.length !== 1 || result.question != null)) throw new Error('不能在不完整范围声称稳定');
  if (['ALL_WORLDS_BLOCKED', 'DIVERGENT_NO_SINGLE_DISCRIMINATING_QUESTION'].includes(status) && (!complete || stable !== false || ask !== false || result.question != null)) throw new Error('有限比较结果不能冒称稳定或额外问题');
  if ((status === 'UNKNOWN' || status === 'CAPACITY_EXCEEDED') && (stable != null || ask != null || result.question != null)) throw new Error('未知比较不能声明稳定或冒充原问题');
  let question: PreferenceQuestion | undefined;
  if (result.question != null) {
    const q = object(result.question), source = ref(q.source_ref, client), qScope = scope(q.scope, client), choices = array(q.choices, choice, 2, 8);
    sameScope(owner, qScope); sameScope(owner, source.scope); unique(choices.map((item) => item.key));
    if (q.protocol !== 'finite-economic-question-go-v1' || source.kind !== 'GOAL' || !request.domains.some((domain) => domain.goal_id === source.id) || !refs.some((item) => item.kind === source.kind && item.id === source.id && item.hash === source.hash && item.version === source.version)) throw new Error('原问题未绑定本次真实对象');
    const domain = request.domains.find((domain) => domain.goal_id === source.id)!;
    const declared = domain.priorities?.map((value) => `priority-${value}`) ?? domain.contribution_targets_cents!.map((value) => `target-${value}`);
    if (choices.length !== declared.length || choices.some((item) => !declared.includes(item.key) || (item.kind === 'priority' ? !domain.priorities?.includes(item.priority) || item.key !== `priority-${item.priority}` : !domain.contribution_targets_cents?.includes(item.amount_cents) || item.key !== `target-${item.amount_cents}`))) throw new Error('问题选项越过了本人声明的有限范围');
    question = { original_question_id: templateID(q.original_question_id), variable_id: text(q.variable_id), scope: qScope, prompt: text(q.prompt), source_ref: source, choices, asked_at: instant(q.asked_at), expires_at: instant(q.expires_at), dependency_hash: hash(q.dependency_hash), object_refs_hash: hash(q.object_refs_hash), domain_hash: hash(q.domain_hash), question_hash: hash(q.question_hash), worst_residual_signatures: integer(q.worst_residual_signatures, 1, 128), bank_authority: noAuthority(q.bank_authority) };
    if (question.dependency_hash !== input.DependencyHash || !complete || stable !== false || ask !== true || status !== 'QUESTION_REQUIRED' || question.worst_residual_signatures >= signatures.length) throw new Error('问题没有证明本次范围内的区分作用');
    if (question.expires_at !== input.ExpiresAt || question.asked_at !== input.Now) throw new Error('原问题有效期与分析时点不一致');
  }
  if (status === 'QUESTION_REQUIRED' && !question || status !== 'QUESTION_REQUIRED' && question) throw new Error('必要追问与结果状态不一致');
  if (complete && input.Complete !== true || status === 'STABLE' && result.question != null) throw new Error('当前事实完整性不足以声称稳定');
  hash(result.input_hash); noAuthority(result.bank_authority);
  return { id: templateID(raw.id), request, plan_hash: hash(raw.plan_hash), scope: owner, as_of: instant(input.Now), expires_at: instant(input.ExpiresAt), dependency_hash: hash(input.DependencyHash), result: { status, complete_within_domain: complete, expected_worlds: expected, evaluated_worlds: evaluated, stable, should_ask: ask, signatures, question, bank_authority: false }, bank_authority: noAuthority(raw.bank_authority) };
}
export function parsePreferenceAnswer(value: unknown, client: Client): PreferenceAnswer {
  const raw = object(value), preference = object(raw.preference), request = parsePreferenceAnswerRequest(raw.request), selected = choice(preference.choice), source = ref(preference.source_ref, client);
  if (preference.reevaluate_required !== true || preference.original_question_id !== request.original_question_id || selected.key !== request.choice_key || raw.analysis_id !== request.analysis_id || preference.answer_id !== raw.id) throw new Error('偏好回答未绑定原问题或需要重新评估');
  return { id: templateID(raw.id), analysis_id: templateID(raw.analysis_id), request, answered_at: instant(raw.answered_at), preference: { answer_id: templateID(preference.answer_id), original_question_id: templateID(preference.original_question_id), variable_id: text(preference.variable_id), choice: selected, source_ref: source, dependency_hash: hash(preference.dependency_hash), reevaluate_required: true, bank_authority: noAuthority(preference.bank_authority) }, bank_authority: noAuthority(raw.bank_authority) };
}
function storageKey(client: Client): string { if (!client.session) throw new Error('请先登录当前主体'); return `zhiyu-go:preference-originals:v1:${client.session.environment}:${client.session.user_id}`; }
function locator(value: unknown, client: Client): PreferenceLocator {
  const raw = object(value), session = client.session; closed(raw, ['environment', 'user_id', 'key', 'stage', 'request', 'analysis_id']);
  if (!session || raw.environment !== session.environment || raw.user_id !== session.user_id) throw new Error('原偏好请求不属于当前本人环境');
  const base = { environment: session.environment, user_id: templateID(raw.user_id), key: templateID(raw.key) };
  if (raw.stage === 'ANALYSIS') return { ...base, stage: raw.stage, request: parsePreferenceRequest(raw.request), analysis_id: raw.analysis_id == null ? undefined : templateID(raw.analysis_id) };
  if (raw.stage === 'ANSWER' && raw.analysis_id == null) return { ...base, stage: raw.stage, request: parsePreferenceAnswerRequest(raw.request) };
  throw new Error('原偏好请求阶段不完整');
}
export function loadPreferenceLocators(client: Client): PreferenceLocator[] { const raw = localStorage.getItem(storageKey(client)); if (!raw) return []; try { const result = array(JSON.parse(raw), (raw) => locator(raw, client), 0, 100); unique(result.map((item) => item.key)); return result; } catch { throw new Error('保存的必要追问原件不完整，已保留资料并停止新的提交'); } }
export function savePreferenceLocator(client: Client, value: PreferenceLocator): PreferenceLocator[] {
  const original = locator(value, client), saved = loadPreferenceLocators(client), index = saved.findIndex((item) => item.key === original.key);
  if (index >= 0) { const old = saved[index]!; if (old.stage !== original.stage || JSON.stringify(old.request) !== JSON.stringify(original.request) || old.stage === 'ANALYSIS' && old.analysis_id && (original.stage !== 'ANALYSIS' || old.analysis_id !== original.analysis_id)) throw new Error('原偏好键不能绑定其他问题、选项或比较范围'); saved[index] = original; }
  else { if (saved.length >= 100) throw new Error('本机原偏好记录已到完整容量，请先核对原件'); saved.push(original); }
  const encoded = JSON.stringify(saved); localStorage.setItem(storageKey(client), encoded); if (localStorage.getItem(storageKey(client)) !== encoded) throw new Error('未能完整保存原偏好请求，尚未发送'); return saved;
}
export class PreferenceClient {
  constructor(readonly client: Client) {}
  async analyze(original: PreferenceLocator): Promise<PreferenceAnalysis> { if (original.stage !== 'ANALYSIS') throw new Error('原请求不是分析'); const value = parsePreferenceAnalysis(await this.client.request('/preference-analyses', { method: 'POST', headers: { 'Idempotency-Key': original.key }, body: JSON.stringify(parsePreferenceRequest(original.request)) }), this.client); if (JSON.stringify(value.request) !== JSON.stringify(parsePreferenceRequest(original.request))) throw new Error('分析结果与原有限范围不一致'); return value; }
  async analysis(id: string): Promise<PreferenceAnalysis> { return parsePreferenceAnalysis(await this.client.request(`/preference-analyses/${encodeURIComponent(templateID(id))}`), this.client); }
  async answer(original: PreferenceLocator): Promise<PreferenceAnswer> { if (original.stage !== 'ANSWER') throw new Error('原请求不是回答'); const value = parsePreferenceAnswer(await this.client.request('/preference-answers', { method: 'POST', headers: { 'Idempotency-Key': original.key }, body: JSON.stringify(parsePreferenceAnswerRequest(original.request)) }), this.client); if (JSON.stringify(value.request) !== JSON.stringify(parsePreferenceAnswerRequest(original.request))) throw new Error('回答原件与原问题及选项不一致'); return value; }
}
