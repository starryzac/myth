import { afterEach, expect, test, vi } from 'vitest';
import { getDecisionSearch, getOriginalDecisionSearch, parseDecisionSearch } from './decision-search';
import { decisionSearchFixture, searchAction, searchOwner, searchQuery, searchVersion } from '../tests/decision-search-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('synthetic response keeps exact filters, typed refs, null and original JSON', () => {
  const query = { ...searchQuery, policy_version_id: searchVersion };
  const fixture = decisionSearchFixture(query); const raw = JSON.stringify(fixture);
  const parsed = parseDecisionSearch(fixture, searchOwner, query, raw);
  expect(parsed.items[0]!.references[1]!.identity).toBe(searchVersion); expect(getOriginalDecisionSearch(parsed)).toBe(raw);
  const unknown = parseDecisionSearch(decisionSearchFixture(query, true), searchOwner, query);
  expect(unknown.total_match_count).toBeNull(); expect(unknown.inventory.unverifiable_count).toBe(1);
  expect(unknown.audit_chain_verified).toBe(false); expect(unknown.financial_success_inferred).toBe(false);
});
test.each(['user_id', 'query', 'scope', 'grants_authority', 'financial_success_inferred', 'absence_is_final', 'archived_records_searched', 'audit_chain_verified'] as const)('refuses forged %s without showing records', (field) => {
  const value = decisionSearchFixture(); Object.assign(value, { [field]: field === 'user_id' ? searchVersion : field === 'query' ? { ...searchQuery, action_id: searchVersion } : field === 'scope' ? 'ALL_HISTORY' : true });
  expect(() => parseDecisionSearch(value, searchOwner, searchQuery)).toThrow();
});
test.each(['denominator', 'zero_unknown', 'future', 'wrong_action', 'wrong_ref', 'duplicate', 'full_as_success', 'false_ref_relation', 'missing_hash'] as const)('refuses inconsistent %s', (caseId) => {
  const value = decisionSearchFixture();
  if (caseId === 'denominator') value.inventory.selected_scope_count = 2;
  if (caseId === 'zero_unknown') { value.state = 'UNKNOWN'; value.total_match_count = 0; }
  if (caseId === 'future') value.items[0]!.as_of = '2030-01-01T00:00:00Z';
  if (caseId === 'wrong_action') value.items[0]!.action_ids = [searchVersion];
  if (caseId === 'wrong_ref') { value.query.policy_version_id = searchVersion; value.version_family = 'MVP'; }
  if (caseId === 'duplicate') { value.items.push(value.items[0]!); value.inventory.returned_count = 2; }
  if (caseId === 'full_as_success') { value.version_family = 'FULL_UNSUPPORTED'; value.query.policy_version_id = searchVersion; }
  if (caseId === 'false_ref_relation') value.items[0]!.references[0]!.relation = 'VERIFIED_TYPED_CAPTURE';
  if (caseId === 'missing_hash') value.items[0]!.trace_hash = null;
  expect(() => parseDecisionSearch(value, searchOwner, caseId === 'wrong_ref' || caseId === 'full_as_success' ? value.query : searchQuery)).toThrow();
});
test('exact key GET preserves special characters and never sends identity/clock/financial data', async () => {
  const query = { ...searchQuery, action_id: null, action_key: 'original + / % : key' };
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  const fetch = vi.fn(async () => new Response(JSON.stringify(decisionSearchFixture(query)))); vi.stubGlobal('fetch', fetch);
  await getDecisionSearch(query, searchOwner); const [path, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  const url = new URL(path); expect(url.pathname).toBe('/api/v1/decision-search'); expect(url.searchParams.get('action_key')).toBe(query.action_key);
  expect(url.searchParams.has('user_id')).toBe(false); expect(url.searchParams.has('now')).toBe(false); expect(options.method).toBe('GET'); expect(options.body).toBeUndefined();
});
test('invalid query does not access network', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  await expect(getDecisionSearch({ ...searchQuery, action_id: null }, searchOwner)).rejects.toThrow();
  await expect(getDecisionSearch({ ...searchQuery, action_key: 'also-key' }, searchOwner)).rejects.toThrow();
  await expect(getDecisionSearch(searchQuery, 'other')).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});
test('empty valid source still cannot claim absence or completion of an original action', () => {
  const value = decisionSearchFixture(); value.items = []; value.inventory.returned_count = 0; value.verified_match_count = 0; value.total_match_count = 0;
  expect(parseDecisionSearch(value, searchOwner, searchQuery).absence_is_final).toBe(false);
  expect(parseDecisionSearch(value, searchOwner, searchQuery).resolved_action_id).toBe(searchAction);
});
test('complete labels cannot bypass row or byte capacity', () => {
  const rows = decisionSearchFixture(); rows.inventory.audit_link_count = 1025; rows.inventory.captured_audit_link_count = 1025;
  expect(() => parseDecisionSearch(rows, searchOwner, searchQuery)).toThrow();
  const bytes = decisionSearchFixture(); bytes.inventory.source_bytes = 67108865;
  expect(() => parseDecisionSearch(bytes, searchOwner, searchQuery)).toThrow();
});
