import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { isFreshMaturityRead, lookupMaturityExecution, originalMaturityResponse, parseMaturityAction, parseMaturityIntent, parseMaturityLookup, parseMaturityPrepare, parseMaturityPreview } from './full-maturity-execution';
import { maturityIntent } from '../features/full-maturity-execution-operation';
import { maturityActionFixture, maturityHash, maturityLookupFixture, maturityPrepareFixture, maturityPreviewFixture, maturityUser } from '../tests/full-maturity-execution-fixture';
beforeEach(() => vi.stubGlobal('crypto', webcrypto)); afterEach(() => vi.unstubAllGlobals());
test('strict five identities reject injected money/time/actor/amount and blank key', () => { const body = maturityPrepareFixture(); expect(parseMaturityPrepare(body)).toEqual(body); for (const more of [{ amount_cents: 1 }, { now: 'fake' }, { actor: 'USER' }, { position_id: 'fake' }, { idempotency_key: ' ' }]) expect(() => parseMaturityPrepare({ ...body, ...more })).toThrow(); });
test('whole original MATURE preview is noauthority; remaining negative checkpoints do not become recovery success', async () => { const body = maturityPrepareFixture(), v = maturityPreviewFixture(body); expect((await parseMaturityPreview(v, body, maturityUser)).proof.remaining_negative_checkpoints).toBe(3); expect((await parseMaturityPreview(maturityPreviewFixture(body, true), body, maturityUser)).proof.command).toBeNull(); v.proof.command!.principal_cents++; await expect(parseMaturityPreview(v, body, maturityUser)).rejects.toThrow(); });
test.each(['PREPARED', 'CONFIRMED', 'UNKNOWN', 'SETTLED'] as const)('original %s current/contract/consent/receipt fields checked independently', async stage => { const body = maturityPrepareFixture(), action = await maturityActionFixture(body, stage), value = await parseMaturityAction(action, body, maturityUser); expect(value.service_receipt_verified).toBe(stage === 'SETTLED'); expect(value.economic_verified).toBe(false); });
test.each(['owner', 'epoch', 'action', 'command', 'bank_key', 'request', 'AUTO', 'receipt', 'role', 'expiry', 'extra'] as const)('forged %s is refused even with rehashed original envelope', async kind => {
  const body = maturityPrepareFixture(), value = await maturityActionFixture(body, 'SETTLED');
  if (kind === 'owner') value.user_id = body.policy_id;
  if (kind === 'epoch') value.epoch_id = body.policy_id;
  if (kind === 'action') value.action_id = body.policy_id;
  if (kind === 'command') value.command.principal_cents++;
  if (kind === 'bank_key') value.bank_key = 'wrong';
  if (kind === 'request') value.original_request.idempotency_key = 'another';
  if (kind === 'AUTO') Object.assign(value, { autonomy_level: 'AUTO_EXECUTE' });
  if (kind === 'receipt') { const event = value.original_event; const original = event.originals as { receipts: Record<string, unknown>[] }; original.receipts[0]!.loss_cents = 1; event.originals_hash = maturityHash(original); }
  if (kind === 'role') value.original_consent!.principal_at_confirmation.role = 'AGENT';
  if (kind === 'expiry') value.original_consent!.confirmed_at = value.command.expires_at;
  if (kind === 'extra') Object.assign(value.original_request, { amount_cents: 1 });
  value.server_request_hash = maturityHash(value.original_action_request); await expect(parseMaturityAction(value, body, maturityUser)).rejects.toThrow();
});
test('same epoch/action/full command survives terminal sealed read, not authority', async () => { const body = maturityPrepareFixture(), value = await maturityActionFixture(body, 'SETTLED'); value.epoch_state = 'SEALED'; value.historical = true; expect((await parseMaturityAction(value, body, maturityUser)).receipt_is_current_authority).toBe(false); });
test('lookup NOT_FOUND is not final, POST/stored parse never gets fresh GET marker', async () => { const intent = await maturityIntent('PREPARE', maturityUser, maturityPrepareFixture()); const missing = await parseMaturityLookup(await maturityLookupFixture(intent, 'NOT_FOUND'), intent); expect(missing.not_found_is_final).toBe(false); expect(isFreshMaturityRead(missing)).toBe(false); const wire = await maturityLookupFixture(intent); const raw = JSON.stringify(wire) + '\n'; vi.stubGlobal('fetch', vi.fn(async () => new Response(raw))); const read = await lookupMaturityExecution(intent); expect(isFreshMaturityRead(read)).toBe(true); expect(originalMaturityResponse(read)).toBe(raw); });
test('saved exact body/hash/path rejects wrong consent and execute identity', async () => { const body = maturityPrepareFixture(), original = await maturityIntent('CONFIRM', maturityUser, body, await maturityActionFixture(body)); await expect(parseMaturityIntent(original)).resolves.toEqual(original); const intent = structuredClone(original); Object.assign(intent.body, { accepted: false }); intent.body_json = JSON.stringify(intent.body); intent.request_hash = maturityHash(intent.body); await expect(parseMaturityIntent(intent)).rejects.toThrow(); });
