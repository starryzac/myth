import { webcrypto } from 'node:crypto';
import { beforeEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { nextBodyFixture, nextForwardedFixture, nextLookupFixture, nextPreviewFixture } from '../tests/full-recovery-next-fixture';
import { recoveryFixtureHash, recoveryUser } from '../tests/full-recovery-execution-fixture';
import { getOriginalRecoveryNextResponse, isOriginalRecoveryNextRead, lookupRecoveryNext, parseRecoveryNextBody, parseRecoveryNextDraft, parseRecoveryNextLookup, parseRecoveryNextPreview, previewRecoveryNext, recoveryNextOriginalIntent, recoveryNextV1Key } from './full-recovery-next';

beforeEach(() => { sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); });
const body = nextBodyFixture();
test('实际generated DTO reader保完整分母/服务端顺序/v1原请求，UTC JSON Z按原Pythonhash核', async () => {
  const value = nextPreviewFixture(); const parsed = await parseRecoveryNextPreview(value, body, recoveryUser, JSON.stringify(value));
  expect(parsed.status).toBe('READY_TO_PREPARE'); expect(parsed.selection!.current_candidate_denominator).toBe(parsed.planning!.plan!.candidates.length); expect(parsed.selection!.next_v1_request).toEqual(nextForwardedFixture()); expect(getOriginalRecoveryNextResponse(parsed)).toBe(JSON.stringify(value)); expect(isOriginalRecoveryNextRead(parsed.existing)).toBe(false);
});
test('root映射仅原root决定，严格body拒绝仓位/金额/clock/role与blank', async () => {
  expect(await recoveryNextV1Key(body.idempotency_key)).toBe(nextForwardedFixture().idempotency_key);
  for (const extra of [{ position_id: nextForwardedFixture().position_id }, { amount_cents: 1 }, { now: '2027-01-01' }, { role: 'USER' }]) expect(() => parseRecoveryNextBody({ ...body, ...extra })).toThrow();
  expect(() => parseRecoveryNextBody({ ...body, idempotency_key: ' ' })).toThrow();
});
test.each(['denominator', 'position', 'fee', 'auto', 'deadline', 'input_hash'])('原%s不一致拒绝，不能把前端候选当资金证明', async (field) => {
  const v = nextPreviewFixture();
  if (field === 'denominator') v.selection!.current_candidate_denominator += 1;
  if (field === 'position') v.selection!.next_v1_request!.position_id = '00000000-0000-0000-0000-000000000999';
  if (field === 'fee') v.original_v1_preview!.proof.candidate.fee_cents = 1;
  if (field === 'auto') (v as unknown as Record<string, unknown>).automatically_advances = true;
  if (field === 'deadline') v.original_v1_preview!.proof.deadline_at = '2027-01-01T00:00:00Z';
  if (field === 'input_hash') v.input_hash = 'a'.repeat(64);
  await expect(parseRecoveryNextPreview(v, body, recoveryUser)).rejects.toThrow();
});
test.each(['UNKNOWN', 'NO_ELIGIBLE'] as const)('%s保null/缺项无假READY', async (state) => { const v = await parseRecoveryNextPreview(nextPreviewFixture(body, state), body, recoveryUser); expect(v.status).not.toBe('READY_TO_PREPARE'); if (state === 'NO_ELIGIBLE') expect(v.selection?.next_v1_request).toBe(null); });
test.each(['principal', 'settlement_delay'])('前后预览%s不一致即使重算外层hash仍拒绝', async (field) => {
  const v = nextPreviewFixture();
  if (field === 'principal') { const candidate = v.planning!.plan!.candidates[0]!; candidate.principal_cents += 1; candidate.net_cents! += 1; candidate.original_quote!.principal_cents += 1; candidate.original_quote!.net_cents += 1; v.planning!.plan!.lossless_steps = [structuredClone(candidate)]; }
  else v.original_v1_preview!.execution_effect!.settlement_delay_days = 1;
  if (field === 'settlement_delay') { const effect = v.original_v1_preview!.execution_effect!; v.original_v1_preview!.proof.effect_hash = recoveryFixtureHash(effect); const { proof_hash: _old, ...proof } = v.original_v1_preview!.proof; void _old; v.original_v1_preview!.proof.proof_hash = recoveryFixtureHash(proof); }
  v.input_hash = recoveryFixtureHash({ protocol: v.protocol, user_id: v.user_id, as_of: v.as_of.replace(/Z$/, '+00:00'), request: body, existing: v.existing, planning: v.planning, selection: v.selection, original_v1_preview: v.original_v1_preview });
  await expect(parseRecoveryNextPreview(v, body, recoveryUser)).rejects.toThrow();
});
test('重复root只核完整原v1Action，无新selection；改当前版本不得替换原boundrequest', async () => {
  const v = await parseRecoveryNextPreview(nextPreviewFixture(body, 'RETAINED'), body, recoveryUser); expect(v.selection).toBe(null); expect(v.original_v1_preview).toBe(null);
  const old = await parseRecoveryNextLookup(nextLookupFixture(body, 'UNKNOWN'), body, recoveryUser); const intent = await recoveryNextOriginalIntent(old); expect(intent.prepare_request).toEqual(nextForwardedFixture(body)); expect(intent.path).toBe('/full-recovery-actions/prepare');
  await expect(parseRecoveryNextLookup(nextLookupFixture(body, 'UNKNOWN'), { ...body, expected_version_id: '00000000-0000-0000-0000-000000000999' }, recoveryUser)).rejects.toThrow();
});
test('真正GET标记与原raw保留，POST/存储parse不能获得；root斜杠严格encoded无query', async () => {
  const calls = installHttpFixture((method) => method === 'GET' ? nextLookupFixture(body, 'PREPARED') : nextPreviewFixture());
  const preview = await previewRecoveryNext(body, recoveryUser); expect(isOriginalRecoveryNextRead(preview.existing)).toBe(false);
  const lookup = await lookupRecoveryNext(body, recoveryUser); expect(isOriginalRecoveryNextRead(lookup)).toBe(true); expect(getOriginalRecoveryNextResponse(lookup)).not.toBe(null); expect(calls[1]!.path).toContain('root-test%2Fone'); expect(calls[1]!.body).toBeUndefined();
  expect(isOriginalRecoveryNextRead(await parseRecoveryNextLookup(JSON.parse(JSON.stringify(lookup)), body, recoveryUser))).toBe(false);
});
test('NOT_FOUND非终局全部原字段null，假独立v2receipt与丢原marker拒绝', async () => {
  const missing = nextLookupFixture(); expect((await parseRecoveryNextLookup(missing, body, recoveryUser)).not_found_is_final).toBe(false);
  missing.bound_request = body; await expect(parseRecoveryNextLookup(missing, body, recoveryUser)).rejects.toThrow();
  const record = nextLookupFixture(body, 'PREPARED'); delete record.original_v1_lookup.original_action_request!.full_recovery_execution; await expect(parseRecoveryNextLookup(record, body, recoveryUser)).rejects.toThrow();
  const forged = nextLookupFixture(body, 'PREPARED'); (forged as unknown as Record<string, unknown>).original_v2_request_separately_recorded = true; await expect(parseRecoveryNextLookup(forged, body, recoveryUser)).rejects.toThrow();
});
test('只读root草稿完整body/hash损坏拒绝，不冒称已发送金融请求', async () => { await expect(parseRecoveryNextDraft({ protocol: 'full-recovery-next-preview-browser-v2', user_id: recoveryUser, body, body_json: JSON.stringify(body), request_hash: 'a'.repeat(64) })).rejects.toThrow(); });
