export const templateNames = ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'DatedExpensePolicy', 'LongTermGoalPolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'] as const;
export type TemplateName = typeof templateNames[number];
export const templateLabels: Record<TemplateName, string> = { RecurringObligationPolicy: '定期账单', LivingReservePolicy: '生活费保护', EmergencyBufferPolicy: '应急备用金', DatedExpensePolicy: '定期专项支出', LongTermGoalPolicy: '长期目标', PeriodicTransferPolicy: '周期转账', AssetAuthorizationPolicy: '资产安排范围', RecoveryPolicy: '零损回收', GoalAllocationPolicy: '多目标分配', CrossGoalReallocationPolicy: '目标间调配', SeasonalReservePolicy: '节庆支出建议', InterventionPolicy: '何时向我确认' };
export const assetClasses = ['CASH', 'CASH_MGMT_T0', 'CASH_MGMT_T1', 'FIXED_DEPOSIT_7D', 'FIXED_DEPOSIT_30D', 'FIXED_DEPOSIT_90D', 'LOW_RISK_TERM'] as const;
export const assetLabels: Record<typeof assetClasses[number], string> = { CASH: '保持现金', CASH_MGMT_T0: '当日可赎回现金管理', CASH_MGMT_T1: '次日到账现金管理', FIXED_DEPOSIT_7D: '七天定期', FIXED_DEPOSIT_30D: '三十天定期', FIXED_DEPOSIT_90D: '九十天定期', LOW_RISK_TERM: '低风险期限产品' };
export const mandatoryInterventions = ['NEW_PAYEE', 'OUT_OF_AUTHORIZATION', 'NEW_ASSET_CLASS', 'NEW_LOSS', 'NEW_RISK', 'VALUE_PREFERENCE_CHANGE', 'UNAUTHORIZED_CONFLICT_REPAIR'] as const;
export const interventionReasons = ['ACTION_SET_CHANGED', 'AUTONOMOUS_AMOUNT_DECREASE', 'RECOVERY_REQUIRED', 'GOAL_MINIMUM_SHORTFALL', ...mandatoryInterventions] as const;
export const interventionLabels: Record<typeof interventionReasons[number], string> = { ACTION_SET_CHANGED: '可行安排发生变化', AUTONOMOUS_AMOUNT_DECREASE: '可安排金额减少', RECOVERY_REQUIRED: '需要回收已安排资产', GOAL_MINIMUM_SHORTFALL: '目标最低要求出现缺口', NEW_PAYEE: '出现新收款对象', OUT_OF_AUTHORIZATION: '超出授权范围', NEW_ASSET_CLASS: '出现新资产类别', NEW_LOSS: '出现新费用或损失', NEW_RISK: '出现新风险', VALUE_PREFERENCE_CHANGE: '需要改变价值偏好', UNAUTHORIZED_CONFLICT_REPAIR: '冲突修复需要新的确认' };

