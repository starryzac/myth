import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { getAssetExecution, getOriginalAssetExecutionResponse, isFreshAssetExecutionRead, lookupAssetExecution, parseAssetConfirm, parseAssetExecute, parseAssetIntent, parseAssetLookup, parseAssetPortfolio, parseAssetPrepare, parseAssetPreview, parseAssetResponse, postAssetExecution, previewAssetExecution } from './full-asset-execution';
import { prepareAssetIntent } from '../features/full-asset-execution-operation';
import { assetConfirmFixture, assetEpoch, assetExecuteFixture, assetFixtureHash, assetId, assetLookupFixture, assetPortfolioFixture, assetPortfolioId, assetPrepareFixture, assetPreviewFixture, assetResponseFixture, assetUser } from '../tests/full-asset-execution-fixture';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('TOOL_ONLY 原多批组合/完整1098点/候选和confirm原证据可读，不推断当前权限', async () => {
  const preview = await parseAssetPreview(assetPreviewFixture(), assetPrepareFixture(), assetUser); expect(preview.portfolio!.batches).toHaveLength(2); expect(preview.portfolio!.combined_original_boundary.calculation_trace).toHaveLength(1098);
  for (const stage of ['PREPARED', 'CONFIRMED', 'UNKNOWN', 'SETTLED'] as const) { const value = await parseAssetResponse(assetResponseFixture(stage), assetUser); expect(value.bank_authority).toBe(false); expect(value.receipt_is_current_authority).toBe(false); expect(value.all_original_service_receipts_verified).toBe(false); }
  expect((await parseAssetPreview(assetPreviewFixture(true), assetPrepareFixture(), assetUser)).portfolio).toBeNull();
});
test.each([{ amount_cents: 1 }, { now: '2026-01-01' }, { bank_receipt: {} }, { accepted: true }, { goal_id: assetId(99) }, { planning_mode: 'AUTO' }, { expected_epoch_id: '../other' }, { idempotency_key: 'a/b' }])('PREPARE 拒绝客户端金额/clock/receipt/权限和非法身份 %j', (patch) => { expect(() => parseAssetPrepare({ ...assetPrepareFixture(), ...patch })).toThrow(); });
test('确认和执行仅原合同字段；固定批次/action不可缺、bool不能整数代替', () => {
  expect(() => parseAssetConfirm({ ...assetConfirmFixture(), accepted: 1 })).toThrow(); expect(() => parseAssetConfirm({ ...assetConfirmFixture(), money: 1 })).toThrow();
  for (const patch of [{ expected_batch_number: 0 }, { expected_batch_number: 5 }, { expected_action_id: '../new' }, { accepted: false }, { idempotency_key: 'NEW_EXECUTE_KEY' }]) expect(() => parseAssetExecute({ ...assetExecuteFixture(), ...patch })).toThrow();
});
test.each(['money', 'batch', 'catalogue', 'bankkey', 'owner', 'curve', 'wholehash', 'config'] as const)('完整原组合拒绝 %s 漂移或完整分母改变', async (kind) => {
  const value = assetPortfolioFixture();
  if (kind === 'money') value.batches[0]!.command.effect.amount_cents++;
  if (kind === 'batch') value.batches.pop();
  if (kind === 'catalogue') value.batches[0]!.catalogue.terms_digest = '0'.repeat(64);
  if (kind === 'bankkey') value.batches[0]!.bank_idempotency_key = 'NEW_KEY';
  if (kind === 'owner') value.user_id = assetId(99);
  if (kind === 'curve') value.combined_original_boundary.calculation_trace.pop();
  if (kind === 'wholehash') value.portfolio_hash = '0'.repeat(64);
  if (kind === 'config') value.full_policy_content_hash = '0'.repeat(64);
  await expect(parseAssetPortfolio(value, assetUser)).rejects.toThrow();
});
test('READY 规划和完整组合逐批金额/目录/版本要一致，UNKNOWN 不能携带伪成功组合', async () => {
  const ready = assetPreviewFixture(); ready.original_full_planning.user_id = assetId(99); await expect(parseAssetPreview(ready, assetPrepareFixture(), assetUser)).rejects.toThrow();
  const mismatch = assetPreviewFixture(); mismatch.original_full_planning.allocation!.policy_version_id = assetId(99); await expect(parseAssetPreview(mismatch, assetPrepareFixture(), assetUser)).rejects.toThrow();
  const unknown = assetPreviewFixture(true); unknown.portfolio = assetPortfolioFixture(); await expect(parseAssetPreview(unknown, assetPrepareFixture(), assetUser)).rejects.toThrow();
});
test.each(['denominator', 'action', 'receipt-money', 'consent-key', 'consent-body', 'consent-hash', 'authority', 'evidence-status'] as const)('原状态/确认和回执拒绝 %s 错配', async (kind) => {
  const value = assetResponseFixture('SETTLED');
  if (kind === 'denominator') value.batches.pop();
  if (kind === 'action') value.batches[0]!.original_action!.action_id = assetId(99);
  if (kind === 'receipt-money') value.batches[0]!.original_action!.receipt!.executed_cents++;
  if (kind === 'consent-key') value.original_consent!.idempotency_key = 'NEW_CONFIRM';
  if (kind === 'consent-body') value.original_consent!.original_request.reviewed_portfolio_hash = '0'.repeat(64);
  if (kind === 'consent-hash') value.original_consent!.evidence_hash = '0'.repeat(64);
  if (kind === 'authority') value.receipt_is_current_authority = true as never;
  if (kind === 'evidence-status') value.original_consent_verified = false;
  await expect(parseAssetResponse(value, assetUser)).rejects.toThrow();
});
test('保留原确认但当前Evidence缺失可只读，历史不当作当前确认；日期同一时刻允许Python ISO格式', async () => {
  const value = assetResponseFixture('CONFIRMED'); value.original_consent_verified = false; value.original_consent_evidence_status = 'RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING'; value.original_consent!.current_evidence_verified = false; value.original_consent!.current_evidence_status = value.original_consent_evidence_status;
  const evidence = value.original_consent!.original_evidence; const content = evidence.content as Record<string, unknown>;
  content.valid_until = value.original_portfolio.expires_at.replace('.000Z', '+00:00'); value.original_consent!.evidence_hash = assetFixtureHash(content); evidence.content_hash = value.original_consent!.evidence_hash;
  const result = await parseAssetResponse(value, assetUser); expect(result.original_consent!.original_request.idempotency_key).toBe(assetConfirmFixture().idempotency_key); expect(result.original_consent_verified).toBe(false);
});
test('原GET完整文本与fresh身份保留，POST不能冒充独立原GET；无query/客户端金额', async () => {
  const intent = await prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); const raw = ` \n${JSON.stringify(assetResponseFixture())}\n`;
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-asset-api.local'); vi.stubGlobal('fetch', vi.fn(async () => new Response(raw)));
  const post = await postAssetExecution(intent), read = await getAssetExecution(assetPortfolioId, assetUser);
  expect(isFreshAssetExecutionRead(post)).toBe(false); expect(isFreshAssetExecutionRead(read)).toBe(true); expect(getOriginalAssetExecutionResponse(read)).toBe(raw); expect(vi.mocked(fetch).mock.calls[0]![1]!.body).toBe(intent.body_json); expect(vi.mocked(fetch).mock.calls[1]![1]!.method).toBe('GET'); expect(String(vi.mocked(fetch).mock.calls[1]![0])).toBe(`http://unit-full-asset-api.local/api/v1/full-asset-executions/portfolios/${assetPortfolioId}`);
});
test('CONFIRM 原键GET完整body/hash不借latest；NOT_FOUND非终局且所有原件null', async () => {
  const intent = await prepareAssetIntent('CONFIRM', assetUser, assetConfirmFixture(), assetPortfolioFixture()); const source = assetLookupFixture(intent);
  expect((await parseAssetLookup(source, intent)).original!.original_consent!.original_request).toEqual(intent.body);
  expect((await parseAssetLookup(assetLookupFixture(intent, false), intent)).original).toBeNull();
  for (const patch of [{ command_kind: 'PREPARE' }, { request_hash: '0'.repeat(64) }, { idempotency_key: 'NEW_CONFIRM' }, { replacement_allowed: true }, { epoch_id: assetId(99) }]) await expect(parseAssetLookup({ ...source, ...patch }, intent)).rejects.toThrow();
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-asset-api.local'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(source)))); const lookup = await lookupAssetExecution(intent); expect(isFreshAssetExecutionRead(lookup)).toBe(true); expect(String(vi.mocked(fetch).mock.calls[0]![0])).toBe(`http://unit-full-asset-api.local/api/v1/full-asset-executions/commands/${assetEpoch}/by-key/${assetConfirmFixture().idempotency_key}`);
});
test('固定execute原intent绑定完整组合/批号/action，无独立execute键也不允许借下一批', async () => {
  const intent = await prepareAssetIntent('EXECUTE', assetUser, assetExecuteFixture(), assetPortfolioFixture()); expect(parseAssetIntent(intent).kind).toBe('EXECUTE');
  const changed = structuredClone(intent); changed.body = assetExecuteFixture(2); changed.body_json = JSON.stringify(changed.body); expect(() => parseAssetIntent({ ...changed, body: { ...changed.body, expected_action_id: assetId(99) }, body_json: JSON.stringify({ ...changed.body, expected_action_id: assetId(99) }) })).toThrow();
  await expect(parseAssetLookup(assetLookupFixture(await prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture())), intent)).rejects.toThrow();
});
test('preview只读接口真实原body，无client结果；非法或409不重试', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(assetPreviewFixture())))); expect((await previewAssetExecution(assetPrepareFixture(), assetUser)).persisted).toBe(false); expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]![1]!.body))).toEqual(assetPrepareFixture());
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'STALE_ORIGINAL', message: 'TOOL_ONLY_409', request_id: 'TOOL_ONLY' } }), { status: 409 })));
  await expect(previewAssetExecution(assetPrepareFixture(), assetUser)).rejects.toMatchObject({ code: 'STALE_ORIGINAL' }); expect(fetch).toHaveBeenCalledTimes(1);
});
