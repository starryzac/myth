import { parseMaturityAction, parseMaturityLookup, parseMaturityPreview, parseMaturityPrepare } from '../api/full-maturity-execution';
import type { MaturityAction, MaturityIntent, MaturityLookup, MaturityPrepare, MaturityPreview } from '../api/full-maturity-execution';
import { spendingHash as hash, spendingCanonicalJson as canonical } from '../api/spending-evidence';
import { request } from '../api/http';
import { object } from '../features/policy-form';
import type { NextState, OperationResult } from './api';
import { context } from './policies';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
import { maturityPath, parseMaturityRecovery, type MaturityRecovery } from './maturity-recovery';
import type { PendingOperation } from './operation';

export type MaturityUpdate = { phase: 'PREPARE' | 'CONFIRM' | 'EXECUTE'; action: MaturityAction; recovery: MaturityRecovery; resume_original: boolean };
const base = '/zhiyu-next/assets/maturity';
export async function previewNextMaturity(body: MaturityPrepare, environment: NextState): Promise<MaturityPreview> {
  parseMaturityPrepare(body);
  const value = await request(`${base}/preview`, 'POST', body, (raw) => { context(raw, environment); check(object(raw) && 'maturity' in raw); return raw.maturity; });
  return parseMaturityPreview(value, body, environment.dashboard.user_id);
}
async function prepareIntent(recovery: MaturityRecovery): Promise<MaturityIntent> {
  const body = recovery.prepare_request;
  return { protocol: 'full-maturity-browser-v1', kind: 'PREPARE', user_id: recovery.user_id, prepare_request: body, action: null, path: '/full-maturity-actions/prepare', body, body_json: JSON.stringify(body), request_hash: await hash(body) };
}
export async function lookupNextMaturity(recovery: MaturityRecovery, environment: NextState): Promise<MaturityLookup> {
  check(recovery.user_id === environment.dashboard.user_id && recovery.prepare_request.expected_epoch_id === environment.epoch_id);
  const raw = await request(`${base}/by-key/${encodeURIComponent(recovery.prepare_request.idempotency_key)}`, 'GET', undefined, (value) => { context(value, environment); check(object(value)); const { environment_id: _environment, epoch_id: _epoch, variant: _variant, ...native } = value; void _environment; void _epoch; void _variant; return native; });
  const lookup = await parseMaturityLookup(raw, await prepareIntent(recovery));
  if (lookup.action) {
    const action = lookup.action;
    if (recovery.action_id !== null) check(action.action_id === recovery.action_id && action.reviewed_command_hash === recovery.reviewed_command_hash);
  }
  return lookup;
}
function resumable(action: MaturityAction): boolean {
  // Existing consent proves only the original confirmation. Accepted UNKNOWN banking
  // is queried by the original consumer; this reader never renews its expiry or authority.
  return !action.historical && action.original_consent !== null && !action.service_receipt_verified && ['AUTHORIZED', 'SUBMITTED', 'UNKNOWN'].includes(action.status);
}
export function maturityUpdate(action: MaturityAction, phase: MaturityUpdate['phase']): MaturityUpdate {
  return { phase, action, recovery: { protocol: 'zhiyu-next-maturity-recovery-v1', user_id: action.user_id, prepare_request: action.original_request, action_id: action.action_id, reviewed_command_hash: action.reviewed_command_hash }, resume_original: resumable(action) };
}
export async function validateMaturityOriginal(pending: PendingOperation, environment: NextState): Promise<void> {
  const recovery = parseMaturityRecovery(pending.maturity_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id);
  check(pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id && recovery.user_id === environment.dashboard.user_id);
  if (maturityPath(pending.path)?.kind !== 'PREPARE') {
    const lookup = await lookupNextMaturity(recovery, environment); check(lookup.status === 'RECORDED' && lookup.action && !lookup.action.historical && !lookup.action.service_receipt_verified);
    if (pending.path.endsWith('/confirm-and-execute')) check(lookup.action.status === 'PLANNED' && lookup.action.original_consent === null);
    else check(resumable(lookup.action));
  }
}
export async function readMaturityOriginal(pending: PendingOperation, environment: NextState): Promise<OperationResult & { maturity?: MaturityUpdate }> {
  const recovery = parseMaturityRecovery(pending.maturity_recovery, pending.path, pending.body, pending.epoch_id, pending.client_request_id);
  check(pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id && recovery.user_id === environment.dashboard.user_id);
  const lookup = await lookupNextMaturity(recovery, environment);
  const result: OperationResult & { maturity?: MaturityUpdate } = { simulation: true, client_request_id: pending.client_request_id, environment_id: pending.environment_id, epoch_id: pending.epoch_id, status: 'PENDING' };
  if (lookup.status === 'NOT_FOUND_NOT_FINAL') return result;
  check(lookup.action); const phase = maturityPath(pending.path)!.kind; result.maturity = maturityUpdate(lookup.action, phase);
  if (phase === 'CONFIRM' && lookup.action.original_consent) check(canonical(lookup.action.original_consent.original_confirmation) === canonical(pending.body));
  // HTTP refusal and absence never prove no acceptance. Only the recorded preparation
  // or a verified native action/bank/service receipt completes this original locator.
  if (phase === 'PREPARE' || lookup.action.service_receipt_verified) result.status = 'COMPLETED';
  return result;
}
export function sameMaturityUpdate(update: MaturityUpdate | null, pending: PendingOperation): boolean {
  return !!update && !!pending.maturity_recovery && canonical(update.recovery.prepare_request) === canonical(pending.maturity_recovery.prepare_request) && update.action.user_id === pending.maturity_recovery.user_id && update.action.epoch_id === pending.epoch_id && (pending.maturity_recovery.action_id === null || update.action.action_id === pending.maturity_recovery.action_id && update.action.reviewed_command_hash === pending.maturity_recovery.reviewed_command_hash);
}
const viewKey = (environment: NextState) => `zhiyu-next-maturity-read-locator-v1:${environment.environment_id}:${environment.epoch_id}`;
export function rememberMaturityView(update: MaturityUpdate, environment: NextState): void {
  const prepare = update.recovery.prepare_request;
  localStorage.setItem(viewKey(environment), JSON.stringify({ protocol: 'zhiyu-next-maturity-read-locator-v1', user_id: environment.dashboard.user_id, environment_id: environment.environment_id, epoch_id: environment.epoch_id, prepare_request: prepare }));
}
export async function readMaturityView(environment: NextState): Promise<MaturityUpdate | null> {
  const raw = localStorage.getItem(viewKey(environment)); if (raw === null) return null;
  const value: unknown = JSON.parse(raw);
  check(object(value) && exact(value, ['protocol', 'user_id', 'environment_id', 'epoch_id', 'prepare_request']) && value.protocol === 'zhiyu-next-maturity-read-locator-v1' && value.user_id === environment.dashboard.user_id && value.environment_id === environment.environment_id && value.epoch_id === environment.epoch_id);
  const prepare = parseMaturityPrepare(value.prepare_request); check(prepare.expected_epoch_id === environment.epoch_id);
  const lookup = await lookupNextMaturity({ protocol: 'zhiyu-next-maturity-recovery-v1', user_id: value.user_id as string, prepare_request: prepare, action_id: null, reviewed_command_hash: null }, environment);
  return lookup.action ? maturityUpdate(lookup.action, 'PREPARE') : null;
}
export async function parseNextMaturityAction(value: unknown, body: MaturityPrepare, environment: NextState): Promise<MaturityAction> {
  context(value, environment); check(object(value)); return parseMaturityAction(value.maturity, body, environment.dashboard.user_id);
}
