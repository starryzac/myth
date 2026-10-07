import { request } from '../api/http';
import { spendingHash as hash, spendingCanonicalJson as canonical, spendingUUID as uuid, spendingDigest as digest } from '../api/spending-evidence';
import { releaseUUID5 } from '../api/goal-release-authorizations';
import { parseLocalActorSession } from '../api/local-actor';
import { object } from '../features/policy-form';
import type { NextState, OperationResult } from './api';
import type { LossBinding, SignedLoss } from './loss';
import { strictLossAction } from './loss';
import { context } from './policies';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
import { revisionBase as base, revisionPath, parseLossRevisionRecovery, parseRenewalRequest, parseRevisionPrepare, type RenewalRequest, type LossRevisionRecovery } from './loss-revision-recovery';
import type { PendingOperation } from './operation';

const priceProtocol = 'simulated-fixed-early-price-revision-v2', lossProtocol = 'lossy-early-redemption-quote-revision-v2';
const same = (left: unknown, right: unknown) => canonical(left) === canonical(right);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const identityKeys = ['simulation', 'variant', 'environment_id', 'epoch_id'];
const displayKeys = ['server_as_of', 'quote_validity'];
type Quote = LossBinding['quote'];
export type QuoteRevision = { protocol: 'recovery-quote-v1'; price_protocol: typeof priceProtocol; simulation: true; rounding: 'CEIL_CENT'; user_id: string; position_id: string; destination_account_id: string; goal_id: string | null; quote: Quote; revision: { epoch_id: string; client_request_id: string; revision_number: number; ancestors: { quote_id: string; evidence_hash: string }[] } };
type QuoteDisplay = { server_as_of: string; quote_validity: { status: 'VALID' | 'EXPIRED'; as_of: string; expires_at: string } };
export type RevisionQuoteView = QuoteDisplay & { revision: QuoteRevision; quote_hash: string; quote_evidence_hash: string };
export type RevisionBinding = Omit<LossBinding, 'protocol' | 'source_request' | 'loss_basis' | 'rounding' | 'accrued_interest_cents' | 'foregone_interest_cents' | 'interest_treatment'> & { protocol: typeof lossProtocol; source_request: NonNullable<LossRevisionRecovery['prepare_request']> };
export type RevisionSigned = Omit<SignedLoss, 'protocol'> & { protocol: 'lossy-early-redemption-quote-revision-user-v2' };
export type RevisionView = QuoteDisplay & { binding: RevisionBinding; action: Awaited<ReturnType<typeof strictLossAction>>; signed: RevisionSigned | null };
export type LossRevisionUpdate = { phase: 'RENEW' | 'PREPARE' | 'CONFIRM' | 'EXECUTE'; recovery: LossRevisionRecovery; quote: RevisionQuoteView | null; view: RevisionView | null; partial: boolean; resume_original: boolean };

