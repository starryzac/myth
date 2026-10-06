import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
export type Policy = components['schemas']['PolicyView'];
export type Proposal = components['schemas']['ProposalView'];
export type Compilation = components['schemas']['CompilationResponse'];
export type ChangePreview = components['schemas']['PolicyChangePreviewResponse'];
export type ChangeCommand = components['schemas']['PolicyChangeRequest'];
export type Lifecycle = components['schemas']['LifecycleResult'];
export type Configuration = ChangeCommand['configuration'];

async function list<T extends { simulation?: true; items: unknown[] }>(path: string, valid: (item: Record<string, unknown>) => boolean): Promise<T> {
  const result = await request<T>(path);
  if (!Array.isArray(result.items) || !result.items.every((item) => object(item) && valid(item))) throw new Error('策略列表未通过完整性校验');
  return result;
}
function validVersion(value: unknown): boolean {
  return object(value) && typeof value.id === 'string' && Number.isSafeInteger(value.version_number) && object(value.configuration) &&
    typeof value.change_reason === 'string' && typeof value.content_hash === 'string';
}
export const getPolicies = () => list<components['schemas']['PolicyList']>('/policies', (item) =>
  typeof item.id === 'string' && typeof item.name === 'string' && typeof item.policy_type === 'string' && typeof item.effective_status === 'string' &&
  typeof item.version_authorized === 'boolean' && (item.current_version === null || validVersion(item.current_version)));
export const getProposals = () => list<components['schemas']['ProposalList']>('/policy-proposals', (item) =>
  typeof item.id === 'string' && typeof item.status === 'string' && typeof item.source_type === 'string' && typeof item.source_text === 'string' &&
  typeof item.compiler_version === 'string' && object(item.configuration) && typeof item.configuration_hash === 'string' &&
  typeof item.validation_ready === 'boolean' && (item.compilation_id === null || typeof item.compilation_id === 'string'));
export const getVersions = (id: string) => list<components['schemas']['PolicyVersionList']>(`/policies/${encodeURIComponent(id)}/versions`, validVersion);
export async function getPolicyMapVersions(id: string) {
  const result = await getVersions(id);
  const identities = new Set<string>(); const numbers = new Set<number>();
  for (const version of result.items) {
    if (version.policy_id !== id || version.version_number < 1 || identities.has(version.id) || numbers.has(version.version_number)) throw new Error('版本列表身份或唯一性未通过校验');
    identities.add(version.id); numbers.add(version.version_number);
  }
  return result;
}
export const discoverPolicies = () => request<components['schemas']['DiscoveryResult']>('/policies/discover', 'POST', {});
async function compilationRequest(path: string, method = 'GET', body?: unknown): Promise<Compilation> {
  const result = await request<Compilation>(path, method, body);
  if (typeof result.compilation_id !== 'string' || !object(result.compilation) || !object(result.compilation.draft) ||
    typeof result.compilation.reference_date !== 'string' || typeof result.compilation.timezone !== 'string' ||
    !Array.isArray(result.compilation.issues) || !Array.isArray(result.compilation.assumptions) ||
    !(result.configuration === null || object(result.configuration))) throw new Error('原编译响应未通过完整性校验');
  return result;
}
export const compilePolicy = (text: string) => compilationRequest('/policies/compile', 'POST', { text, engine: 'rules' });
export const getCompilation = (id: string) => compilationRequest(`/policy-compilations/${encodeURIComponent(id)}`);
export const reviseCompilation = (id: string, configuration: Configuration) => compilationRequest(`/policy-compilations/${encodeURIComponent(id)}/revise`, 'POST', { configuration });
export const confirmProposal = (id: string, reviewed_hash: string) => request<Lifecycle>(`/policy-proposals/${encodeURIComponent(id)}/confirm`, 'POST', { accepted: true, reviewed_hash });
export async function previewChange(id: string, expected_version_id: string, configuration: Configuration): Promise<ChangePreview> {
  const result = await request<ChangePreview>(`/policies/${encodeURIComponent(id)}/change-preview`, 'POST', { expected_version_id, configuration });
  if (result.schema_version !== 'policy-change-preview-v1' || result.preview_only !== true || result.financial_only !== true ||
    result.policy_id !== id || result.expected_version_id !== expected_version_id || !object(result.configuration) ||
    typeof result.configuration_hash !== 'string' || !/^[0-9a-f]{64}$/.test(result.configuration_hash) ||
    !object(result.before) || !object(result.after) || !Number.isFinite(Date.parse(result.as_of)) ||
    !['Asia/Shanghai', 'UTC'].includes(result.timezone) || typeof result.user_id !== 'string' ||
    !Array.isArray(result.notes) || !result.notes.every((item) => typeof item === 'string') ||
    ![result.before, result.after].every((card) => typeof card.state === 'string' &&
      ['safe_idle_cents', 'minimum_margin_cents', 'deficit_cents'].every((key) => card[key as keyof typeof card] === null || Number.isSafeInteger(card[key as keyof typeof card])))) throw new Error('变更预览未通过完整性校验');
  return result;
}
export const changePolicy = (id: string, command: ChangeCommand) => request<Lifecycle>(`/policies/${encodeURIComponent(id)}`, 'PATCH', command);
export const changeState = (id: string, expected_version_id: string, operation: 'suspend' | 'revoke') => request<Lifecycle>(`/policies/${encodeURIComponent(id)}/${operation}`, 'POST', { expected_version_id });
