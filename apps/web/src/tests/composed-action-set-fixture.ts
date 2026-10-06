/** SYNTHETIC_DOMAIN_RESPONSE_SHAPES_ONLY; not PG, bank, complete audit or browser evidence. */
import source from './composed-action-set-fixture.json';
import type { ComposedActionSet } from '../api/composed-action-set';
export function composedFixture(kind: 'auto' | 'ask' | 'unknown' = 'auto'): ComposedActionSet {
  return structuredClone(source[kind]) as unknown as ComposedActionSet;
}
