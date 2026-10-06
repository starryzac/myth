/** Pure domain synthetic inputs only. No PG, bank, audit or browser acceptance. */
import source from './recovery-composed-action-set-fixture.json';
import type { RecoveryComposedActionSet } from '../api/recovery-composed-action-set';
export function recoveryComposedFixture(kind: 'complete' | 't0_unknown_original' | 't1_unknown' = 't0_unknown_original'): RecoveryComposedActionSet { return structuredClone(source[kind]) as unknown as RecoveryComposedActionSet; }
