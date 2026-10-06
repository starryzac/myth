/** TOOL_ONLY: outputs from existing pure domain risk fixtures; no database or bank was used. */
import source from './registered-action-set-fixture.json';
import type { RegisteredActionSet } from '../api/registered-action-set';
export type RegisteredFixtureKind = keyof typeof source;
export function registeredFixture(kind: RegisteredFixtureKind = 'complete'): RegisteredActionSet {
  return structuredClone(source[kind]) as unknown as RegisteredActionSet;
}
