import { ApiError } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { object } from '../features/policy-form';
import { displayText } from '../zhiyu/display';

export const money = (value: number | null | undefined) => value == null ? '待核实' : `¥${formatMoneyCents(value)}`;
const uuid = /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/gi;
const digest = /\b[0-9a-f]{64}\b/gi;
/** Technical originals stay in the diagnostic panel. Unknown codes become actionable prose. */
export function businessText(raw: string): string {
  if (raw.trimStart().startsWith('[') || raw.trimStart().startsWith('{') || /"[a-z_]+"\s*:/.test(raw)) return '详细内容已保存在服务端，请查看当前规则与结果。';
  return displayText(raw).replace(uuid, '原操作').replace(digest, '已保存的结果依据')
    .replace(/\b[A-Z][A-Z0-9_]{2,}\b/g, '当前条件需要核实').replace(/\b[a-z]+(?:_[a-z]+)+\b/g, '相关条件');
}
export function userError(error: unknown): string {
  if (error instanceof ApiError) {
    const labels: Record<string, string> = {
      NETWORK_ERROR: '连接暂时中断，系统会继续核对同一原请求。', LLM_NOT_CONFIGURED: '模型尚未配置，请打开模型设置或使用离线模板。',
      LLM_TIMEOUT: '模型响应超时，请稍后重试；已授权安排与原结果核对会继续。', LLM_AUTHENTICATION_FAILED: '模型认证失败，请检查模型设置中的密钥。',
      STALE_VERSION: '规则或资金事实已变化，请读取新候选后再确认。', INVALID_RESPONSE: '服务响应未通过核实，请保留原操作并继续查询。',
      AGENT_CONTEXT_CLOSED: '这条需求的补问已结束，请开始新的需求。', AGENT_CONTEXT_NOT_FOUND: '先前的需求尚未核实，请保留当前回答，或明确开始新的需求。',
    };
    return labels[error.code] ?? (error.status >= 500 ? '服务暂时无法完成请求，请稍后核对原状态。' : businessText(error.message));
  }
  return error instanceof Error ? businessText(error.message) : '暂时无法读取结果，请稍后再试。';
}
export const ruleNames: Record<string, string> = { recurring_obligation: '周期义务', living_reserve: '生活准备金', emergency_buffer: '应急金', dated_expense: '定期支出', goal_saving: '目标储蓄', long_term_goal: '长期目标', periodic_transfer: '周期转账', asset_authorization: '资产配置', recovery: '资金回收', goal_allocation: '目标分配', cross_goal_reallocation: '目标间调整', seasonal_reserve: '季节储备', intervention: '必要介入' };
export function ruleSummary(configuration: Record<string, unknown>): string[] {
  const rows: string[] = [];
  if (Number.isSafeInteger(configuration.amount_cents)) rows.push(`保留 ${money(configuration.amount_cents as number)}`);
  if (Number.isSafeInteger(configuration.target_cents)) rows.push(`目标 ${money(configuration.target_cents as number)}`);
  if (typeof configuration.deadline === 'string') rows.push(`截止 ${configuration.deadline}`);
  const monthly = configuration.monthly_contribution;
  if (object(monthly) && Number.isSafeInteger(monthly.max_cents)) rows.push(`每月最多安排 ${money(monthly.max_cents as number)}`);
  if (Number.isSafeInteger(configuration.single_action_cap_cents)) rows.push(`单笔上限 ${money(configuration.single_action_cap_cents as number)}`);
  if (typeof configuration.valid_until === 'string') rows.push(`有效至 ${configuration.valid_until}`);
  return rows.length ? rows : ['请核对下面的必要授权内容。'];
}
