import { Client, states } from './api';
import type { OperationState } from './api';
import type { JointPlan, JointUse } from './joint-api';
import { templateID, templateMoney } from './template-schema';

export type BridgeStepState = OperationState | 'AWAITING_ARRIVAL' | 'NOT_ACTIVATED_STOPPED' | 'NOT_ACTIVATED_EXPIRED';
export interface BridgeEffect {
  kind: 'TRANSFER_INTERNAL' | 'ALLOCATE_GOAL'; business_key: string; amount_cents: string;
  cash_uses: { account_id: string; amount_cents: string }[];
  origin_uses: { origin_id: string; account_id: string; amount_cents: string; bucket: 'AVAILABLE' }[];
  return_uses: never[]; destination_account_id: string; source_goal_id: ''; destination_goal_id: string;
  product: null; position_id: ''; position_opening_hash: ''; return_account_id: '';
  principal_available_by: string; planned_redeem_at: string; fee_cents: '0'; loss_cents: '0'; net_cents: '0'; quote_id: ''; liability: null; latest_arrival_at: string;
}
export interface BridgeRequest {
  protocol: 'zhiyu-bank-v2'; namespace: string; key: string; operation_id: string; user_id: string; permit_id: string; reference_hash: string; requested_at: string; expires_at: string;
  effect: BridgeEffect;
  authorities: { role: 'EXECUTE'; source_id: string; version: 1; goal_id: string; policy_id: string; policy_version: number; allows_fee: false; allows_loss: false }[];
  cost_consent: null;
}
export interface BridgeStep {
  number: number; child_number: number; operation_id: string; previous_operation_id?: string; confirmation_id: string; goal_id: string; initial_source: boolean;
  command_kind: 'BRIDGE_TRANSFER' | 'BRIDGE_ALLOCATE'; bank_request: BridgeRequest; bank_request_hash: string; effect_hash: string;
  resources: { income_uses: JointUse[]; income_operation: 'TRANSFER_INTERNAL' | 'ALLOCATE_GOAL'; destination_account_id?: string; cash_account_id: string; amount_cents: string };
}
export interface BridgePreview { protocol: 'native-bridge-group-v1'; id: string; plan_id: string; environment_id: string; user_id: string; session_id: string; plan_hash: string; review_hash: string; created_at: string; expires_at: string; steps: BridgeStep[] }
export interface BridgeGroup { id: string; plan_id: string; state: 'IN_PROGRESS' | 'SETTLED' | 'STOPPED' | 'PARTIAL_EXPIRED'; steps: { step: BridgeStep; state: BridgeStepState; receipt_id?: string }[] }
export interface BridgeCommand { preview_id: string; reviewed_hash: string }
export interface BridgeLocator { environment: string; user_id: string; key: string; command: BridgeCommand; preview: BridgePreview }

function obj(raw: unknown): Record<string, unknown> { if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('到款衔接原件结构不完整'); return raw as Record<string, unknown>; }
function closed(value: Record<string, unknown>, allowed: string[]) { if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error('到款衔接原件包含未识别字段'); }
function text(raw: unknown, maximum = 256): string { if (typeof raw !== 'string' || !raw || raw.length > maximum) throw new Error('到款衔接文字字段不完整'); return raw; }
function int(raw: unknown, min = 1, max = Number.MAX_SAFE_INTEGER): number { if (!Number.isSafeInteger(raw) || (raw as number) < min || (raw as number) > max) throw new Error('到款衔接序号不完整'); return raw as number; }
function flag(raw: unknown): boolean { if (typeof raw !== 'boolean') throw new Error('到款衔接开关不完整'); return raw; }
function hash(raw: unknown): string { const value = text(raw, 64); if (!/^[a-f0-9]{64}$/.test(value)) throw new Error('到款衔接原件摘要不完整'); return value; }
function instant(raw: unknown): string { const value = text(raw, 64); if (!Number.isFinite(Date.parse(value))) throw new Error('到款衔接时间不完整'); return value; }
function zeroTime(raw: unknown): string { const value = instant(raw); if (value !== '0001-01-01T00:00:00Z') throw new Error('转账与归属不得暗含资产锁定或赎回时点'); return value; }
function empty(raw: unknown): '' { if (raw !== '') throw new Error('到款衔接包含范围外经济效果'); return ''; }
function zero(raw: unknown): '0' { if (raw !== '0') throw new Error('到款衔接不得暗含费用或损失'); return '0'; }
function nil(raw: unknown): null { if (raw !== null) throw new Error('到款衔接包含未审阅资产或负债'); return null; }
function list<T>(raw: unknown, parse: (item: unknown) => T, max: number): T[] { if (!Array.isArray(raw) || raw.length > max) throw new Error('到款衔接列表超出完整范围'); return raw.map(parse); }
function unique(values: string[]) { if (new Set(values).size !== values.length) throw new Error('到款衔接原件对象重复'); }
function positive(raw: unknown): string { const value = templateMoney(raw); if (value === '0') throw new Error('到款衔接金额必须大于零'); return value; }
function sum(values: string[]) { return values.reduce((total, value) => total + BigInt(value), 0n); }
function use(raw: unknown): JointUse { const value = obj(raw); closed(value, ['FragmentID', 'OriginID', 'AccountID', 'Amount']); return { fragment_id: templateID(value.FragmentID), origin_id: templateID(value.OriginID), account_id: templateID(value.AccountID), amount_cents: positive(value.Amount) }; }
function wireUse(value: JointUse) { return { FragmentID: value.fragment_id, OriginID: value.origin_id, AccountID: value.account_id, Amount: value.amount_cents }; }

