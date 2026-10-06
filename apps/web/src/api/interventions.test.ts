import { webcrypto } from 'node:crypto';
import { beforeEach, expect, test, vi } from 'vitest';
import { getIntervention, listInterventions, lookupIntervention, parseIntervention, parseInterventionBody, parseInterventionList, parseInterventionLookup, postIntervention, readQuestionObservation, verifyInterventionHash } from './interventions';
import { interventionCurrentObservationFixture, interventionFixture, interventionHashFixture, interventionId, interventionIntentFixture, interventionListFixture, interventionLookupFixture, interventionQuestionFixture, interventionTraceFixture, observeBodyFixture } from '../tests/intervention-fixture';
import { questionSession } from '../tests/question-fixture';
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); vi.stubEnv('VITE_API_BASE_URL', 'http://intervention-reader-unit.local'); });

test('实际原件合同保持非权限；当前、失效、完整库存与固定收件原件分别核验', async () => {
  const current = interventionFixture(true); expect(parseIntervention(current).original_inbox_claim!.actual_human_view_verified).toBe(false); await verifyInterventionHash(current);
  const stale = interventionFixture(); stale.source_status = 'STALE'; stale.current_source_binding = 'UNVERIFIED'; stale.effective_state = 'INVALIDATED'; stale.pending = false; stale.current_question = null; expect(parseIntervention(stale).pending).toBe(false);
  const list = interventionListFixture(); expect(parseInterventionList(list).actual_message_count).toBe(1); list.actual_message_count = 2; expect(() => parseInterventionList(list)).toThrow(); list.presentation_truncated = true; expect(parseInterventionList(list).actual_message_count).toBe(2);
});

test.each(['grant', 'owner', 'epoch', 'consumer', 'hash', 'human', 'time', 'absent', 'question', 'stale-pending'] as const)('收件或消息 %s 不一致不能认作当前原件', (field) => {
  const view = interventionFixture(true); const claim = view.original_inbox_claim!;
  if (field === 'grant') (view as unknown as Record<string, unknown>).authority_granted = true;
  if (field === 'owner') claim.user_id = interventionId; if (field === 'epoch') claim.epoch_id = interventionId;
  if (field === 'consumer') (claim as unknown as Record<string, unknown>).consumer_ref = 'OTHER_CONSUMER';
  if (field === 'hash') claim.payload_hash = '0'.repeat(64); if (field === 'human') (claim as unknown as Record<string, unknown>).actual_human_view_verified = true;
  if (field === 'time') claim.received_at = '2026-10-04T00:00:00Z'; if (field === 'absent') view.original_inbox_claim = null;
  if (field === 'question') view.current_question!.question_id = interventionId; if (field === 'stale-pending') view.source_status = 'STALE';
  expect(() => parseIntervention(view)).toThrow();
});

test.each(['extra', 'clock', 'accepted', 'revision', 'money', 'key'] as const)('恶意原请求 %s 在HTTP前拒绝', (field) => {
  const body: Record<string, unknown> = field === 'accepted' ? { ...interventionIntentFixture('ACKNOWLEDGE').body, acknowledged: 1 } : observeBodyFixture();
  if (field === 'extra') body.bank_authority = true; if (field === 'clock') body.clock = '2026-10-05T00:00:00Z'; if (field === 'revision') body.expected_revision = true; if (field === 'money') body.amount_cents = 0; if (field === 'key') body.idempotency_key = 'bad key';
  expect(() => parseInterventionBody(field === 'accepted' ? 'ACKNOWLEDGE' : 'OBSERVE', body)).toThrow();
});

test('原键返回完整原命令；不同body/hash或未找到带伪回执均拒绝', () => {
  const intent = interventionIntentFixture(); const exact = interventionLookupFixture(intent); expect(parseInterventionLookup(exact, intent).status).toBe('RECORDED');
  (exact.original_receipt!.original_command.request as Record<string, unknown>).expected_revision = 2; expect(() => parseInterventionLookup(exact, intent)).toThrow();
  const different = interventionLookupFixture(intent); different.original_receipt!.request_hash = '0'.repeat(64); expect(() => parseInterventionLookup(different, intent)).toThrow();
  const missing = interventionLookupFixture(intent, false); expect(parseInterventionLookup(missing, intent).status).toBe('NOT_FOUND_NOT_FINAL'); missing.original_receipt = different.original_receipt; expect(() => parseInterventionLookup(missing, intent)).toThrow();
});

