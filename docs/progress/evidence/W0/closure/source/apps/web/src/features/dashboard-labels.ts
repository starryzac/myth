const labels: Record<string, string> = {
  PROVEN: '已核验', NOT_PROVEN: '待核验', INCOMPLETE: '核验未完整',
  READY: '资金边界可计算', LIQUIDITY_RISK: '存在流动性缺口', INSUFFICIENT_EVIDENCE: '资料不足，边界待核验',
  MATCHED: '账面与模拟银行一致', VALID: '审计链核验通过', LEGACY_UNAUDITED: '历史数据未建立审计链',
  INTEGRITY_ERROR: '审计完整性异常', UNSUPPORTED_VERSION: '审计版本尚不支持',
  HEAD_MISSING: '缺少审计链头', UNKNOWN: '结果未知，等待核对',
  NONE: '暂无已知待介入事项', CONFIRMATION_REQUIRED: '存在待确认事项',
  RECONCILIATION_REQUIRED: '存在结果待核对事项', REVIEW_REQUIRED: '存在待复核建议',
  AUTO_EXECUTE: '符合自主执行分级', ASK_ONCE: '需单次确认', ADVISE_ONLY: '仅提供建议', BLOCKED: '已阻止',
  PLANNED: '已准备', AUTHORIZED: '已授权', SUBMITTED: '已提交', SUCCEEDED: '已成功', RECONCILED: '已核对',
  FAILED: '已失败', INVALIDATED: '原动作已失效', CANCELLED: '已取消', ACCEPTED: '模拟银行已受理',
  REJECTED: '已拒绝', SETTLED: '已结算', HELD: '持有中', REDEEMING: '赎回处理中',
  PURCHASE_ASSET: '产品配置', REDEEM_ASSET: '本金赎回', ALLOCATE_GOAL: '目标划拨',
  PAY_RECURRING: '周期义务付款', TRANSFER_INTERNAL: '账户内转账',
  NO_RECOVERY_NEEDED: '无需恢复', AUTO_RECOVERY_AVAILABLE: '存在自主恢复候选',
  PARTIAL_RECOVERY_AVAILABLE: '存在部分恢复候选', NO_SAFE_RECOVERY: '暂无安全恢复方案',
  BILL_ACTUAL: '已确认账单金额', POLICY_EXACT: '已确认固定金额',
  POLICY_RANGE_MAX: '按策略区间上限保护', SETTLEMENT_FINAL: '已确认最终金额',
  EXACT: '金额已确认', UPPER_BOUND: '包含区间上限', MIXED: '确认金额与区间上限混合',
};

export function label(code: string): string {
  return labels[code] ?? `待说明（${code}）`;
}

export function calendarDate(value: string | null): string {
  if (value === null) return '待核验';
  const [year, month, day] = value.split('-');
  return `${year}年${Number(month)}月${Number(day)}日`;
}

export function snapshotTime(value: string, timezone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}
