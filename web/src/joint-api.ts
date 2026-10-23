import { Client, states } from './api';
import type { Dashboard, OperationState } from './api';
import { templateDate, templateID, templateMoney } from './template-schema';

export interface JointUse { fragment_id: string; origin_id: string; account_id: string; amount_cents: string }
export interface JointAllocation { goal_id: string; version_id: string; amount_cents: string; projected_owned_cents: string; minimum_shortfall_cents: string; delay_lower_bound_days: number; delay_censored: boolean; completion_date?: string }
export interface JointChild { number: number; goal_id: string; policy_id: string; version_id: string; source_account_id: string; destination_account_id: string; amount_cents: string; uses: JointUse[]; effect_hash: string }
export interface JointOrigin { id: string; account_id: string; amount_cents: string; occurred_at: string; observed_at: string; recorded_at: string }
export interface JointLocation { fragment_id: string; origin_id: string; account_id: string; available_cents: string; reserved_cents: string; assigned_cents: string; spent_cents: string }
export interface JointPlan {
  id: string; scope: { environment_id: string; user_id: string }; created_at: string; expires_at: string; reviewed_bundle_hash: string;
  references: { goal_id: string; goal_version: number; policy_id: string; version_id: string; version: number }[];
  origins: JointOrigin[]; locations: JointLocation[]; timezone: 'UTC' | 'Asia/Shanghai';
  result: { algorithm: 'joint-point-protection-go-v2'; status: 'OPTIMAL' | 'INFEASIBLE' | 'UNKNOWN'; budget_cents: string; allocations: JointAllocation[]; children: JointChild[]; uses: { goal_id: string; use: JointUse }[]; objective_exact: boolean; visited_nodes: number; bank_authority: false };
  bank_authority: false;
}
export interface JointOperation { id: string; group_id: string; child_number: number; kind: 'ALLOCATE_GOAL'; state: OperationState; amount_cents: string; bank_key: string; bank_observed_status?: string; processing_health: string; receipt_id?: string; version: number; created_at: string; updated_at: string }
export interface JointGroup { id: string; plan_id: string; operations: JointOperation[] }
export interface JointCommand { plan_id: string; reviewed_bundle_hash: string }
export interface JointLocator { environment: string; user_id: string; key: string; command: JointCommand }

