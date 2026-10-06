import { afterEach, expect, test, vi } from 'vitest';
import { getOriginalMultiPreview, multiConfigurationHash, parseMultiPreview, parseMultiPreviewCandidate, previewMultiTemplate, readMultiPreviewSources } from './full-policy-change-multi';
import { multiBody, multiFixture, multiInventoryFixture, multiSource, multiUnknown } from '../tests/multi-template-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test.each(['projected', 'asset', 'recovery', 'goal'] as const)('%s纯域原完整结果可消费但不是金融实测', async (kind) => {
  const v = multiFixture(kind), raw = ` \n${JSON.stringify(v)}\n`; const data = await parseMultiPreview(v, multiSource(kind), multiBody(kind), raw);
  expect(data.financial_impact.before?.calculation_trace).toHaveLength(1098); expect(data.financial_impact.future_income_in_execution_cents).toBe(0); expect(getOriginalMultiPreview(data)).toBe(raw);
  if (kind === 'asset') expect(data.financial_impact.product_capacities).toHaveLength(2);
  if (kind === 'recovery') expect(data.financial_impact.recovery_candidates).toHaveLength(1);
  if (kind === 'goal') expect(data.financial_impact.goal_allocation_after?.goals).toHaveLength(2);
});
test('只从原current列表建立来源，实际owner错配留不可用', async () => {
  const v = multiInventoryFixture(); installHttpFixture((_m, path) => path.endsWith('/dashboard') ? v.dashboard : path.endsWith('/full-policies') ? v.full : v.mvp);
  const result = await readMultiPreviewSources(); expect(result.items).toHaveLength(2); expect(result.items.every((r) => r.eligible)).toBe(true);
  v.mvp.items[0]!.current_version!.confirmation.user_id = v.mvp.items[0]!.id;
  expect((await readMultiPreviewSources()).items[0]?.eligible).toBe(false);
});
test('实际POST仅3原字段，无确认/clock/amount/effect/额外GET', async () => {
  const calls = installHttpFixture(() => multiFixture()); const source = multiSource();
  await previewMultiTemplate(source, multiBody().configuration);
  expect(calls).toEqual([{ method: 'POST', path: `/api/v1/policy-financial-previews/MVP_POLICY/${source.policyId}`, body: multiBody() }]);
});
test('UNKNOWN原before可保留，null不能伪作零', async () => {
  const v = multiUnknown(); expect((await parseMultiPreview(v, multiSource(), multiBody())).financial_impact.delta_safe_idle_cents).toBeNull(); v.financial_impact.delta_safe_idle_cents = 0;
  await expect(parseMultiPreview(v, multiSource(), multiBody())).rejects.toThrow();
});
test.each(['owner', 'epoch', 'version', 'source', 'grant', 'futureIncome', 'hash', 'point', 'phase', 'delta', 'money', 'count', 'extraBody'])('%s不得借PROJECTED通过', async (kind) => {
  const v = multiFixture(), p = v.financial_impact, body = multiBody();
  switch (kind) {
    case 'owner': v.user_id = v.policy_id; break; case 'epoch': v.epoch_id = v.policy_id; break; case 'version': v.expected_version_id = v.policy_id; break;
    case 'source': v.source_kind = 'FULL_POLICY'; break; case 'grant': Object.assign(p, { bank_authority: true }); break; case 'futureIncome': Object.assign(p, { future_income_in_current_cash_cents: 1 }); break;
    case 'hash': v.candidate_configuration_hash = 'a'.repeat(64); break; case 'point': p.after!.calculation_trace.pop(); break;
    case 'phase': p.after!.calculation_trace[0]!.phase = 'AFTER_PAYMENT'; break; case 'delta': p.delta_safe_idle_cents = 0; break;
    case 'money': p.after!.safe_idle_cents = Number.MAX_SAFE_INTEGER + 1; break; case 'count': v.source_counts.actions = 1; break;
    case 'extraBody': Object.assign(body, { accepted: true }); break;
  }
  await expect(parseMultiPreview(v, multiSource(), body)).rejects.toThrow();
});
test('PARTIAL原目录/全仓/Goal分母不缩减，个产品差额真实一致', async () => {
  const asset = multiFixture('asset'); asset.financial_impact.product_capacities.pop(); await expect(parseMultiPreview(asset, multiSource('asset'), multiBody('asset'))).rejects.toThrow();
  const recovery = multiFixture('recovery'); recovery.financial_impact.recovery_candidates.pop(); await expect(parseMultiPreview(recovery, multiSource('recovery'), multiBody('recovery'))).rejects.toThrow();
  const goal = multiFixture('goal'); goal.financial_impact.goal_allocation_delta_cents = {}; await expect(parseMultiPreview(goal, multiSource('goal'), multiBody('goal'))).rejects.toThrow();
});
test('整仓候选准时净额差额不能借PARTIAL错算；未知arrival不得填零', async () => {
  const v = multiFixture('recovery'); v.financial_impact.recovery_candidates[0]!.conditional_on_time_net_delta_cents = 0;
  await expect(parseMultiPreview(v, multiSource('recovery'), multiBody('recovery'))).rejects.toThrow();
  const unknown = multiFixture('recovery'); const row = unknown.financial_impact.recovery_candidates[0]!; row.after.on_time = null; row.conditional_on_time_net_delta_cents = null;
  expect((await parseMultiPreview(unknown, multiSource('recovery'), multiBody('recovery'))).financial_impact.recovery_candidates[0]?.conditional_on_time_net_delta_cents).toBeNull();
});
test('编辑仅原配置字段/type；整数分与实际浮点quantile摘要', async () => {
  const c = multiSource().configuration; expect(parseMultiPreviewCandidate(JSON.stringify(multiBody().configuration), c)).toEqual(multiBody().configuration);
  for (const candidate of [{ ...c, grant: true }, { ...c, type: 'asset_authorization' }, { ...c, amount_cents: 1.25 }]) expect(() => parseMultiPreviewCandidate(JSON.stringify(candidate), c)).toThrow();
  // Exact SHA from original Python configuration_hash, including typed quantile 1.0.
  expect(await multiConfigurationHash({ method: { name: 'rolling_window_quantile', quantile: 1 } })).toBe('d7f7688ed9139ef8d2ff5d225109483c0e493e2315923b49d8078607c9929f08');
});
