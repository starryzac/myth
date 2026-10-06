import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { spendingCanonicalJson as canonical, spendingHash as hash, spendingUUID as uuid, spendingDigest as digest } from './spending-evidence';
import { parseAssetOptions, parseFullAssetPlan, productBoundaryPoints } from './full-products';
import { parseFullAnnualPlanning } from './full-annual';

export type AssetPrepare = components['schemas']['FullAssetPrepareRequest'];
export type AssetConfirm = components['schemas']['FullAssetConfirmRequest'];
export type AssetExecute = components['schemas']['FullAssetExecuteRequest'];
export type AssetPortfolio = components['schemas']['FullAssetFrozenPortfolio'];
export type AssetPreview = components['schemas']['FullAssetExecutionPreview'];
export type AssetResponse = components['schemas']['FullAssetExecutionResponse'];
export type AssetLookup = components['schemas']['FullAssetExecutionLookup'];
export type AssetIntent = {
  protocol: 'full-asset-browser-command-v1'; kind: 'PREPARE' | 'CONFIRM' | 'EXECUTE';
  user_id: string; portfolio_id: string | null; path: string;
  body: AssetPrepare | AssetConfirm | AssetExecute; body_json: string; request_hash: string;
  reviewed_portfolio: AssetPortfolio | null;
};
function check(value: unknown): asserts value { if (!value) throw new Error('原资产组合、完整批次、确认或原请求未匹配；保留原件与待核对请求。'); }
const exact = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const integer = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && Number(value) >= min;
const key = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value);
const ids = (value: unknown) => Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length;
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === 'string');
export const assetCanonicalJson = canonical;
export const assetRequestHash = hash;
const originals = new WeakMap<object, string>();
const freshReads = new WeakSet<object>();
export const getOriginalAssetExecutionResponse = (value: object) => originals.get(value) ?? null;
export const isFreshAssetExecutionRead = (value: object) => freshReads.has(value);
const save = <T extends object>(value: T, raw?: string): T => { if (raw !== undefined) originals.set(value, raw); return value; };