test('消息payload更改必须重新算原hash，source CURRENT不能掩盖篡改', async () => {
  const view = interventionFixture(); view.original_message.created_at = '2026-10-05T12:00:01Z'; await expect(verifyInterventionHash(parseIntervention(view))).rejects.toThrow();
  vi.stubGlobal('fetch', vi.fn()); const intent = interventionIntentFixture(); intent.path = '/actions/execute'; await expect(postIntervention(intent)).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});

test('所有恢复为真实GET原id/key无query；观察身份来自当前问答和完整原DecisionTrace', async () => {
  const actual: { method: string; url: URL }[] = []; const intent = interventionIntentFixture();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); actual.push({ method: init?.method ?? 'GET', url }); expect(init?.body).toBeUndefined();
    const value = url.pathname.includes('/by-key/') ? interventionLookupFixture(intent) : url.pathname.includes('/finite-planning/sessions/') ? interventionQuestionFixture() : url.pathname.includes('/decisions/') ? interventionTraceFixture() : url.pathname.endsWith('/interventions') ? interventionListFixture() : interventionFixture(true);
    return new Response(JSON.stringify(value));
  }));
  await getIntervention(interventionId); await lookupIntervention(intent); await listInterventions(); const original = await readQuestionObservation(questionSession, 'TOOL_ONLY_ACTUAL_SOURCE_READ'); expect(original.body).toMatchObject({ kind: 'QUESTION', session_id: questionSession, reviewed_source_trace_hash: 'a'.repeat(64) });
  expect(actual.every((row) => row.method === 'GET')).toBe(true); expect(actual[0]!.url.search).toBe(''); expect(actual[1]!.url.search).toBe(''); expect([...actual[2]!.url.searchParams.keys()]).toEqual(['limit']);
});

test('原DecisionTrace不完整或来源修订不同不提交观察；没有金融成功回填', async () => {
  const trace = interventionTraceFixture(); trace.trace!.outcome.question_revision = { ...interventionQuestionFixture().current_revision, revision: 2 };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes('/decisions/') ? trace : interventionQuestionFixture()))));
  await expect(readQuestionObservation(questionSession, 'TOOL_ONLY_BAD_SOURCE')).rejects.toThrow();
});

test('新原观察可绑定新会话与问题ID，完整原payload及原来源保持不变', async () => {
  const view = interventionCurrentObservationFixture();
  const original = structuredClone(view.original_message); const payload = view.payload_hash;
  expect(parseIntervention(view).current_source_binding).toBe('CURRENT_OBSERVATION');
  expect(view.current_question!.question_id).not.toBe(original.question!.question_id);
  expect(view.current_question_observation!.session_id).not.toBe(original.session_id);
  await verifyInterventionHash(view);
  expect(view.original_message).toEqual(original); expect(view.payload_hash).toBe(payload);
});

