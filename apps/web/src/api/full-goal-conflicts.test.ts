import { expect, test, vi } from 'vitest';
import { getFullGoalConflicts, getOriginalGoalConflictResponse, parseFullGoalConflicts, parseFullGoalRepairs, previewFullGoalRepairs, validateGoalConflictGoals } from './full-goal-conflicts';
import { conflictFixture, conflictGoals, repairFixture, repairSelection, unknownConflictFixture, unknownRepairFixture } from '../tests/goal-conflict-fixture';

test('精确原DTO、删除见证和服务器预览保留原文本，不升权限', () => {
  const original = conflictFixture(), body = repairSelection(), reply = repairFixture();
  const read = parseFullGoalConflicts(original, JSON.stringify(original));
  validateGoalConflictGoals(read, conflictGoals());
  expect(read.explanation!.conflict.constraint_ids).toHaveLength(2);
  expect(read.current_permission_repair!.status).toBe('NO_PERMITTED_REPAIR');
  expect(getOriginalGoalConflictResponse(read)).toBe(JSON.stringify(original));
  const parsed = parseFullGoalRepairs(reply, read, body, JSON.stringify(reply));
  expect(parsed.proposal!.repair.status).toBe('PROPOSAL');
  expect(parsed.version_previews[0]!.proposed_monthly_max_cents).toBe(8);
  expect(parsed.writes_performed).toBe(false);
  expect(parsed.version_previews[0]!.confirmation_bindings).not.toHaveProperty('accepted');
});
test('UNKNOWN保原目标分母，金额/候选原null，不伪装空成功', () => {
  const value = parseFullGoalConflicts(unknownConflictFixture()); expect(value.registered_goal_count).toBe(1);
  expect(value.explanation).toBeNull(); expect(value.current_permission_repair).toBeNull();
  const reply = parseFullGoalRepairs(unknownRepairFixture(), conflictFixture(), repairSelection());
  expect(reply.proposal).toBeNull(); expect(reply.version_previews).toEqual([]);
  const fake = unknownConflictFixture(); fake.explanation = conflictFixture().explanation;
  expect(() => parseFullGoalConflicts(fake)).toThrow();
});
test.each(['fraction', 'bool', 'missing_witness', 'other_compatible', 'owner', 'denominator', 'authority', 'counterfactual'])('拒绝原冲突%s污染', (kind) => {
  const v = conflictFixture(), e = v.explanation!;
  if (kind === 'fraction') e.constraints[0]!.current_owned_cents = 0.1;
  if (kind === 'bool') (e.constraints[0] as unknown as Record<string, unknown>).current_owned_cents = true;
  if (kind === 'missing_witness') e.conflict.deletion_checks.pop();
  if (kind === 'other_compatible') (e as unknown as Record<string, unknown>).all_other_goal_constraints_proven_compatible = true;
  if (kind === 'owner') e.constraints[0]!.source_refs[0]!.user_id = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
  if (kind === 'denominator') v.registered_goal_count = 2;
  if (kind === 'authority') (v as unknown as Record<string, unknown>).grants_authority = true;
  if (kind === 'counterfactual') (e.conflict.deletion_checks[0] as unknown as Record<string, unknown>).counterfactual_only = false;
  expect(() => parseFullGoalConflicts(v)).toThrow();
});
test.each(['stale_review', 'wrong_epoch', 'selected_range', 'version', 'config', 'hash', 'accepted', 'deviation', 'unaffected', 'uses', 'unknown_success'])('拒绝修复%s污染', (kind) => {
  const reply = repairFixture(), p = reply.proposal!, version = reply.version_previews[0]!;
  if (kind === 'stale_review') reply.original_conflicts.review_state_hash = '0'.repeat(64);
  if (kind === 'wrong_epoch') reply.original_conflicts.epoch_id = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
  if (kind === 'selected_range') p.actual_preview_selection.adjustments[0]!.maximum_new_monthly_max_cents = 7;
  if (kind === 'version') version.current_version_id = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
  if (kind === 'config') (version.actual_existing_preview.full_configuration.monthly_contribution as Record<string, unknown>).max_cents = 9;
  if (kind === 'hash') version.confirmation_bindings.reviewed_base_hash = '0'.repeat(64);
  if (kind === 'accepted') version.confirmation_bindings.accepted = true;
  if (kind === 'deviation') p.repair.parameter_deviation_numerator = 0.1;
  if (kind === 'unaffected') p.repair.unaffected_goal_ids = [version.goal_id];
  if (kind === 'uses') p.repair.hypothetical_allocation!.income_uses[0]!.amount_cents += 1;
  if (kind === 'unknown_success') reply.original_conflicts = unknownConflictFixture();
  expect(() => parseFullGoalRepairs(reply, conflictFixture(), repairSelection())).toThrow();
});
test('实际网络GET和只读POST只发送精确范围，失效/extra钱拒绝发送', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(conflictFixture()))).mockResolvedValueOnce(new Response(JSON.stringify(repairFixture()))); vi.stubGlobal('fetch', fetch);
  const read = await getFullGoalConflicts(); await previewFullGoalRepairs(read, repairSelection());
  expect(fetch).toHaveBeenCalledTimes(2); expect(fetch.mock.calls[0]![0]).toBe('http://http-unit-fixture.local/api/v1/planning/full-goal-conflicts');
  expect(JSON.parse(fetch.mock.calls[1]![1].body)).toEqual(repairSelection());
  for (const value of [true, 1.5, '6', -1, Number.MAX_SAFE_INTEGER + 1]) {
    const body = repairSelection(); (body.adjustments[0] as unknown as Record<string, unknown>).minimum_new_monthly_max_cents = value;
    expect(() => previewFullGoalRepairs(read, body)).toThrow();
  }
  expect(() => previewFullGoalRepairs(read, { ...repairSelection(), accepted: true })).toThrow();
  expect(() => validateGoalConflictGoals(read, [{ ...conflictGoals()[0]!, policy_version_id: 'ffffffff-ffff-ffff-ffff-ffffffffffff' }])).toThrow();
  expect(fetch).toHaveBeenCalledTimes(2);
});