function display(value: Record<string, unknown>, quote: Quote): QuoteDisplay {
  const validity = value.quote_validity;
  check(time(value.server_as_of) && object(validity) && exact(validity, ['status', 'as_of', 'expires_at']) && validity.as_of === value.server_as_of && validity.expires_at === quote.expires_at && Date.parse(value.server_as_of) >= Date.parse(quote.request_at) && validity.status === (Date.parse(value.server_as_of) < Date.parse(quote.expires_at) ? 'VALID' : 'EXPIRED'));
  return { server_as_of: value.server_as_of, quote_validity: validity as QuoteDisplay['quote_validity'] };
}
export async function parseQuoteRevision(value: unknown, environment: NextState, renewal: RenewalRequest): Promise<QuoteRevision> {
  check(object(value) && exact(value, ['protocol', 'price_protocol', 'simulation', 'rounding', 'user_id', 'position_id', 'destination_account_id', 'goal_id', 'quote', 'revision']) && value.protocol === 'recovery-quote-v1' && value.price_protocol === priceProtocol && value.simulation === true && value.rounding === 'CEIL_CENT' && value.user_id === environment.dashboard.user_id && value.position_id === renewal.position_id && uuid(value.destination_account_id) && (value.goal_id === null || uuid(value.goal_id)));
  const header = value.revision; check(object(header) && exact(header, ['epoch_id', 'client_request_id', 'revision_number', 'ancestors']) && header.epoch_id === environment.epoch_id && header.client_request_id === renewal.client_request_id && integer(header.revision_number) && header.revision_number > 0 && header.revision_number <= 16 && Array.isArray(header.ancestors) && header.ancestors.length === header.revision_number);
  check(header.ancestors.every((ancestor) => object(ancestor) && exact(ancestor, ['quote_id', 'evidence_hash']) && uuid(ancestor.quote_id) && digest(ancestor.evidence_hash)));
  const ancestors = header.ancestors as QuoteRevision['revision']['ancestors']; check(new Set(ancestors.map((a) => a.quote_id)).size === ancestors.length && ancestors[0]!.quote_id === await releaseUUID5(renewal.position_id, 'simulated-fixed-early-price-v1') && ancestors.at(-1)!.quote_id === renewal.previous_quote_id);
  const quote = value.quote;
  check(object(quote) && exact(quote, ['evidence_ids', 'quote_id', 'user_id', 'position_id', 'product_id', 'product_version_number', 'terms_digest', 'kind', 'principal_cents', 'fee_cents', 'loss_cents', 'net_cents', 'request_at', 'principal_available_at', 'expires_at']) && quote.user_id === value.user_id && quote.position_id === renewal.position_id && uuid(quote.product_id) && digest(quote.terms_digest) && integer(quote.product_version_number) && quote.product_version_number > 0 && quote.kind === 'EARLY_WITHDRAW' && integer(quote.principal_cents) && quote.principal_cents > 0 && quote.fee_cents === 0 && integer(quote.loss_cents) && quote.loss_cents > 0 && integer(quote.net_cents) && quote.net_cents === quote.principal_cents - quote.loss_cents && time(quote.request_at) && time(quote.principal_available_at) && time(quote.expires_at));
  check(quote.quote_id === await releaseUUID5(environment.epoch_id, `${priceProtocol}:${renewal.position_id}:${renewal.client_request_id}`) && !ancestors.some((a) => a.quote_id === quote.quote_id) && same(quote.evidence_ids, [quote.quote_id]) && Date.parse(quote.expires_at) - Date.parse(quote.request_at) === 900000 && Date.parse(quote.principal_available_at) >= Date.parse(quote.request_at));
  return value as QuoteRevision;
}
export async function readRevisionQuote(renewal: RenewalRequest, quoteId: string, environment: NextState): Promise<RevisionQuoteView> {
  check(uuid(quoteId)); const raw = await request(`${base}/quotes/${quoteId}`, 'GET', undefined, (value) => { context(value, environment); check(exact(value, [...identityKeys, 'protocol', 'quote_revision', 'quote_hash', 'quote_evidence_hash', ...displayKeys, 'bank_authority']) && value.protocol === priceProtocol && value.bank_authority === false); return value; });
  const revision = await parseQuoteRevision(raw.quote_revision, environment, renewal); check(revision.quote.quote_id === quoteId && raw.quote_hash === await hash(revision.quote) && raw.quote_evidence_hash === await hash(revision));
  return { ...display(raw, revision.quote), revision, quote_hash: raw.quote_hash as string, quote_evidence_hash: raw.quote_evidence_hash as string };
}
function missing(raw: Record<string, unknown>, client: string): boolean {
  if (raw.status !== 'NOT_FOUND_NOT_FINAL') return false;
  check(exact(raw, [...identityKeys, 'status', 'client_request_id', ...displayKeys, 'bank_authority']) && raw.client_request_id === client && time(raw.server_as_of) && raw.quote_validity === null && raw.bank_authority === false); return true;
}
/** Source availability only. The actual acceptance capability is a separate server gate. */
export async function revisionSourceAvailable(environment: NextState): Promise<boolean> {
  const client = crypto.randomUUID(); return request(`${base}/commands/${client}`, 'GET', undefined, (value) => { context(value, environment); check(missing(value, client)); return true; });
}
export async function parseRevisionView(value: unknown, environment: NextState, recovery: LossRevisionRecovery, quote: RevisionQuoteView): Promise<RevisionView> {
  check(object(value) && exact(value, ['protocol', 'binding', 'action', 'original_signed_acceptance', ...displayKeys, 'bank_authority', 'replacement_allowed']) && value.protocol === lossProtocol && value.bank_authority === false && value.replacement_allowed === false && recovery.prepare_request !== null);
  const action = await strictLossAction(value.action, environment), binding = value.binding;
  check(object(binding) && exact(binding, ['protocol', 'user_id', 'epoch_id', 'action_id', 'source_request', 'effect', 'effect_hash', 'quote', 'quote_hash', 'quote_evidence_hash', 'bank_authority']) && binding.protocol === lossProtocol && binding.user_id === recovery.user_id && binding.epoch_id === environment.epoch_id && binding.action_id === action.action_id && same(binding.source_request, recovery.prepare_request) && same(binding.effect, action.effect) && binding.effect_hash === action.effect_hash && same(binding.quote, quote.revision.quote) && binding.quote_hash === quote.quote_hash && binding.quote_evidence_hash === quote.quote_evidence_hash && binding.bank_authority === false && recovery.prepare_request.quote_id === quote.revision.quote.quote_id);
  const effect = action.effect, q = quote.revision.quote;
  check(effect.position_id === q.position_id && effect.product_id === q.product_id && effect.product_version_number === q.product_version_number && effect.terms_digest === q.terms_digest && effect.quote_id === q.quote_id && effect.goal_id === quote.revision.goal_id && effect.destination_account_id === quote.revision.destination_account_id && effect.amount_cents === q.principal_cents && effect.fee_cents === q.fee_cents && effect.loss_cents === q.loss_cents && effect.net_cents === q.net_cents && Date.parse(effect.valid_from) >= Date.parse(q.request_at) && Date.parse(action.prepared_at) >= Date.parse(q.request_at) && Date.parse(action.prepared_at) < Date.parse(q.expires_at) && Date.parse(effect.expires_at) > Date.parse(effect.valid_from) && Date.parse(effect.expires_at) <= Date.parse(q.expires_at) && time(effect.latest_arrival_at) && Date.parse(effect.latest_arrival_at) >= Date.parse(q.principal_available_at));
  const timing = display(value, q); check(timing.server_as_of === action.as_of);
  if (recovery.action_id !== null) check(action.action_id === recovery.action_id && binding.quote_hash === recovery.reviewed_quote_hash && binding.effect_hash === recovery.reviewed_effect_hash);
  const signed = value.original_signed_acceptance;
  if (signed !== null) {
    check(object(signed) && exact(signed, ['protocol', 'user_id', 'epoch_id', 'action_id', 'original_request', 'principal_at_acceptance', 'accepted_at', 'expires_at', 'bank_authority']) && signed.protocol === 'lossy-early-redemption-quote-revision-user-v2' && signed.user_id === action.user_id && signed.epoch_id === environment.epoch_id && signed.action_id === action.action_id && signed.bank_authority === false && time(signed.accepted_at) && time(signed.expires_at));
    const body = signed.original_request; check(object(body) && exact(body, ['expected_epoch_id', 'reviewed_quote_hash', 'reviewed_effect_hash', 'accepted', 'client_request_id']) && body.expected_epoch_id === environment.epoch_id && body.reviewed_quote_hash === binding.quote_hash && body.reviewed_effect_hash === binding.effect_hash && body.accepted === true && uuid(body.client_request_id));
    const actor = parseLocalActorSession({ simulation: true, principal: signed.principal_at_acceptance, bank_authority: false, confirms_financial_action: false }).principal;
    check(actor.role === 'USER' && actor.user_id === action.user_id && Date.parse(actor.issued_at) <= Date.parse(signed.accepted_at) && Date.parse(signed.accepted_at) < Date.parse(actor.expires_at) && Date.parse(effect.valid_from) <= Date.parse(signed.accepted_at) && Date.parse(signed.accepted_at) <= Date.parse(action.as_of) && Date.parse(signed.expires_at) > Date.parse(signed.accepted_at) && Date.parse(signed.expires_at) <= Math.min(Date.parse(actor.expires_at), Date.parse(q.expires_at), Date.parse(effect.expires_at)));
  }
  return { ...timing, binding: binding as RevisionBinding, action, signed: signed as RevisionSigned | null };
}
export async function getRevisionView(recovery: LossRevisionRecovery, environment: NextState): Promise<{ quote: RevisionQuoteView; view: RevisionView }> {
  check(uuid(recovery.action_id) && recovery.prepare_request !== null); const quote = await readRevisionQuote(recovery.renewal_request, recovery.prepare_request.quote_id, environment);
  const raw = await request(`${base}/actions/${recovery.action_id}`, 'GET', undefined, (value) => { context(value, environment); check(exact(value, [...identityKeys, 'protocol', 'binding', 'action', 'original_signed_acceptance', ...displayKeys, 'bank_authority', 'replacement_allowed'])); return value; });
  const { simulation: _simulation, variant: _variant, environment_id: _environment, epoch_id: _epoch, ...original } = raw; void _simulation; void _variant; void _environment; void _epoch;
  return { quote, view: await parseRevisionView(original, environment, recovery, quote) };
}
export async function readLossRevisionOriginal(pending: PendingOperation, environment: NextState): Promise<OperationResult & { revision?: LossRevisionUpdate }> {
  const recovery = parseLossRevisionRecovery(pending.loss_revision_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id); check(pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id && recovery.user_id === environment.dashboard.user_id);
  const result: OperationResult & { revision?: LossRevisionUpdate } = { simulation: true, environment_id: pending.environment_id, epoch_id: pending.epoch_id, client_request_id: pending.client_request_id, status: 'PENDING' }; const phase = revisionPath(pending.path)!.kind;
  let quote: RevisionQuoteView, view: RevisionView | null = null;
  if (phase === 'RENEW' || phase === 'PREPARE') {
    const raw = await request(`${base}/${phase === 'RENEW' ? 'commands' : 'prepare-commands'}/${pending.client_request_id}`, 'GET', undefined, (value) => { context(value, environment); return value; });
    if (missing(raw, pending.client_request_id)) return result;
    const original = raw.original_request; check(object(original) && exact(original, ['path', 'body']) && original.path === `/api/v1${pending.path}` && same(original.body, pending.body) && raw.client_request_id === pending.client_request_id && raw.bank_authority === false);
    if (phase === 'RENEW') {
      check(exact(raw, [...identityKeys, 'status', 'client_request_id', 'original_request', 'quote_revision', ...displayKeys, 'bank_authority']) && raw.status === 'RECORDED'); const revision = await parseQuoteRevision(raw.quote_revision, environment, recovery.renewal_request); display(raw, revision.quote);
      quote = await readRevisionQuote(recovery.renewal_request, revision.quote.quote_id, environment); check(same(quote.revision, revision));
    } else {
      check(exact(raw, [...identityKeys, 'status', 'client_request_id', 'original_request', 'original', ...displayKeys, 'bank_authority']) && ['RECORDED', 'PARTIAL_NOT_FINAL'].includes(String(raw.status)) && time(raw.server_as_of));
      if (raw.status === 'PARTIAL_NOT_FINAL') { check(raw.original === null && raw.quote_validity === null); result.revision = { phase, recovery, quote: null, view: null, partial: true, resume_original: true }; return result; }
      const prepare = recovery.prepare_request!; quote = await readRevisionQuote(recovery.renewal_request, prepare.quote_id, environment); view = await parseRevisionView(raw.original, environment, recovery, quote); check(raw.server_as_of === view.server_as_of && same(raw.quote_validity, view.quote_validity));
    }
  } else { ({ quote, view } = await getRevisionView(recovery, environment)); if (phase === 'CONFIRM' && view.signed) check(same(view.signed.original_request, pending.body)); }
  const fixed = view ? { ...recovery, action_id: view.action.action_id, reviewed_quote_hash: view.binding.quote_hash, reviewed_effect_hash: view.binding.effect_hash } : recovery;
  const accepted = !!view && ['SUBMITTED', 'UNKNOWN'].includes(view.action.status) && view.action.bank_status !== null && view.action.bank_status !== 'REJECTED';
  const unaccepted = !!view?.signed && ['PLANNED', 'AUTHORIZED'].includes(view.action.status) && view.action.bank_status === null && Date.parse(view.server_as_of) < Date.parse(view.signed.expires_at);
  result.revision = { phase, recovery: fixed, quote, view, partial: false, resume_original: !!view?.signed && !view.action.receipt && (accepted || unaccepted) };
  if (phase === 'RENEW' || phase === 'PREPARE' || !!view?.signed && !!view.action.receipt && ['SUCCEEDED', 'RECONCILED'].includes(view.action.status) && view.action.bank_status === 'SETTLED') result.status = 'COMPLETED'; return result;
}
export async function validateRevisionOriginal(pending: PendingOperation, environment: NextState): Promise<void> {
  const recovery = parseLossRevisionRecovery(pending.loss_revision_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id);
  if (revisionPath(pending.path)?.kind === 'RENEW') { await readLossRevisionOriginal(pending, environment); return; }
  if (revisionPath(pending.path)?.kind === 'PREPARE') { const quote = await readRevisionQuote(recovery.renewal_request, recovery.prepare_request!.quote_id, environment); check(quote.quote_validity.status === 'VALID'); await readLossRevisionOriginal(pending, environment); return; }
  const { view } = await getRevisionView(recovery, environment);
  if (pending.path.endsWith('/confirm-and-execute')) check(view.action.status === 'PLANNED' && view.signed === null && view.action.receipt === null && view.action.bank_status === null && view.quote_validity.status === 'VALID' && Date.parse(view.server_as_of) < Date.parse(view.action.effect.expires_at));
  else check(view.signed !== null && ['AUTHORIZED', 'SUBMITTED', 'UNKNOWN'].includes(view.action.status) && view.action.receipt === null);
}
export function sameRevisionUpdate(update: LossRevisionUpdate | null, pending: PendingOperation): boolean { return !!update && !!pending.loss_revision_recovery && update.recovery.user_id === pending.loss_revision_recovery.user_id && same(update.recovery.renewal_request, pending.loss_revision_recovery.renewal_request) && same(update.recovery.prepare_request, pending.loss_revision_recovery.prepare_request) && (pending.loss_revision_recovery.action_id === null || pending.loss_revision_recovery.action_id === update.recovery.action_id && pending.loss_revision_recovery.reviewed_quote_hash === update.recovery.reviewed_quote_hash && pending.loss_revision_recovery.reviewed_effect_hash === update.recovery.reviewed_effect_hash); }
const pointer = (e: NextState) => `zhiyu-next-quote-revision-latest:${e.environment_id}:${e.epoch_id}`;
const key = (e: NextState, client: string) => `zhiyu-next-quote-revision-original:${e.environment_id}:${e.epoch_id}:${client}`;
export function rememberRevisionUpdate(update: LossRevisionUpdate, environment: NextState): void {
  const client = update.recovery.renewal_request.client_request_id;
  localStorage.setItem(key(environment, client), JSON.stringify({ protocol: 'zhiyu-next-quote-revision-read-v2', environment_id: environment.environment_id, epoch_id: environment.epoch_id, user_id: update.recovery.user_id, renewal_request: update.recovery.renewal_request, prepare_request: update.recovery.prepare_request })); localStorage.setItem(pointer(environment), client);
}
export async function readRevisionUpdate(environment: NextState): Promise<LossRevisionUpdate | null> {
  const client = localStorage.getItem(pointer(environment)); if (client === null) return null; check(uuid(client)); const stored = localStorage.getItem(key(environment, client)); check(stored !== null); const value: unknown = JSON.parse(stored);
  check(object(value) && exact(value, ['protocol', 'environment_id', 'epoch_id', 'user_id', 'renewal_request', 'prepare_request']) && value.protocol === 'zhiyu-next-quote-revision-read-v2' && value.environment_id === environment.environment_id && value.epoch_id === environment.epoch_id && value.user_id === environment.dashboard.user_id);
  const renewal = parseRenewalRequest(value.renewal_request); check(renewal.client_request_id === client); const prepare = value.prepare_request === null ? null : parseRevisionPrepare(value.prepare_request);
  const body = prepare ?? renewal; const pending: PendingOperation = { protocol: 'zhiyu-next-original-v1', environment_id: environment.environment_id, epoch_id: environment.epoch_id, path: `${base}/${prepare ? 'prepare' : 'renew'}`, client_request_id: body.client_request_id, body, body_json: JSON.stringify(body), loss_revision_recovery: { protocol: 'zhiyu-next-loss-revision-recovery-v2', user_id: environment.dashboard.user_id, renewal_request: renewal, prepare_request: prepare, action_id: null, reviewed_quote_hash: null, reviewed_effect_hash: null } };
  return (await readLossRevisionOriginal(pending, environment)).revision ?? null;
}