function parseEffect(raw: unknown): BridgeEffect {
  const e = obj(raw); closed(e, ['kind', 'business_key', 'amount_cents', 'cash_uses', 'origin_uses', 'return_uses', 'destination_account_id', 'source_goal_id', 'destination_goal_id', 'product', 'position_id', 'position_opening_hash', 'return_account_id', 'principal_available_by', 'planned_redeem_at', 'fee_cents', 'loss_cents', 'net_cents', 'quote_id', 'liability', 'latest_arrival_at']);
  if (e.kind !== 'TRANSFER_INTERNAL' && e.kind !== 'ALLOCATE_GOAL') throw new Error('到款衔接经济类型不受支持');
  const cash_uses = list(e.cash_uses, (raw) => { const v = obj(raw); closed(v, ['account_id', 'amount_cents']); return { account_id: templateID(v.account_id), amount_cents: positive(v.amount_cents) }; }, 1);
  const origin_uses = list(e.origin_uses, (raw) => { const v = obj(raw); closed(v, ['origin_id', 'account_id', 'amount_cents', 'bucket']); if (v.bucket !== 'AVAILABLE') throw new Error('到款衔接不得使用尚未到达的收入'); return { origin_id: templateID(v.origin_id), account_id: templateID(v.account_id), amount_cents: positive(v.amount_cents), bucket: 'AVAILABLE' as const }; }, 128);
  if (!Array.isArray(e.return_uses) || e.return_uses.length || cash_uses.length !== 1 || !origin_uses.length) throw new Error('到款衔接原来源不完整');
  const amount = positive(e.amount_cents); if (sum(cash_uses.map((item) => item.amount_cents)) !== BigInt(amount) || sum(origin_uses.map((item) => item.amount_cents)) !== BigInt(amount)) throw new Error('到款衔接经济效果不守恒');
  unique(origin_uses.map((item) => `${item.origin_id}:${item.account_id}`));
  return { kind: e.kind, business_key: text(e.business_key), amount_cents: amount, cash_uses, origin_uses, return_uses: [], destination_account_id: e.kind === 'TRANSFER_INTERNAL' ? templateID(e.destination_account_id) : empty(e.destination_account_id), source_goal_id: empty(e.source_goal_id), destination_goal_id: e.kind === 'ALLOCATE_GOAL' ? templateID(e.destination_goal_id) : empty(e.destination_goal_id), product: nil(e.product), position_id: empty(e.position_id), position_opening_hash: empty(e.position_opening_hash), return_account_id: empty(e.return_account_id), principal_available_by: zeroTime(e.principal_available_by), planned_redeem_at: zeroTime(e.planned_redeem_at), fee_cents: zero(e.fee_cents), loss_cents: zero(e.loss_cents), net_cents: zero(e.net_cents), quote_id: empty(e.quote_id), liability: nil(e.liability), latest_arrival_at: instant(e.latest_arrival_at) };
}
function parseRequest(raw: unknown): BridgeRequest {
  const r = obj(raw); closed(r, ['protocol', 'namespace', 'key', 'operation_id', 'user_id', 'permit_id', 'reference_hash', 'requested_at', 'expires_at', 'effect', 'authorities', 'cost_consent']);
  if (r.protocol !== 'zhiyu-bank-v2') throw new Error('到款衔接银行协议不受支持');
  const authorities = list(r.authorities, (raw) => { const a = obj(raw); closed(a, ['role', 'source_id', 'version', 'goal_id', 'policy_id', 'policy_version', 'allows_fee', 'allows_loss']); if (a.role !== 'EXECUTE' || a.version !== 1 || a.allows_fee !== false || a.allows_loss !== false) throw new Error('到款衔接原授权范围不符'); return { role: 'EXECUTE' as const, source_id: templateID(a.source_id), version: 1 as const, goal_id: templateID(a.goal_id), policy_id: templateID(a.policy_id), policy_version: int(a.policy_version), allows_fee: false as const, allows_loss: false as const }; }, 1);
  if (authorities.length !== 1) throw new Error('到款衔接缺少逐步原确认');
  return { protocol: r.protocol, namespace: text(r.namespace, 128), key: templateID(r.key), operation_id: templateID(r.operation_id), user_id: templateID(r.user_id), permit_id: templateID(r.permit_id), reference_hash: hash(r.reference_hash), requested_at: instant(r.requested_at), expires_at: instant(r.expires_at), effect: parseEffect(r.effect), authorities, cost_consent: nil(r.cost_consent) };
}
function parseStep(raw: unknown): BridgeStep {
  const s = obj(raw); closed(s, ['number', 'child_number', 'operation_id', 'previous_operation_id', 'confirmation_id', 'goal_id', 'initial_source', 'command_kind', 'bank_request', 'bank_request_hash', 'effect_hash', 'resources']);
  if (s.command_kind !== 'BRIDGE_TRANSFER' && s.command_kind !== 'BRIDGE_ALLOCATE') throw new Error('到款衔接步骤类型不受支持');
  const r = obj(s.resources); closed(r, ['income_uses', 'income_operation', 'destination_account_id', 'cash_account_id', 'amount_cents']);
  const operation = s.command_kind === 'BRIDGE_TRANSFER' ? 'TRANSFER_INTERNAL' : 'ALLOCATE_GOAL'; if (r.income_operation !== operation) throw new Error('到款衔接资源类型不一致');
  const step: BridgeStep = { number: int(s.number, 1, 16), child_number: int(s.child_number, 1, 8), operation_id: templateID(s.operation_id), previous_operation_id: s.previous_operation_id == null ? undefined : templateID(s.previous_operation_id), confirmation_id: templateID(s.confirmation_id), goal_id: templateID(s.goal_id), initial_source: flag(s.initial_source), command_kind: s.command_kind, bank_request: parseRequest(s.bank_request), bank_request_hash: hash(s.bank_request_hash), effect_hash: hash(s.effect_hash), resources: { income_uses: list(r.income_uses, use, 128), income_operation: operation, destination_account_id: r.destination_account_id == null ? undefined : templateID(r.destination_account_id), cash_account_id: templateID(r.cash_account_id), amount_cents: positive(r.amount_cents) } };
  const request = step.bank_request, effect = request.effect, resource = step.resources;
  if (request.key !== step.operation_id || request.operation_id !== step.operation_id || request.authorities[0]!.source_id !== step.confirmation_id || request.authorities[0]!.goal_id !== step.goal_id || effect.kind !== operation || effect.amount_cents !== resource.amount_cents || effect.cash_uses[0]!.account_id !== resource.cash_account_id || sum(resource.income_uses.map((item) => item.amount_cents)) !== BigInt(resource.amount_cents)) throw new Error('到款衔接原请求与资源绑定不一致');
  unique(resource.income_uses.map((item) => item.fragment_id));
  const origins = resource.income_uses.map((item) => `${item.origin_id}:${item.account_id}:${item.amount_cents}`).sort();
  if (resource.income_uses.some((item) => item.account_id !== resource.cash_account_id) || origins.join('|') !== effect.origin_uses.map((item) => `${item.origin_id}:${item.account_id}:${item.amount_cents}`).sort().join('|')) throw new Error('到款衔接重复或更换原收入');
  if (operation === 'TRANSFER_INTERNAL' ? !resource.destination_account_id || resource.destination_account_id !== effect.destination_account_id || resource.destination_account_id === resource.cash_account_id : resource.destination_account_id !== undefined || effect.destination_goal_id !== step.goal_id) throw new Error('到款衔接目的绑定不一致');
  return step;
}
function serializeStep(step: BridgeStep) { return { ...step, resources: { ...step.resources, income_uses: step.resources.income_uses.map(wireUse) } }; }
function serializePreview(preview: BridgePreview) { return { ...preview, steps: preview.steps.map(serializeStep) }; }

