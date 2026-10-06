/** Synthetic source responses and real parser/hash checks; no actual PG/bank acceptance. */
import { webcrypto } from 'node:crypto';
import { afterEach, expect, test, vi } from 'vitest';
import { dependencyFixture, dependencyPolicyId, dependencyVersionId } from '../tests/full-policy-dependencies-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
import { releaseHash } from './goal-release-authorizations';
import { getFullPolicyDependencies, originalFullPolicyDependencies, parseFullPolicyDependencies } from './full-policy-dependencies';
import type { FullPolicyDependencyReview } from './full-policy-dependencies';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const crypto = () => vi.stubGlobal('crypto', webcrypto);
const binding = { policyId: dependencyPolicyId, currentVersionId: dependencyVersionId };
const parse = (data: FullPolicyDependencyReview) => parseFullPolicyDependencies(data, binding, JSON.stringify(data));
async function rehash(data: FullPolicyDependencyReview) { const body: Partial<FullPolicyDependencyReview> = { ...data }; delete body.review_hash; data.review_hash = await releaseHash(body); return data; }

test('actual pure-domain source bytes preserve complete roots, exact declared cycles and no authority', async () => {
  crypto(); const data = dependencyFixture(); const raw = JSON.stringify(data);
  const result = await parseFullPolicyDependencies(data, binding, raw);
  expect(result.current_policy_count).toBe(result.captured_policy_count);
  expect(result.cyclic_components[0]!.financial_infeasibility_proven).toBe(false);
  expect(originalFullPolicyDependencies(result)).toBe(raw);
  expect(result.bank_authority).toBe(false);
});
test('GET forwards exact selected identity with no query or financial mutation', async () => {
  crypto(); const calls = installHttpFixture((method, path) => { expect(method).toBe('GET'); expect(path).toBe(`/api/v1/full-policy-dependencies/${dependencyPolicyId}`); return dependencyFixture(); });
  await getFullPolicyDependencies(binding); expect(calls).toHaveLength(1); expect(calls[0]!.body).toBeUndefined();
});
test('unknown root source preserves denominator and does not become an empty complete graph', async () => {
  crypto(); const data = dependencyFixture(); data.status = 'UNKNOWN'; data.policies = []; data.edges = []; data.cyclic_components = []; data.captured_policy_count = 0; data.reasons = ['TOOL_ONLY_MISSING_CURRENT_ORIGINALS'];
  const result = await parse(await rehash(data)); expect(result.current_policy_count).toBe(2); expect(result.current_policy_ids).toHaveLength(2); expect(result.policies).toHaveLength(0);
});
test.each(['owner', 'epoch', 'denominator', 'missing-edge', 'extra-edge', 'bank', 'hash', 'configuration', 'cycle-drop', 'cycle-invent', 'review', 'unsafe-money'])('rehashed %s cannot manufacture a valid current graph', async change => {
  crypto(); const data = dependencyFixture();
  if (change === 'owner') data.user_id = data.epoch_id;
  else if (change === 'epoch') data.policies[0]!.epoch_id = data.selected_policy_id;
  else if (change === 'denominator') data.current_policy_count++;
  else if (change === 'missing-edge') data.edges.pop();
  else if (change === 'extra-edge') data.edges.push(structuredClone(data.edges[0]!));
  else if (change === 'bank') Object.assign(data, { bank_authority: true });
  else if (change === 'hash') data.input_hash = 'bad';
  else if (change === 'configuration') data.policies[0]!.configuration = { amount_cents: 400 };
  else if (change === 'cycle-drop') data.cyclic_components = [];
  else if (change === 'cycle-invent') data.cyclic_components[0]!.example_path = [dependencyPolicyId, dependencyPolicyId];
  else if (change === 'review') data.review_required = false;
  else Object.assign(data.policies[0]!.configuration, { forged_cents: Number.MAX_SAFE_INTEGER + 1 });
  await expect(parse(change === 'unsafe-money' ? data : await rehash(data))).rejects.toThrow();
});
test('different original body or expected current version is refused even with a valid response hash', async () => {
  crypto(); const data = dependencyFixture();
  await expect(parseFullPolicyDependencies(data, binding, JSON.stringify({ ...data, archived_policy_count: 99 }))).rejects.toThrow();
  await expect(parseFullPolicyDependencies(data, { ...binding, currentVersionId: data.policies[1]!.version_id }, JSON.stringify(data))).rejects.toThrow();
});
test('MVP derived expiry and cross-table same UUID retain raw ACTIVE and create no false full cycle', async () => {
  crypto(); const data = dependencyFixture(); const first = data.policies[0]!; const ref = first.current_references![0]!; const old = first.recorded_references[0]!;
  ref.kind = 'MVP_POLICY'; old.kind = 'MVP_POLICY'; first.reference_effective_statuses = { [String(ref.id)]: 'EXPIRED' };
  data.edges[0]!.kind = 'MVP_POLICY'; data.edges[0]!.current_target_status = 'EXPIRED'; data.edges[0]!.original_reference = old; data.edges[0]!.current_reference = ref; data.cyclic_components = [];
  const result = await parse(await rehash(data)); expect(result.review_required).toBe(true); expect(result.edges[0]!.current_reference!.snapshot).toHaveProperty('status', 'ACTIVE'); expect(result.cyclic_components).toHaveLength(0);
});