interface Common { name?: string; valid_from?: string; valid_until?: string }
interface Priority { importance: number; minimum_cents: string; reducible: boolean; deferrable: boolean }
interface Contribution { min_cents: string; target_cents: string; max_cents: string }
type Rule = { kind: 'exact'; amount_cents: string } | { kind: 'range'; min_cents: string; max_cents: string } | { kind: 'bill_balance'; account_id: string };
type Scoped = { scope: 'general_idle_funds'; goal_id?: never } | { scope: 'goal'; goal_id: string };
export type TemplateConfiguration = Common & (
  { type: 'recurring_obligation'; payee_id: string; amount_rule: Rule; due_day: number; prepare_days_before: number; auto_execute: boolean; priority: Priority } |
  { type: 'living_reserve'; horizon_days: number; method: { name: 'rolling_window_quantile'; lookback_days: number; quantile: string; essential_categories: string[]; exclude_one_off: boolean }; extra_buffer_cents: string; reconfirm_on_boundary_crossing: boolean } |
  { type: 'emergency_buffer'; amount_cents: string } |
  { type: 'dated_expense'; window: { start: string; end: string }; amount: Contribution; priority: Priority; must_not_reduce_policy_ids: string[] } |
  { type: 'long_term_goal'; target_cents: string; deadline: string; monthly_contribution: Contribution; importance: number; minimum_guarantee_cents: string; allow_partial: boolean; dynamic_reserve_math_version?: 'SAFE_PARTIAL_V2'; allow_deferral: boolean; deferral_cost_cents_per_day: string; asset_policy_id?: string; cross_goal_reallocation_allowed: boolean; cross_goal_reallocation_policy_id?: string } |
  { type: 'periodic_transfer'; source_account_id: string; payee_id: string; amount_rule: Exclude<Rule, { kind: 'bill_balance' }>; due_day: number; prepare_days_before: number; single_action_cap_cents: string; auto_execute: boolean } |
  ({ type: 'asset_authorization'; allowed_asset_classes: typeof assetClasses[number][]; max_auto_managed_cents: string; single_action_cap_cents: string; max_redemption_delay_days: number; max_lock_days: number; max_principal_risk_level: number; allow_auto_recovery_without_penalty: boolean; allow_early_withdrawal_with_penalty: boolean } & Scoped) |
  ({ type: 'recovery'; asset_policy_id: string; triggers: string[]; single_action_cap_cents: string; max_redemption_delay_days: number; allow_auto_recovery_without_penalty: boolean; max_fee_cents: string; max_loss_cents: string } & Scoped) |
  { type: 'goal_allocation'; goal_ids: string[]; method: 'lexicographic_v1'; funds_scope: 'NEW_UNASSIGNED_SAFE_FUNDS'; max_single_allocation_cents: string } |
  { type: 'cross_goal_reallocation'; scope_version?: 'GOAL_TRANSFER_V1'; destination_scope: 'PROTECTED_CASH' | 'GOAL'; enabled: boolean; source_goal_ids: string[]; target_goal_ids?: string[]; emergency_conditions?: string[]; single_action_cap_cents: string; total_cap_cents: string; must_not_reduce_policy_ids?: string[] } |
  { type: 'seasonal_reserve'; holiday_code: string; window: { start: string; end: string }; lookback_days: number; minimum_historical_windows: number; quantile: string; essential_categories: string[]; adjustment_cap_cents: string; requires_confirmation: true; advice_only: true } |
  { type: 'intervention'; must_ask_on: string[]; deduplicate_by_boundary_event: true; minimum_reask_interval_seconds: number; safety_events_bypass_throttle: true; silent_when_action_set_unchanged: true }
);

