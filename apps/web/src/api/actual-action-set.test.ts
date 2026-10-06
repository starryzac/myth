import { afterEach, describe, expect, it, vi } from 'vitest';
import { getActualActionSet, getOriginalActualActionSet, parseActualActionSet } from './actual-action-set';
import { snapshot } from '../tests/actual-action-set-fixture';

afterEach(() => vi.unstubAllGlobals());
describe('actual v2 read-only reader', () => {
  it('retains exact original bytes and makes only GET', async () => {
    const raw = JSON.stringify(snapshot(), null, 3);
    const fetcher = vi.fn().mockResolvedValue(new Response(raw, { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    const result = await getActualActionSet();
    expect(getOriginalActualActionSet(result)).toBe(raw);
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/boundary/actual-action-set/current'), expect.objectContaining({ method: 'GET' }));
    expect(fetcher.mock.calls[0]![1]).not.toHaveProperty('body');
  });
  it('keeps incomplete real counts and missing families UNKNOWN', () => {
    const data = snapshot();
    data.status = 'UNKNOWN'; data.global_action_set_complete = false; data.action_set_signature = null;
    data.table_coverage[0]! = { ...data.table_coverage[0]!, actual_count: 5000, captured_count: 4096, complete: false };
    data.unsupported_producers = ['UNPROVEN_RECOVERY']; data.reasons = ['CAPACITY_EXCEEDED'];
    const result = parseActualActionSet(data, JSON.stringify(data));
    expect(result.table_coverage[0]!.actual_count).toBe(5000);
    expect(result.global_action_set_complete).toBe(false);
  });
  it.each(['grant', 'truncated', 'different_table', 'missing_candidate', 'unsafe_cents', 'unknown_signature'])('refuses contradictory %s', (risk) => {
    const data = snapshot();
    if (risk === 'grant') Object.assign(data, { grants_authority: true });
    else if (risk === 'truncated') data.table_coverage[0]!.actual_count = 1;
    else if (risk === 'different_table') data.table_coverage[0]!.table = 'not_a_registered_table';
    else if (risk === 'missing_candidate') data.candidates = [];
    else if (risk === 'unsafe_cents') data.candidates[0]!.amount_cents = Number.MAX_SAFE_INTEGER + 1;
    else { data.status = 'UNKNOWN'; data.global_action_set_complete = false; }
    expect(() => parseActualActionSet(data, JSON.stringify(data))).toThrow();
  });
});
