/** TOOL_ONLY HTTP fixture. No credentials, cookie, user or financial proof was observed. */
import type { LocalActorSession } from '../api/local-actor';
export function localActorFixture(now = Date.now()): LocalActorSession {
  return {
    simulation: true, bank_authority: false, confirms_financial_action: false,
    principal: {
      user_id: '11111111-1111-1111-1111-111111111111', role: 'USER',
      session_id: '22222222-2222-2222-2222-222222222222',
      issued_at: new Date(now).toISOString(), expires_at: new Date(now + 900_000).toISOString(),
      authentication_source: 'LOCAL_SIGNED_SESSION', authenticated: true, human_identity_verified: false,
    },
  };
}
