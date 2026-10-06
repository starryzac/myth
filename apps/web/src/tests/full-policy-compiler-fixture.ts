/** Synthetic UI/HTTP fixtures only; no user, bank, model or runtime evidence. */
import { compilerTemplates, fullCompilerTextHash, fullCompilerVersion } from '../api/full-policy-compilation';
import type { FullCompilation, FullGrammar } from '../api/full-policy-compilation';
import type { TemplateCandidate } from '../api/full-policies';
export const compilerUser = '60000000-0000-4000-8000-000000000001';
export const compilerConfig = () => ({ type: 'emergency_buffer', name: null, valid_from: null, valid_until: null, amount_cents: 300000 });
export async function compilerFixture(text = '保留3000元应急金'): Promise<FullCompilation> {
  const points = Array.from(text); const moneyStart = points.join('').indexOf('3000');
  const start = Array.from(text.slice(0, moneyStart)).length;
  return { simulation: true, user_id: compilerUser, reference_date: '2026-10-06', timezone: 'Asia/Shanghai', compiler_version: fullCompilerVersion, comparison_source: 'NONE', confirmation_record_created: false, grants_authority: false, bank_authority: false,
    compilation: { simulation: true, compiler_version: fullCompilerVersion, dsl_version: 'FULL_V1', engine: 'rules', status: 'READY_FOR_REVIEW', template_name: 'EmergencyBufferPolicy', original_text_sha256: await fullCompilerTextHash(text), redacted_source_text: text, draft: { type: 'emergency_buffer', amount_cents: 300000 }, configuration: compilerConfig(), configuration_hash: 'a'.repeat(64), source_fragments: [{ field: 'amount_cents', start, end: start + 5, redacted_text: '3000元', original_fragment_sha256: await fullCompilerTextHash('3000元') }], issues: [], defaulted_fields: ['name', 'valid_from', 'valid_until'], differences: [], summary: 'SYNTHETIC_UI_CANDIDATE · 应急缓冲 ¥3000.00，尚未确认。', evidence_level: 'USER_DECLARED', requires_confirmation: true, grants_authority: false, bank_authority: false, policy_created: false, reference_validation: 'NOT_SERVER_VERIFIED', manual_review_required: true, privacy_redactions: {} } };
}
export function compilerGrammarFixture(): FullGrammar { return { simulation: true, compiler_version: fullCompilerVersion, examples: Object.fromEntries(compilerTemplates.map((name) => [name, `${name} · SYNTHETIC_CONTROLLED_SENTENCE`])) as FullGrammar['examples'], original_examples_are_synthetic: true, supported_scope: 'SYNTHETIC_READONLY_TWELVE_GRAMMAR', bank_authority: false }; }
export function compiledCandidateFixture(): TemplateCandidate { return { template_name: 'EmergencyBufferPolicy', dsl_version: 'FULL_V1', normalized_configuration: compilerConfig(), configuration_hash: 'a'.repeat(64), candidate_only: true, authority_granted: false, reference_validation_pending: true }; }
