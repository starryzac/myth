import { afterEach, expect, test, vi } from 'vitest';
import { getFullTemplateCatalog, getFullTemplateSchema, getOriginalFullPolicyResponse, lookupFullPolicyCommand, parseFullCommands, parseFullLookup, parseFullPolicies, parseFullPreview, parseFullReceipt, parseFullVersions, sendFullPolicyCommand, validateFullPolicyCandidate } from './full-policies';
import { createFullIntent, fullCandidateFixture, fullCatalogFixture, fullCommandFixture, fullFlags, fullLookupFixture, fullPolicyFixture, fullPolicyId, fullPreviewFixture, fullReceiptFixture, fullSchemaFixture, fullVersionId, stateFullIntent } from '../tests/full-policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('完整版本/命令连续链保原identity与hash，断序/跨策略/伪当前权限拒绝', () => {
  const policy = fullPolicyFixture(); expect(parseFullPolicies({ ...fullFlags, items: [policy] }).items[0]!.policy_id).toBe(fullPolicyId); expect(parseFullVersions({ ...fullFlags, items: [policy.current_version] }, fullPolicyId).items).toHaveLength(1); expect(parseFullCommands({ ...fullFlags, items: [fullCommandFixture()] }, fullPolicyId).items).toHaveLength(1);
  expect(() => parseFullPolicies({ ...fullFlags, items: [policy, policy] })).toThrow(); expect(() => parseFullVersions({ ...fullFlags, items: [{ ...policy.current_version, previous_hash: 'a'.repeat(64) }] }, fullPolicyId)).toThrow(); expect(() => parseFullCommands({ ...fullFlags, items: [{ ...fullCommandFixture(), command_number: 2 }] }, fullPolicyId)).toThrow(); expect(() => parseFullReceipt({ ...fullReceiptFixture(), receipt_is_current_authority: true })).toThrow();
});
test('历史版本可保原证据缺行状态，但不能把ARCHIVED当当前规划授权', () => {
  const policy = fullPolicyFixture(); policy.current_version.confirmation_evidence_status = 'RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING'; policy.effective_status = 'ARCHIVED'; policy.reference_validation = 'ARCHIVED'; policy.planning_confirmation_valid = false; expect(parseFullPolicies({ ...fullFlags, items: [policy] }).items).toHaveLength(1); policy.planning_confirmation_valid = true; expect(() => parseFullPolicies({ ...fullFlags, items: [policy] })).toThrow();
});
test('只读preview严格原version/before配置/金融null，未实现不是0', () => {
  const original = fullPreviewFixture(); expect(parseFullPreview(original, fullPolicyFixture()).delta_goal_allocation_cents).toBeNull(); expect(() => parseFullPreview({ ...original, delta_goal_allocation_cents: 0 }, fullPolicyFixture())).toThrow(); expect(() => parseFullPreview({ ...original, expected_version_id: '71000000-0000-4000-8000-000000000099' }, fullPolicyFixture())).toThrow(); expect(() => parseFullPreview({ ...original, before_configuration: {} }, fullPolicyFixture())).toThrow();
});
test('lookup NOT_FOUND保未终结语义，伪final或错误命令不成为恢复成功', () => {
  const intent = createFullIntent(); expect(parseFullLookup(fullLookupFixture(intent, false), intent).status).toBe('NOT_FOUND'); expect(() => parseFullLookup({ ...fullLookupFixture(intent, false), not_found_is_final: true }, intent)).toThrow(); expect(() => parseFullLookup({ ...fullLookupFixture(intent), request_hash: 'd'.repeat(64) }, intent)).toThrow();
});
test('by-key完整encode斜线，写入发送原DTO/body字节一次且回执不代替lookup', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-api.local'); const intent = stateFullIntent('RESUME'); const calls: { input: string; method: string; body?: BodyInit | null }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => { calls.push({ input: String(input), method: init?.method ?? 'GET', body: init?.body }); return new Response(JSON.stringify(init?.method === 'POST' ? fullReceiptFixture(intent) : fullLookupFixture(intent))); }));
  await sendFullPolicyCommand(intent); const lookup = await lookupFullPolicyCommand(intent); expect(calls).toHaveLength(2); expect(calls[0]!.body).toBe(intent.body_json); expect(calls[1]!.input).toContain(encodeURIComponent(intent.body.idempotency_key)); expect(calls[1]!.input).not.toContain('?'); expect(getOriginalFullPolicyResponse(lookup)).toBe(JSON.stringify(fullLookupFixture(intent)));
});
test('目录/Schema没有simulation仍必须候选且无授权，fraction合法而不安全金额拒绝', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify(init?.method === 'POST' ? { ...fullCandidateFixture(), template_name: 'SeasonalReservePolicy', normalized_configuration: { quantile: .95 } } : String(input).includes('/schema?') ? fullSchemaFixture() : fullCatalogFixture()))));
  expect((await getFullTemplateCatalog()).templates).toHaveLength(12); expect((await getFullTemplateSchema('DatedExpensePolicy')).schema_sha256).toHaveLength(64); expect((await validateFullPolicyCandidate('SeasonalReservePolicy', { quantile: .95 })).authority_granted).toBe(false);
  expect(() => validateFullPolicyCandidate('DatedExpensePolicy', { amount_cents: Number.MAX_SAFE_INTEGER + 1 })).toThrow();
});
test('模板候选返回wrong template或伪authority时拒绝，不自动转确认POST', async () => {
  const bad = { ...fullCandidateFixture(), authority_granted: true }; vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(bad)))); await expect(validateFullPolicyCandidate('DatedExpensePolicy', {})).rejects.toThrow(); expect(fetch).toHaveBeenCalledTimes(1);
});
test('暂停body无accepted/configuration；恢复不能同旧version回执假成功', () => {
  const stop = stateFullIntent('SUSPEND'); expect(Object.keys(stop.body).sort()).toEqual(['expected_version_id', 'idempotency_key', 'reason']); expect(() => parseFullReceipt({ ...fullReceiptFixture(stateFullIntent('RESUME')), version_id: fullVersionId }, stateFullIntent('RESUME'))).toThrow();
});
