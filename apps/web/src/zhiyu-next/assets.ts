import { request } from '../api/http';
import { parseAssetPrepare, parseAssetConfirm, parseAssetLookup, parseAssetPreview, parseAssetPortfolio, parseAssetResponse, type AssetPrepare, type AssetIntent, type AssetPreview, type AssetResponse } from '../api/full-asset-execution';
import { parseLocalActorSession, type LocalActorSession } from '../api/local-actor';
import { releaseUUID5 } from '../api/goal-release-authorizations';
import { spendingHash as hash, spendingCanonicalJson as canonical, spendingUUID as uuid } from '../api/spending-evidence';
import { object } from '../features/policy-form';
import { parseAction } from '../zhiyu/api';
import type { NextState, OperationResult } from './api';
import { context } from './policies';
import { assetCheck as check, assetExact as exact, assetPath, parseAssetRequest, parseAssetRecovery, type AssetRequest, type AssetRecovery } from './asset-recovery';
import type { PendingOperation } from './operation';
import { makePending } from './operation';

const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const effectHash = (effect: AssetResponse['original_portfolio']['batches'][number]['command']['effect']) => hash({ ...effect, cash_uses: [...effect.cash_uses!].sort((a, b) => a.account_id.localeCompare(b.account_id)), income_uses: [...effect.income_uses!].sort((a, b) => `${a.origin_transaction_id}:${a.account_id}`.localeCompare(`${b.origin_transaction_id}:${b.account_id}`)), policy_version_ids: [...effect.policy_version_ids!].sort() });
export const nativeAssetKey = (epoch: string, family: 'asset-prepare' | 'asset-confirm', client: string) => `zhiyu-next:${epoch}:${family}:${client}`;
export function nativeAssetRequest(body: AssetRequest): AssetPrepare { const { client_request_id, ...rest } = parseAssetRequest(body); return parseAssetPrepare({ ...rest, idempotency_key: nativeAssetKey(body.expected_epoch_id, 'asset-prepare', client_request_id) }); }
export function wrapperAssetRequest(body: AssetPrepare): AssetRequest { const prefix = `zhiyu-next:${body.expected_epoch_id}:asset-prepare:`; check(body.idempotency_key.startsWith(prefix)); const { idempotency_key, ...rest } = body; return parseAssetRequest({ ...rest, client_request_id: idempotency_key.slice(prefix.length) }); }
export type SignedAssetAcceptance = { protocol: 'zhiyu-asset-portfolio-user-v1'; user_id: string; epoch_id: string; portfolio_id: string; portfolio_hash: string; original_wrapper_request: Record<string, unknown>; original_native_request: Record<string, unknown>; principal_at_acceptance: LocalActorSession['principal']; accepted_at: string; expires_at: string; bank_authority: false };
export type AssetView = { portfolio: AssetResponse; signed: SignedAssetAcceptance | null };
export type AssetUpdate = AssetView & { phase: 'PREPARE' | 'CONFIRM' | 'CONTINUE'; recovery: AssetRecovery; resume_original: boolean };