export function parseBridgePreview(raw: unknown, client: Client, plan?: JointPlan): BridgePreview {
  const p = obj(raw); closed(p, ['protocol', 'id', 'plan_id', 'environment_id', 'user_id', 'session_id', 'plan_hash', 'review_hash', 'created_at', 'expires_at', 'steps']);
  if (p.protocol !== 'native-bridge-group-v1') throw new Error('到款衔接预览协议不受支持');
  const preview: BridgePreview = { protocol: p.protocol, id: templateID(p.id), plan_id: templateID(p.plan_id), environment_id: templateID(p.environment_id), user_id: templateID(p.user_id), session_id: templateID(p.session_id), plan_hash: hash(p.plan_hash), review_hash: hash(p.review_hash), created_at: instant(p.created_at), expires_at: instant(p.expires_at), steps: list(p.steps, parseStep, 16) };
  if (!client.session || preview.user_id !== client.session.user_id || !preview.steps.length || Date.parse(preview.expires_at) <= Date.parse(preview.created_at) || Date.parse(preview.expires_at) - Date.parse(preview.created_at) > 120000) throw new Error('到款衔接主体或有效期不完整');
  unique(preview.steps.map((step) => step.operation_id)); unique(preview.steps.map((step) => step.confirmation_id));
  for (const [i, step] of preview.steps.entries()) {
    const r = step.bank_request;
    if (step.number !== i + 1 || step.previous_operation_id !== preview.steps[i - 1]?.operation_id || r.user_id !== preview.user_id || r.reference_hash !== preview.plan_hash || r.requested_at !== preview.created_at || r.expires_at !== preview.expires_at || r.effect.latest_arrival_at !== preview.expires_at || r.effect.business_key !== `bridge:${preview.id}:${step.number}` || r.namespace !== preview.steps[0]!.bank_request.namespace) throw new Error('到款衔接逐步顺序或原事实不一致');
    const prior = preview.steps[i - 1];
    if (!step.initial_source && (!prior || prior.command_kind !== 'BRIDGE_TRANSFER' || step.command_kind !== 'BRIDGE_ALLOCATE' || step.child_number !== prior.child_number || step.goal_id !== prior.goal_id || step.resources.cash_account_id !== prior.resources.destination_account_id || step.resources.amount_cents !== prior.resources.amount_cents || step.resources.income_uses.map((item) => `${item.origin_id}:${item.amount_cents}`).sort().join('|') !== prior.resources.income_uses.map((item) => `${item.origin_id}:${item.amount_cents}`).sort().join('|'))) throw new Error('尚未到款的后继必须绑定前序实际转账');
    if (step.command_kind === 'BRIDGE_TRANSFER' && !step.initial_source) throw new Error('到款衔接不能用预测到款继续转账');
    if (step.command_kind === 'BRIDGE_TRANSFER' && (preview.steps[i + 1]?.command_kind !== 'BRIDGE_ALLOCATE' || preview.steps[i + 1]?.initial_source !== false || preview.steps[i + 1]?.child_number !== step.child_number)) throw new Error('每笔跨账户转账必须保留其原到款归属后继');
  }
  const childNumbers = preview.steps.filter((step) => step.initial_source).map((step) => step.child_number);
  if (childNumbers.some((value, i) => value !== i + 1) || preview.steps.at(-1)!.command_kind !== 'BRIDGE_ALLOCATE') throw new Error('到款衔接子计划不完整');
  if (plan) {
    if (preview.plan_id !== plan.id || preview.plan_hash !== plan.reviewed_bundle_hash || preview.environment_id !== plan.scope.environment_id || preview.expires_at !== plan.expires_at || Date.parse(preview.created_at) < Date.parse(plan.created_at) || plan.result.status !== 'OPTIMAL' || childNumbers.length !== plan.result.children.length) throw new Error('到款衔接不能替换原联合计划');
    for (const child of plan.result.children) {
      const steps = preview.steps.filter((step) => step.child_number === child.number), cross = child.source_account_id !== child.destination_account_id;
      if (steps.length !== (cross ? 2 : 1)) throw new Error('到款衔接遗漏转账或归属步骤');
      for (const step of steps) {
        const transfer = step.command_kind === 'BRIDGE_TRANSFER', expectedAccount = transfer ? child.source_account_id : child.destination_account_id, authority = step.bank_request.authorities[0]!, reference = plan.references.find((ref) => ref.version_id === child.version_id);
        if (step.goal_id !== child.goal_id || step.resources.amount_cents !== child.amount_cents || step.resources.cash_account_id !== expectedAccount || authority.policy_id !== child.policy_id || authority.policy_version !== reference?.version || transfer !== (cross && step === steps[0])) throw new Error('到款衔接更改原目标、规则或金额');
        const expected = child.uses.map((item) => `${item.origin_id}:${item.amount_cents}`).sort().join('|');
        if (step.resources.income_uses.map((item) => `${item.origin_id}:${item.amount_cents}`).sort().join('|') !== expected || transfer && step.resources.destination_account_id !== child.destination_account_id || transfer && step.resources.income_uses.some((item) => !child.uses.some((use) => use.fragment_id === item.fragment_id))) throw new Error('到款衔接更换原收入用途');
      }
    }
  }
  return preview;
}

