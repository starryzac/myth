import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { candidateRefFixture, declarationFixture, emergencyText, onboardingBinding, proposalFixtures } from '../tests/onboarding-fixture';
let draft: typeof import('./onboarding-draft');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://unit-onboarding-draft.local'); draft = await import('./onboarding-draft'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
test('8步cursor/完整输入仅本地恢复；不能添加completed/authority字段', () => {
  draft.setOnboardingStep(8); draft.updateOnboardingInput('emergency_text', emergencyText); expect(draft.getOnboardingDraft().draft.step).toBe(8); expect(draft.validOnboardingDraft({ ...draft.getOnboardingDraft().draft, completed: true })).toBe(false); expect(draft.validOnboardingDraft({ ...draft.getOnboardingDraft().draft, grants_authority: true })).toBe(false);
});
test('重载严格读原输入和step，无自动fetch/compile/confirm', () => {
  const original = draft.emptyOnboardingDraft(); original.step = 6; original.inputs.goal_text = '用户原文待补日期'; sessionStorage.setItem('bounded-funds-onboarding-draft-v1:http://unit-onboarding-draft.local', JSON.stringify(original)); vi.stubGlobal('fetch', vi.fn()); expect(draft.recoverOnboardingDraft().draft).toEqual(original); expect(fetch).not.toHaveBeenCalled();
});
test('POST之前原body/服务用户周期日期保存，未知响应保pending禁止换文/新步骤写', () => {
  const original = draft.prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText); draft.beginOnboardingCommand(original); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toContain(original.body_json.replaceAll('"', '\\"')); draft.endOnboardingAttempt(); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); expect(() => draft.updateOnboardingInput('emergency_text', '另一个原文')).toThrow('不能改写'); expect(() => draft.beginOnboardingCommand(draft.prepareOnboardingIntent('GOAL', onboardingBinding(), '另一目标'))).toThrow();
});
test('候选原text/user/epoch/date必须匹配才保原ref，结果只有候选身份无权限', () => {
  const original = draft.prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText); draft.beginOnboardingCommand(original); draft.endOnboardingAttempt(); const bad = candidateRefFixture(original); bad.binding = { ...bad.binding, reference_date: '2026-10-05' }; expect(() => draft.retainOnboardingCandidate(original, bad)).toThrow(); draft.retainOnboardingCandidate(original, candidateRefFixture(original)); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(draft.getOnboardingDraft().draft.candidates.emergency!.input_text).toBe(emergencyText); expect(Object.keys(draft.getOnboardingDraft().draft)).not.toContain('authorized');
});
test.each(['invalid_step', 'unknown_input', 'broken_body', 'false_clock', 'missing_epoch', 'candidate_authority'])('坏session %s锁住新候选，不静默重置旧字节', (kind) => {
  const value = draft.emptyOnboardingDraft() as unknown as Record<string, unknown>;
  if (kind === 'invalid_step') value.step = 9;
  if (kind === 'unknown_input') (value.inputs as Record<string, unknown>).bank_authority = true;
  if (kind === 'broken_body') value.pending = { kind: 'DISCOVERY', body_json: '{', binding: onboardingBinding() };
  if (kind === 'false_clock') value.pending = { kind: 'DISCOVERY', body_json: '{}', binding: { ...onboardingBinding(), reference_date: '2026-02-30' } };
  if (kind === 'missing_epoch') value.pending = { kind: 'DISCOVERY', body_json: '{}', binding: { ...onboardingBinding(), epoch_id: null } };
  if (kind === 'candidate_authority') (value.candidates as Record<string, unknown>).goal = { authority: true };
  const original = JSON.stringify(value); sessionStorage.setItem('bounded-funds-onboarding-draft-v1:http://unit-onboarding-draft.local', original); expect(draft.recoverOnboardingDraft().storage_error).not.toBeNull(); expect(() => draft.beginOnboardingCommand(draft.prepareOnboardingIntent('DISCOVERY', onboardingBinding()))).toThrow(); expect(sessionStorage.getItem('bounded-funds-onboarding-draft-v1:http://unit-onboarding-draft.local')).toBe(original);
});
test('保存拒绝不进入发送busy，不丢原已保存草稿', () => {
  draft.updateOnboardingInput('goal_text', '保留原草稿'); const bytes = sessionStorage.getItem(sessionStorage.key(0)!); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('UNIT_STORAGE_DENIED'); }); expect(() => draft.beginOnboardingCommand(draft.prepareOnboardingIntent('DISCOVERY', onboardingBinding()))).toThrow('无法保存'); expect(draft.getOnboardingDraft().busy).toBe(false); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(bytes);
});
test('服务actor/epoch/date绑定与body严格，无owner/time/grant注入', () => {
  const original = draft.prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText); expect(draft.validOnboardingIntent({ ...original, body_json: JSON.stringify({ text: emergencyText, engine: 'rules', now: '2026-10-04', granted: true }) })).toBe(false); expect(draft.validOnboardingBinding({ ...onboardingBinding(), user_id: 'someone-else' })).toBe(false);
});
test('旧demo或FULL原请求待核对阻止引导POST；无需先进入旧页面', async () => {
  sessionStorage.setItem('bounded-funds-demo-operation-v1:http://unit-onboarding-draft.local', JSON.stringify({ kind: 'reset', epoch_id: null, reset_key: 'unit-other-original' })); expect(() => draft.beginOnboardingCommand(draft.prepareOnboardingIntent('DISCOVERY', onboardingBinding()))).toThrow(); const demo = await import('./demo-operation'); demo.endDemoOperation(true); const full = await import('./full-policy-operation'); const fixtures = await import('../tests/full-policy-fixture'); full.beginFullPolicyOperation(fixtures.createFullIntent()); full.endFullPolicyAttempt(); expect(() => draft.beginOnboardingCommand(draft.prepareOnboardingIntent('DISCOVERY', onboardingBinding()))).toThrow();
});
test('结构化声明保存原config/key/epoch/source；手动重放仅同原声明，不能换key/config', async () => {
  const api = await import('../api/onboarding'); const original = draft.prepareOnboardingDeclaration('OBLIGATION', onboardingBinding(), proposalFixtures()[0]!.configuration, proposalFixtures()[0]!.id); draft.beginOnboardingCommand(original); draft.endOnboardingAttempt(); const raw = JSON.parse(original.body_json); expect(raw.expected_epoch_id).toBe(onboardingBinding().epoch_id); expect(raw.source_proposal_id).toBe(proposalFixtures()[0]!.id); expect(raw.idempotency_key).toMatch(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/);
  expect(() => draft.beginOnboardingDeclarationReplay({ ...original, body_json: JSON.stringify({ ...raw, idempotency_key: 'another-key' }) })).toThrow(); draft.beginOnboardingDeclarationReplay(original); draft.endOnboardingAttempt(); expect(draft.getOnboardingDraft().draft.pending!.body_json).toBe(original.body_json); const ref = api.declarationRef(original, declarationFixture(original)); draft.retainOnboardingDeclaration(original, ref); expect(draft.getOnboardingDraft().draft.pending).toBeNull(); expect(draft.getOnboardingDraft().draft.declarations.obligation).toEqual(ref); expect(ref).not.toHaveProperty('authorized');
});
test('declaration原request缺null来源、wrongepoch、含资金grant或unsafe数拒绝，旧compile不可重放', () => {
  const original = draft.prepareOnboardingDeclaration('OBLIGATION', onboardingBinding(), proposalFixtures()[0]!.configuration); const body = JSON.parse(original.body_json); for (const changed of [{ ...body, expected_epoch_id: 'other' }, { ...body, bank_authority: true }, { ...body, configuration: { ...body.configuration, amount_cents: Number.MAX_SAFE_INTEGER + 1 } }]) expect(draft.validOnboardingIntent({ ...original, body_json: JSON.stringify(changed) })).toBe(false); delete body.source_proposal_id; expect(draft.validOnboardingIntent({ ...original, body_json: JSON.stringify(body) })).toBe(false);
  const compile = draft.prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText); draft.beginOnboardingCommand(compile); draft.endOnboardingAttempt(); expect(() => draft.beginOnboardingDeclarationReplay(compile)).toThrow('不能重放');
});
test('声明ref变body/key/epoch/任一hash格式不得清原pending；坏session原decl不能重标权限', async () => {
  const api = await import('../api/onboarding'); const original = draft.prepareOnboardingDeclaration('OBLIGATION', onboardingBinding(), proposalFixtures()[0]!.configuration); draft.beginOnboardingCommand(original); draft.endOnboardingAttempt(); const ref = api.declarationRef(original, declarationFixture(original)); expect(() => draft.retainOnboardingDeclaration(original, { ...ref, body_json: original.body_json.replace('onboarding-obligation:', 'new-key:') })).toThrow(); expect(() => draft.retainOnboardingDeclaration(original, { ...ref, request_hash: 'unknown' })).toThrow(); expect(draft.getOnboardingDraft().draft.pending).toEqual(original); const saved = draft.emptyOnboardingDraft(); saved.declarations.obligation = { ...ref, grants_authority: true } as typeof ref; expect(draft.validOnboardingDraft(saved)).toBe(false);
});
