import type { components } from '../../../../packages/contracts/schema';

export type Configuration = components['schemas']['PolicyChangeRequest']['configuration'];
export type Field = { path: string; label: string; kind: 'text' | 'date' | 'money' | 'integer' | 'ratio' | 'boolean' | 'list' | 'classes' | 'select'; optional?: boolean; choices?: string[]; fallback?: string | boolean };
export const policyTypes: Record<string, string> = { recurring_obligation: '周期义务', living_reserve: '生活备用', emergency_buffer: '应急金', goal_saving: '目标储蓄', asset_authorization: '资产自主配置' };
export const lifecycleLabels: Record<string, string> = { ACTIVE: '生效中', CONFIRMED: '已确认，等待生效', SUSPENDED: '已暂停', EXPIRED: '已到期', REVOKED: '已撤销', PROPOSED: '待确认', INVALIDATED: '已失效' };

export function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
export function readPath(config: Configuration, path: string): unknown {
  let value: unknown = config;
  for (const key of path.split('.')) value = object(value) ? value[key] : undefined;
  return value;
}
export function setPath(config: Configuration, path: string, value: unknown): Configuration {
  const result = structuredClone(config);
  const keys = path.split('.'); let current = result;
  for (const key of keys.slice(0, -1)) {
    if (!object(current[key])) current[key] = {};
    current = current[key] as Configuration;
  }
  current[keys.at(-1)!] = value; return result;
}
export function parseMoneyInput(value: string): number {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error('金额需填写非负十进制数，最多两位小数');
  const [yuan = '', fraction = ''] = value.split('.');
  const cents = BigInt(yuan) * 100n + BigInt(fraction.padEnd(2, '0'));
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('金额超出可精确表示的整数分范围');
  return Number(cents);
}
export function moneyInput(cents: unknown): string {
  if (!Number.isSafeInteger(cents)) return '';
  const value = BigInt(cents as number);
  return `${value / 100n}.${(value % 100n).toString().padStart(2, '0')}`;
}
const priority: Field[] = [
  { path: 'priority.importance', label: '重要程度（0–100）', kind: 'integer', fallback: '50' },
  { path: 'priority.minimum_cents', label: '最低保护金额（元）', kind: 'money', fallback: '0.00' },
  { path: 'priority.reducible', label: '允许降低承诺', kind: 'boolean', fallback: false },
  { path: 'priority.deferrable', label: '允许延期', kind: 'boolean', fallback: false },
];
export function fieldsFor(config: Configuration): Field[] {
  const common: Field[] = [{ path: 'name', label: '策略名称', kind: 'text', optional: true },
    { path: 'valid_from', label: '开始日期', kind: 'date', optional: true },
    { path: 'valid_until', label: '结束日期（含当天）', kind: 'date', optional: true }];
  let fields: Field[] = [];
  switch (config.type) {
    case 'emergency_buffer': fields = [{ path: 'amount_cents', label: '应急金金额（元）', kind: 'money' }]; break;
    case 'goal_saving': fields = [
      { path: 'target_cents', label: '目标金额（元）', kind: 'money' }, { path: 'deadline', label: '目标截止日期', kind: 'date' },
      ...(['min', 'target', 'max'] as const).map((key) => ({ path: `monthly_contribution.${key}_cents`, label: `每月${{ min: '最低', target: '目标', max: '最高' }[key]}金额（元）`, kind: 'money' as const })),
      ...priority, { path: 'cross_goal_reallocation_allowed', label: '允许跨目标重分配（不代表已执行）', kind: 'boolean', fallback: false },
      { path: 'asset_policy_id', label: '关联资产策略编号', kind: 'text', optional: true }]; break;
    case 'recurring_obligation': {
      const kind = readPath(config, 'amount_rule.kind') ?? 'exact';
      fields = [{ path: 'payee_id', label: '收款对象编号', kind: 'text' },
        { path: 'amount_rule.kind', label: '义务金额规则', kind: 'select', choices: ['exact', 'range', 'bill_balance'], fallback: 'exact' },
        ...(kind === 'range' ? [{ path: 'amount_rule.min_cents', label: '区间最低金额（元）', kind: 'money' as const }, { path: 'amount_rule.max_cents', label: '区间最高金额（元）', kind: 'money' as const }]
          : kind === 'bill_balance' ? [{ path: 'amount_rule.account_id', label: '真实账单账户编号', kind: 'text' as const }]
            : [{ path: 'amount_rule.amount_cents', label: '固定金额（元）', kind: 'money' as const }]),
        { path: 'due_day', label: '每月到期日（1–31）', kind: 'integer' },
        { path: 'prepare_days_before', label: '提前准备天数', kind: 'integer', fallback: '0' },
        { path: 'auto_execute', label: '允许自动执行', kind: 'boolean', fallback: false }, ...priority]; break;
    }
    case 'living_reserve': fields = [
      { path: 'horizon_days', label: '保护窗口天数', kind: 'integer' },
      { path: 'method.name', label: '估计方法', kind: 'select', choices: ['rolling_window_quantile'], fallback: 'rolling_window_quantile' },
      { path: 'method.lookback_days', label: '历史回看天数', kind: 'integer' },
      { path: 'method.quantile', label: '分位比例（大于0，不超过1）', kind: 'ratio' },
      { path: 'method.essential_categories', label: '必要支出分类（逗号分隔）', kind: 'list' },
      { path: 'method.exclude_one_off', label: '排除一次性支出', kind: 'boolean', fallback: true },
      { path: 'extra_buffer_cents', label: '额外缓冲（元）', kind: 'money', fallback: '0.00' },
      { path: 'reconfirm_on_boundary_crossing', label: '越过边界需重新确认', kind: 'boolean', fallback: true }]; break;
    case 'asset_authorization': fields = [
      { path: 'scope', label: '资产授权范围', kind: 'select', choices: ['general_idle_funds', 'goal'] },
      ...(config.scope === 'goal' ? [{ path: 'goal_id', label: '目标编号', kind: 'text' as const }] : []),
      { path: 'allowed_asset_classes', label: '允许资产类别', kind: 'classes', choices: ['CASH', 'CASH_MGMT_T0', 'CASH_MGMT_T1', 'FIXED_DEPOSIT'] },
      { path: 'max_auto_managed_cents', label: '自主配置总额上限（元）', kind: 'money' },
      { path: 'single_action_cap_cents', label: '单次动作上限（元）', kind: 'money' },
      { path: 'max_redemption_delay_days', label: '最长赎回到账天数', kind: 'integer' },
      { path: 'max_lock_days', label: '最长锁定天数', kind: 'integer' },
      { path: 'max_principal_risk_level', label: '本金风险等级上限（0–5）', kind: 'integer', fallback: '0' },
      { path: 'allow_auto_recovery_without_penalty', label: '允许无损自动恢复', kind: 'boolean', fallback: false },
      { path: 'allow_early_withdrawal_with_penalty', label: '允许有损提前退出', kind: 'boolean', fallback: false }]; break;
  }
  return [...common, ...fields];
}
export function formValues(config: Configuration): Record<string, string | boolean> {
  return Object.fromEntries(fieldsFor(config).map((field) => {
    const value = readPath(config, field.path);
    return [field.path, value == null ? field.fallback ?? '' : field.kind === 'money' ? moneyInput(value)
      : field.kind === 'boolean' ? value === true : Array.isArray(value) ? value.join(', ') : String(value)];
  }));
}
export function buildConfiguration(base: Configuration, values: Record<string, string | boolean>): Configuration {
  let config: Configuration = structuredClone(base);
  const amountKind = values['amount_rule.kind'];
  if (amountKind && amountKind !== readPath(config, 'amount_rule.kind')) config.amount_rule = { kind: amountKind };
  if (values.scope && values.scope !== config.scope) config.scope = values.scope;
  if (config.type === 'asset_authorization' && config.scope === 'general_idle_funds') config.goal_id = null;
  for (const field of fieldsFor(config)) {
    const input = values[field.path] ?? field.fallback ?? '';
    let value: unknown = input;
    if (field.kind === 'boolean') value = input === true;
    else if (field.optional && input === '') value = null;
    else {
      if (typeof input !== 'string' || !input.trim()) throw new Error(`${field.label}：待补齐`);
      if (field.kind === 'money') value = parseMoneyInput(input);
      else if (field.kind === 'integer') {
        if (!/^\d+$/.test(input) || !Number.isSafeInteger(Number(input))) throw new Error(`${field.label}：需要非负整数`);
        value = Number(input);
      } else if (field.kind === 'ratio') {
        if (!/^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(input) || Number(input) <= 0) throw new Error(`${field.label}：比例范围不符`);
        value = Number(input);
      } else if (field.kind === 'list' || field.kind === 'classes') value = input.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
      else value = input.trim();
    }
    config = setPath(config, field.path, value);
  }
  return config;
}
export function validateForm(config: Configuration): string[] {
  const issues: string[] = [];
  if (!Object.hasOwn(policyTypes, String(config.type))) issues.push('策略类型未识别');
  const minimum = readPath(config, 'monthly_contribution.min_cents');
  const target = readPath(config, 'monthly_contribution.target_cents');
  const maximum = readPath(config, 'monthly_contribution.max_cents');
  if (config.type === 'goal_saving' && !(typeof minimum === 'number' && typeof target === 'number' && typeof maximum === 'number' && minimum <= target && target <= maximum)) issues.push('月度贡献需满足最低 ≤ 目标 ≤ 最高');
  if (config.type === 'goal_saving' && !(typeof config.target_cents === 'number' && config.target_cents > 0)) issues.push('目标金额必须大于零');
  if (typeof config.valid_from === 'string' && typeof config.valid_until === 'string' && config.valid_from > config.valid_until) issues.push('结束日期不得早于开始日期');
  return issues;
}
