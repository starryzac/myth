import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { request } from './http';

export const LOCAL_USERNAME = 'bounded-user';
/** Generated from the registered actual server DTO; display grants no authority. */
export type LocalActorSession = components['schemas']['LocalSessionResponse'];
export type LocalActorRole = LocalActorSession['principal']['role'];
export type LocalActorLogout = { simulation: true; logged_out: true; bank_authority: false };

function check(value: unknown): asserts value {
  if (!value) throw new Error('本地身份响应无法完整核对，显示身份已清空。');
}
const exact = (value: Record<string, unknown>, fields: string[]) =>
  Object.keys(value).sort().join('|') === [...fields].sort().join('|');
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const clock = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));

export function parseLocalActorSession(value: unknown): LocalActorSession {
  check(object(value) && exact(value, ['simulation', 'principal', 'bank_authority', 'confirms_financial_action']) && value.simulation === true && value.bank_authority === false && value.confirms_financial_action === false);
  const principal = value.principal;
  check(object(principal) && exact(principal, ['user_id', 'role', 'session_id', 'issued_at', 'expires_at', 'authentication_source', 'authenticated', 'human_identity_verified']));
  check(uuid(principal.user_id) && uuid(principal.session_id) && ['USER', 'AGENT', 'REVIEWER', 'SYSTEM', 'DEMO_ADMIN'].includes(principal.role as string) && principal.authentication_source === 'LOCAL_SIGNED_SESSION' && principal.authenticated === true && principal.human_identity_verified === false && clock(principal.issued_at) && clock(principal.expires_at));
  const duration = Date.parse(String(principal.expires_at)) - Date.parse(String(principal.issued_at));
  check(duration > 0 && duration <= 900_000);
  return value as LocalActorSession;
}
export function parseLocalActorLogout(value: unknown): LocalActorLogout {
  check(object(value) && exact(value, ['simulation', 'logged_out', 'bank_authority']) && value.simulation === true && value.logged_out === true && value.bank_authority === false);
  return value as LocalActorLogout;
}
export const readLocalActorSession = () => request('/local-actor/session', 'GET', undefined, parseLocalActorSession);
export function loginLocalActor(secret: string): Promise<LocalActorSession> {
  check(typeof secret === 'string' && secret.length >= 1 && secret.length <= 1024);
  // No token, original raw response, secret or authorization context is retained.
  return request('/local-actor/login', 'POST', { username: LOCAL_USERNAME, secret }, parseLocalActorSession);
}
export const logoutLocalActor = () => request('/local-actor/logout', 'POST', {}, parseLocalActorLogout);
