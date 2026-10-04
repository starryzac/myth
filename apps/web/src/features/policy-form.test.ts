import { expect, test } from 'vitest';
import { buildConfiguration, fieldsFor, formValues, parseMoneyInput, readPath, setPath, validateForm } from './policy-form';

test('金额输入保留精确分，空值/小数过多/不安全整数拒绝而不舍入', () => {
  expect(parseMoneyInput('0.01')).toBe(1);
  expect(parseMoneyInput('90071992547409.90')).toBe(9007199254740990);
  for (const value of ['', ' ', '-1', '1.001', '1e2', '90071992547409.92']) {
    expect(() => parseMoneyInput(value)).toThrow();
  }
});

test('五类DSL字段完整往返，义务金额规则与资产scope切换不残留错误字段', () => {
  const configurations = [
    { type: 'emergency_buffer', amount_cents: 12345 },
    { type: 'goal_saving', target_cents: 120000, deadline: '2027-01-01', monthly_contribution: { min_cents: 0, target_cents: 10000, max_cents: 20000 } },
    { type: 'recurring_obligation', payee_id: 'rent', amount_rule: { kind: 'exact', amount_cents: 12345 }, due_day: 31 },
    { type: 'living_reserve', horizon_days: 30, method: { name: 'rolling_window_quantile', lookback_days: 60, quantile: 0.9, essential_categories: ['food'], exclude_one_off: true } },
    { type: 'asset_authorization', scope: 'goal', goal_id: 'goal-id', allowed_asset_classes: ['CASH', 'FIXED_DEPOSIT'], max_auto_managed_cents: 20000, single_action_cap_cents: 10000, max_redemption_delay_days: 1, max_lock_days: 30 },
  ];
  for (const configuration of configurations) {
    const values = formValues(configuration); const result = buildConfiguration(configuration, values);
    expect(result).toMatchObject(configuration); expect(fieldsFor(result).every((field) => readPath(result, field.path) !== undefined)).toBe(true);
  }
  const recurring = configurations[2]!;
  const range = buildConfiguration(recurring, { ...formValues(recurring), 'amount_rule.kind': 'range', 'amount_rule.min_cents': '1.00', 'amount_rule.max_cents': '2.00' });
  expect(range.amount_rule).toEqual({ kind: 'range', min_cents: 100, max_cents: 200 });
  const asset = configurations[4]!;
  expect(buildConfiguration(asset, { ...formValues(asset), scope: 'general_idle_funds' }).goal_id).toBeNull();
});

test('嵌套编辑保留原配置并校验月度范围，不把空白当0', () => {
  const original = { type: 'goal_saving', target_cents: 100, deadline: '2026-12-01',
    monthly_contribution: { min_cents: 0, target_cents: 50, max_cents: 100 } };
  const changed = setPath(original, 'monthly_contribution.min_cents', 60);
  expect(readPath(original, 'monthly_contribution.min_cents')).toBe(0);
  expect(readPath(changed, 'monthly_contribution.min_cents')).toBe(60);
  expect(validateForm(changed).some((issue) => issue.includes('月度'))).toBe(true);
});
