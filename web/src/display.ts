import { ApiError } from './api';

const labels: Record<string, string> = {
  ACTIVE: '当前有效', PAUSED: '已暂停', REVOKED: '已撤销', EXPIRED: '已到期',
  READY: '可以提交', NO_ACTION: '当前无需安排', LIQUIDITY_RISK: '资金保护条件未满足', MINIMUM_SHORTFALL: '最低目标要求未满足',
  ACCEPTED: '银行已受理，等待结清', SETTLED: '银行已结清', REJECTED: '银行已拒绝',
  NOT_FOUND: '银行尚未查到原键，仍需核对', UNKNOWN: '银行结果尚待核实',
  HEALTHY: '正常处理', BACKOFF: '等待下次核对', RETRY_EXHAUSTED: '自动重试暂止，原结果仍待核对',
  RETRIES_EXHAUSTED: '自动重试暂止，原结果仍待核对', STOPPED: '自动处理已暂停', DEGRADED: '处理延迟，继续保留原操作',
};
const errors: Record<string, string> = {
  AGENT_INVALID_INPUT: '助手请求字段不符合只读工具合同，请重新选择当前对象。',
  AGENT_TOOL_REJECTED: '助手工具请求越过了注册边界，已拒绝。',
  AGENT_REFERENCE_REJECTED: '助手引用不属于当前可核实的本人对象，请重新读取。',
  AGENT_STALE_REFERENCE: '助手引用的业务对象已经变化，请重新读取当前事实。',
  AGENT_BUDGET_EXCEEDED: '本轮助手预算已到上限，调用已停止。结构化表单仍可使用。',
  AGENT_PROVIDER_NOT_CONFIGURED: '当前未配置模型，可以继续使用离线助手与结构化表单。',
  UNAUTHENTICATED: '会话码无效或会话已过期，请重新登录。原操作记录仍会保留。',
  FORBIDDEN: '当前会话不能访问或确认这项内容，请重新核对所属用户与环境。',
  NOT_FOUND: '尚未查到这条原记录。未查到不代表没有发生效果，请保留原键继续核对。',
  AUTHORIZATION_REVOKED: '原持续授权已经撤销，不能据此发起新的安排；已在途原操作仍需核对。',
  CONFIRMATION_INVALID: '本次确认与原计划不匹配，请重新读取计划并明确确认。',
  EVIDENCE_INVALID: '当前事实未通过核验，请先重新读取账户与目标。',
  EVIDENCE_INCOMPLETE: '当前事实还不完整，暂不能生成可执行安排。',
  IDEMPOTENCY_CONFLICT: '原提交键已经绑定另一份请求，不能更改后重用。请保留原记录。',
  INSUFFICIENT_FUNDS: '当前资金不足以覆盖这笔安排，请重新读取当前事实。',
  SOURCE_CHANGED: '收入来源或资金版本已变化，请读取原操作与最新事实。',
  INVALID_COMMAND: '这份安排请求不符合当前合同，请重新核对原计划。',
  UNSUPPORTED_MULTI_SOURCE: '此入口暂不支持这份多来源安排，请保留计划。',
  PLANNING_REJECTED: '当前条件无法形成可提交计划，请先查看目标与规则。',
  INVALID_GRANT: '持续授权的范围或上限不符合当前规则。',
  INVALID_POLICY: '规则的目标或单次上限不符合当前条件。',
  INTERNAL_ERROR: '服务暂时无法完成请求，请保留原操作并稍后核对。',
  INVALID_RESPONSE: '服务端暂未返回可识别的数据，请保留原操作并稍后核对。',
};

export function businessState(value?: string): string { return value ? labels[value] ?? '状态尚待核实' : '等待回读'; }
export function userError(cause: unknown): string {
  if (cause instanceof ApiError) return errors[cause.code] ?? '请求尚未完成，请保留原记录并重新核对。';
  if (cause instanceof DOMException && (cause.name === 'TimeoutError' || cause.name === 'AbortError')) return '本次读取等待已结束，请保留原请求并沿原键核对；这不代表请求没有保存。';
  if (cause instanceof TypeError) return '当前连接未完成，请保留原操作并重新核对。';
  return cause instanceof Error ? cause.message : '请求尚未完成，请核对原操作。';
}
export function requiresLogin(cause: unknown): boolean { return cause instanceof ApiError && (cause.status === 401 || cause.status === 403); }
export function timestamp(value: string): string {
  if (!Number.isFinite(Date.parse(value))) return '等待服务端时间';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value));
}