export function parseAssetPrepare(value: unknown): AssetPrepare {
  check(object(value) && exact(value, ['full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'goal_id', 'expected_goal_policy_version_id', 'expected_epoch_id', 'idempotency_key', 'planning_mode']));
  check(['full_policy_id', 'expected_full_policy_version_id', 'mvp_asset_policy_id', 'expected_mvp_policy_version_id', 'expected_epoch_id'].every((field) => uuid(value[field])) && key(value.idempotency_key) && ['PORTFOLIO', 'FIXED_LADDER'].includes(value.planning_mode as string));
  check(value.goal_id === null ? value.expected_goal_policy_version_id === null : uuid(value.goal_id) && uuid(value.expected_goal_policy_version_id));
  return value as AssetPrepare;
}
export function parseAssetConfirm(value: unknown): AssetConfirm {
  check(object(value) && exact(value, ['accepted', 'reviewed_portfolio_hash', 'expected_epoch_id', 'idempotency_key']) && value.accepted === true && digest(value.reviewed_portfolio_hash) && uuid(value.expected_epoch_id) && key(value.idempotency_key)); return value as AssetConfirm;
}
export function parseAssetExecute(value: unknown): AssetExecute {
  check(object(value) && exact(value, ['accepted', 'reviewed_portfolio_hash', 'expected_epoch_id', 'expected_batch_number', 'expected_action_id']) && value.accepted === true && digest(value.reviewed_portfolio_hash) && uuid(value.expected_epoch_id) && integer(value.expected_batch_number, 1) && value.expected_batch_number <= 4 && uuid(value.expected_action_id)); return value as AssetExecute;
}
/** Bind the actual portfolio DTO. These SHA checks do not reproduce the bank,
 * current permission engine or the server's independent audit verification. */
export async function parseAssetPortfolio(value: unknown, expectedUser?: string): Promise<AssetPortfolio> {
  check(object(value) && value.protocol === 'full-asset-execution-portfolio-v1' && value.simulation === true && uuid(value.portfolio_id) && uuid(value.user_id) && (!expectedUser || value.user_id === expectedUser) && uuid(value.epoch_id));
  check(time(value.prepared_at) && time(value.expires_at) && Date.parse(value.expires_at) > Date.parse(value.prepared_at) && Date.parse(value.expires_at) - Date.parse(value.prepared_at) <= 900_000 && value.bank_authority === false && value.funds_reserved === false && value.cross_operation_atomicity === 'NOT_AVAILABLE' && value.financial_experiment_verified === false);
  assertMoneyFields(value); const body = parseAssetPrepare(value.original_request);
  check(body.expected_epoch_id === value.epoch_id && object(value.full_configuration) && ['client_request_hash', 'full_policy_content_hash', 'original_mvp_configuration_hash', 'original_planning_response_hash', 'original_planning_input_hash', 'combined_full_protection_hash', 'portfolio_hash'].every((field) => digest(value[field])) && ids(value.source_evidence_ids));
  check(value.client_request_hash === await hash(body) && value.full_policy_content_hash === await hash(value.full_configuration) && integer(value.total_purchase_cents, 1) && Array.isArray(value.batches) && value.batches.length >= 1 && value.batches.length <= 4);
  productBoundaryPoints(value.combined_original_boundary); check(object(value.combined_original_boundary) && value.combined_original_boundary.status === 'READY');
  const batchProducts = new Set<string>(); const actions = new Set<string>(); let total = 0n;
  for (const [index, batch] of value.batches.entries()) {
    check(object(batch) && exact(batch, ['batch_number', 'action_id', 'bank_idempotency_key', 'catalogue', 'command']) && batch.batch_number === index + 1 && uuid(batch.action_id) && batch.bank_idempotency_key === `full-asset:${value.portfolio_id}:batch:${index + 1}` && object(batch.catalogue) && object(batch.command));
    const catalogue = batch.catalogue; check(exact(catalogue, ['product_id', 'catalogue_version_id', 'product_record_hash', 'terms_digest']) && uuid(catalogue.product_id) && uuid(catalogue.catalogue_version_id) && digest(catalogue.product_record_hash) && digest(catalogue.terms_digest));
    const command = batch.command; check(exact(command, ['effect', 'effect_hash']) && object(command.effect) && digest(command.effect_hash));
    const effect = command.effect;
    check(effect.simulation === true && effect.operation_id === batch.action_id && effect.user_id === value.user_id && effect.action_type === 'PURCHASE_ASSET' && integer(effect.amount_cents, 1) && effect.fee_cents === 0 && effect.loss_cents === 0 && effect.goal_id === body.goal_id && effect.policy_id === body.mvp_asset_policy_id && effect.policy_version_id === body.expected_mvp_policy_version_id && effect.product_id === catalogue.product_id && effect.terms_digest === catalogue.terms_digest && effect.valid_from === value.prepared_at && effect.expires_at === value.expires_at && Array.isArray(effect.cash_uses) && effect.cash_uses.length > 0 && Array.isArray(effect.income_uses));
    check(ids(effect.policy_version_ids) && canonical(effect.policy_version_ids) === canonical([body.expected_mvp_policy_version_id, ...(body.expected_goal_policy_version_id ? [body.expected_goal_policy_version_id] : [])]));
    let cash = 0n; for (const use of effect.cash_uses) { check(object(use) && uuid(use.account_id) && integer(use.amount_cents, 1)); cash += BigInt(use.amount_cents); }
    check(cash === BigInt(effect.amount_cents) && !batchProducts.has(catalogue.product_id) && !actions.has(batch.action_id)); batchProducts.add(catalogue.product_id); actions.add(batch.action_id); total += BigInt(effect.amount_cents);
  }
  check(total === BigInt(value.total_purchase_cents)); const { portfolio_hash: declared, ...original } = value; check(declared === await hash(original));
  return value as AssetPortfolio;
}

export async function parseAssetPreview(value: unknown, body: AssetPrepare, expectedUser?: string, raw?: string): Promise<AssetPreview> {
  check(object(value) && value.schema_version === 'full-asset-execution-preview-v1' && value.simulation === true && uuid(value.user_id) && (!expectedUser || value.user_id === expectedUser) && value.epoch_id === body.expected_epoch_id && time(value.as_of) && canonical(value.original_request) === canonical(body));
  check(['READY_TO_REVIEW', 'BLOCKED', 'UNKNOWN'].includes(value.state as string) && value.bank_authority === false && value.funds_reserved === false && value.persisted === false && value.financial_experiment_verified === false && value.execution_support === 'DURABLE_ORIGINAL_PURCHASE_CONSUMER_REQUIRES_SHARED_GUARDS' && strings(value.reasons) && strings(value.limitations));
  check(object(value.original_full_planning)); const planning = parseFullAssetPlan(value.original_full_planning, body.full_policy_id, parseAssetOptions(value.original_full_planning.planning_constraints)); const protection = parseFullAnnualPlanning(value.original_full_protection);
  check(planning.user_id === value.user_id && protection.user_id === value.user_id && planning.as_of === value.as_of && protection.as_of === value.as_of);
  if (value.state === 'READY_TO_REVIEW') {
    const portfolio = await parseAssetPortfolio(value.portfolio, value.user_id); const allocation = planning.allocation;
    check(canonical(portfolio.original_request) === canonical(body) && portfolio.prepared_at === value.as_of && value.reasons.length === 0 && allocation?.status === 'OPTIMAL' && allocation.policy_version_id === body.expected_full_policy_version_id && allocation.goal_id === body.goal_id && allocation.total_purchase_cents === portfolio.total_purchase_cents && allocation.batches.length === portfolio.batches.length && portfolio.original_planning_response_hash === planning.input_hash && portfolio.original_planning_input_hash === allocation.input_hash);
    for (const [index, frozen] of portfolio.batches.entries()) {
      const planned = allocation.batches[index]!; const effect = frozen.command.effect;
      check(planned.product_id === frozen.catalogue.product_id && planned.version_number === effect.product_version_number && planned.terms_digest === effect.terms_digest && planned.amount_cents === effect.amount_cents && canonical(planned.cash_uses) === canonical(effect.cash_uses) && canonical(planned.exit_plan) === canonical(effect.purchase_exit) && planning.catalogue.bindings.some((binding) => canonical(binding) === canonical(frozen.catalogue)));
    }
  }
  else check(value.portfolio === null && value.reasons.length > 0);
  return save(value as AssetPreview, raw);
}

export async function parseAssetResponse(value: unknown, expectedUser?: string, expectedPortfolio?: AssetPortfolio, raw?: string): Promise<AssetResponse> {
  check(object(value) && value.schema_version === 'full-asset-execution-v1' && value.simulation === true && uuid(value.user_id) && time(value.as_of));
  const portfolio = await parseAssetPortfolio(value.original_portfolio, expectedUser); check(value.user_id === portfolio.user_id && value.epoch_id === portfolio.epoch_id && value.original_request_hash === portfolio.client_request_hash && (!expectedPortfolio || canonical(portfolio) === canonical(expectedPortfolio)));
  check(['PREPARED_UNRESERVED', 'CONFIRMED_UNRESERVED', 'PARTIALLY_SETTLED', 'SERVICE_RECEIPTS_VERIFIED', 'UNRESOLVED', 'STOPPED', 'RETAINED_HISTORY'].includes(value.state as string) && value.bank_authority === false && value.funds_reserved === false && value.reservation_scope === 'WHOLE_UNRESERVED_CHILD_CLAIMS_USE_ORIGINAL_PIPELINE' && value.cross_operation_atomicity === 'NOT_AVAILABLE' && value.current_authority_assessed === false && value.receipt_is_current_authority === false && value.economic_experiment_verified === false && typeof value.current_epoch_open === 'boolean');
  check(Array.isArray(value.batches) && value.batches.length === portfolio.batches.length && typeof value.all_original_service_receipts_verified === 'boolean'); assertMoneyFields(value);
  for (const [index, batch] of value.batches.entries()) {
    const original = portfolio.batches[index]!; check(object(batch) && batch.batch_number === original.batch_number && batch.action_id === original.action_id && batch.bank_idempotency_key === original.bank_idempotency_key && batch.receipt_is_current_authority === false && typeof batch.original_trace_verified === 'boolean' && typeof batch.current_action_missing === 'boolean');
    const action = batch.original_action;
    if (action === null) { check(batch.current_action_missing && !batch.original_trace_verified && batch.original_request_hash === null && !value.current_epoch_open); continue; }
    check(object(action) && action.simulation === true && action.user_id === portfolio.user_id && action.action_id === original.action_id && uuid(action.decision_run_id) && typeof action.status === 'string' && action.autonomy_level === 'ASK_ONCE' && canonical(action.effect) === canonical(original.command.effect) && action.effect_hash === original.command.effect_hash && batch.original_trace_verified === true && batch.current_action_missing === false && digest(batch.original_request_hash) && time(action.prepared_at) && time(action.as_of) && (action.bank_status === null || typeof action.bank_status === 'string'));
    if (action.receipt !== null) { const receipt = action.receipt; check(object(receipt) && receipt.simulation === true && uuid(receipt.receipt_id) && receipt.action_id === original.action_id && uuid(receipt.bank_operation_id) && typeof receipt.status === 'string' && integer(receipt.executed_cents) && integer(receipt.fee_cents) && integer(receipt.loss_cents) && ids(receipt.posting_ids) && time(receipt.occurred_at) && (receipt.reconciled_at === null || time(receipt.reconciled_at)) && receipt.executed_cents === original.command.effect.amount_cents && receipt.fee_cents === 0 && receipt.loss_cents === 0); }
  }
  const verified = value.batches.every((batch) => object(batch) && object(batch.original_action) && ['SUCCEEDED', 'RECONCILED'].includes(batch.original_action.status as string) && batch.original_action.bank_status === 'SETTLED' && batch.original_action.receipt !== null);
  check(value.all_original_service_receipts_verified === verified && (value.state !== 'SERVICE_RECEIPTS_VERIFIED' || verified));
  const consent = value.original_consent;
  if (consent === null) check(value.original_consent_id === null && value.original_consent_evidence_id === null && value.original_consent_verified === false && value.original_consent_evidence_status === 'NOT_RECORDED');
  else {
    check(object(consent) && exact(consent, ['consent_id', 'user_id', 'epoch_id', 'portfolio_id', 'idempotency_key', 'original_request', 'request_hash', 'portfolio_hash', 'evidence_id', 'evidence_hash', 'original_evidence', 'current_evidence_status', 'current_evidence_verified', 'receipt_is_current_authority', 'current_authority_assessed']));
    const body = parseAssetConfirm(consent.original_request);
    check(uuid(consent.consent_id) && consent.consent_id === value.original_consent_id && consent.user_id === portfolio.user_id && consent.epoch_id === portfolio.epoch_id && consent.portfolio_id === portfolio.portfolio_id && consent.portfolio_hash === portfolio.portfolio_hash && consent.idempotency_key === body.idempotency_key && body.expected_epoch_id === portfolio.epoch_id && body.reviewed_portfolio_hash === portfolio.portfolio_hash && consent.request_hash === await hash(body) && uuid(consent.evidence_id) && consent.evidence_id === value.original_consent_evidence_id && digest(consent.evidence_hash) && consent.receipt_is_current_authority === false && consent.current_authority_assessed === false && typeof consent.current_evidence_verified === 'boolean' && consent.current_evidence_verified === value.original_consent_verified && consent.current_evidence_status === value.original_consent_evidence_status && ['CURRENT_EVIDENCE_MATCHED', 'RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING'].includes(consent.current_evidence_status as string));
    check(consent.current_evidence_verified === (consent.current_evidence_status === 'CURRENT_EVIDENCE_MATCHED') && object(consent.original_evidence)); const evidence = consent.original_evidence;
    check(evidence.id === consent.evidence_id && evidence.user_id === portfolio.user_id && evidence.source_ref === portfolio.portfolio_id && evidence.source_type === 'USER_FULL_ASSET_PORTFOLIO_CONFIRMATION' && evidence.evidence_level === 'USER_CONFIRMED_ACTION' && evidence.content_hash === consent.evidence_hash && object(evidence.content));
    const content = evidence.content;
    check(content.protocol === 'full-asset-portfolio-consent-v1' && content.simulation === true && content.user_id === portfolio.user_id && content.portfolio_id === portfolio.portfolio_id && content.epoch_id === portfolio.epoch_id && content.portfolio_hash === portfolio.portfolio_hash && content.client_request_hash === portfolio.client_request_hash && content.accepted === true && time(content.confirmed_at) && time(content.valid_until) && Date.parse(content.valid_until) === Date.parse(portfolio.expires_at) && Date.parse(content.confirmed_at) >= Date.parse(portfolio.prepared_at) && consent.evidence_hash === await hash(content));
  }
  return save(value as AssetResponse, raw);
}

export function parseAssetIntent(value: unknown): AssetIntent {
  check(object(value) && exact(value, ['protocol', 'kind', 'user_id', 'portfolio_id', 'path', 'body', 'body_json', 'request_hash', 'reviewed_portfolio']) && value.protocol === 'full-asset-browser-command-v1' && ['PREPARE', 'CONFIRM', 'EXECUTE'].includes(value.kind as string) && uuid(value.user_id) && digest(value.request_hash));
  if (value.kind === 'PREPARE') { parseAssetPrepare(value.body); check(value.portfolio_id === null && value.reviewed_portfolio === null && value.path === '/full-asset-executions/prepare'); }
  else { const body = value.kind === 'CONFIRM' ? parseAssetConfirm(value.body) : parseAssetExecute(value.body); check(uuid(value.portfolio_id) && object(value.reviewed_portfolio) && value.reviewed_portfolio.portfolio_id === value.portfolio_id && value.reviewed_portfolio.user_id === value.user_id && value.reviewed_portfolio.epoch_id === body.expected_epoch_id && value.reviewed_portfolio.portfolio_hash === body.reviewed_portfolio_hash && value.path === `/full-asset-executions/portfolios/${value.portfolio_id}/${value.kind === 'CONFIRM' ? 'confirm' : 'execute-next'}`); if (value.kind === 'EXECUTE') { const execute = parseAssetExecute(value.body); check(Array.isArray(value.reviewed_portfolio.batches)); const batch = value.reviewed_portfolio.batches[execute.expected_batch_number - 1]; check(object(batch) && batch.batch_number === execute.expected_batch_number && batch.action_id === execute.expected_action_id); } }
  check(value.body_json === JSON.stringify(value.body) && typeof value.body_json === 'string' && value.body_json.length <= 20000); return value as AssetIntent;
}
export async function verifyAssetIntent(intent: AssetIntent): Promise<void> { parseAssetIntent(intent); check(await hash(intent.body) === intent.request_hash); if (intent.reviewed_portfolio) await parseAssetPortfolio(intent.reviewed_portfolio, intent.user_id); }
export async function parseAssetLookup(value: unknown, intent: AssetIntent, raw?: string): Promise<AssetLookup> {
  check(intent.kind !== 'EXECUTE' && object(value) && value.simulation === true && value.user_id === intent.user_id && value.epoch_id === intent.body.expected_epoch_id && 'idempotency_key' in intent.body && value.idempotency_key === intent.body.idempotency_key && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(value.status as string) && value.not_found_is_final === false && value.replacement_allowed === false);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original === null && value.original_request === null && value.request_hash === null && value.command_kind === null);
  else { check(value.command_kind === intent.kind && canonical(value.original_request) === canonical(intent.body) && value.request_hash === intent.request_hash && await hash(value.original_request) === value.request_hash); const original = await parseAssetResponse(value.original, intent.user_id, intent.reviewed_portfolio ?? undefined); check(original.epoch_id === intent.body.expected_epoch_id); if (intent.kind === 'PREPARE') check(canonical(original.original_portfolio.original_request) === canonical(intent.body)); else check(original.original_consent !== null && canonical(original.original_consent.original_request) === canonical(intent.body) && original.original_consent.request_hash === intent.request_hash); }
  return save(value as AssetLookup, raw);
}
export async function previewAssetExecution(body: AssetPrepare, user?: string): Promise<AssetPreview> { parseAssetPrepare(body); const raw = await request<{ simulation: true; original: unknown; raw: string }>('/full-asset-executions/preview', 'POST', body, (value, text) => ({ simulation: true, original: value, raw: text })); return parseAssetPreview(raw.original, body, user, raw.raw); }
export async function getAssetExecution(id: string, user?: string, portfolio?: AssetPortfolio): Promise<AssetResponse> { check(uuid(id)); const value = await request<{ simulation: true; original: unknown; raw: string }>(`/full-asset-executions/portfolios/${id}`, 'GET', undefined, (input, raw) => ({ simulation: true, original: input, raw })); const parsed = await parseAssetResponse(value.original, user, portfolio, value.raw); check(parsed.original_portfolio.portfolio_id === id); freshReads.add(parsed); return parsed; }
export async function lookupAssetExecution(intent: AssetIntent): Promise<AssetLookup> { await verifyAssetIntent(intent); check(intent.kind !== 'EXECUTE' && 'idempotency_key' in intent.body); const value = await request<{ simulation: true; original: unknown; raw: string }>(`/full-asset-executions/commands/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (input, raw) => ({ simulation: true, original: input, raw })); const parsed = await parseAssetLookup(value.original, intent, value.raw); freshReads.add(parsed); return parsed; }
export async function postAssetExecution(intent: AssetIntent): Promise<AssetResponse> { await verifyAssetIntent(intent); const value = await request<{ simulation: true; original: unknown; raw: string }>(intent.path, 'POST', intent.body, (input, raw) => ({ simulation: true, original: input, raw })); const original = await parseAssetResponse(value.original, intent.user_id, intent.reviewed_portfolio ?? undefined, value.raw); if (intent.kind === 'PREPARE') check(canonical(original.original_portfolio.original_request) === canonical(intent.body)); if (intent.kind === 'CONFIRM') check(original.original_consent && canonical(original.original_consent.original_request) === canonical(intent.body) && original.original_consent.request_hash === intent.request_hash); return original; }
