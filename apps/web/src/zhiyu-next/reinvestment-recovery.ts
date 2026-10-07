import { parseMaturityPrepare, type MaturityPrepare } from '../api/full-maturity-execution';
import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { assetCheck as check, assetExact as exact, assetPath } from './asset-recovery';

export type ReinvestmentRequest = { expected_epoch_id: string; maturity_action_id: string; full_policy_id: string; expected_full_policy_version_id: string; mvp_asset_policy_id: string; expected_mvp_policy_version_id: string; expected_goal_policy_version_id: string | null; planning_mode: 'PORTFOLIO' | 'FIXED_LADDER'; client_request_id: string };
/** Original locators only: neither a received-money fact nor a grant. */
export type ReinvestmentRecovery = { protocol: 'zhiyu-next-reinvestment-recovery-v1'; user_id: string; maturity_prepare_request: MaturityPrepare; prepare_request: ReinvestmentRequest; portfolio_id: string | null; reviewed_portfolio_hash: string | null };
export function reinvestmentPath(path: string) { return path === '/zhiyu-next/assets/reinvestments/prepare' ? { kind: 'PREPARE' as const, portfolio_id: null } : assetPath(path)?.kind !== 'PREPARE' ? assetPath(path) : null; }
export const isReinvestmentPrepare = (path: string) => path === '/zhiyu-next/assets/reinvestments/prepare';
export function parseReinvestmentRequest(value: unknown): ReinvestmentRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'maturity_action_id', 'full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'expected_goal_policy_version_id', 'planning_mode', 'client_request_id']));
  check(['expected_epoch_id', 'maturity_action_id', 'full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'client_request_id'].every((key) => uuid(value[key])) && (value.expected_goal_policy_version_id === null || uuid(value.expected_goal_policy_version_id)) && ['PORTFOLIO', 'FIXED_LADDER'].includes(String(value.planning_mode)));
  return value as ReinvestmentRequest;
}
export function parseReinvestmentRecovery(value: unknown, path: string, body: Record<string, unknown>, epoch: string, client: string): ReinvestmentRecovery {
  const info = reinvestmentPath(path);
  check(info && object(value) && exact(value, ['protocol', 'user_id', 'maturity_prepare_request', 'prepare_request', 'portfolio_id', 'reviewed_portfolio_hash']) && value.protocol === 'zhiyu-next-reinvestment-recovery-v1' && uuid(value.user_id));
  const maturity = parseMaturityPrepare(value.maturity_prepare_request); const prepare = parseReinvestmentRequest(value.prepare_request);
  check(uuid(maturity.idempotency_key) && maturity.expected_epoch_id === epoch && prepare.expected_epoch_id === epoch && body.expected_epoch_id === epoch);
  if (info.kind === 'PREPARE') check(value.portfolio_id === null && value.reviewed_portfolio_hash === null && JSON.stringify(body) === JSON.stringify(prepare) && prepare.client_request_id === client);
  else check(info.portfolio_id === value.portfolio_id && digest(value.reviewed_portfolio_hash) && body.reviewed_portfolio_hash === value.reviewed_portfolio_hash && exact(body, info.kind === 'CONFIRM' ? ['expected_epoch_id', 'reviewed_portfolio_hash', 'accepted', 'client_request_id'] : ['expected_epoch_id', 'reviewed_portfolio_hash']) && (info.kind !== 'CONFIRM' || body.accepted === true && body.client_request_id === client));
  return value as ReinvestmentRecovery;
}
