/** Synthetic service-double JSON only; never current bank, permission or solver evidence. */
import raw from './goal-conflict-fixtures.json';
import type { FullGoalConflicts, FullGoalRepairs, GoalRepairSelection } from '../api/full-goal-conflicts';
import type { Goal } from '../api/goals';
export const conflictGoals = () => structuredClone(raw.goals) as Goal[];
export const conflictFixture = () => structuredClone(raw.conflicts) as FullGoalConflicts;
// Imported JSON cannot infer the fixed eight-value tuple. This cast is confined
// to the explicitly synthetic fixture; production reads still parse every field.
export const repairFixture = (): FullGoalRepairs => structuredClone(raw.repairs) as unknown as FullGoalRepairs;
export const repairSelection = () => structuredClone(raw.selection) as GoalRepairSelection;
export function unknownConflictFixture(): FullGoalConflicts {
  return { ...conflictFixture(), state: 'UNKNOWN', explanation: null, current_permission_repair: null,
    review_state_hash: null, reasons: ['SYNTHETIC_MISSING_BANK_ORIGINAL'] };
}
export function unknownRepairFixture(): FullGoalRepairs {
  return { ...repairFixture(), state: 'UNKNOWN', original_conflicts: unknownConflictFixture(),
    proposal: null, version_previews: [], reasons: ['SYNTHETIC_MISSING_BANK_ORIGINAL'] };
}
