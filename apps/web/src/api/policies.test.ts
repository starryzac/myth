import { afterEach, expect, test, vi } from 'vitest';
import { changePolicy, getPolicyMapVersions, previewChange } from './policies';
import { ApiError } from './http';
import { failure, hash, installHttpFixture, lifecycleFixture, policyFixture, policyId, previewFixture, versionId } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('只读预览仅传期望版本和配置；响应身份/模拟/精确金额入口校验', async () => {
  const fixture = previewFixture(); const requests = installHttpFixture(() => fixture);
  expect((await previewChange(policyId, versionId, policyFixture().current_version!.configuration)).configuration_hash).toBe(hash);
  expect(Object.keys(requests[0]!.body as object).sort()).toEqual(['configuration', 'expected_version_id']);
  fixture.delta_safe_idle_cents = 9007199254740992;
  await expect(previewChange(policyId, versionId, {})).rejects.toThrow('精确');
  fixture.delta_safe_idle_cents = null; fixture.policy_id = 'foreign-policy';
  await expect(previewChange(policyId, versionId, {})).rejects.toThrow('完整性');
});
test('PATCH不自动重试，原命令键与配置由调用者固定，服务错误保留request_id', async () => {
  let rejected = true;
  const requests = installHttpFixture(() => rejected ? failure() : lifecycleFixture());
  const command = { accepted: true, reviewed_hash: hash, expected_version_id: versionId, configuration: {}, reason: '单元fixture', idempotency_key: 'frozen-key' };
  await expect(changePolicy(policyId, command)).rejects.toMatchObject({ requestId: 'unit-request-409', status: 409 });
  expect(requests).toHaveLength(1); rejected = false;
  await changePolicy(policyId, command); expect(requests[1]!.body).toEqual(requests[0]!.body);
});
test('非JSON/真实环境响应拒绝，网络中断保留可识别错误', async () => {
  installHttpFixture(() => ({ simulation: false }));
  await expect(changePolicy(policyId, {} as never)).rejects.toBeInstanceOf(ApiError);
  installHttpFixture(() => { throw new Error('unit socket lost'); });
  await expect(changePolicy(policyId, {} as never)).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' });
});

test('地图版本reader拒绝其他策略身份和重复编号，不通过空列表推定完整', async () => {
  const version = policyFixture().current_version!;
  let items = [{ ...version, policy_id: 'foreign-policy' }]; installHttpFixture(() => ({ simulation: true, items }));
  await expect(getPolicyMapVersions(policyId)).rejects.toThrow('身份');
  items = [version, { ...version, id: 'different-id' }]; await expect(getPolicyMapVersions(policyId)).rejects.toThrow('唯一性');
  items = [version, { ...version, version_number: 2 }]; await expect(getPolicyMapVersions(policyId)).rejects.toThrow('唯一性');
  items = []; expect((await getPolicyMapVersions(policyId)).items).toEqual([]);
});
