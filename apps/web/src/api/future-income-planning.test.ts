import { webcrypto } from 'node:crypto';
import { beforeEach, expect, test, vi } from 'vitest';
import { futureEpoch, futureUser, futureCandidateFixture, futureConfirmationFixture, futureCandidateBody, futureConfirmBody, futureLookupFixture, futurePlanningFixture, futureUnknownFixture } from '../tests/future-income-planning-fixture';
import { futureOriginalJson, isFreshFutureIncomeLookup, lookupFutureIncomeCommand, parseFutureIncomeBody, parseFutureIncomeCandidate, parseFutureIncomeConfirmation, parseFutureIncomeLookup, parseFutureIncomePlanning, futureRequestHash } from './future-income-planning';
beforeEach(() => vi.stubGlobal('crypto', webcrypto));
test('真实严格四/五字段请求，无客户金额、时钟、角色或结果', () => {
  expect(parseFutureIncomeBody(futureCandidateBody(), 'CANDIDATE')).toEqual(futureCandidateBody());
  expect(parseFutureIncomeBody(futureConfirmBody(), 'CONFIRM')).toEqual(futureConfirmBody());
  for (const changes of [{ amount_cents: 1 }, { now: 'fake' }, { role: 'USER' }, { result: {} }, { expected_origin_hash: 'bad' }, { idempotency_key: ' ' }, { idempotency_key: 'a'.repeat(121) }]) expect(() => parseFutureIncomeBody({ ...futureCandidateBody(), ...changes }, 'CANDIDATE')).toThrow();
  expect(() => parseFutureIncomeBody({ ...futureConfirmBody(), accepted: 1 }, 'CONFIRM')).toThrow();
});
test('与原Python synthetic DTO相同的source/request/assumption/metadata原hash，只确认条件不授权', async () => {
  const candidate = await parseFutureIncomeCandidate(futureCandidateFixture(), futureUser, futureEpoch);
  expect(candidate.source.origin_hash).toBe(await futureRequestHash(candidate.source.origin));
  const confirmation = await parseFutureIncomeConfirmation(futureConfirmationFixture(candidate), candidate);
  expect(confirmation.confirms_financial_action).toBe(false); expect(candidate.assumption.included_in_execution_cents).toBe(0);
});
test.each(['owner', 'origin', 'metadata', 'clock', 'window', 'authority', 'salary'] as const)('候选%s原件或权限旗不一致拒绝', async kind => {
  const value = futureCandidateFixture();
  if (kind === 'owner') value.user_id = futureEpoch;
  if (kind === 'origin') value.source.origin.amount_cents++;
  if (kind === 'metadata') value.original_evidence.status = 'SUPERSEDED';
  if (kind === 'clock') value.original_evidence.observed_at = '2026-10-06T01:00:01Z';
  if (kind === 'window') value.assumption.valid_from = '2026-99-01';
  if (kind === 'authority') Object.assign(value, { grants_authority: true });
  if (kind === 'salary') Object.assign(value.source, { implies_recurring_salary: true });
  await expect(parseFutureIncomeCandidate(value, futureUser, futureEpoch)).rejects.toThrow();
});
test('确认需原候选完整snapshot及自身hash，不能别人的候选或口头接受替代', async () => {
  const c = futureCandidateFixture(), value = futureConfirmationFixture(c);
  (value.original_evidence.content as Record<string, unknown>).original_candidate_snapshot = {};
  await expect(parseFutureIncomeConfirmation(value, c)).rejects.toThrow();
  const declined = futureConfirmationFixture(c); Object.assign(declined.original_request, { accepted: false });
  await expect(parseFutureIncomeConfirmation(declined, c)).rejects.toThrow();
});
test('365完整条件日程与短月末，未登记和UNKNOWN全部null，不扩今日金额', async () => {
  const known = await parseFutureIncomePlanning(futurePlanningFixture(true), futureUser, futureEpoch);
  expect(known.daily_schedule).toHaveLength(365); expect(known.daily_schedule.find(d => d.date === '2027-02-28')!.conditional_income_cents).toBe(231007);
  expect(known.included_in_current_cash_cents).toBe(0); expect(known.included_in_execution_cents).toBe(0);
  const empty = await parseFutureIncomePlanning(futurePlanningFixture(), futureUser, futureEpoch), unknown = await parseFutureIncomePlanning(futureUnknownFixture(), futureUser, futureEpoch);
  expect(empty.total_conditional_income_cents).toBeNull(); expect(unknown.daily_schedule.every(d => d.conditional_income_cents === null)).toBe(true);
});
test.each(['truncated', 'amount', 'day', 'shift', 'duplicate', 'today', 'unsafe', 'null-zero'] as const)('规划%s分母/金额/条件日期/权限篡改拒绝', async kind => {
  const v = kind === 'null-zero' ? futureUnknownFixture() : futurePlanningFixture(true);
  if (kind === 'truncated') v.daily_schedule.pop();
  if (kind === 'amount') v.daily_schedule[0]!.conditional_income_cents = 1;
  if (kind === 'day') v.daily_schedule[0]!.date = '2026-10-08';
  if (kind === 'shift') { const i = v.daily_schedule.findIndex(d => d.conditional_income_cents! > 0); Object.assign(v.daily_schedule[i - 1]!, { conditional_income_cents: v.daily_schedule[i]!.conditional_income_cents, candidate_ids: v.daily_schedule[i]!.candidate_ids }); Object.assign(v.daily_schedule[i]!, { conditional_income_cents: 0, candidate_ids: [] }); }
  if (kind === 'duplicate') v.plans.push(structuredClone(v.plans[0]!));
  if (kind === 'today') Object.assign(v, { included_in_current_cash_cents: 231007 });
  if (kind === 'unsafe') v.total_conditional_income_cents = Number.MAX_SAFE_INTEGER + 1;
  if (kind === 'null-zero') v.daily_schedule[0]!.conditional_income_cents = 0;
  await expect(parseFutureIncomePlanning(v, futureUser, futureEpoch)).rejects.toThrow();
});
test('缺当前epoch的原UNKNOWN可诚实读取，不能创建当前轮次绑定', async () => { const value = futureUnknownFixture(); value.epoch_id = null; value.sources.epoch_id = null; expect((await parseFutureIncomePlanning(value, futureUser, futureEpoch)).epoch_id).toBeNull(); });
test('同originID却当前source hash变化不能沿用旧条件，保原metadata；未来发生/知悉也不借旧受理', async () => {
  const v = futurePlanningFixture(true), retained = structuredClone(v.plans[0]!.original_metadata);
  v.sources.sources[0]!.origin.amount_cents++;
  v.sources.sources[0]!.origin_hash = await futureRequestHash(v.sources.sources[0]!.origin);
  await expect(parseFutureIncomePlanning(v, futureUser, futureEpoch)).rejects.toThrow(); expect(v.plans[0]!.original_metadata).toEqual(retained);
  const c = futureCandidateFixture();
  // Rebind every declared hash and original payload before testing the sole
  // source-time mismatch; an unrelated stale hash must not make this test red.
  const bind = async () => {
    c.source.origin_hash = await futureRequestHash(c.source.origin); c.original_request.expected_origin_hash = c.source.origin_hash;
    c.assumption.origin_hash = c.source.origin_hash; c.candidate_hash = await futureRequestHash(c.assumption); c.request_hash = await futureRequestHash(c.original_request);
    const content = c.original_evidence.content as Record<string, unknown>;
    Object.assign(content, { source: structuredClone(c.source), original_request: structuredClone(c.original_request), assumption: structuredClone(c.assumption), request_hash: c.request_hash, candidate_hash: c.candidate_hash });
    c.evidence_hash = c.original_evidence.content_hash = await futureRequestHash(content);
  };
  await bind(); await expect(parseFutureIncomeCandidate(c, futureUser, futureEpoch)).resolves.toBe(c);
  c.source.origin.observed_at = '2026-10-06T01:00:01Z'; await bind();
  await expect(parseFutureIncomeCandidate(c, futureUser, futureEpoch)).rejects.toThrow();
});
test('RECORDED查原键完整body/hash，NOT_FOUND非终局且plain JSON没有freshGET标记', async () => {
  const body = futureCandidateBody(), original = futureLookupFixture('CANDIDATE', body);
  expect((await parseFutureIncomeLookup(original, futureUser, futureEpoch, body.idempotency_key)).request_hash).toBe(await futureRequestHash(body)); expect(isFreshFutureIncomeLookup(original)).toBe(false);
  const empty = await parseFutureIncomeLookup(futureLookupFixture('CANDIDATE', body, undefined, true), futureUser, futureEpoch, body.idempotency_key);
  expect(empty.replacement_allowed).toBe(false);
  const raw = JSON.stringify(original); vi.stubGlobal('fetch', vi.fn(async () => new Response(raw)));
  const read = await lookupFutureIncomeCommand(futureUser, futureEpoch, body.idempotency_key); expect(isFreshFutureIncomeLookup(read)).toBe(true); expect(futureOriginalJson(read)).toBe(raw);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/commands/${futureEpoch}/by-key/${body.idempotency_key}`), expect.objectContaining({ method: 'GET' }));
});