/** Reuse the native complete financial parser, adding Next identity and actual settlement gates. */
export async function parseNextAssetResponse(v: unknown, environment: NextState, recovery?: AssetRecovery): Promise<AssetResponse> {
  const parsed = await parseAssetResponse(v, environment.dashboard.user_id); const p = parsed.original_portfolio;
  check(parsed.epoch_id === environment.epoch_id && p.portfolio_id === await releaseUUID5('8770c713-1586-598a-9c02-5822631b4402', `${p.user_id}:${p.epoch_id}:${p.original_request.idempotency_key}`));
  if (recovery) check(canonical(p.original_request) === canonical(nativeAssetRequest(recovery.prepare_request)) && (!recovery.portfolio_id || recovery.portfolio_id === p.portfolio_id && recovery.reviewed_portfolio_hash === p.portfolio_hash));
  for (const [i, row] of parsed.batches.entries()) {
    const fixed = p.batches[i]!; check(fixed.action_id === await releaseUUID5(p.portfolio_id, `batch:${i + 1}`) && fixed.command.effect_hash === await effectHash(fixed.command.effect));
    if (row.original_action === null) continue;
    const action = parseAction(row.original_action); check(action.user_id === p.user_id && action.autonomy_level === 'ASK_ONCE' && ['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED', 'FAILED', 'REJECTED', 'CANCELLED', 'EXPIRED', 'INVALIDATED', 'BLOCKED'].includes(action.status));
    if (action.receipt) check(['SUCCEEDED', 'RECONCILED'].includes(action.status) && action.bank_status === 'SETTLED' && action.receipt.status === 'SUCCEEDED' && action.receipt.posting_ids.length >= 2);
    if (['SUCCEEDED', 'RECONCILED'].includes(action.status)) check(action.receipt !== null && action.bank_status === 'SETTLED');
  }
  return parsed;
}
export function parseSignedAsset(v: unknown, response: AssetResponse): SignedAssetAcceptance | null {
  if (v === null) return null;
  const p = response.original_portfolio;
  check(object(v) && exact(v, ['protocol', 'user_id', 'epoch_id', 'portfolio_id', 'portfolio_hash', 'original_wrapper_request', 'original_native_request', 'principal_at_acceptance', 'accepted_at', 'expires_at', 'bank_authority']) && v.protocol === 'zhiyu-asset-portfolio-user-v1' && v.user_id === p.user_id && v.epoch_id === p.epoch_id && v.portfolio_id === p.portfolio_id && v.portfolio_hash === p.portfolio_hash && v.bank_authority === false && time(v.accepted_at) && time(v.expires_at));
  const body = v.original_wrapper_request; check(object(body) && exact(body, ['expected_epoch_id', 'reviewed_portfolio_hash', 'accepted', 'client_request_id']) && body.expected_epoch_id === p.epoch_id && body.reviewed_portfolio_hash === p.portfolio_hash && body.accepted === true && uuid(body.client_request_id));
  const native = parseAssetConfirm(v.original_native_request); check(canonical(native) === canonical({ expected_epoch_id: p.epoch_id, reviewed_portfolio_hash: p.portfolio_hash, accepted: true, idempotency_key: nativeAssetKey(p.epoch_id, 'asset-confirm', body.client_request_id) }));
  const actor = parseLocalActorSession({ simulation: true, principal: v.principal_at_acceptance, bank_authority: false, confirms_financial_action: false });
  check(actor.principal.role === 'USER' && actor.principal.user_id === p.user_id && Date.parse(actor.principal.issued_at) <= Date.parse(v.accepted_at) && Date.parse(v.accepted_at) < Date.parse(actor.principal.expires_at) && Date.parse(v.accepted_at) >= Date.parse(p.prepared_at) && Date.parse(v.accepted_at) <= Date.parse(response.as_of) && Date.parse(v.expires_at) > Date.parse(v.accepted_at) && Date.parse(v.expires_at) <= Math.min(Date.parse(actor.principal.expires_at), Date.parse(p.expires_at)));
  if (response.original_consent) check(canonical(response.original_consent.original_request) === canonical(native));
  return v as SignedAssetAcceptance;
}
export async function previewNextAsset(body: AssetRequest, environment: NextState): Promise<AssetPreview> {
  const b = parseAssetRequest(body); const v = await request('/zhiyu-next/assets/portfolios/preview', 'POST', b, (v) => { context(v, environment); return v; }); const native = await parseAssetPreview(v.preview, nativeAssetRequest(b), environment.dashboard.user_id);
  if (native.portfolio) { const p = await parseAssetPortfolio(native.portfolio, environment.dashboard.user_id); check(p.epoch_id === environment.epoch_id && p.portfolio_id === await releaseUUID5('8770c713-1586-598a-9c02-5822631b4402', `${p.user_id}:${p.epoch_id}:${p.original_request.idempotency_key}`)); for (const [i, row] of p.batches.entries()) check(row.action_id === await releaseUUID5(p.portfolio_id, `batch:${i + 1}`) && row.command.effect_hash === await effectHash(row.command.effect)); }
  return native;
}
export async function getNextAsset(id: string, environment: NextState, recovery?: AssetRecovery): Promise<AssetView> { check(uuid(id)); const v = await request(`/zhiyu-next/assets/portfolios/${id}`, 'GET', undefined, (v) => { context(v, environment); return v; }); const portfolio = await parseNextAssetResponse(v.portfolio, environment, recovery); check(portfolio.original_portfolio.portfolio_id === id); return { portfolio, signed: parseSignedAsset(v.original_signed_acceptance, portfolio) }; }
export async function validateAssetOriginal(original: PendingOperation, environment: NextState) { const info = assetPath(original.path); check(info && original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id); const recovery = parseAssetRecovery(original.asset_recovery, original.path, original.body, environment.epoch_id, original.client_request_id); check(recovery.user_id === environment.dashboard.user_id); return { info, recovery }; }
export async function readAssetOriginal(original: PendingOperation, environment: NextState): Promise<OperationResult & { asset?: AssetUpdate }> {
  const { info, recovery } = await validateAssetOriginal(original, environment); let view: AssetView;
  if (info.kind === 'PREPARE') {
    const body = nativeAssetRequest(recovery.prepare_request); const nativeIntent: AssetIntent = { protocol: 'full-asset-browser-command-v1', kind: 'PREPARE', user_id: recovery.user_id, portfolio_id: null, path: '/full-asset-executions/prepare', body, body_json: JSON.stringify(body), request_hash: await hash(body), reviewed_portfolio: null };
    const v = await request(`/zhiyu-next/assets/portfolio-commands/${environment.epoch_id}/${original.client_request_id}`, 'GET', undefined, (v) => { context(v, environment); return v; });
    const lookup = await parseAssetLookup(v, nativeIntent);
    if (v.wrapper_operation !== null && v.wrapper_operation !== undefined) {
      const side = v.wrapper_operation; check(lookup.status === 'NOT_FOUND_NOT_FINAL' && object(side) && exact(side, ['status', 'original_request', 'error', 'rejection_proof', 'bank_authority']) && side.status === 'REJECTED' && side.bank_authority === false && object(side.original_request) && exact(side.original_request, ['path', 'body']) && side.original_request.path === `/api/v1${original.path}` && canonical(side.original_request.body) === canonical(original.body));
      const proof = side.rejection_proof; check(object(proof) && exact(proof, ['protocol', 'epoch_id', 'user_id', 'checked_at', 'audit_chain_status', 'requested_native_acceptance_absent']) && proof.protocol === 'zhiyu-asset-no-native-preparation-v1' && proof.epoch_id === original.epoch_id && proof.user_id === recovery.user_id && time(proof.checked_at) && proof.audit_chain_status === 'VALID' && proof.requested_native_acceptance_absent === true);
      check(object(side.error) && exact(side.error, ['code', 'message', 'status_code']) && typeof side.error.code === 'string' && !!side.error.code && typeof side.error.message === 'string' && !!side.error.message && Number.isSafeInteger(side.error.status_code) && Number(side.error.status_code) >= 400 && Number(side.error.status_code) < 500);
      return { simulation: true, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id, status: 'REJECTED', result: side.error };
    }
    if (lookup.status === 'NOT_FOUND_NOT_FINAL') return { simulation: true, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id, status: 'PENDING' };
    const portfolio = await parseNextAssetResponse(lookup.original, environment, recovery); view = { portfolio, signed: null };
  } else {
    view = await getNextAsset(info.portfolio_id!, environment, recovery);
    if (info.kind === 'CONFIRM' && view.signed) check(canonical(view.signed.original_wrapper_request) === canonical(original.body));
  }
  const p = view.portfolio, fixed = p.original_portfolio; const next = p.batches.find((r) => r.original_action?.receipt === null || !r.original_action);
  const completed = info.kind === 'PREPARE' || ['SERVICE_RECEIPTS_VERIFIED', 'STOPPED', 'RETAINED_HISTORY'].includes(p.state) && (info.kind !== 'CONFIRM' || !!view.signed && !!p.original_consent);
  const acceptedRecovery = !!next?.original_action && (['UNKNOWN', 'SUBMITTED'].includes(next.original_action.status) || next.original_action.bank_status !== null) && next.original_action.receipt === null;
  const unacceptedOriginal = !!next?.original_action && ['PLANNED', 'AUTHORIZED'].includes(next.original_action.status) && next.original_action.bank_status === null && next.original_action.receipt === null && !!view.signed && Date.parse(p.as_of) < Date.parse(view.signed.expires_at) && p.original_consent_verified;
  const resume_original = info.kind !== 'PREPARE' && !!view.signed && !!p.original_consent && p.current_epoch_open && !['STOPPED', 'RETAINED_HISTORY', 'SERVICE_RECEIPTS_VERIFIED'].includes(p.state) && (acceptedRecovery || unacceptedOriginal);
  return { simulation: true, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id, status: completed ? 'COMPLETED' : 'PENDING', asset: { ...view, phase: info.kind, recovery: { ...recovery, portfolio_id: fixed.portfolio_id, reviewed_portfolio_hash: fixed.portfolio_hash }, resume_original } };
}
export function sameAssetUpdate(update: AssetUpdate | null, original: PendingOperation) { return !!update && !!original.asset_recovery && update.recovery.user_id === original.asset_recovery.user_id && update.portfolio.epoch_id === original.epoch_id && canonical(update.recovery.prepare_request) === canonical(original.asset_recovery.prepare_request) && (assetPath(original.path)?.kind === 'PREPARE' || update.recovery.portfolio_id === original.asset_recovery.portfolio_id); }
const viewKey = (e: NextState) => `zhiyu-next-asset-read-locator-v1:${e.environment_id}:${e.epoch_id}`;
export function rememberAssetView(update: AssetUpdate, environment: NextState) { check(update.recovery.user_id === environment.dashboard.user_id && update.portfolio.epoch_id === environment.epoch_id); localStorage.setItem(viewKey(environment), JSON.stringify({ protocol: 'zhiyu-next-asset-read-locator-v1', environment_id: environment.environment_id, epoch_id: environment.epoch_id, user_id: update.recovery.user_id, prepare_request: update.recovery.prepare_request })); }
export async function readAssetView(environment: NextState): Promise<AssetUpdate | null> { const raw = localStorage.getItem(viewKey(environment)); if (raw === null) return null; const v: unknown = JSON.parse(raw); check(object(v) && exact(v, ['protocol', 'environment_id', 'epoch_id', 'user_id', 'prepare_request']) && v.protocol === 'zhiyu-next-asset-read-locator-v1' && v.environment_id === environment.environment_id && v.epoch_id === environment.epoch_id && v.user_id === environment.dashboard.user_id); const body = parseAssetRequest(v.prepare_request); const original = makePending(environment, '/zhiyu-next/assets/portfolios/prepare', body, undefined, undefined, { protocol: 'zhiyu-next-asset-recovery-v1', user_id: String(v.user_id), prepare_request: body, portfolio_id: null, reviewed_portfolio_hash: null }); const result = await readAssetOriginal(original, environment); if (!result.asset) return null; return { ...result.asset, ...(await getNextAsset(result.asset.portfolio.original_portfolio.portfolio_id, environment, result.asset.recovery)) }; }
