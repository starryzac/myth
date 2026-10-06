import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cashOriginalJson, parseCashAction, parseCashCandidate, parseCashExecute, parseCashIntent, parseCashLookup, parseCashPrepare, readCashIntent } from './goal-cash-releases';
import { cashActionFixture, cashCandidateFixture, cashIntentFixture, cashLookupFixture } from '../tests/goal-cash-release-fixture';
import { releaseHash } from './goal-release-authorizations';
beforeEach(() => vi.stubGlobal('crypto', webcrypto)); afterEach(() => { vi.unstubAllGlobals(); });
test('原prepare九身份无金融输入；execute只有明确True+原效果+原epoch', async () => {
  const intent = await cashIntentFixture(); expect(parseCashPrepare(intent.body)).toEqual(intent.body); expect(await parseCashIntent(intent)).toEqual(intent);
  for (const field of ['amount_cents', 'bank_facts', 'now', 'accepted', 'grant']) expect(() => parseCashPrepare({ ...intent.body, [field]: true })).toThrow();
  const action = await cashActionFixture(intent); const body = { accepted: true, reviewed_effect_hash: action.original_command.effect_hash, expected_epoch_id: intent.body.expected_epoch_id };
  expect(parseCashExecute(body)).toEqual(body); for (const accepted of [1, false, 'true', null]) expect(() => parseCashExecute({ ...body, accepted })).toThrow(); expect(() => parseCashExecute({ ...body, amount_cents: 1 })).toThrow(); expect(() => parseCashExecute({})).toThrow();
});
test('READY与UNKNOWN保原JSON；缺原件金额null不补0', async () => {
  const intent = await cashIntentFixture(); const value = await cashCandidateFixture(intent); const raw = JSON.stringify(value, null, 2); expect((await parseCashCandidate(JSON.parse(raw), intent, raw)).state).toBe('READY'); expect(cashOriginalJson(value)).toBeNull();
  const unknown = await cashCandidateFixture(intent, true); expect((await parseCashCandidate(unknown, intent)).amount_cents).toBeNull(); unknown.amount_cents = 0; await expect(parseCashCandidate(unknown, intent)).rejects.toThrow();
});
test.each(['owner', 'epoch', 'body', 'three-count', 'protection', 'cap', 'unknown-source', 'floats'])('原预览%s拒绝，不信READY字符串', async (field) => {
  const intent = await cashIntentFixture(), value = await cashCandidateFixture(intent);
  if (field === 'owner') value.user_id = value.epoch_id; if (field === 'epoch') value.epoch_id = value.user_id; if (field === 'body') value.original_request.idempotency_key = 'other';
  if (field === 'three-count') value.actual_inventory.inventory[0]!.captured_count = 1;
  if (field === 'protection') value.protection!.compared_point_count = 1097; if (field === 'cap') value.actual_inventory.policy_usage.cap_occupied_cents = 70001;
  if (field === 'unknown-source') value.actual_inventory.state = 'UNKNOWN'; if (field === 'floats') value.selected_release_uses[0]!.amount_cents = 1.5;
  await expect(parseCashCandidate(value, intent)).rejects.toThrow();
});
test.each(['effect', 'requesthash', 'body', 'income', 'confirmation', 'receipt', 'posting', 'transaction', 'fee', 'fake-success'])('终态%s不能解除完整原请求', async (field) => {
  const intent = await cashIntentFixture(), old = await cashActionFixture(intent), value = await cashActionFixture(intent, 'SUCCEEDED');
  if (field === 'effect') value.original_command.effect.amount_cents++; if (field === 'requesthash') value.original_request_hash = 'a'.repeat(64); if (field === 'body') value.original_prepare_request.idempotency_key = 'new';
  if (field === 'income') value.original_command.effect.available_income_increase_cents = 1 as never; if (field === 'confirmation') value.action_confirmation_evidence_id = intent.body.policy_id;
  if (field === 'receipt') value.original_receipt!.action_plan_id = intent.body.policy_id;
  if (field === 'posting') (value.original_receipt!.response as { posting_ids: string[] }).posting_ids.pop();
  if (field === 'transaction') (value.original_receipt!.response as { transaction_ids: string[] }).transaction_ids[0] = intent.body.policy_id;
  if (field === 'fee') value.original_receipt!.fee_cents = 1; if (field === 'fake-success') { value.service_receipt_verified = false; value.original_receipt = null; value.receipt_id = null; }
  await expect(parseCashAction(value, intent, old)).rejects.toThrow();
});
test('原client intent hash与server封套hash分列；UNKNOWN银行已结算但无receipt不假终态', async () => {
  const intent = await cashIntentFixture(), old = await cashActionFixture(intent), unknown = await cashActionFixture(intent, 'UNKNOWN');
  expect((await parseCashAction(unknown, intent, old)).service_receipt_verified).toBe(false); expect(intent.client_intent_hash).not.toBe(old.original_request_hash);
  await expect(parseCashIntent({ ...intent, client_intent_hash: old.original_request_hash })).rejects.toThrow(); const missing = cashLookupFixture(intent, null); expect((await parseCashLookup(missing, intent)).not_found_is_final).toBe(false);
});
test('已知行动只GET exact action；未知prepare只GET原epoch/key，不附money/query或自动POST', async () => {
  const intent = await cashIntentFixture(), action = await cashActionFixture(intent); const calls: RequestInit[] = []; const paths: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => { calls.push(init); paths.push(url); return new Response(JSON.stringify(url.includes('/by-key/') ? cashLookupFixture(intent, null) : action)); }));
  await readCashIntent(intent, null); await readCashIntent(intent, action); expect(calls.every((c) => c.method === 'GET' && c.body === undefined)).toBe(true); expect(paths[0]).toContain(`/commands/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`); expect(paths[1]).toContain(`/actions/${action.action_id}`);
});
test('自洽的未知effect字段不能扩大冻结协议', async () => {
  const intent = await cashIntentFixture(), value = await cashActionFixture(intent);
  Object.assign(value.original_command.effect, { unexpected_grant: true });
  value.original_command.effect_hash = await releaseHash(value.original_command.effect);
  await expect(parseCashAction(value, intent)).rejects.toThrow();
});
test('READY选择必须来自当前完整inventory中的原allocation×fragment×hash', async () => {
  const intent = await cashIntentFixture(), value = await cashCandidateFixture(intent);
  value.actual_inventory.release_uses_available[0]!.allocation_effect_hash = '9'.repeat(64);
  await expect(parseCashCandidate(value, intent)).rejects.toThrow();
});