export function bridgeTerminal(group: BridgeGroup): boolean { return group.state !== 'IN_PROGRESS'; }
export function parseBridgeGroup(raw: unknown, original: BridgeLocator, prior?: BridgeGroup | null): BridgeGroup {
  const g = obj(raw); closed(g, ['id', 'plan_id', 'state', 'steps']);
  if (!['IN_PROGRESS', 'SETTLED', 'STOPPED', 'PARTIAL_EXPIRED'].includes(g.state as string)) throw new Error('到款衔接整组状态不受支持');
  const steps = list(g.steps, (raw) => { const v = obj(raw); closed(v, ['step', 'state', 'receipt_id']); if (![...states, 'AWAITING_ARRIVAL', 'NOT_ACTIVATED_STOPPED', 'NOT_ACTIVATED_EXPIRED'].includes(v.state as OperationState)) throw new Error('到款衔接步骤状态不受支持'); const receipt = v.receipt_id == null ? undefined : templateID(v.receipt_id); if (v.state === 'SETTLED' ? !receipt : receipt !== undefined) throw new Error('到款衔接结清与原回执不一致'); return { step: parseStep(v.step), state: v.state as BridgeStepState, receipt_id: receipt }; }, 16);
  const group: BridgeGroup = { id: templateID(g.id), plan_id: templateID(g.plan_id), state: g.state as BridgeGroup['state'], steps };
  if (group.id !== original.preview.id || group.plan_id !== original.preview.plan_id || steps.length !== original.preview.steps.length || steps.some((item, i) => JSON.stringify(item.step) !== JSON.stringify(original.preview.steps[i]))) throw new Error('到款衔接结果改变了整组原审阅');
  if (steps.some((item) => item.step.initial_source && !states.includes(item.state as OperationState))) throw new Error('当前来源已受理的原步骤不能伪装为未激活');
  const terminal = (state: BridgeStepState) => ['SETTLED', 'REJECTED', 'CANCELLED', 'NOT_ACTIVATED_STOPPED', 'NOT_ACTIVATED_EXPIRED'].includes(state);
  if (group.state === 'SETTLED' ? steps.some((item) => item.state !== 'SETTLED') : group.state !== 'IN_PROGRESS' ? steps.some((item) => !terminal(item.state)) : steps.every((item) => terminal(item.state))) throw new Error('到款衔接整组不能跳过未知步骤宣称结束');
  if (group.state === 'PARTIAL_EXPIRED' && !steps.some((item) => item.state === 'SETTLED')) throw new Error('到款衔接部分结清状态缺少原回执');
  if (steps.every((item) => item.state === 'SETTLED') && group.state !== 'SETTLED') throw new Error('全部实际结清的原组状态不一致');
  if (prior && (prior.id !== group.id || prior.plan_id !== group.plan_id || prior.steps.length !== steps.length || prior.steps.some((item, i) => ['SETTLED', 'REJECTED', 'CANCELLED'].includes(item.state) && (item.state !== steps[i]!.state || item.receipt_id !== steps[i]!.receipt_id)))) throw new Error('到款衔接原终态不能被降级或替换');
  return group;
}
export function bindBridgeGroup(group: BridgeGroup, original: BridgeLocator, prior?: BridgeGroup | null): BridgeGroup { return parseBridgeGroup({ ...group, steps: group.steps.map((item) => ({ ...item, step: serializeStep(item.step) })) }, original, prior); }
function command(raw: unknown): BridgeCommand { const c = obj(raw); closed(c, ['preview_id', 'reviewed_hash']); return { preview_id: templateID(c.preview_id), reviewed_hash: hash(c.reviewed_hash) }; }
const storageKey = (client: Client) => { if (!client.session) throw new Error('请重新登录后核对原到款衔接'); return `zhiyu-go:bridge-originals:v1:${client.session.environment}:${client.session.user_id}`; };
function locator(raw: unknown, client: Client): BridgeLocator { const l = obj(raw), session = client.session; closed(l, ['environment', 'user_id', 'key', 'command', 'preview']); if (!session || l.environment !== session.environment || l.user_id !== session.user_id) throw new Error('保存的原到款衔接跨越当前主体'); const value = { environment: text(l.environment), user_id: templateID(l.user_id), key: templateID(l.key), command: command(l.command), preview: parseBridgePreview(l.preview, client) }; if (value.command.preview_id !== value.preview.id || value.command.reviewed_hash !== value.preview.review_hash) throw new Error('保存的原到款衔接与完整审阅不一致'); return value; }
function serializeLocator(original: BridgeLocator) { return { ...original, preview: serializePreview(original.preview) }; }
export function loadBridgeLocators(client: Client): BridgeLocator[] { try { const raw = localStorage.getItem(storageKey(client)); if (raw === null) return []; const list = JSON.parse(raw); if (!Array.isArray(list) || list.length > 100) throw new Error(); const values = list.map((item) => locator(item, client)); unique(values.map((item) => item.key)); unique(values.map((item) => item.command.preview_id)); return values; } catch { throw new Error('保存的原到款衔接请求不完整，请保留浏览器原资料并先核对'); } }
export function saveBridgeLocator(client: Client, original: BridgeLocator): BridgeLocator[] { const value = locator(serializeLocator(original), client), saved = loadBridgeLocators(client), prior = saved.find((item) => item.key === value.key || item.command.preview_id === value.command.preview_id); if (prior) { if (JSON.stringify(prior) !== JSON.stringify(value)) throw new Error('不能更改原到款衔接键、载荷或完整审阅'); return saved; } if (saved.length >= 100) throw new Error('原到款衔接记录已达容量，请先核对原记录'); const next = [...saved, value]; localStorage.setItem(storageKey(client), JSON.stringify(next.map(serializeLocator))); const verified = loadBridgeLocators(client); if (JSON.stringify(verified) !== JSON.stringify(next)) throw new Error('到款衔接原请求未完整持久保存'); return verified; }
export class BridgeClient {
  constructor(readonly client: Client) {}
  async preview(plan: JointPlan, signal?: AbortSignal): Promise<BridgePreview> { return parseBridgePreview(await this.client.request('/bridge-previews', { method: 'POST', body: JSON.stringify({ plan_id: plan.id }), signal }), this.client, plan); }
  async original(id: string, signal?: AbortSignal): Promise<BridgePreview> { return parseBridgePreview(await this.client.request(`/bridge-previews/${encodeURIComponent(id)}`, { signal }), this.client); }
  async accept(original: BridgeLocator): Promise<BridgeGroup> { return parseBridgeGroup(await this.client.request('/bridge-operations', { method: 'POST', headers: { 'Idempotency-Key': original.key }, body: JSON.stringify(original.command) }), original); }
  async group(original: BridgeLocator, signal?: AbortSignal): Promise<BridgeGroup> { return parseBridgeGroup(await this.client.request(`/bridge-operations/${encodeURIComponent(original.command.preview_id)}`, { signal }), original); }
  async byKey(original: BridgeLocator, signal?: AbortSignal): Promise<BridgeGroup> { return parseBridgeGroup(await this.client.request(`/bridge-commands/${encodeURIComponent(original.key)}`, { signal }), original); }
}
