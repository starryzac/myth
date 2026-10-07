import { object } from '../features/policy-form';
import { spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';

export type AssetRequest = { expected_epoch_id: string; full_policy_id: string; expected_full_policy_version_id: string; mvp_asset_policy_id: string; expected_mvp_policy_version_id: string; goal_id: string | null; expected_goal_policy_version_id: string | null; planning_mode: 'PORTFOLIO' | 'FIXED_LADDER'; client_request_id: string };
export type AssetRecovery = { protocol: 'zhiyu-next-asset-recovery-v1'; user_id: string; prepare_request: AssetRequest; portfolio_id: string | null; reviewed_portfolio_hash: string | null };
export function assetCheck(value: unknown): asserts value { if (!value) throw new Error('资产原组合、实签确认或原请求未匹配，请保留同一定位。'); }
export const assetExact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).sort().join('|') === [...fields].sort().join('|');
export function assetPath(path: string) { if (path === '/zhiyu-next/assets/portfolios/prepare') return { kind: 'PREPARE' as const, portfolio_id: null }; const m = /^\/zhiyu-next\/assets\/portfolios\/([0-9a-f-]{36})\/(confirm-and-execute|continue-original)$/.exec(path); if (!m || !uuid(m[1])) return null; return { kind: m[2] === 'confirm-and-execute' ? 'CONFIRM' as const : 'CONTINUE' as const, portfolio_id: m[1]! }; }
export const isAssetPath = (path: string) => !!assetPath(path);
export const assetUsesClientId = (path: string) => ['PREPARE', 'CONFIRM'].includes(assetPath(path)?.kind ?? '');
export function parseAssetRequest(v: unknown): AssetRequest {
  assetCheck(object(v) && assetExact(v, ['expected_epoch_id', 'full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'goal_id', 'expected_goal_policy_version_id', 'planning_mode', 'client_request_id']));
  assetCheck(['expected_epoch_id', 'full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'client_request_id'].every((k) => uuid(v[k])) && ['PORTFOLIO', 'FIXED_LADDER'].includes(String(v.planning_mode)) && (v.goal_id === null ? v.expected_goal_policy_version_id === null : uuid(v.goal_id) && uuid(v.expected_goal_policy_version_id)));
  return v as AssetRequest;
}
/** A compact original locator, not a financial input or an authority grant. */
export function parseAssetRecovery(v: unknown, path: string, body: Record<string, unknown>, epoch: string, clientId: string): AssetRecovery {
  const info = assetPath(path); assetCheck(info && object(v) && assetExact(v, ['protocol', 'user_id', 'prepare_request', 'portfolio_id', 'reviewed_portfolio_hash']) && v.protocol === 'zhiyu-next-asset-recovery-v1' && uuid(v.user_id));
  const prepare = parseAssetRequest(v.prepare_request); assetCheck(prepare.expected_epoch_id === epoch && body.expected_epoch_id === epoch);
  if (info.kind === 'PREPARE') assetCheck(v.portfolio_id === null && v.reviewed_portfolio_hash === null && JSON.stringify(body) === JSON.stringify(prepare) && body.client_request_id === clientId);
  else {
    assetCheck(v.portfolio_id === info.portfolio_id && digest(v.reviewed_portfolio_hash) && body.reviewed_portfolio_hash === v.reviewed_portfolio_hash && assetExact(body, info.kind === 'CONFIRM' ? ['expected_epoch_id', 'reviewed_portfolio_hash', 'accepted', 'client_request_id'] : ['expected_epoch_id', 'reviewed_portfolio_hash']));
    if (info.kind === 'CONFIRM') assetCheck(body.accepted === true && body.client_request_id === clientId);
  }
  return v as AssetRecovery;
}
