/** Actual pure-domain serialization of synthetic declaration sources; no PG/bank result. */
import type { FullPolicyDependencyReview } from '../api/full-policy-dependencies';
import original from './full-policy-dependencies-fixture.json';
export const dependencyPolicyId = original.selected_policy_id;
export const dependencyVersionId = original.policies[0]!.version_id;
export function dependencyFixture(): FullPolicyDependencyReview { return structuredClone(original) as FullPolicyDependencyReview; }
