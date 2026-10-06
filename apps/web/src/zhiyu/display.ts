/** Presentation-only labels. Original wire text remains in folded details. */
const labels: Record<string, string> = {
  SAFE: '安全资金安排',
  REVOKED: '规则撤销后的拒绝',
  RESPONSE_LOSS: '提交响应丢失后的原操作恢复',
  AUTO_EXECUTE: '已确认规则允许本次资金安排',
  ASK_ONCE: '这笔资金安排需要明确确认',
  ADVISE_ONLY: '当前仅提供建议',
  BLOCKED: '当前条件不允许执行',
  SETTLED: '已到账',
  UNKNOWN: '结果待核实',
  SUBMITTED: '已提交，结果待核实',
  CURRENT_AUTHORIZED_SAFE_EFFECT: '已确认规则允许本次安全资金安排',
  EXACT_GOAL_POLICY_VERSION_REQUIRED: '权限版本需有效，并与目标的确认版本一致',
  GOAL_POLICY_NOT_EFFECTIVE_NOW: '目标规则当前未生效',
  POLICY_INACTIVE: '当前规则已失效，不能用于新执行',
  INACTIVE_POLICY: '当前规则未生效，不能执行',
  ORIGINAL_ACTION_UNRESOLVED: '原操作结果待核实，请先核对原件',
  EXPLICIT_CONFIRMATION_REQUIRED: '这笔资金安排需要明确确认',
  EXPLICIT_TRANSFER_CONFIRMATION_REQUIRED: '这笔转入需要明确确认',
  EXPLICIT_COST_CONFIRMATION_REQUIRED: '本次费用或损失需要明确确认',
  ORDINARY_ALLOCATION_REQUIRES_SAFE_BOUNDARY: '储备资金必须处于安全边界内',
  TARGET_CUMULATIVE_MONTHLY_CONTRIBUTION: '按本月已确认的储备额度安排',
  PREVIEW_DOES_NOT_CONSUME_INCOME: '只读预览不使用收入资金',
};
export function displayText(raw: string): string {
  return raw.replace(/\b[A-Z][A-Z0-9_]*\b/g, (code) => labels[code] ?? code);
}
export function displayAuthorization(raw: string): string {
  return /[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/i.test(raw) ? '用户确认的策略版本' : displayText(raw);
}
