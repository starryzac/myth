import { request } from '../api/http';
import { parseAssetPrepare, parseAssetPreview, parseAssetLookup, parseAssetPortfolio, type AssetPrepare, type AssetIntent, type AssetPreview, type AssetPortfolio } from '../api/full-asset-execution';
import type { MaturityAction } from '../api/full-maturity-execution';
import { parseLocalActorSession } from '../api/local-actor';
import { spendingHash as hash, spendingCanonicalJson as canonical } from '../api/spending-evidence';
import { releaseUUID5 } from '../api/goal-release-authorizations';
import { object } from '../features/policy-form';
import type { NextState, OperationResult } from './api';
import { context } from './policies';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
import { lookupNextMaturity } from './maturity';
import { getNextAsset, parseNextAssetResponse, type AssetView } from './assets';
import { parseReinvestmentRequest, parseReinvestmentRecovery, reinvestmentPath, type ReinvestmentRequest, type ReinvestmentRecovery } from './reinvestment-recovery';
import type { PendingOperation } from './operation';
import type { components } from '../../../../packages/contracts/schema';

const protocol = 'maturity-settled-reinvestment-v1';
const base = '/zhiyu-next/assets/reinvestments';
const same = (left: unknown, right: unknown) => canonical(left) === canonical(right);
/** The native maturity parser has already validated the complete event and originals. */
export const receivedMaturityEvent = (source: MaturityAction) => source.original_event as components['schemas']['OriginalMaturityEvent'];
export type ReinvestmentUpdate = { phase: 'PREPARE' | 'CONFIRM' | 'CONTINUE'; source: MaturityAction; view: AssetView | null; recovery: ReinvestmentRecovery; partial_preparation: boolean; resume_original: boolean };
export function nativeReinvestmentRequest(body: ReinvestmentRequest, source: MaturityAction): AssetPrepare {
  const requestBody = parseReinvestmentRequest(body); const event = receivedMaturityEvent(source);
  check(source.action_id === body.maturity_action_id && source.epoch_id === body.expected_epoch_id && source.service_receipt_verified && event.proof_status === 'VERIFIED' && event.service_receipt_verified && event.destination_account_id && (event.goal_id === null ? body.expected_goal_policy_version_id === null : body.expected_goal_policy_version_id !== null));
  const { maturity_action_id, client_request_id: _client, ...rest } = requestBody; void _client;
  return parseAssetPrepare({ ...rest, goal_id: event.goal_id, idempotency_key: `zhiyu-next:${body.expected_epoch_id}:maturity-reinvest:${maturity_action_id}` });
}
export async function readReinvestmentSource(recovery: ReinvestmentRecovery, environment: NextState): Promise<MaturityAction> {
  check(recovery.user_id === environment.dashboard.user_id && recovery.prepare_request.expected_epoch_id === environment.epoch_id);
  const lookup = await lookupNextMaturity({ protocol: 'zhiyu-next-maturity-recovery-v1', user_id: recovery.user_id, prepare_request: recovery.maturity_prepare_request, action_id: null, reviewed_command_hash: null }, environment);
  check(lookup.status === 'RECORDED' && lookup.action && lookup.action.action_id === recovery.prepare_request.maturity_action_id && lookup.action.service_receipt_verified);
  nativeReinvestmentRequest(recovery.prepare_request, lookup.action); return lookup.action;
}
/** The event proves an actual original return; it does not reserve that cash. */
export function verifyReinvestmentFunding(portfolio: AssetPortfolio, source: MaturityAction, body: ReinvestmentRequest): void {
  const event = receivedMaturityEvent(source);
  check(same(portfolio.original_request, nativeReinvestmentRequest(body, source)) && portfolio.user_id === source.user_id && portfolio.epoch_id === source.epoch_id && portfolio.total_purchase_cents > 0 && portfolio.total_purchase_cents <= event.principal_cents && portfolio.batches.every((batch) => batch.command.effect.goal_id === event.goal_id && batch.command.effect.return_account_id === event.destination_account_id && batch.command.effect.cash_uses!.every((use) => use.account_id === event.destination_account_id)));
}
async function verifyFrozen(portfolio: AssetPortfolio, source: MaturityAction, body: ReinvestmentRequest) {
  verifyReinvestmentFunding(portfolio, source, body);
  check(portfolio.portfolio_id === await releaseUUID5('8770c713-1586-598a-9c02-5822631b4402', `${portfolio.user_id}:${portfolio.epoch_id}:${portfolio.original_request.idempotency_key}`));
  for (const [i, batch] of portfolio.batches.entries()) check(batch.action_id === await releaseUUID5(portfolio.portfolio_id, `batch:${i + 1}`));
}
export async function previewNextReinvestment(recovery: ReinvestmentRecovery, environment: NextState): Promise<AssetPreview> {
  const source = await readReinvestmentSource(recovery, environment); const native = nativeReinvestmentRequest(recovery.prepare_request, source);
  const value = await request(`${base}/preview`, 'POST', recovery.prepare_request, (value) => { context(value, environment); check(object(value) && exact(value, ['simulation', 'variant', 'environment_id', 'epoch_id', 'protocol', 'source_event', 'preview', 'bank_authority', 'event_is_exclusive_funding_reservation']) && value.protocol === protocol && value.bank_authority === false && value.event_is_exclusive_funding_reservation === false && same(value.source_event, source.original_event)); return value; });
  const preview = await parseAssetPreview(value.preview, native, environment.dashboard.user_id);
  if (preview.portfolio) await verifyFrozen(await parseAssetPortfolio(preview.portfolio, environment.dashboard.user_id), source, recovery.prepare_request);
  return preview;
}
async function readPrepared(recovery: ReinvestmentRecovery, source: MaturityAction, environment: NextState): Promise<{ view: AssetView | null; partial: boolean; resume: boolean }> {
  const raw = await request(`${base}/by-maturity-action/${recovery.prepare_request.maturity_action_id}`, 'GET', undefined, (value) => { context(value, environment); check(object(value) && value.protocol === protocol && value.replacement_allowed === false); return value; });
  const native = nativeReinvestmentRequest(recovery.prepare_request, source);
  if (raw.status !== 'RECORDED') {
    check(exact(raw, ['simulation', 'variant', 'environment_id', 'epoch_id', 'protocol', 'status', 'original_intent', 'native_lookup', 'replacement_allowed', 'bank_execute_allowed']) && ['NOT_FOUND_NOT_FINAL', 'PARTIAL_BINDING_NOT_FINAL'].includes(String(raw.status)) && raw.bank_execute_allowed === false);
    if (raw.original_intent === null) { check(raw.native_lookup === null && raw.status === 'NOT_FOUND_NOT_FINAL'); return { view: null, partial: false, resume: false }; }
    const intent = raw.original_intent;
    check(object(intent) && exact(intent, ['protocol', 'user_id', 'epoch_id', 'original_request', 'original_maturity_event', 'original_native_request', 'principal_at_capture', 'captured_at', 'bank_authority', 'event_is_exclusive_funding_reservation']) && intent.protocol === protocol && intent.user_id === recovery.user_id && intent.epoch_id === environment.epoch_id && intent.bank_authority === false && intent.event_is_exclusive_funding_reservation === false && same(intent.original_request, recovery.prepare_request) && same(intent.original_maturity_event, source.original_event) && same(intent.original_native_request, native) && typeof intent.captured_at === 'string' && Number.isFinite(Date.parse(intent.captured_at)));
    const actor = parseLocalActorSession({ simulation: true, principal: intent.principal_at_capture, bank_authority: false, confirms_financial_action: false }).principal;
    const event = receivedMaturityEvent(source);
    check(actor.role === 'USER' && actor.user_id === recovery.user_id && Date.parse(actor.issued_at) <= Date.parse(intent.captured_at) && Date.parse(intent.captured_at) < Date.parse(actor.expires_at) && event.settled_at && Date.parse(event.settled_at) <= Date.parse(intent.captured_at));
    const nativeIntent: AssetIntent = { protocol: 'full-asset-browser-command-v1', kind: 'PREPARE', user_id: recovery.user_id, portfolio_id: null, path: '/full-asset-executions/prepare', body: native, body_json: JSON.stringify(native), request_hash: await hash(native), reviewed_portfolio: null };
    const lookup = await parseAssetLookup(raw.native_lookup, nativeIntent);
    if (lookup.original === null) { check(raw.status === 'NOT_FOUND_NOT_FINAL'); return { view: null, partial: true, resume: true }; }
    check(raw.status === 'PARTIAL_BINDING_NOT_FINAL'); const portfolio = await parseNextAssetResponse(lookup.original, environment); await verifyFrozen(portfolio.original_portfolio, source, recovery.prepare_request);
    const resume = portfolio.state === 'PREPARED_UNRESERVED' && portfolio.original_consent === null && portfolio.batches.every((batch) => batch.original_action === null || batch.original_action.status === 'PLANNED' && batch.original_action.bank_status === null && batch.original_action.receipt === null);
    return { view: { portfolio, signed: null }, partial: true, resume };
  }
  check(exact(raw, ['simulation', 'variant', 'environment_id', 'epoch_id', 'protocol', 'status', 'source_binding', 'portfolio', 'replacement_allowed']));
  const portfolio = await parseNextAssetResponse(raw.portfolio, environment); const p = portfolio.original_portfolio; await verifyFrozen(p, source, recovery.prepare_request);
  const binding = raw.source_binding; const event = receivedMaturityEvent(source);
  check(object(binding) && exact(binding, ['protocol', 'user_id', 'epoch_id', 'source_action_id', 'source_bank_operation_id', 'source_receipt_id', 'source_originals_hash', 'source_cash_account_id', 'original_request', 'original_native_request', 'portfolio_id', 'portfolio_hash', 'original_action_ids', 'source_principal_cents', 'original_purchase_cents', 'bank_authority', 'event_is_exclusive_funding_reservation']) && binding.protocol === protocol && binding.user_id === recovery.user_id && binding.epoch_id === environment.epoch_id && binding.source_action_id === source.action_id && binding.source_bank_operation_id === event.bank_operation_id && binding.source_receipt_id === event.original_receipt_id && binding.source_originals_hash === event.originals_hash && binding.source_cash_account_id === event.destination_account_id && same(binding.original_request, recovery.prepare_request) && same(binding.original_native_request, native) && binding.portfolio_id === p.portfolio_id && binding.portfolio_hash === p.portfolio_hash && same(binding.original_action_ids, p.batches.map((batch) => batch.action_id)) && binding.source_principal_cents === event.principal_cents && binding.original_purchase_cents === p.total_purchase_cents && binding.bank_authority === false && binding.event_is_exclusive_funding_reservation === false);
  if (recovery.portfolio_id !== null) check(recovery.portfolio_id === p.portfolio_id && recovery.reviewed_portfolio_hash === p.portfolio_hash);
  const fresh = await getNextAsset(p.portfolio_id, environment); check(same(fresh.portfolio.original_portfolio, p));
  return { view: fresh, partial: false, resume: false };
}
export async function getNextReinvestment(recovery: ReinvestmentRecovery, environment: NextState): Promise<ReinvestmentUpdate> {
  const source = await readReinvestmentSource(recovery, environment); const read = await readPrepared(recovery, source, environment); const response = read.view?.portfolio; const p = response?.original_portfolio;
  const next = response?.batches.find((batch) => !batch.original_action?.receipt);
  const accepted = !!next?.original_action && (['UNKNOWN', 'SUBMITTED'].includes(next.original_action.status) || next.original_action.bank_status !== null) && next.original_action.receipt === null;
  const unaccepted = !!next?.original_action && ['PLANNED', 'AUTHORIZED'].includes(next.original_action.status) && next.original_action.bank_status === null && !!read.view?.signed && Date.parse(response!.as_of) < Date.parse(read.view.signed.expires_at) && response!.original_consent_verified;
  return { phase: 'PREPARE', source, view: read.view, recovery: { ...recovery, portfolio_id: p?.portfolio_id ?? null, reviewed_portfolio_hash: p?.portfolio_hash ?? null }, partial_preparation: read.partial, resume_original: read.partial ? read.resume : !!read.view?.signed && !!response?.original_consent && response.current_epoch_open && !['STOPPED', 'RETAINED_HISTORY', 'SERVICE_RECEIPTS_VERIFIED'].includes(response.state) && (accepted || unaccepted) };
}
export async function validateReinvestmentOriginal(pending: PendingOperation, environment: NextState): Promise<void> {
  const recovery = parseReinvestmentRecovery(pending.reinvestment_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id); check(pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id && recovery.user_id === environment.dashboard.user_id);
  if (reinvestmentPath(pending.path)?.kind === 'PREPARE') { await readReinvestmentSource(recovery, environment); return; }
  const read = await getNextReinvestment(recovery, environment); check(!read.partial_preparation && read.view);
  if (pending.path.endsWith('/confirm-and-execute')) check(read.view.portfolio.state === 'PREPARED_UNRESERVED' && read.view.signed === null && read.view.portfolio.original_consent === null && Date.parse(read.view.portfolio.as_of) < Date.parse(read.view.portfolio.original_portfolio.expires_at));
  else check(read.resume_original);
}
export async function readReinvestmentOriginal(pending: PendingOperation, environment: NextState): Promise<OperationResult & { reinvestment?: ReinvestmentUpdate }> {
  const recovery = parseReinvestmentRecovery(pending.reinvestment_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id); check(pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id);
  const update = await getNextReinvestment(recovery, environment); update.phase = reinvestmentPath(pending.path)!.kind;
  const response = update.view?.portfolio;
  if (update.phase === 'CONFIRM' && update.view?.signed) check(same(update.view.signed.original_wrapper_request, pending.body));
  const complete = !!response && !update.partial_preparation && (update.phase === 'PREPARE' || ['SERVICE_RECEIPTS_VERIFIED', 'STOPPED', 'RETAINED_HISTORY'].includes(response.state) && !!update.view?.signed && !!response.original_consent);
  return { simulation: true, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: pending.client_request_id, status: complete ? 'COMPLETED' : 'PENDING', reinvestment: update };
}
export function sameReinvestmentUpdate(update: ReinvestmentUpdate | null, pending: PendingOperation): boolean { return !!update && !!pending.reinvestment_recovery && update.recovery.user_id === pending.reinvestment_recovery.user_id && same(update.recovery.prepare_request, pending.reinvestment_recovery.prepare_request) && same(update.recovery.maturity_prepare_request, pending.reinvestment_recovery.maturity_prepare_request) && (pending.reinvestment_recovery.portfolio_id === null || pending.reinvestment_recovery.portfolio_id === update.recovery.portfolio_id && pending.reinvestment_recovery.reviewed_portfolio_hash === update.recovery.reviewed_portfolio_hash); }
const viewKey = (environment: NextState) => `zhiyu-next-reinvestment-read-locator-v1:${environment.environment_id}:${environment.epoch_id}`;
export function rememberReinvestmentView(update: ReinvestmentUpdate, environment: NextState): void { localStorage.setItem(viewKey(environment), JSON.stringify({ protocol: 'zhiyu-next-reinvestment-read-locator-v1', environment_id: environment.environment_id, epoch_id: environment.epoch_id, user_id: environment.dashboard.user_id, maturity_prepare_request: update.recovery.maturity_prepare_request, prepare_request: update.recovery.prepare_request })); }
export async function readReinvestmentView(environment: NextState): Promise<ReinvestmentUpdate | null> {
  const raw = localStorage.getItem(viewKey(environment)); if (raw === null) return null; const value: unknown = JSON.parse(raw);
  check(object(value) && exact(value, ['protocol', 'environment_id', 'epoch_id', 'user_id', 'maturity_prepare_request', 'prepare_request']) && value.protocol === 'zhiyu-next-reinvestment-read-locator-v1' && value.environment_id === environment.environment_id && value.epoch_id === environment.epoch_id && value.user_id === environment.dashboard.user_id);
  const recovery = { protocol: 'zhiyu-next-reinvestment-recovery-v1' as const, user_id: environment.dashboard.user_id, maturity_prepare_request: value.maturity_prepare_request, prepare_request: value.prepare_request, portfolio_id: null, reviewed_portfolio_hash: null };
  const body = parseReinvestmentRequest(value.prepare_request); return getNextReinvestment(parseReinvestmentRecovery(recovery, `${base}/prepare`, body, environment.epoch_id, body.client_request_id), environment);
}
