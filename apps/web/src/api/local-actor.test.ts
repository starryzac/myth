import { afterEach, describe, expect, it, vi } from 'vitest';
import { localActorFixture } from '../tests/local-actor-fixture';
import { ApiError } from './http';
import { LOCAL_USERNAME, loginLocalActor, logoutLocalActor, parseLocalActorLogout, parseLocalActorSession, readLocalActorSession } from './local-actor';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function http(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } }); }

describe('local actor strict actual DTO reader — TOOL_ONLY', () => {
  it('keeps actual identity/time/role without adding authority', () => {
    const original = localActorFixture();
    expect(parseLocalActorSession(original)).toEqual(original);
    expect(original.bank_authority).toBe(false);
    expect(original.confirms_financial_action).toBe(false);
  });
  it.each(['simulation', 'bank_authority', 'confirms_financial_action'])('rejects wrong outer flag %s', (field) => {
    const value = localActorFixture() as unknown as Record<string, unknown>;
    value[field] = field === 'simulation' ? false : true;
    expect(() => parseLocalActorSession(value)).toThrow();
  });
  it.each(['USER', 'AGENT', 'REVIEWER', 'SYSTEM', 'DEMO_ADMIN'])('retains actual server enum %s, never bank permission', (role) => {
    const value = localActorFixture();
    const raw = { ...value, principal: { ...value.principal, role } };
    expect(parseLocalActorSession(raw).principal.role).toBe(role);
    expect(parseLocalActorSession(raw).bank_authority).toBe(false);
  });
  it.each([
    ['user_id', 'not-uuid'], ['session_id', 'not-uuid'], ['role', 'ADMIN'],
    ['authenticated', false], ['human_identity_verified', true],
    ['authentication_source', 'CLIENT_CLAIM'], ['issued_at', '2026-10-06T00:00:00'],
    ['expires_at', null],
  ])('rejects principal %s mutation', (field, changed) => {
    const value = localActorFixture();
    expect(() => parseLocalActorSession({ ...value, principal: { ...value.principal, [String(field)]: changed } })).toThrow();
  });
  it.each([0, -1, 900_001])('refuses invalid actual duration %s ms', (duration) => {
    const value = localActorFixture(0);
    value.principal.expires_at = new Date(duration).toISOString();
    expect(() => parseLocalActorSession(value)).toThrow();
  });
  it.each(['secret', 'token', 'role', 'amount_cents'])('refuses echoed or injected outer field %s', (field) => {
    expect(() => parseLocalActorSession({ ...localActorFixture(), [field]: 'synthetic-only' })).toThrow();
  });
  it('refuses a nested echoed credential', () => {
    const value = localActorFixture();
    expect(() => parseLocalActorSession({ ...value, principal: { ...value.principal, secret: 'synthetic-only' } })).toThrow();
  });
  it('only accepts exact actual logout response', () => {
    const value = { simulation: true, logged_out: true, bank_authority: false };
    expect(parseLocalActorLogout(value)).toEqual(value);
    for (const changed of [{ ...value, logged_out: false }, { ...value, token: 'synthetic-only' }, { ...value, bank_authority: true }]) expect(() => parseLocalActorLogout(changed)).toThrow();
  });
  it('GET current state has no credential or caller identity body', async () => {
    const fetcher = vi.fn().mockResolvedValue(http(localActorFixture())); vi.stubGlobal('fetch', fetcher);
    await readLocalActorSession();
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/v1/local-actor/session', { method: 'GET' });
  });
  it('login uses fixed username and original secret once, with no role/permission', async () => {
    const fetcher = vi.fn().mockResolvedValue(http(localActorFixture())); vi.stubGlobal('fetch', fetcher);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    await loginLocalActor(' synthetic-fixture-secret ');
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/v1/local-actor/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: LOCAL_USERNAME, secret: ' synthetic-fixture-secret ' }),
    });
    expect(storage).not.toHaveBeenCalled();
  });
  it('empty or oversized credential never calls transport', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    expect(() => loginLocalActor('')).toThrow(); expect(() => loginLocalActor('x'.repeat(1025))).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('logout posts only the actual empty DTO', async () => {
    const fetcher = vi.fn().mockResolvedValue(http({ simulation: true, logged_out: true, bank_authority: false })); vi.stubGlobal('fetch', fetcher);
    await logoutLocalActor();
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/v1/local-actor/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  });
  it('401 remains 401 rather than local success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(http({ detail: '未配置' }, 401)));
    await expect(readLocalActorSession()).rejects.toMatchObject({ status: 401 });
    await expect(loginLocalActor('synthetic-only')).rejects.toBeInstanceOf(ApiError);
  });
});