test.each(['extra', 'grant', 'execution', 'observation-run', 'observation-trace', 'source-run', 'source-trace', 'session', 'revision', 'semantic', 'owner', 'epoch', 'message', 'payload', 'key', 'kind', 'clock', 'question', 'choice', 'impact', 'current', 'missing', 'unverified'] as const)('新当前观察 %s 不一致拒绝，并保原消息', (field) => {
  const view = interventionCurrentObservationFixture(); const original = structuredClone(view.original_message);
  const proof = view.current_question_observation!; const recorded = proof.original_receipt;
  const raw = proof as unknown as Record<string, unknown>;
  if (field === 'extra') raw.bank_result = 'SETTLED'; if (field === 'grant') raw.authority_granted = true; if (field === 'execution') raw.execution_eligible = true;
  if (field === 'observation-run') proof.observation_run_id = 'invalid'; if (field === 'observation-trace') proof.observation_trace_hash = 'not-a-hash';
  if (field === 'source-run') proof.source_run_id = interventionId; if (field === 'source-trace') proof.source_trace_hash = '0'.repeat(64);
  if (field === 'session') proof.session_id = interventionId; if (field === 'revision') proof.revision = 17;
  if (field === 'semantic') proof.semantic_key = '0'.repeat(64); if (field === 'owner') recorded.user_id = interventionId;
  if (field === 'epoch') recorded.epoch_id = interventionId; if (field === 'message') recorded.message_id = interventionUserForNegative;
  if (field === 'payload') recorded.payload_hash = '0'.repeat(64); if (field === 'key') recorded.idempotency_key = 'OTHER_KEY';
  if (field === 'kind') (recorded.original_command.request as Record<string, unknown>).kind = 'SINGLE_ACTION_BOUNDARY';
  if (field === 'clock') recorded.recorded_at = '2026-10-04T00:00:00Z';
  if (field === 'question') proof.current_question.variable_id = 'different';
  if (field === 'choice') { const selected = proof.current_question.choices[0]!.value; if (selected.kind === 'money') selected.amount_cents += 1; }
  if (field === 'impact') proof.current_question.affected_action_types = ['OTHER_ACTION'];
  if (field === 'current') view.current_question!.question_id = interventionId;
  if (field === 'missing') view.current_question_observation = null; if (field === 'unverified') view.current_source_binding = 'UNVERIFIED';
  expect(() => parseIntervention(view)).toThrow(); expect(view.original_message).toEqual(original);
});
const interventionUserForNegative = '20000000-0000-4000-8000-000000000099';

test('声明的CURRENT不替代原OBSERVE请求hash，完整body被改即使内部字段对齐仍拒绝', async () => {
  const view = interventionCurrentObservationFixture(); const recorded = view.current_question_observation!.original_receipt;
  recorded.request_hash = '0'.repeat(64);
  await expect(verifyInterventionHash(parseIntervention(view))).rejects.toThrow();
  recorded.request_hash = interventionHashFixture(recorded.original_command);
  await verifyInterventionHash(parseIntervention(view));
  view.original_message.source_trace_hash = 'f'.repeat(64);
  await expect(verifyInterventionHash(parseIntervention(view))).rejects.toThrow();
});

test('历史INVALIDATED即使当前原观察存在仍终态；UNKNOWN不显示或假称current证明', async () => {
  const terminal = interventionCurrentObservationFixture(); terminal.stored_state = 'INVALIDATED'; terminal.effective_state = 'INVALIDATED'; terminal.pending = false; terminal.current_source_binding = 'LEGACY_TERMINAL_SOURCE';
  await verifyInterventionHash(parseIntervention(terminal)); expect(terminal.previously_claimed).toBe(false);
  terminal.pending = true; expect(() => parseIntervention(terminal)).toThrow(); terminal.pending = false;
  terminal.effective_state = 'PENDING'; expect(() => parseIntervention(terminal)).toThrow();
  const unknown = interventionFixture(); unknown.source_status = 'UNKNOWN'; unknown.effective_state = 'UNKNOWN'; unknown.current_source_binding = 'UNVERIFIED'; unknown.pending = false; unknown.current_question = null;
  expect(parseIntervention(unknown).source_status).toBe('UNKNOWN');
  unknown.current_question_observation = interventionCurrentObservationFixture().current_question_observation;
  expect(() => parseIntervention(unknown)).toThrow();
});

test('新proof严格消费原DTO，读取列表/详情/原键没有新增问答或Trace重复GET', async () => {
  const view = interventionCurrentObservationFixture(); const intent = interventionIntentFixture(); const calls: URL[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); calls.push(url); expect(init?.method).toBe('GET'); expect(init?.body).toBeUndefined();
    return new Response(JSON.stringify(url.pathname.includes('/by-key/') ? interventionLookupFixture(intent, true, view) : url.pathname.endsWith('/interventions') ? interventionListFixture([view]) : view));
  }));
  await getIntervention(interventionId); await listInterventions(); await lookupIntervention(intent);
  expect(calls).toHaveLength(3); expect(calls.every((url) => url.pathname.includes('/interventions'))).toBe(true);
});
