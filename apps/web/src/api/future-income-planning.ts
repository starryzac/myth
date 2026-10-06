import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { dynamicCanonicalJson, dynamicRequestHash } from './full-dynamic-goal-execution';
import { ApiError, request } from './http';

export type FutureIncomeCandidateBody = components['schemas']['FutureIncomeCandidateRequest'];
export type FutureIncomeConfirmBody = components['schemas']['FutureIncomeConfirmationRequest'];
export type FutureIncomeCandidate = components['schemas']['FutureIncomeCandidate'];
export type FutureIncomeConfirmation = components['schemas']['FutureIncomeConfirmation'];
export type FutureIncomePlanning = components['schemas']['FutureIncomePlanningResponse'];
export type FutureIncomeLookup = components['schemas']['FutureIncomeCommandLookup'];
export type FutureIncomeSource = components['schemas']['PlanningIncomeSource'];
export const futureCanonical = dynamicCanonicalJson;
export const futureRequestHash = dynamicRequestHash;
const originals = new WeakMap<object, string>();
const freshLookups = new WeakSet<object>();
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const digest = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const money = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const key = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/.test(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string');
const dateNumber = (v: unknown) => {
  if (typeof v !== 'string' || !/^\d{4}-\d\d-\d\d$/.test(v)) return NaN;
  const stamp = Date.parse(`${v}T00:00:00Z`);
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === v ? stamp / 86400000 : NaN;
};
export function futureCheck(ok: unknown): asserts ok { if (!ok) throw new ApiError('未来收入规划响应不一致，保留未知与原请求', 200, 'INVALID_FUTURE_INCOME_RESPONSE', null); }
function keep<T extends object>(v: T, raw?: string): T { if (raw !== undefined) originals.set(v, raw); return v; }
export function futureOriginalJson(v: object): string | null { return originals.get(v) ?? null; }
export function isFreshFutureIncomeLookup(v: FutureIncomeLookup): boolean { return freshLookups.has(v); }

export function parseFutureIncomeBody(v: unknown, kind: 'CANDIDATE' | 'CONFIRM'): FutureIncomeCandidateBody | FutureIncomeConfirmBody {
  futureCheck(object(v) && uuid(v.expected_epoch_id) && key(v.idempotency_key));
  const fields = kind === 'CANDIDATE' ? ['expected_epoch_id', 'origin_transaction_id', 'expected_origin_hash', 'idempotency_key'] : ['expected_epoch_id', 'candidate_id', 'reviewed_candidate_hash', 'accepted', 'idempotency_key'];
  futureCheck(Object.keys(v).length === fields.length && Object.keys(v).every(k => fields.includes(k)));
  if (kind === 'CANDIDATE') futureCheck(uuid(v.origin_transaction_id) && digest(v.expected_origin_hash));
  else futureCheck(uuid(v.candidate_id) && digest(v.reviewed_candidate_hash) && v.accepted === true);
  return v as FutureIncomeCandidateBody | FutureIncomeConfirmBody;
}

async function parseSource(v: unknown, user: string): Promise<FutureIncomeSource> {
  futureCheck(object(v) && v.user_id === user && v.qualification === 'COMPLETE_ORIGINAL_INCOME_LEDGER' && v.implies_recurring_salary === false && v.bank_promises_future_payment === false && digest(v.origin_hash) && uuid(v.ledger_evidence_id) && digest(v.ledger_evidence_hash));
  const o = v.origin; futureCheck(object(o) && uuid(o.origin_transaction_id) && uuid(o.origin_account_id) && money(o.amount_cents) && o.amount_cents > 0 && time(o.occurred_at) && time(o.observed_at) && uuid(o.bank_evidence_id) && digest(o.bank_evidence_hash));
  futureCheck(Object.keys(o).length === 7 && v.origin_hash === await futureRequestHash(o));
  return v as FutureIncomeSource;
}

function metadata(v: unknown, id: string, user: string, hash: string, sourceType: string) {
  futureCheck(object(v) && v.id === id && v.user_id === user && v.evidence_level === 'USER_DECLARED' && v.source_type === sourceType && v.status === 'VALID' && v.valid_to === null && v.supersedes_id === null && v.content_hash === hash && time(v.created_at) && v.observed_at === v.created_at && v.valid_from === v.created_at && object(v.content));
  return v;
}

export async function parseFutureIncomeCandidate(v: unknown, user: string, epoch: string, raw?: string): Promise<FutureIncomeCandidate> {
  futureCheck(uuid(user) && uuid(epoch) && object(v) && v.simulation === true && v.user_id === user && v.epoch_id === epoch && uuid(v.candidate_id) && digest(v.candidate_hash) && digest(v.request_hash) && digest(v.evidence_hash) && time(v.admitted_at) && time(v.confirmation_deadline) && Date.parse(v.confirmation_deadline) - Date.parse(v.admitted_at) === 900000 && v.grants_authority === false && v.receipt_is_current_authority === false && ['REQUIRES_EXPLICIT_CONFIRMATION', 'EXPIRED', 'USER_CONFIRMED', 'UNKNOWN'].includes(v.state as string));
  const body = parseFutureIncomeBody(v.original_request, 'CANDIDATE') as FutureIncomeCandidateBody;
  const source = await parseSource(v.source, user); const a = v.assumption;
  futureCheck(body.expected_epoch_id === epoch && body.origin_transaction_id === source.origin.origin_transaction_id && body.expected_origin_hash === source.origin_hash && v.request_hash === await futureRequestHash(body));
  futureCheck(Date.parse(source.origin.occurred_at) <= Date.parse(v.admitted_at) && Date.parse(source.origin.observed_at) <= Date.parse(v.admitted_at));
  futureCheck(object(a) && a.protocol === 'future-income-monthly-assumption-v1' && a.user_id === user && a.epoch_id === epoch && a.origin_transaction_id === body.origin_transaction_id && a.source_account_id === source.origin.origin_account_id && a.origin_hash === source.origin_hash && a.original_bank_evidence_id === source.origin.bank_evidence_id && a.original_bank_evidence_hash === source.origin.bank_evidence_hash && a.conditional_amount_cents === source.origin.amount_cents && ['UTC', 'Asia/Shanghai'].includes(a.timezone as string) && money(a.monthly_local_day) && a.monthly_local_day >= 1 && a.monthly_local_day <= 31 && Number.isFinite(dateNumber(a.valid_from)) && dateNumber(a.valid_until) - dateNumber(a.valid_from) === 364);
  futureCheck(a.basis === 'USER_DECLARED_HYPOTHETICAL_MONTHLY_REPETITION' && a.short_month_rule === 'CLAMP_TO_LAST_CALENDAR_DAY' && a.arrival_time_within_day_known === false && a.income_is_settled_cash === false && a.included_in_current_cash_cents === 0 && a.included_in_execution_cents === 0 && a.grants_authority === false && v.candidate_hash === await futureRequestHash(a));
  const offset = a.timezone === 'Asia/Shanghai' ? 28800000 : 0;
  const admittedDate = new Date(Date.parse(v.admitted_at) + offset).toISOString().slice(0, 10);
  futureCheck(dateNumber(a.valid_from) === dateNumber(admittedDate) + 1 && a.monthly_local_day === new Date(Date.parse(source.origin.occurred_at) + offset).getUTCDate());
  const e = metadata(v.original_evidence, v.candidate_id, user, v.evidence_hash, 'FUTURE_INCOME_PLANNING_CANDIDATE');
  const c = e.content; futureCheck(object(c) && c.protocol === 'future-income-candidate-v1' && c.simulation === true && c.candidate_id === v.candidate_id && c.user_id === user && c.epoch_id === epoch && c.admitted_at === v.admitted_at && c.confirmation_deadline === v.confirmation_deadline && c.candidate_hash === v.candidate_hash && c.request_hash === v.request_hash && c.grants_authority === false && futureCanonical(c.original_request) === futureCanonical(body) && futureCanonical(c.source) === futureCanonical(source) && futureCanonical(c.assumption) === futureCanonical(a) && v.evidence_hash === await futureRequestHash(c));
  return keep(v as FutureIncomeCandidate, raw);
}

export async function parseFutureIncomeConfirmation(v: unknown, candidate: FutureIncomeCandidate, raw?: string): Promise<FutureIncomeConfirmation> {
  futureCheck(object(v) && v.simulation === true && v.user_id === candidate.user_id && v.epoch_id === candidate.epoch_id && v.candidate_id === candidate.candidate_id && v.candidate_hash === candidate.candidate_hash && uuid(v.confirmation_id) && time(v.confirmed_at) && Date.parse(v.confirmed_at) >= Date.parse(candidate.admitted_at) && Date.parse(v.confirmed_at) < Date.parse(candidate.confirmation_deadline) && digest(v.request_hash) && digest(v.evidence_hash) && v.grants_authority === false && v.confirms_financial_action === false && v.dedicated_audit_event_recorded === false && v.receipt_is_current_authority === false);
  const body = parseFutureIncomeBody(v.original_request, 'CONFIRM') as FutureIncomeConfirmBody;
  futureCheck(body.expected_epoch_id === candidate.epoch_id && body.candidate_id === candidate.candidate_id && body.reviewed_candidate_hash === candidate.candidate_hash && v.request_hash === await futureRequestHash(body));
  const e = metadata(v.original_evidence, v.confirmation_id, candidate.user_id, v.evidence_hash, 'FUTURE_INCOME_PLANNING_CONFIRM');
  const c = e.content; futureCheck(object(c) && c.protocol === 'future-income-confirmation-v1' && c.simulation === true && c.confirmation_id === v.confirmation_id && c.user_id === candidate.user_id && c.epoch_id === candidate.epoch_id && c.candidate_id === candidate.candidate_id && c.candidate_hash === candidate.candidate_hash && c.confirmed_at === v.confirmed_at && c.request_hash === v.request_hash && c.grants_authority === false && futureCanonical(c.original_request) === futureCanonical(body) && futureCanonical(c.original_candidate_snapshot) === futureCanonical(candidate.original_evidence) && v.evidence_hash === await futureRequestHash(c));
  return keep(v as FutureIncomeConfirmation, raw);
}

export async function parseFutureIncomeLookup(v: unknown, user: string, epoch: string, expectedKey: string, raw?: string): Promise<FutureIncomeLookup> {
  futureCheck(object(v) && v.protocol === 'future-income-command-lookup-v1' && v.simulation === true && v.user_id === user && v.epoch_id === epoch && v.idempotency_key === expectedKey && key(expectedKey) && v.not_found_is_final === false && v.replacement_allowed === false && v.grants_authority === false && v.receipt_is_current_authority === false);
  if (v.status === 'NOT_FOUND_NOT_FINAL') futureCheck(['command_kind', 'original_request', 'request_hash', 'candidate', 'confirmation'].every(k => v[k] === null));
  else {
    futureCheck(v.status === 'RECORDED' && (v.command_kind === 'CANDIDATE' || v.command_kind === 'CONFIRM') && digest(v.request_hash));
    const body = parseFutureIncomeBody(v.original_request, v.command_kind); const candidate = await parseFutureIncomeCandidate(v.candidate, user, epoch);
    futureCheck(body.expected_epoch_id === epoch && body.idempotency_key === expectedKey && v.request_hash === await futureRequestHash(body));
    if (v.command_kind === 'CANDIDATE') futureCheck(v.confirmation === null && futureCanonical(candidate.original_request) === futureCanonical(body) && candidate.request_hash === v.request_hash);
    else { const receipt = await parseFutureIncomeConfirmation(v.confirmation, candidate); futureCheck(futureCanonical(receipt.original_request) === futureCanonical(body) && receipt.request_hash === v.request_hash); }
  }
  return keep(v as FutureIncomeLookup, raw);
}

export async function parseFutureIncomePlanning(v: unknown, user: string, epoch: string, raw?: string): Promise<FutureIncomePlanning> {
  futureCheck(object(v) && v.protocol === 'future-income-conditional-planning-v1' && v.simulation === true && v.user_id === user && (v.epoch_id === epoch || (v.epoch_id === null && v.status === 'UNKNOWN')) && time(v.as_of) && (v.timezone === 'UTC' || v.timezone === 'Asia/Shanghai') && v.horizon_days === 365 && v.included_in_current_cash_cents === 0 && v.included_in_execution_cents === 0 && v.writes_financial_facts === false && v.grants_authority === false && v.bank_promises_future_payment === false && v.original_execution_view_changed === false && digest(v.input_hash) && strings(v.issues));
  const s = v.sources; futureCheck(object(s) && s.simulation === true && s.user_id === user && s.epoch_id === v.epoch_id && s.as_of === v.as_of && s.timezone === v.timezone && s.grants_authority === false && s.included_in_current_cash_cents === 0 && s.included_in_execution_cents === 0 && strings(s.issues) && Array.isArray(s.sources) && money(s.captured_origin_count) && s.captured_origin_count === s.sources.length && (s.original_origin_count === null || money(s.original_origin_count)));
  for (const source of s.sources) await parseSource(source, user);
  const originIds = s.sources.map(source => (source as FutureIncomeSource).origin.origin_transaction_id); futureCheck(new Set(originIds).size === originIds.length);
  if (s.status === 'VERIFIED_ORIGINAL_INCOME_SOURCES') futureCheck(s.complete === true && s.original_origin_count === s.captured_origin_count && s.issues.length === 0);
  else futureCheck(s.status === 'UNKNOWN' && s.complete === false && s.issues.length > 0 && s.sources.length === 0);
  futureCheck(Array.isArray(v.plans)); const planIds = new Set<string>(); const active = new Map<string, FutureIncomeCandidate>();
  for (const p of v.plans) {
    futureCheck(object(p) && uuid(p.candidate_id) && !planIds.has(p.candidate_id) && Array.isArray(p.original_metadata) && p.original_metadata.every(object) && strings(p.issues) && ['USER_CONFIRMED_CONDITION', 'REQUIRES_EXPLICIT_CONFIRMATION', 'EXPIRED', 'UNKNOWN', 'CONFLICTED'].includes(p.state as string)); planIds.add(p.candidate_id);
    const candidate = p.candidate === null ? null : await parseFutureIncomeCandidate(p.candidate, user, epoch);
    if (candidate !== null) futureCheck(candidate.candidate_id === p.candidate_id);
    if (p.confirmation !== null) { futureCheck(candidate !== null); const confirmation = await parseFutureIncomeConfirmation(p.confirmation, candidate); futureCheck(confirmation.confirmation_id === p.confirmation_id); }
    else futureCheck(p.confirmation_id === null || p.state === 'UNKNOWN');
    if (p.state === 'USER_CONFIRMED_CONDITION') { futureCheck(candidate !== null && p.confirmation !== null && s.sources.some(source => source.origin.origin_transaction_id === candidate.source.origin.origin_transaction_id && source.origin_hash === candidate.source.origin_hash) && s.complete === true); active.set(p.candidate_id, candidate); }
    if (candidate === null) futureCheck(p.state === 'UNKNOWN');
  }
  const known = v.status === 'CONDITIONAL_PLANNING'; futureCheck(known ? active.size > 0 && v.issues.length === 0 && money(v.total_conditional_income_cents) : ['NO_CONFIRMED_REGISTERED_SOURCE', 'UNKNOWN'].includes(v.status as string) && v.total_conditional_income_cents === null);
  if (v.status === 'NO_CONFIRMED_REGISTERED_SOURCE') futureCheck(active.size === 0 && v.issues.length === 0);
  if (v.status === 'UNKNOWN') futureCheck(v.issues.length > 0);
  futureCheck(Array.isArray(v.daily_schedule) && v.daily_schedule.length === 365);
  const today = new Date(Date.parse(v.as_of) + (v.timezone === 'Asia/Shanghai' ? 28800000 : 0)).toISOString().slice(0, 10);
  let total = 0n;
  for (const [index, day] of v.daily_schedule.entries()) {
    futureCheck(object(day) && day.day === index + 1 && dateNumber(day.date) === dateNumber(today) + index + 1 && day.is_settled_cash === false && day.availability_within_day_known === false && Array.isArray(day.candidate_ids) && day.candidate_ids.every(uuid) && new Set(day.candidate_ids).size === day.candidate_ids.length);
    if (known) {
      const date = new Date(`${day.date}T00:00:00Z`), monthLastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
      const matching = [...active.entries()].filter(([, candidate]) => { const a = candidate.assumption; return a.timezone === v.timezone && dateNumber(a.valid_from) <= dateNumber(day.date) && dateNumber(day.date) <= dateNumber(a.valid_until) && date.getUTCDate() === Math.min(a.monthly_local_day, monthLastDay); }).map(([id]) => id).sort();
      futureCheck(money(day.conditional_income_cents) && futureCanonical(day.candidate_ids) === futureCanonical(matching));
      let expected = 0n; for (const id of matching) expected += BigInt(active.get(id)!.assumption.conditional_amount_cents);
      futureCheck(BigInt(day.conditional_income_cents) === expected); total += BigInt(day.conditional_income_cents);
    }
    else futureCheck(day.conditional_income_cents === null && day.candidate_ids.length === 0);
  }
  if (known) futureCheck(total === BigInt(v.total_conditional_income_cents as number));
  return keep(v as FutureIncomePlanning, raw);
}

const base = '/planning/future-income';
export async function readFutureIncomePlanning(user: string, epoch: string): Promise<FutureIncomePlanning> { return request(base, 'GET', undefined, (v, raw) => parseFutureIncomePlanning(v, user, epoch, raw)); }
export async function submitFutureIncomeCandidate(body: FutureIncomeCandidateBody, user: string): Promise<FutureIncomeCandidate> { parseFutureIncomeBody(body, 'CANDIDATE'); return request(`${base}/candidates`, 'POST', body, (v, raw) => parseFutureIncomeCandidate(v, user, body.expected_epoch_id, raw)); }
export async function submitFutureIncomeConfirmation(body: FutureIncomeConfirmBody, candidate: FutureIncomeCandidate): Promise<FutureIncomeConfirmation> { parseFutureIncomeBody(body, 'CONFIRM'); return request(`${base}/confirm`, 'POST', body, (v, raw) => parseFutureIncomeConfirmation(v, candidate, raw)); }
export async function lookupFutureIncomeCommand(user: string, epoch: string, originalKey: string): Promise<FutureIncomeLookup> { futureCheck(uuid(user) && uuid(epoch) && key(originalKey)); const result = await request(`${base}/commands/${encodeURIComponent(epoch)}/by-key/${encodeURIComponent(originalKey)}`, 'GET', undefined, (v, raw) => parseFutureIncomeLookup(v, user, epoch, originalKey, raw)); freshLookups.add(result); return result; }
