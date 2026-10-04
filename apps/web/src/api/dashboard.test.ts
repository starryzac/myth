import { expect, test } from 'vitest';
import { parseDashboard } from './dashboard';
import { dashboardFixture } from '../tests/dashboard-fixture';

test('真实合同形状的单元fixture仅通过解析，不代表金融验收', () => {
  const fixture = dashboardFixture();
  expect(parseDashboard(fixture)).toBe(fixture);
});

test.each([
  null, {}, { schema_version: 'dashboard-v1', simulation: true },
  { ...dashboardFixture(), as_of: 'invalid' },
  { ...dashboardFixture(), schema_version: 'other-v1' },
  { ...dashboardFixture(), account_facts: { facts: {} } },
  { ...dashboardFixture(), pending_actions: { state: 'PROVEN', total: -1, items: [], list_complete: true, has_more: false } },
  { ...dashboardFixture(), intervention: { status: 'INVALID', complete: true, reason_codes: [] } },
  { ...dashboardFixture(), account_facts: { ...dashboardFixture().account_facts, facts: {
    ...dashboardFixture().account_facts.facts, cash_balance_cents: undefined } } },
  { ...dashboardFixture(), next_obligations: { ...dashboardFixture().next_obligations, items: [null] } },
  { ...dashboardFixture(), pending_actions: { ...dashboardFixture().pending_actions, items: [{}] } },
])('拒绝不完整或不支持的响应 %#', (value) => {
  expect(() => parseDashboard(value)).toThrow();
});
