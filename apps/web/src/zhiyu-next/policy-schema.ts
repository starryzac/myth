import { object, moneyInput, parseMoneyInput } from '../features/policy-form';
import { isRunId } from '../api/decisions';
import { formatMoneyCents } from '../features/money';

export type Schema = Record<string, unknown>;
export type ReferenceChoices = Record<string, { value: string; label: string }[]>;
export function choicesAt(choices: ReferenceChoices, path: string) { return choices[path === 'amount_rule.account_id' ? 'account_id' : path]; }
export const templateNames = ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'DatedExpensePolicy', 'LongTermGoalPolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'] as const;
export type TemplateName = typeof templateNames[number];
export type Lifecycle = 'MVP' | 'FULL' | 'GOAL_BRIDGE';
export const templateTags: Record<TemplateName, string> = { RecurringObligationPolicy: 'recurring_obligation', LivingReservePolicy: 'living_reserve', EmergencyBufferPolicy: 'emergency_buffer', DatedExpensePolicy: 'dated_expense', LongTermGoalPolicy: 'long_term_goal', PeriodicTransferPolicy: 'periodic_transfer', AssetAuthorizationPolicy: 'asset_authorization', RecoveryPolicy: 'recovery', GoalAllocationPolicy: 'goal_allocation', CrossGoalReallocationPolicy: 'cross_goal_reallocation', SeasonalReservePolicy: 'seasonal_reserve', InterventionPolicy: 'intervention' };
export const templateTitles: Record<TemplateName, string> = { RecurringObligationPolicy: '周期负债', LivingReservePolicy: '生活准备金', EmergencyBufferPolicy: '应急金', DatedExpensePolicy: '明确时间支出', LongTermGoalPolicy: '长期目标', PeriodicTransferPolicy: '周期转账规划', AssetAuthorizationPolicy: '资产安排范围', RecoveryPolicy: '资金回收规则', GoalAllocationPolicy: '多目标分配', CrossGoalReallocationPolicy: '目标间应急调剂', SeasonalReservePolicy: '季节准备金', InterventionPolicy: '必要询问与通知' };
export function lifecycleFor(template: TemplateName): Lifecycle { return template === 'LongTermGoalPolicy' ? 'GOAL_BRIDGE' : ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy'].includes(template) ? 'MVP' : 'FULL'; }
export const lifecycleText: Record<Lifecycle, string> = { MVP: '基础规则', FULL: '完整规划规则', GOAL_BRIDGE: '已有目标扩展' };
const labels: Record<string, string> = {
  name: '规则名称', valid_from: '生效日期', valid_until: '失效日期', payee_id: '收款对象', amount_rule: '金额方式', kind: '金额方式', amount_cents: '金额', min_cents: '最低金额', target_cents: '目标金额', max_cents: '最高金额', due_day: '每月到期日', prepare_days_before: '提前准备天数', auto_execute: '允许自动执行', priority: '优先与底线', importance: '重要程度', minimum_cents: '最低保护金额', reducible: '允许降低', deferrable: '允许延期', horizon_days: '生活保护天数', method: '估算方式', lookback_days: '历史观察天数', quantile: '分位数（大于零且不超过一）', essential_categories: '必要消费类别', exclude_one_off: '排除一次性消费', extra_buffer_cents: '额外缓冲金额', reconfirm_on_boundary_crossing: '越过边界时重新询问', window: '支出时间窗', start: '开始日期', end: '结束日期', amount: '支出范围', must_not_reduce_policy_ids: '不可降低的规则', deadline: '目标截止日期', monthly_contribution: '月度安排范围', minimum_guarantee_cents: '最低保证金额', allow_partial: '允许部分完成', allow_deferral: '允许延期', deferral_cost_cents_per_day: '每天延期成本', asset_policy_id: '关联资产规则', cross_goal_reallocation_allowed: '允许目标间调剂', cross_goal_reallocation_policy_id: '关联调剂规则', source_account_id: '来源账户', account_id: '账单账户', single_action_cap_cents: '单次金额上限', scope: '资金范围', goal_id: '关联目标', allowed_asset_classes: '允许的资产类别', max_auto_managed_cents: '自动管理金额上限', max_redemption_delay_days: '最长赎回等待天数', max_lock_days: '最长锁定天数', max_principal_risk_level: '本金风险上限（零至五）', allow_auto_recovery_without_penalty: '允许无罚金自动回收', allow_early_withdrawal_with_penalty: '允许有罚金提前支取', triggers: '回收触发条件', max_fee_cents: '费用上限', max_loss_cents: '损失上限', goal_ids: '参与分配的目标', funds_scope: '资金来源范围', max_single_allocation_cents: '单次分配金额上限', enabled: '开启应急调剂', source_goal_ids: '可调出资金的目标', emergency_conditions: '应急触发条件', destination_scope: '调入范围', total_cap_cents: '累计金额上限', holiday_code: '节假日名称', minimum_historical_windows: '最低历史窗口数量', adjustment_cap_cents: '调整金额上限', requires_confirmation: '调整需要确认', advice_only: '仅产生建议', must_ask_on: '必须询问的情况', deduplicate_by_boundary_event: '同一边界事件不重复询问', minimum_reask_interval_seconds: '重复询问最短间隔（秒）', safety_events_bypass_throttle: '安全事件立即询问', silent_when_action_set_unchanged: '安排未变化时保持安静', type: '规则类别', cross_goal_reallocation: '目标间调剂' };
const values: Record<string, string> = { exact: '固定金额', range: '金额范围', bill_balance: '实际账单余额', general_idle_funds: '通用闲置资金', goal: '目标资金', CASH: '活期现金', CASH_MGMT_T0: '当日赎回现金管理', CASH_MGMT_T1: '次日赎回现金管理', FIXED_DEPOSIT_7D: '七天定期', FIXED_DEPOSIT_30D: '三十天定期', FIXED_DEPOSIT_90D: '九十天定期', LOW_RISK_TERM: '低风险期限产品', BOUNDARY_SHRINK: '资金边界收缩', AUTHORIZATION_REVOKED: '授权撤销', POLICY_EXPIRED: '规则到期', LIQUIDITY_SHORTFALL: '流动资金不足', HARD_OBLIGATION_SHORTFALL: '硬性负债不足', LIVING_RESERVE_SHORTFALL: '生活准备金不足', EMERGENCY_BUFFER_SHORTFALL: '应急金不足', ACTION_SET_CHANGED: '安排发生变化', AUTONOMOUS_AMOUNT_DECREASE: '自主安排金额降低', RECOVERY_REQUIRED: '需要回收资金', GOAL_MINIMUM_SHORTFALL: '目标最低承诺不足', NEW_PAYEE: '新增收款对象', OUT_OF_AUTHORIZATION: '超出授权范围', NEW_ASSET_CLASS: '新增资产类别', NEW_LOSS: '新增损失', NEW_RISK: '新增风险', VALUE_PREFERENCE_CHANGE: '价值偏好变化', UNAUTHORIZED_CONFLICT_REPAIR: '未经授权的冲突修复', NEW_UNASSIGNED_SAFE_FUNDS: '新的未分配安全资金', PROTECTED_CASH: '受保护现金', lexicographic_v1: '按优先顺序分配', rolling_window_quantile: '滚动窗口分位估算' };
export function fieldLabel(path: string): string { const name = path.split('.').at(-1)!; const label = labels[name]; if (!label) throw new Error('此模板包含尚未支持的业务字段，请保留候选。'); return label; }
export function valueLabel(value: string): string { return value === 'FIXED_DEPOSIT' ? '定期产品' : values[value] ?? value; }
export function configurationSummary(configuration: Record<string, unknown>, choices: ReferenceChoices = {}): string[] {
  const rows: string[] = [];
  function visit(value: unknown, path: string) {
    if (value === null || value === undefined || path === 'type') return;
    if (object(value)) { for (const [name, child] of Object.entries(value)) visit(child, path ? `${path}.${name}` : name); return; }
    let label: string; try { label = fieldLabel(path); } catch { rows.push('其他限制已保存在服务端，请核对候选影响。'); return; }
    const shown = (item: unknown) => /(?:_id|_ids)$/.test(path) ? choicesAt(choices, path)?.find((option) => option.value === item)?.label ?? '已关联的本轮对象' : typeof item === 'boolean' ? item ? '是' : '否' : typeof item === 'number' && /_cents(?:_per_day)?$/.test(path) ? `¥${formatMoneyCents(item)}` : valueLabel(String(item));
    rows.push(`${label}：${Array.isArray(value) ? value.length ? value.map(shown).join('、') : '无' : shown(value)}`);
  }
  visit(configuration, ''); return rows;
}
export function resolveSchema(root: Schema, input: unknown): Schema {
  if (!object(input)) throw new Error('模板字段结构尚未核实。');
  if (typeof input.$ref === 'string') { const match = /^#\/\$defs\/([A-Za-z0-9_]+)$/.exec(input.$ref); if (!match || !object(root.$defs) || !object(root.$defs[match[1]!])) throw new Error('模板字段引用尚未核实。'); return root.$defs[match[1]!] as Schema; }
  const options = input.anyOf;
  if (Array.isArray(options)) { const nonnull = options.filter((item) => object(item) && item.type !== 'null'); if (nonnull.length !== 1) throw new Error('模板字段有未支持的多种结构。'); return { ...resolveSchema(root, nonnull[0]), ...(Object.hasOwn(input, 'default') ? { default: input.default } : {}) }; }
  return input;
}
export function isOptional(schema: Schema): boolean { return Array.isArray(schema.anyOf) && schema.anyOf.some((item) => object(item) && item.type === 'null'); }
export function unionOptions(root: Schema, schema: Schema): { value: string; schema: Schema }[] {
  if (!Array.isArray(schema.oneOf)) return [];
  return schema.oneOf.map((item) => { const branch = resolveSchema(root, item); const properties = branch.properties; if (!object(properties) || !object(properties.kind) || typeof properties.kind.const !== 'string') throw new Error('金额方式尚未核实。'); return { value: properties.kind.const, schema: branch }; });
}
export type FormValues = Record<string, string | string[]>;
export function initialForm(root: Schema): FormValues {
  const result: FormValues = {};
  function visit(input: unknown, path: string, depth: number) {
    if (depth > 5) throw new Error('模板嵌套范围尚未支持。');
    const schema = resolveSchema(root, input); const union = unionOptions(root, schema);
    if (union.length) { result[`${path}.kind`] = ''; return; }
    if (schema.type === 'object') { if (!object(schema.properties) || schema.additionalProperties !== false) throw new Error('模板业务字段未封闭。'); for (const [name, item] of Object.entries(schema.properties)) visit(item, path ? `${path}.${name}` : name, depth + 1); return; }
    if (path) fieldLabel(path);
    if (schema.const !== undefined) return;
    if (!['string', 'number', 'integer', 'boolean', 'array'].includes(String(schema.type))) throw new Error('模板字段类型尚未支持。');
    if (schema.type === 'array' && resolveSchema(root, schema.items).type !== 'string') throw new Error('模板列表类型尚未支持。');
    const value = schema.default;
    result[path] = value === null || value === undefined ? schema.type === 'array' ? [] : '' : schema.type === 'array' && Array.isArray(value) ? value.map(String) : /_cents(?:_per_day)?$/.test(path) && typeof value === 'number' ? moneyInput(value) : String(value);
  }
  visit(root, '', 0); return result;
}
export function formFromConfiguration(root: Schema, configuration: Record<string, unknown>): FormValues {
  const result = initialForm(root);
  function visit(value: unknown, path: string) { if (object(value)) { for (const [name, child] of Object.entries(value)) visit(child, path ? `${path}.${name}` : name); return; } result[path] = value === null || value === undefined ? '' : Array.isArray(value) ? value.map(String) : typeof value === 'number' && /_cents(?:_per_day)?$/.test(path) ? moneyInput(value) : String(value); }
  visit(configuration, ''); return result;
}
export function buildFromSchema(root: Schema, form: FormValues, choices: ReferenceChoices): Record<string, unknown> {
  function visit(input: unknown, path: string, required: boolean, depth: number): unknown {
    if (depth > 5) throw new Error('模板嵌套范围尚未支持。');
    const schema = resolveSchema(root, input); const union = unionOptions(root, schema);
    if (union.length) { const branch = union.find((item) => item.value === form[`${path}.kind`]); if (!branch) throw new Error('请选择金额方式。'); return visit(branch.schema, path, required, depth + 1); }
    if (schema.const !== undefined) return schema.const;
    if (schema.type === 'object') {
      if (!object(schema.properties) || schema.additionalProperties !== false) throw new Error('模板业务字段未封闭。');
      const requiredKeys = Array.isArray(schema.required) ? schema.required : [];
      return Object.fromEntries(Object.entries(schema.properties).map(([name, item]) => [name, visit(item, path ? `${path}.${name}` : name, requiredKeys.includes(name), depth + 1)]).filter(([, value]) => value !== undefined));
    }
    const label = fieldLabel(path); const raw = form[path]; const nullable = isOptional(object(input) ? input : {});
    if (raw === undefined || raw === '' || Array.isArray(raw) && raw.length === 0) { if (nullable) return null; if (!required && schema.default === undefined) return undefined; if (schema.type === 'array' && Number(schema.minItems ?? 0) === 0) return []; throw new Error(`请填写${label}。`); }
    const references = choicesAt(choices, path);
    if (schema.format === 'uuid' || /(?:_id|_ids)$/.test(path)) {
      if (!references) throw new Error(`${label}尚无可核实的选项。`);
      const selected = Array.isArray(raw) ? raw : [raw]; const item = schema.type === 'array' ? resolveSchema(root, schema.items) : schema;
      if (selected.some((value) => !references.some((option) => option.value === value) || item.format === 'uuid' && !isRunId(value))) throw new Error(`${label}已变化，请重新选择。`);
    }
    if (schema.type === 'array') {
      const item = resolveSchema(root, schema.items); const list = Array.isArray(raw) ? raw : raw.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean);
      if (list.length < Number(schema.minItems ?? 0) || list.length > Number(schema.maxItems ?? 100) || new Set(list).size !== list.length) throw new Error(`${label}数量或重复项不符合要求。`);
      for (const value of list) { if (Array.isArray(item.enum) && !item.enum.includes(value) || value.length < Number(item.minLength ?? 0) || value.length > Number(item.maxLength ?? 160)) throw new Error(`${label}包含无效选项。`); }
      return list;
    }
    if (typeof raw !== 'string') throw new Error(`${label}格式无效。`);
    if (schema.type === 'boolean') { if (!['true', 'false'].includes(raw)) throw new Error(`请选择${label}。`); return raw === 'true'; }
    if (schema.type === 'integer' || schema.type === 'number') {
      const amount = /_cents(?:_per_day)?$/.test(path);
      if (!amount && !(schema.type === 'integer' ? /^\d+$/ : /^(?:0|[1-9]\d*)(?:\.\d+)?$/).test(raw)) throw new Error(`${label}格式无效。`);
      const number = amount ? parseMoneyInput(raw) : Number(raw);
      if (!Number.isFinite(number) || schema.type === 'integer' && !Number.isSafeInteger(number) || number < Number(schema.minimum ?? 0) || number > Number(schema.maximum ?? Number.MAX_SAFE_INTEGER) || schema.exclusiveMinimum !== undefined && number <= Number(schema.exclusiveMinimum)) throw new Error(`${label}超出允许范围。`);
      return number;
    }
    if (schema.type !== 'string' || raw.length < Number(schema.minLength ?? 0) || raw.length > Number(schema.maxLength ?? 160) || Array.isArray(schema.enum) && !schema.enum.includes(raw)) throw new Error(`${label}格式无效。`);
    if (schema.format === 'date' && (!/^\d{4}-\d{2}-\d{2}$/.test(raw) || Number.isNaN(Date.parse(raw)) || new Date(raw).toISOString().slice(0, 10) !== raw)) throw new Error(`${label}需要真实的日历日期。`);
    return raw.trim();
  }
  const result = visit(root, '', true, 0); if (!object(result)) throw new Error('模板配置结构无效。'); return result;
}
