/** SYNTHETIC_PURE_MODEL_HTTP_SHAPE_NOT_PRODUCT_PROOF; zero DB/browser observations. */
import source from './financial-history-preview-fixture.json';
import type { HistoryChangePreview, HistoryPreviewBinding, HistoryPreviewBody } from '../api/full-policy-change-history';

export const historyFixture = (): HistoryChangePreview => structuredClone(source) as unknown as HistoryChangePreview;
export const historyBinding = (): HistoryPreviewBinding => ({ policyId: source.policy_id, userId: source.user_id, expectedVersionId: source.expected_version_id, expectedVersionNumber: 2, expectedEpochId: source.epoch_id });
export const historyBody = (): HistoryPreviewBody => ({ expected_version_id: source.expected_version_id, configuration: structuredClone(source.after_configuration) });
export function historyUnknownFixture(): HistoryChangePreview {
  const value = historyFixture(); const p = value.financial_impact;
  p.status = 'UNKNOWN'; p.after = null; p.delta_safe_idle_cents = null; p.delta_minimum_margin_cents = null; p.delta_max_allocatable_by_product = null;
  p.goals = []; p.positions = []; p.candidate_commitments = []; p.retained_original_occurrence_ids = [];
  p.reasons = ['ORIGINAL_FULL_CURVE_NOT_RECONSTRUCTIBLE_FOR_THIS_HISTORY_BRANCH'];
  return value;
}
