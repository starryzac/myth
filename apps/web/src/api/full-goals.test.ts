import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { confirmFullGoalModel, getFullGoalModel, getOriginalFullGoalResponse, lookupFullGoalCommand, parseFullGoalCommandLookup, parseFullGoalConfiguration, parseFullGoalModel, parseFullGoalPreview, parseFullGoalReceipt, previewFullGoalModel } from './full-goals';
import { fullConfiguration, fullGoalIntentFixture, fullGoalLookupFixture, fullGoalReceiptFixture, fullModelFixture, fullPreviewFixture, modelGoal, otherGoalId, versionId } from '../tests/full-goal-fixture';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('原模型GET身份/原文本保留；候选只POST只读preview，body不含confirm授权/资金键', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const calls: { url: string; method: string; body: unknown }[] = []; const original = ` \n${JSON.stringify(fullModelFixture())}\n`;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => { calls.push({ url: String(input), method: options?.method ?? 'GET', body: options?.body ? JSON.parse(String(options.body)) : undefined }); return new Response(options?.method === 'POST' ? JSON.stringify(fullPreviewFixture()) : original); }));
  const model = await getFullGoalModel(modelGoal); const preview = await previewFullGoalModel(modelGoal, { ...fullConfiguration() });
  expect(getOriginalFullGoalResponse(model)).toBe(original); expect(preview.preview_only).toBe(true); expect(calls).toEqual([{ url: `http://http-unit-fixture.local/api/v1/goals/${modelGoal.id}/full-model`, method: 'GET', body: undefined }, { url: `http://http-unit-fixture.local/api/v1/goals/${modelGoal.id}/full-model/preview`, method: 'POST', body: { expected_version_id: versionId, configuration: fullConfiguration() } }]);
});
test('缺MODEL保持NULL/UNKNOWN，不接受遗留full配置或伪权限', () => {
  expect(parseFullGoalModel(fullModelFixture(true), modelGoal).full_configuration).toBeNull();
  for (const patch of [{ full_configuration: fullConfiguration() }, { bank_authority: true }, { dedicated_audit_event: true }, { evidence_id: otherGoalId }]) expect(() => parseFullGoalModel({ ...fullModelFixture(true), ...patch }, modelGoal)).toThrow('校验');
});
test('版本/目标/策略身份漂移、反转日期、错monthly/跨目标回拨及不精确金额拒绝', () => {
  for (const patch of [{ goal_id: otherGoalId }, { policy_id: otherGoalId }, { base_policy_version_id: otherGoalId }]) expect(() => parseFullGoalModel({ ...fullModelFixture(), ...patch }, modelGoal)).toThrow('校验');
  for (const patch of [{ target_cents: Number.MAX_SAFE_INTEGER + 1 }, { valid_from: '2028-01-01' }, { deadline: '2027-02-30' }, { cross_goal_reallocation_allowed: true, cross_goal_reallocation_policy_id: otherGoalId }, { allow_deferral: false, deferral_cost_cents_per_day: 101 }, { monthly_contribution: { min_cents: 30007, target_cents: 20003, max_cents: 10001 } }]) expect(() => parseFullGoalConfiguration({ ...fullConfiguration(), ...patch })).toThrow();
});
test('双hash桥、完整配置→base投影及impact绑定保持；字典顺序不作为金融差异', () => {
  const source = fullPreviewFixture(); source.base_policy_impact.configuration = Object.fromEntries(Object.entries(source.base_configuration).reverse()); expect(parseFullGoalPreview(source, modelGoal).grants_authority).toBe(false);
  for (const mutate of [
    (value: ReturnType<typeof fullPreviewFixture>) => { value.base_configuration_hash = 'a'.repeat(64); },
    (value: ReturnType<typeof fullPreviewFixture>) => { value.base_configuration.target_cents = 1; },
    (value: ReturnType<typeof fullPreviewFixture>) => { value.base_policy_impact.policy_id = otherGoalId; },
    (value: ReturnType<typeof fullPreviewFixture>) => { value.base_policy_impact.expected_version_id = otherGoalId; },
    (value: ReturnType<typeof fullPreviewFixture>) => { value.grants_authority = true as never; },
    (value: ReturnType<typeof fullPreviewFixture>) => { value.extra_fields_in_base_impact = true as never; },
  ]) { const value = fullPreviewFixture(); mutate(value); expect(() => parseFullGoalPreview(value, modelGoal)).toThrow('校验'); }
});
test('非法ID/unsafe候选预联网拒绝；原STALE_POLICY_VERSION不重试或confirm', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'STALE_POLICY_VERSION', message: '原版本已变', request_id: 'UNIT-MODEL-STALE' } }), { status: 409 })); vi.stubGlobal('fetch', fetch);
  expect(() => getFullGoalModel({ ...modelGoal, id: '../confirm' })).toThrow('校验'); expect(() => previewFullGoalModel(modelGoal, { ...fullConfiguration(), target_cents: Number.MAX_SAFE_INTEGER + 1 })).toThrow(); expect(fetch).not.toHaveBeenCalled();
  await expect(previewFullGoalModel(modelGoal, { ...fullConfiguration() })).rejects.toMatchObject({ code: 'STALE_POLICY_VERSION', requestId: 'UNIT-MODEL-STALE' }); expect(fetch).toHaveBeenCalledTimes(1);
});
test('原正式确认精确canonical请求与双hash；原键完整编码GET无query/HTTP重试', async () => {
  const intent = await fullGoalIntentFixture(); const receipt = fullGoalReceiptFixture(intent); const original = ` \n${JSON.stringify(fullGoalLookupFixture(intent))}\n`; vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-goal-api.local'); vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(init?.method === 'POST' ? JSON.stringify(receipt) : original))); await confirmFullGoalModel(intent); const lookup = await lookupFullGoalCommand(intent); const calls = vi.mocked(fetch).mock.calls;
  expect(calls).toHaveLength(2); expect(calls[0]![1]!.body).toBe(intent.body_json); expect(calls[1]![1]!.method).toBe('GET'); expect(String(calls[1]![0])).toBe(`http://unit-full-goal-api.local/api/v1/goals/${intent.goal_id}/full-model/commands/by-key/${encodeURIComponent(intent.body.idempotency_key)}`); expect(getOriginalFullGoalResponse(lookup)).toBe(original); expect(lookup.record!.receipt.receipt_is_current_authority).toBe(false);
});
test('confirmation receipt目标/policy/prev-newversion/epoch/双hash/配置/UNKNOWN/授权错误拒绝', async () => {
  const intent = await fullGoalIntentFixture(); const source = fullGoalReceiptFixture(intent); expect(parseFullGoalReceipt(source, intent).lifecycle.previous_version_id).toBe(intent.body.expected_version_id);
  for (const patch of [{ goal_id: otherGoalId }, { epoch_id: otherGoalId }, { full_configuration_hash: '0'.repeat(64) }, { base_configuration_hash: '0'.repeat(64) }, { bank_authority: true }, { receipt_is_current_authority: true }, { full_configuration: { ...source.full_configuration, target_cents: 1 } }, { lifecycle: { ...source.lifecycle, current_version_id: intent.body.expected_version_id } }, { lifecycle: { ...source.lifecycle, policy_id: otherGoalId } }, { lifecycle: { ...source.lifecycle, previous_version_id: otherGoalId } }, { lifecycle: { ...source.lifecycle, status: 'UNKNOWN' } }]) expect(() => parseFullGoalReceipt({ ...source, ...patch }, intent)).toThrow('校验');
});
test('查询非终局NOT_FOUND必须无record，RECORDED具体原body/hash/历史replay须匹配', async () => {
  const intent = await fullGoalIntentFixture(); expect(parseFullGoalCommandLookup(fullGoalLookupFixture(intent, false), intent).record).toBeNull(); const source = fullGoalLookupFixture(intent); for (const patch of [{ not_found_is_final: true }, { status: 'NOT_FOUND' }, { record: { ...source.record, request_hash: '0'.repeat(64) } }, { record: { ...source.record, receipt: { ...source.record!.receipt, idempotent_replay: false } } }, { record: { ...source.record, epoch_archive_verified: true } }]) expect(() => parseFullGoalCommandLookup({ ...source, ...patch }, intent)).toThrow('校验');
});
