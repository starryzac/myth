import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';

export type Dashboard = components['schemas']['DashboardResponse'];

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalid(): never {
  throw new Error('资金总览响应未通过模拟环境与数据完整性校验');
}

function moneyFields(value: Record<string, unknown>, keys: string[], nullable = true): void {
  for (const key of keys) {
    if (!(nullable && value[key] === null) && !Number.isSafeInteger(value[key])) invalid();
  }
}

function stringFields(value: Record<string, unknown>, keys: string[]): void {
  for (const key of keys) if (typeof value[key] !== 'string') invalid();
}

function records(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value) || !value.every(record)) invalid();
  return value;
}

function strings(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

/** Reject unsafe money and mismatched envelopes before publishing any card. */
export function parseDashboard(value: unknown): Dashboard {
  assertMoneyFields(value);
  if (!record(value) || value.schema_version !== 'dashboard-v1' || value.simulation !== true ||
    typeof value.user_id !== 'string' || typeof value.as_of !== 'string' ||
    !Number.isFinite(Date.parse(value.as_of)) ||
    !['Asia/Shanghai', 'UTC'].includes(String(value.timezone))) invalid();

  const cards = ['account_facts', 'boundary', 'goal_ownership', 'managed_assets',
    'next_obligations', 'pending_actions', 'recovery_proposals', 'intervention', 'audit'];
  for (const key of cards) if (!record(value[key])) invalid();
  const account = value.account_facts as Record<string, unknown>;
  const boundary = value.boundary as Record<string, unknown>;
  if (!record(account.facts) || account.facts.simulation !== true ||
    account.facts.user_id !== value.user_id || account.facts.timezone !== value.timezone ||
    !Array.isArray(account.facts.accounts) || !Array.isArray(account.facts.credit_card_bills) ||
    boundary.financial_only !== true ||
    !['READY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'].includes(String(boundary.status)) ||
    typeof boundary.window_start !== 'string' || typeof boundary.window_end !== 'string' ||
    typeof boundary.boundary_hash !== 'string' || typeof boundary.input_digest !== 'string' ||
    !Array.isArray(boundary.blocking_constraints) || !Array.isArray(boundary.calculation_notes) ||
    !Array.isArray(value.source_evidence_ids)) invalid();

  for (const key of ['account_facts', 'boundary', 'goal_ownership', 'managed_assets',
    'pending_actions', 'recovery_proposals']) {
    const card = value[key] as Record<string, unknown>;
    if (!['PROVEN', 'NOT_PROVEN', 'INCOMPLETE'].includes(String(card.state))) invalid();
  }
  for (const key of ['goal_ownership', 'pending_actions', 'recovery_proposals', 'next_obligations']) {
    if (!Array.isArray((value[key] as Record<string, unknown>).items)) invalid();
  }
  if (!Array.isArray((value.managed_assets as Record<string, unknown>).by_goal)) invalid();
  moneyFields(account.facts, ['cash_balance_cents', 'position_principal_cents',
    'unknown_position_principal_cents', 'credit_card_unpaid_cents'], false);
  moneyFields(boundary, ['safe_idle_cents', 'minimum_margin_cents', 'deficit_cents',
    'current_protected_cents', 'current_margin_cents']);
  for (const key of ['protected_cents_by_reason', 'current_protected_cents_by_reason']) {
    if (boundary[key] !== null && !record(boundary[key])) invalid();
  }
  const ownership = value.goal_ownership as Record<string, unknown>;
  moneyFields(ownership, ['cash_owned_cents', 'principal_owned_cents', 'allocated_cents', 'unassigned_goal_cash_cents']);
  const assets = value.managed_assets as Record<string, unknown>;
  moneyFields(assets, ['managed_current_principal_cents', 'general_principal_cents',
    'held_or_matured_cents', 'redeeming_cents', 'pending_purchase_cents']);
  moneyFields(assets, ['excluded_manual_count', 'unknown_position_count'], false);
  for (const item of records(account.facts.accounts)) {
    stringFields(item, ['id', 'name', 'account_type']); moneyFields(item, ['balance_cents'], false);
  }
  for (const item of records(ownership.items)) {
    stringFields(item, ['goal_id', 'name']);
    moneyFields(item, ['cash_owned_cents', 'principal_owned_cents', 'allocated_cents'], false);
  }
  for (const item of records(assets.by_goal)) {
    stringFields(item, ['goal_id']); moneyFields(item, ['principal_cents', 'pending_purchase_cents'], false);
  }
  const next = value.next_obligations as Record<string, unknown>;
  if (!['PROVEN', 'NOT_PROVEN'].includes(String(next.status)) || typeof next.items_complete !== 'boolean') invalid();
  moneyFields(next, ['next_count', 'next_remaining_protection_cents']);
  for (const item of records(next.items)) {
    stringFields(item, ['occurrence_id', 'kind', 'due_date', 'projection_payment_date', 'total_basis']);
    moneyFields(item, ['remaining_protection_cents', 'protected_total_cents'], false);
    if (typeof item.overdue !== 'boolean') invalid();
  }
  for (const item of records((value.pending_actions as Record<string, unknown>).items)) {
    stringFields(item, ['action_id', 'decision_run_id', 'action_type', 'status', 'prepared_level', 'audit_status']);
    moneyFields(item, ['amount_cents', 'fee_cents', 'loss_cents']);
    if (!strings(item.reason_codes) || typeof item.bank_state_proven !== 'boolean' ||
      typeof item.receipt_verified !== 'boolean' ||
      (item.current_decision !== null && (!record(item.current_decision) || typeof item.current_decision.level !== 'string'))) invalid();
  }
  for (const item of records((value.recovery_proposals as Record<string, unknown>).items)) {
    stringFields(item, ['run_id', 'as_of', 'status', 'original_status', 'audit_status']);
    moneyFields(item, ['fee_cents', 'loss_cents']);
    if (!Number.isFinite(Date.parse(String(item.as_of))) || !strings(item.reason_codes)) invalid();
  }
  for (const key of ['account_facts', 'boundary', 'goal_ownership', 'managed_assets']) {
    const card = value[key] as Record<string, unknown>;
    if (card.issues !== undefined) for (const issue of records(card.issues)) {
      stringFields(issue, ['code', 'source_ref', 'message']);
    }
  }
  for (const item of records(boundary.blocking_constraints)) stringFields(item, ['code']);
  if (!strings(boundary.calculation_notes) || !strings(value.source_evidence_ids)) invalid();
  for (const key of ['pending_actions', 'recovery_proposals']) {
    const card = value[key] as Record<string, unknown>;
    if (!Number.isSafeInteger(card.total) || Number(card.total) < 0 ||
      typeof card.list_complete !== 'boolean' || typeof card.has_more !== 'boolean') invalid();
  }
  const intervention = value.intervention as Record<string, unknown>;
  const audit = value.audit as Record<string, unknown>;
  moneyFields(intervention, ['known_required_count'], false);
  stringFields(audit, ['status']);
  if (!['NONE', 'CONFIRMATION_REQUIRED', 'RECONCILIATION_REQUIRED', 'REVIEW_REQUIRED', 'NOT_PROVEN']
    .includes(String(intervention.status)) || typeof intervention.complete !== 'boolean' ||
    !Array.isArray(intervention.reason_codes) || !record(audit.anchored_run_statuses) ||
    audit.scope !== 'CURRENT_LIVE_EPOCH' || typeof audit.complete !== 'boolean') invalid();
  return value as Dashboard;
}

export async function getDashboard(): Promise<Dashboard> {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/dashboard`);
  if (!response.ok) throw new Error('暂时无法获取资金总览，请稍后重试');
  return parseDashboard(await response.json());
}