export function templateMoney(value: unknown): string {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value) || BigInt(value) > 9000000000000000n) throw new Error('规则金额须为支持范围内的整数分字符串');
  return value;
}
export function templateYuan(value: string): string {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/.exec(value.trim());
  if (!match) throw new Error('金额请填写最多两位小数');
  return templateMoney((BigInt(match[1]!) * 100n + BigInt((match[2] ?? '').padEnd(2, '0'))).toString());
}
export function editYuan(value: string): string { const amount = BigInt(templateMoney(value)); return `${amount / 100n}.${(amount % 100n).toString().padStart(2, '0')}`; }
export function templateID(value: unknown): string { if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value) || value === '00000000-0000-0000-0000-000000000000') throw new Error('当前对象引用不完整，请重新读取'); return value; }
export function templateDate(value: unknown): string { if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000') || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value) throw new Error('请填写有效的自然日期'); return value; }
export function templateQuantile(value: unknown): string { if (typeof value !== 'string' || !/^(0(?:\.\d{1,18})?|1(?:\.0{1,18})?)$/.test(value) || BigInt(value.replace('.', '')) === 0n) throw new Error('分位数须填写大于零、不超过一的小数字符串'); return value; }

type Schema = ((value: unknown) => unknown) | { [key: string]: Schema };
const optional = new WeakSet<object>();
function opt(rule: Schema): Schema { const wrapped = (value: unknown) => value == null ? undefined : validate(rule, value); optional.add(wrapped); return wrapped; }
function obj(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('规则结构不完整'); return value as Record<string, unknown>; }
function str(value: unknown): string { if (typeof value !== 'string' || !value.trim() || [...value].some((c) => { const n = c.codePointAt(0)!; return n >= 0xd800 && n <= 0xdfff; }) || [...value].length > 160) throw new Error('请填写有效名称或登记内容'); return value; }
function bool(value: unknown): boolean { if (typeof value !== 'boolean') throw new Error('规则开关类型不符'); return value; }
const fixed = <T extends string | boolean>(expected: T) => (value: unknown): T => { if (value !== expected) throw new Error('规则固定范围不符'); return expected; };
const int = (min: number, max: number) => (value: unknown): number => { if (!Number.isSafeInteger(value) || (value as number) < min || (value as number) > max) throw new Error(`请填写 ${min} 到 ${max} 之间的整数`); return value as number; };
const choices = (allowed: readonly string[]) => (value: unknown): string => { if (typeof value !== 'string' || !allowed.includes(value)) throw new Error('规则选项不受支持'); return value; };
const list = (item: Schema, min: number, max: number) => (value: unknown): unknown[] => { if (!Array.isArray(value) || value.length < min || value.length > max) throw new Error(`请选择 ${min} 到 ${max} 项`); const parsed = value.map((v) => validate(item, v)); if (new Set(parsed.map((v) => JSON.stringify(v))).size !== parsed.length) throw new Error('规则选项不能重复'); return parsed; };
const money = templateMoney, id = templateID, date = templateDate;
const priority: Schema = { importance: int(0, 100), minimum_cents: money, reducible: bool, deferrable: bool };
const contribution: Schema = { min_cents: money, target_cents: money, max_cents: money };
const window: Schema = { start: date, end: date };
const common: Record<string, Schema> = { name: opt(str), valid_from: opt(date), valid_until: opt(date) };
const amountRule = (bill: boolean) => (value: unknown): unknown => { const item = obj(value); const schemas: Record<string, Schema> = { exact: { kind: fixed('exact'), amount_cents: money }, range: { kind: fixed('range'), min_cents: money, max_cents: money } }; if (bill) schemas.bill_balance = { kind: fixed('bill_balance'), account_id: id }; const rule = typeof item.kind === 'string' ? schemas[item.kind] : undefined; if (!rule) throw new Error('金额规则不受支持'); return validate(rule, item); };
const scoped = { scope: choices(['general_idle_funds', 'goal']), goal_id: opt(id) };
const schema: Record<TemplateName, Record<string, Schema>> = {
  RecurringObligationPolicy: { type: fixed('recurring_obligation'), payee_id: str, amount_rule: amountRule(true), due_day: int(1, 31), prepare_days_before: int(0, 3650), auto_execute: bool, priority },
  LivingReservePolicy: { type: fixed('living_reserve'), horizon_days: int(1, 3650), method: { name: fixed('rolling_window_quantile'), lookback_days: int(1, 3650), quantile: templateQuantile, essential_categories: list(str, 1, 48), exclude_one_off: bool }, extra_buffer_cents: money, reconfirm_on_boundary_crossing: bool },
  EmergencyBufferPolicy: { type: fixed('emergency_buffer'), amount_cents: money },
  DatedExpensePolicy: { type: fixed('dated_expense'), window, amount: contribution, priority, must_not_reduce_policy_ids: list(id, 0, 32) },
  LongTermGoalPolicy: { type: fixed('long_term_goal'), target_cents: money, deadline: date, monthly_contribution: contribution, importance: int(0, 100), minimum_guarantee_cents: money, allow_partial: bool, dynamic_reserve_math_version: opt(fixed('SAFE_PARTIAL_V2')), allow_deferral: bool, deferral_cost_cents_per_day: money, asset_policy_id: opt(id), cross_goal_reallocation_allowed: bool, cross_goal_reallocation_policy_id: opt(id) },
  PeriodicTransferPolicy: { type: fixed('periodic_transfer'), source_account_id: id, payee_id: str, amount_rule: amountRule(false), due_day: int(1, 31), prepare_days_before: int(0, 3650), single_action_cap_cents: money, auto_execute: bool },
  AssetAuthorizationPolicy: { type: fixed('asset_authorization'), ...scoped, allowed_asset_classes: list(choices(assetClasses), 1, 7), max_auto_managed_cents: money, single_action_cap_cents: money, max_redemption_delay_days: int(0, 3660), max_lock_days: int(0, 3660), max_principal_risk_level: int(0, 5), allow_auto_recovery_without_penalty: bool, allow_early_withdrawal_with_penalty: bool },
  RecoveryPolicy: { type: fixed('recovery'), ...scoped, asset_policy_id: id, triggers: list(choices(['BOUNDARY_SHRINK', 'AUTHORIZATION_REVOKED', 'POLICY_EXPIRED', 'LIQUIDITY_SHORTFALL']), 1, 4), single_action_cap_cents: money, max_redemption_delay_days: int(0, 3660), allow_auto_recovery_without_penalty: bool, max_fee_cents: money, max_loss_cents: money },
  GoalAllocationPolicy: { type: fixed('goal_allocation'), goal_ids: list(id, 2, 32), method: fixed('lexicographic_v1'), funds_scope: fixed('NEW_UNASSIGNED_SAFE_FUNDS'), max_single_allocation_cents: money },
  CrossGoalReallocationPolicy: { type: fixed('cross_goal_reallocation'), scope_version: opt(fixed('GOAL_TRANSFER_V1')), destination_scope: choices(['PROTECTED_CASH', 'GOAL']), enabled: bool, source_goal_ids: list(id, 0, 32), target_goal_ids: opt(list(id, 1, 32)), emergency_conditions: opt(list(choices(['HARD_OBLIGATION_SHORTFALL', 'LIVING_RESERVE_SHORTFALL', 'EMERGENCY_BUFFER_SHORTFALL']), 0, 3)), single_action_cap_cents: money, total_cap_cents: money, must_not_reduce_policy_ids: opt(list(id, 0, 32)) },
  SeasonalReservePolicy: { type: fixed('seasonal_reserve'), holiday_code: str, window, lookback_days: int(1, 3650), minimum_historical_windows: int(1, 10), quantile: templateQuantile, essential_categories: list(str, 1, 48), adjustment_cap_cents: money, requires_confirmation: fixed(true), advice_only: fixed(true) },
  InterventionPolicy: { type: fixed('intervention'), must_ask_on: list(choices(interventionReasons), 7, 11), deduplicate_by_boundary_event: fixed(true), minimum_reask_interval_seconds: int(0, 86400), safety_events_bypass_throttle: fixed(true), silent_when_action_set_unchanged: fixed(true) },
};
function validate(rule: Schema, value: unknown): unknown { if (typeof rule === 'function') return rule(value); const record = obj(value); for (const key of Object.keys(record)) if (!(key in rule)) throw new Error('规则含有未登记字段'); const result: Record<string, unknown> = {}; for (const [key, child] of Object.entries(rule)) { if (!(key in record) && !optional.has(child)) throw new Error('规则缺少必要字段'); const parsed = validate(child, record[key]); if (parsed !== undefined) result[key] = parsed; } return result; }
export function parseTemplateName(value: unknown): TemplateName { if (typeof value !== 'string' || !(templateNames as readonly string[]).includes(value)) throw new Error('无法识别这类规则'); return value as TemplateName; }
export function parseConfiguration(name: TemplateName, raw: unknown): TemplateConfiguration {
  const value = validate({ ...common, ...schema[name] }, raw) as TemplateConfiguration;
  if (value.valid_from && value.valid_until && value.valid_from > value.valid_until) throw new Error('结束日期不能早于开始日期');
  if ('window' in value && (value.window.start > value.window.end || (value.valid_from && value.window.start < value.valid_from) || (value.valid_until && value.window.end > value.valid_until))) throw new Error('支出窗口须在规则有效期内');
  for (const amounts of ['monthly_contribution' in value ? value.monthly_contribution : undefined, 'amount' in value ? value.amount : undefined]) if (amounts && (BigInt(amounts.min_cents) > BigInt(amounts.target_cents) || BigInt(amounts.target_cents) > BigInt(amounts.max_cents))) throw new Error('最低金额、期望金额、最高金额须依次递增');
  if ('amount_rule' in value && value.amount_rule.kind === 'range' && BigInt(value.amount_rule.min_cents) > BigInt(value.amount_rule.max_cents)) throw new Error('金额范围的上限不能小于下限');
  if ('scope' in value && (value.scope === 'goal') !== !!value.goal_id) throw new Error('目标范围须绑定一项当前目标');
  if (value.type === 'long_term_goal' && (BigInt(value.target_cents) === 0n || BigInt(value.minimum_guarantee_cents) > BigInt(value.target_cents) || value.cross_goal_reallocation_allowed !== !!value.cross_goal_reallocation_policy_id || (!value.allow_deferral && value.deferral_cost_cents_per_day !== '0'))) throw new Error('目标、最低保证或延期与调配范围不符合约定');
  if (value.type === 'long_term_goal' && (value.valid_from && value.deadline < value.valid_from || value.valid_until && value.deadline > value.valid_until)) throw new Error('目标期限须在规则有效期内');
  if (value.type === 'living_reserve' && value.method.lookback_days < value.horizon_days) throw new Error('真实支出回看范围不能短于生活费保护天数');
  if (value.type === 'dated_expense' && BigInt(value.priority.minimum_cents) > BigInt(value.amount.max_cents)) throw new Error('不可降低的最低金额不能超过专项支出上限');
  if (value.type === 'periodic_transfer' && (value.single_action_cap_cents === '0' || BigInt(value.single_action_cap_cents) < BigInt(value.amount_rule.kind === 'exact' ? value.amount_rule.amount_cents : value.amount_rule.max_cents))) throw new Error('单次转账上限须大于零且覆盖本次已确认金额范围');
  if (value.type === 'asset_authorization' && BigInt(value.single_action_cap_cents) > BigInt(value.max_auto_managed_cents)) throw new Error('单次资产安排不能超过管理总额上限');
  if (value.type === 'recovery' && (value.single_action_cap_cents === '0' || value.max_fee_cents !== '0' || value.max_loss_cents !== '0')) throw new Error('当前回收模板仅支持费用和本金损失均为零，单次上限须大于零');
  if (value.type === 'goal_allocation' && value.max_single_allocation_cents === '0') throw new Error('联合分配单次上限须大于零');
  if (value.type === 'intervention' && mandatoryInterventions.some((reason) => !value.must_ask_on.includes(reason))) throw new Error('安全确认原因必须保留');
  if (value.type === 'cross_goal_reallocation' && ((value.scope_version === 'GOAL_TRANSFER_V1') !== (value.destination_scope === 'GOAL') || (value.destination_scope === 'GOAL' && ('emergency_conditions' in value || !value.target_goal_ids?.length)))) throw new Error('目标接收与生活现金保护的范围不能混用');
  if (value.type === 'cross_goal_reallocation') {
    if (value.destination_scope === 'PROTECTED_CASH' && ('scope_version' in value || 'target_goal_ids' in value || 'must_not_reduce_policy_ids' in value)) throw new Error('生活现金保护不能继承目标接收或跨目标保护引用');
    const mustHaveCaps = value.destination_scope === 'GOAL' || value.enabled;
    if (mustHaveCaps && (!value.source_goal_ids.length || !value.valid_from || !value.valid_until || value.single_action_cap_cents === '0' || BigInt(value.single_action_cap_cents) > BigInt(value.total_cap_cents))) throw new Error('启用的调配范围须明确来源、有效期、单次和全周期金额上限');
    if (value.destination_scope === 'PROTECTED_CASH' && (value.enabled ? !value.emergency_conditions?.length : value.single_action_cap_cents !== '0' || value.total_cap_cents !== '0' || !!value.emergency_conditions?.length)) throw new Error('生活现金调配启用时须明确缺口；停用时不得保留有效额度或触发条件');
  }
  if (value.type === 'seasonal_reserve' && (Date.parse(value.window.end) - Date.parse(value.window.start)) / 86400000 + 1 > value.lookback_days) throw new Error('季节窗口长度不能超过真实历史回看天数');
  return value;
}