function object(raw: unknown): Record<string, unknown> { if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('联合计划原件结构不完整'); return raw as Record<string, unknown>; }
function text(raw: unknown): string { if (typeof raw !== 'string') throw new Error('联合计划字段类型不符'); return raw; }
function integer(raw: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number { if (!Number.isSafeInteger(raw) || (raw as number) < min || (raw as number) > max) throw new Error('联合计划整数范围不完整'); return raw as number; }
function flag(raw: unknown): boolean { if (typeof raw !== 'boolean') throw new Error('联合计划开关字段不完整'); return raw; }
function noAuthority(raw: unknown): false { if (raw !== false) throw new Error('联合候选不得包含银行权限'); return false; }
function digest(raw: unknown): string { const value = text(raw); if (!/^[a-f0-9]{64}$/.test(value)) throw new Error('联合原件摘要不完整'); return value; }
function instant(raw: unknown): string { const value = text(raw); if (!Number.isFinite(Date.parse(value))) throw new Error('联合原件时间不完整'); return value; }
function array<T>(raw: unknown, parse: (value: unknown) => T, maximum: number): T[] { if (!Array.isArray(raw) || raw.length > maximum) throw new Error('联合原件列表超出完整范围'); return raw.map(parse); }
function unique(values: string[]): void { if (new Set(values).size !== values.length) throw new Error('联合原件存在重复对象'); }
function owner(raw: unknown): { environment_id: string; user_id: string } { const value = object(raw); return { environment_id: templateID(value.environment_id), user_id: templateID(value.user_id) }; }
function sameOwner(left: ReturnType<typeof owner>, right: unknown): void { const value = owner(right); if (left.environment_id !== value.environment_id || left.user_id !== value.user_id) throw new Error('联合原件跨越当前主体'); }
function sum(values: string[]): bigint { return values.reduce((total, value) => total + BigInt(value), 0n); }
function use(raw: unknown): JointUse { const value = object(raw); return { fragment_id: templateID(value.FragmentID), origin_id: templateID(value.OriginID), account_id: templateID(value.AccountID), amount_cents: templateMoney(value.Amount) }; }

export function parseJointPlan(raw: unknown, client: Client, dashboard?: Dashboard): JointPlan {
  const envelope = object(raw), plan = object(envelope.plan), result = object(plan.result), input = object(plan.input), ledger = object(input.ledger), proof = object(input.protection_forecast), planner = object(input.planner);
  if (plan.protocol !== 'native-joint-plan-go-v1' || result.algorithm !== 'joint-point-protection-go-v2') throw new Error('联合计划算法或冻结协议不受支持');
  const scope = owner(plan.scope); if (!client.session || scope.user_id !== client.session.user_id || ledger.user_id !== scope.user_id || ledger.currency !== 'CNY') throw new Error('联合计划不属于当前会话');
  sameOwner(scope, proof.scope); sameOwner(scope, planner.Scope);
  digest(plan.source_hash); digest(plan.inventory_hash); digest(plan.input_hash); digest(plan.result_hash);
  const created = instant(plan.created_at), expires = instant(plan.expires_at);
  if (Date.parse(expires) <= Date.parse(created) || Date.parse(expires) - Date.parse(created) > 120000 || instant(ledger.as_of) !== created || instant(proof.as_of) !== created) throw new Error('联合计划冻结时点不完整');
  if (proof.timezone !== 'UTC' && proof.timezone !== 'Asia/Shanghai') throw new Error('联合保护时区不受支持');
  if (proof.complete !== true || object(proof.facts).complete !== true || planner.Complete !== true || object(proof.facts).source_hash !== plan.source_hash || proof.inventory_hash !== plan.inventory_hash) throw new Error('联合计划保护依据未完整读取');
  sameOwner(scope, object(proof.facts).scope);
  const references = array(plan.policy_references, (raw) => { const ref = object(raw); digest(ref.configuration_hash); return { goal_id: templateID(ref.goal_id), goal_version: integer(ref.goal_version, 1), policy_id: templateID(ref.policy_id), version_id: templateID(ref.version_id), version: integer(ref.version, 1) }; }, 8);
  if (references.length < 2) throw new Error('联合计划需要至少两个明确目标');
  unique(references.map((ref) => ref.goal_id)); unique(references.map((ref) => ref.policy_id));
  if (dashboard && references.some((ref) => !dashboard.goals.some((goal) => goal.id === ref.goal_id))) throw new Error('联合计划引用了当前范围外的目标');
  const origins = array(ledger.origin_facts, (raw): JointOrigin => { const fact = object(raw); if (fact.Currency !== 'CNY' || fact.Role !== 'INCOME' || fact.Source !== 'BANK_CONFIRMED') throw new Error('联合计划不能把预测或返本当成新收入'); const evidence = array(fact.EvidenceHash, (raw) => integer(raw, 0, 255), 32); if (evidence.length !== 32) throw new Error('联合原收入证据不完整'); templateID(fact.EvidenceID); return { id: templateID(fact.TransactionID), account_id: templateID(fact.AccountID), amount_cents: templateMoney(fact.Amount), occurred_at: instant(fact.OccurredAt), observed_at: instant(fact.ObservedAt), recorded_at: instant(fact.RecordedAt) }; }, 100000);
  unique(origins.map((origin) => origin.id));
  const locations = array(ledger.locations, (raw): JointLocation => { const value = object(raw); return { fragment_id: templateID(value.FragmentID), origin_id: templateID(value.OriginID), account_id: templateID(value.AccountID), available_cents: templateMoney(value.Available), reserved_cents: templateMoney(value.Reserved), assigned_cents: templateMoney(value.Assigned), spent_cents: templateMoney(value.Spent) }; }, 128);
  unique(locations.map((location) => location.fragment_id));
  for (const origin of origins) {
    if (Date.parse(origin.observed_at) < Date.parse(origin.occurred_at) || Date.parse(origin.recorded_at) < Date.parse(origin.observed_at) || Date.parse(origin.recorded_at) > Date.parse(created)) throw new Error('联合原收入时序不完整');
    const relevant = locations.filter((location) => location.origin_id === origin.id);
    if (relevant.reduce((total, location) => total + sum([location.available_cents, location.reserved_cents, location.assigned_cents, location.spent_cents]), 0n) !== BigInt(origin.amount_cents)) throw new Error('联合原收入及位置不守恒');
  }
  if (locations.some((location) => !origins.some((origin) => origin.id === location.origin_id))) throw new Error('联合来源位置缺少原收入');
  const status = text(result.status); if (!['OPTIMAL', 'INFEASIBLE', 'UNKNOWN'].includes(status)) throw new Error('联合求解状态不受支持');
  const allocations = array(result.allocations, (raw): JointAllocation => { const value = object(raw); return { goal_id: templateID(value.goal_id), version_id: templateID(value.version_id), amount_cents: templateMoney(value.amount_cents), projected_owned_cents: templateMoney(value.projected_owned_cents), minimum_shortfall_cents: templateMoney(value.minimum_shortfall_cents), delay_lower_bound_days: integer(value.delay_lower_bound_days), delay_censored: flag(value.delay_censored), completion_date: value.completion_date == null ? undefined : templateDate(value.completion_date) }; }, 8);
  const children = array(result.children, (raw): JointChild => { const value = object(raw); return { number: integer(value.number, 1, 800), goal_id: templateID(value.goal_id), policy_id: templateID(value.policy_id), version_id: templateID(value.version_id), source_account_id: templateID(value.source_account_id), destination_account_id: templateID(value.destination_account_id), amount_cents: templateMoney(value.amount_cents), uses: array(value.uses, use, 128), effect_hash: digest(value.effect_hash) }; }, 800);
  const uses = array(result.uses, (raw) => { const value = object(raw); return { goal_id: templateID(value.goal_id), use: use(value.use) }; }, 1024);
  if (status !== 'OPTIMAL') { if (allocations.length || children.length || uses.length || result.objective != null || result.effect_hash) throw new Error('未知或不可行联合结果不能包含可执行金额'); }
  else {
    digest(result.effect_hash); digest(result.input_hash); digest(result.dependency_hash);
    if (allocations.length !== references.length || !Array.isArray(result.objective) || result.objective.length !== 8) throw new Error('联合最优原件不完整');
    unique(allocations.map((allocation) => allocation.goal_id));
    for (const allocation of allocations) if (!references.some((ref) => ref.goal_id === allocation.goal_id && ref.version_id === allocation.version_id)) throw new Error('联合分配与规则版本不一致');
    for (const [index, child] of children.entries()) {
      unique(child.uses.map((item) => item.fragment_id));
      if (child.number !== index + 1 || child.amount_cents === '0' || sum(child.uses.map((use) => use.amount_cents)) !== BigInt(child.amount_cents) || !references.some((ref) => ref.goal_id === child.goal_id && ref.policy_id === child.policy_id && ref.version_id === child.version_id) || child.uses.some((use) => use.account_id !== child.source_account_id)) throw new Error('联合子操作顺序、金额或原规则不一致');
    }
    for (const allocation of allocations) {
      if (sum(children.filter((child) => child.goal_id === allocation.goal_id).map((child) => child.amount_cents)) !== BigInt(allocation.amount_cents)) throw new Error('联合子操作与目标分配不守恒');
    }
    const identity = (goal: string, item: JointUse) => `${goal}:${item.fragment_id}:${item.origin_id}:${item.account_id}:${item.amount_cents}`;
    const actualUses = children.flatMap((child) => child.uses.map((item) => identity(child.goal_id, item))).sort();
    const declaredUses = uses.map((item) => identity(item.goal_id, item.use)).sort();
    if (actualUses.join('|') !== declaredUses.join('|')) throw new Error('联合原收入用途与完整子操作不一致');
    for (const location of locations) {
      const spent = uses.filter((item) => item.use.fragment_id === location.fragment_id);
      if (spent.some((item) => item.use.origin_id !== location.origin_id || item.use.account_id !== location.account_id) || sum(spent.map((item) => item.use.amount_cents)) > BigInt(location.available_cents)) throw new Error('联合计划重复使用或超用了原收入');
    }
    if (uses.some((item) => !locations.some((location) => location.fragment_id === item.use.fragment_id))) throw new Error('联合计划使用了范围外原来源');
    if (sum(allocations.map((item) => item.amount_cents)) > BigInt(templateMoney(result.budget_cents))) throw new Error('联合计划超过原来源与保护预算');
  }
  // The old public objective is int64 JSON numbers. Unsafe components are
  // explicitly unavailable for display, never rounded into claimed exact cents.
  const objectiveExact = Array.isArray(result.objective) && result.objective.every((item) => Number.isSafeInteger(item) && item >= 0);
  return { id: templateID(plan.id), scope, created_at: created, expires_at: expires, reviewed_bundle_hash: digest(envelope.reviewed_bundle_hash), references, origins, locations, timezone: proof.timezone, result: { algorithm: result.algorithm, status: status as JointPlan['result']['status'], budget_cents: templateMoney(result.budget_cents), allocations, children, uses, objective_exact: objectiveExact, visited_nodes: integer(result.visited_nodes, 0, 1000000), bank_authority: noAuthority(result.bank_authority) }, bank_authority: noAuthority(plan.bank_authority) };
}

export function parseJointGroup(raw: unknown): JointGroup {
  const value = object(raw), group = { id: templateID(value.id), plan_id: templateID(value.plan_id), operations: array(value.operations, (raw): JointOperation => {
    const op = object(raw), state = text(op.state); if (!(states as readonly string[]).includes(state) || op.kind !== 'ALLOCATE_GOAL') throw new Error('联合子操作状态或资金族不受支持');
    const receipt = op.receipt_id == null ? undefined : templateID(op.receipt_id); if (state === 'SETTLED' && !receipt) throw new Error('联合结清子操作缺少回执');
    return { id: templateID(op.id), group_id: templateID(op.group_id), child_number: integer(op.child_number, 1, 800), kind: op.kind, state: state as OperationState, amount_cents: templateMoney(op.amount_cents), bank_key: text(op.bank_key), bank_observed_status: op.bank_observed_status == null ? undefined : text(op.bank_observed_status), processing_health: text(op.processing_health), receipt_id: receipt, version: integer(op.version, 1), created_at: instant(op.created_at), updated_at: instant(op.updated_at) };
  }, 800) };
  if (!group.operations.length || group.operations.some((op, index) => op.child_number !== index + 1 || op.group_id !== group.id || !op.bank_key || op.amount_cents === '0')) throw new Error('联合组原子操作范围不完整');
  unique(group.operations.map((op) => op.id)); unique(group.operations.map((op) => op.bank_key)); return group;
}
export function bindJointGroup(locator: JointLocator, plan: JointPlan, group: JointGroup, previous?: JointGroup | null): JointGroup {
  if (group.plan_id !== locator.command.plan_id || plan.id !== locator.command.plan_id || plan.reviewed_bundle_hash !== locator.command.reviewed_bundle_hash || group.operations.length !== plan.result.children.length || previous && previous.id !== group.id) throw new Error('联合结果不属于这份原审阅');
  for (const op of group.operations) {
    const child = plan.result.children[op.child_number - 1]!, prior = previous?.operations.find((item) => item.child_number === op.child_number);
    if (op.amount_cents !== child.amount_cents || prior && (op.id !== prior.id || op.bank_key !== prior.bank_key || op.amount_cents !== prior.amount_cents || op.version < prior.version || ['SETTLED', 'REJECTED', 'CANCELLED'].includes(prior.state) && (op.state !== prior.state || op.receipt_id !== prior.receipt_id))) throw new Error('联合原子操作发生错配或终态倒退');
  }
  return group;
}
export function jointTerminal(group: JointGroup): boolean { return group.operations.every((op) => ['SETTLED', 'REJECTED', 'CANCELLED'].includes(op.state)); }
function storageKey(client: Client): string { if (!client.session) throw new Error('请先登录当前主体'); return `zhiyu-go:joint-originals:v1:${client.session.environment}:${client.session.user_id}`; }
function parseLocator(raw: unknown, client: Client): JointLocator {
  const value = object(raw), command = object(value.command), session = client.session;
  if (!session || Object.keys(value).sort().join(',') !== 'command,environment,key,user_id' || Object.keys(command).sort().join(',') !== 'plan_id,reviewed_bundle_hash' || value.environment !== session.environment || value.user_id !== session.user_id) throw new Error('原联合请求的主体或完整载荷不一致');
  return { environment: session.environment, user_id: templateID(value.user_id), key: templateID(value.key), command: { plan_id: templateID(command.plan_id), reviewed_bundle_hash: digest(command.reviewed_bundle_hash) } };
}
export function loadJointLocators(client: Client): JointLocator[] {
  const raw = localStorage.getItem(storageKey(client)); if (!raw) return [];
  try { const result = array(JSON.parse(raw), (value) => parseLocator(value, client), 100); unique(result.map((item) => item.key)); unique(result.map((item) => item.command.plan_id)); return result; }
  catch { throw new Error('保存的原联合请求不完整，已保留资料并停止新的提交'); }
}
export function saveJointLocator(client: Client, locator: JointLocator): JointLocator[] {
  const parsed = parseLocator(locator, client), originals = loadJointLocators(client);
  const existing = originals.find((value) => value.key === parsed.key || value.command.plan_id === parsed.command.plan_id);
  if (existing) { if (JSON.stringify(existing) !== JSON.stringify(parsed)) throw new Error('原联合键不能绑定另一份计划或审阅'); return originals; }
  if (originals.length >= 100) throw new Error('原联合记录已达到本机完整容量，请保留资料并先核对');
  const result = [...originals, parsed], encoded = JSON.stringify(result); localStorage.setItem(storageKey(client), encoded);
  if (localStorage.getItem(storageKey(client)) !== encoded) throw new Error('原联合请求未能完整保存，尚未发送');
  return result;
}
export class JointClient {
  constructor(readonly client: Client) {}
  async preview(dashboard: Dashboard, timezone: 'UTC' | 'Asia/Shanghai'): Promise<JointPlan> { return parseJointPlan(await this.client.request('/joint-plans', { method: 'POST', body: JSON.stringify({ timezone }) }), this.client, dashboard); }
  async plan(id: string, signal?: AbortSignal): Promise<JointPlan> { return parseJointPlan(await this.client.request(`/joint-plans/${encodeURIComponent(templateID(id))}`, { signal }), this.client); }
  async accept(original: JointLocator): Promise<JointGroup> { const value = parseLocator(original, this.client); return parseJointGroup(await this.client.request('/joint-operations', { method: 'POST', headers: { 'Idempotency-Key': value.key }, body: JSON.stringify(value.command) })); }
  async byKey(key: string, signal?: AbortSignal): Promise<JointGroup> { return parseJointGroup(await this.client.request(`/joint-commands/${encodeURIComponent(templateID(key))}`, { signal })); }
  async group(id: string, signal?: AbortSignal): Promise<JointGroup> { return parseJointGroup(await this.client.request(`/joint-operations/${encodeURIComponent(templateID(id))}`, { signal })); }
}
