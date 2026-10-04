import { expect, test } from 'vitest';
import { assertMoneyFields, formatMoneyCents } from './money';

test.each([
  [null, '—'], [0, '0.00'], [1, '0.01'], [-1, '-0.01'], [101, '1.01'], [-101, '-1.01'],
  [100000, '1,000.00'], [9007199254740990, '90,071,992,547,409.90'],
  [Number.MAX_SAFE_INTEGER, '90,071,992,547,409.91'],
  [-9007199254740990, '-90,071,992,547,409.90'],
] as const)('整数分格式化 %s → %s', (cents, expected) => {
  expect(formatMoneyCents(cents)).toBe(expected);
});

test.each([Number.MAX_SAFE_INTEGER + 1, 0.1, NaN, Infinity])('拒绝不精确格式化 %s', (cents) => {
  expect(() => formatMoneyCents(cents)).toThrow(/整数分/);
});

test('API金额校验递归覆盖卡片、数组与保护分项，null与0不同', () => {
  expect(() => assertMoneyFields({ safe_idle_cents: null, accounts: [{ balance_cents: 0 }],
    current_protected_cents_by_reason: { obligations: 12345, emergency: 0 } })).not.toThrow();
  expect(() => assertMoneyFields({ items: [{ amount_cents: Number.MAX_SAFE_INTEGER + 1 }] })).toThrow();
  expect(() => assertMoneyFields({ current_protected_cents_by_reason: { emergency: 0.5 } })).toThrow();
  expect(() => assertMoneyFields({ amount_cents: '100' })).toThrow();
  expect(() => assertMoneyFields({ amount_cents: true })).toThrow();
});
