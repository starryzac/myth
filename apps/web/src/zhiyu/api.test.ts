import { expect, test } from 'vitest';
import { parseState, terminal } from './api';
import { zhiyuAction, zhiyuState } from './test-fixtures';
test('unsafe money, wrong user and malformed activity never become displayable state', () => {
  const value = zhiyuState(); value.dashboard.boundary.safe_idle_cents = Number.MAX_SAFE_INTEGER + 1;
  expect(() => parseState(value)).toThrow('金额');
  const other = zhiyuState(); other.actions = [{ ...zhiyuAction(), user_id: '10000000-0000-0000-0000-000000000999' }];
  expect(() => parseState(other)).toThrow();
  const broken = { ...zhiyuState(), activity: [{ id: 'unit', at: 'not-a-time', intent: '收入', authorization: '', decision: '', amount_cents: null, status: 'UNKNOWN', action_id: null }] };
  expect(() => parseState(broken)).toThrow();
});
test('a success label without a settled receipt remains unresolved', () => {
  expect(terminal(zhiyuAction('UNKNOWN'))).toBe(false);
  expect(terminal(zhiyuAction('SUCCEEDED'))).toBe(true);
  const fake = zhiyuAction('SUCCEEDED'); fake.receipt = null;
  expect(terminal(fake)).toBe(false);
});
