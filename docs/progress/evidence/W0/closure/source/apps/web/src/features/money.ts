/** Integer-cent formatting; null is unknown and never becomes zero. */
export function formatMoneyCents(cents: number | null): string {
  if (cents === null) return '—';
  if (!Number.isSafeInteger(cents)) throw new Error('金额超出可精确显示的整数分范围');
  const value = BigInt(cents);
  const magnitude = value < 0n ? -value : value;
  const yuan = (magnitude / 100n).toLocaleString('zh-CN');
  const fraction = (magnitude % 100n).toString().padStart(2, '0');
  return `${value < 0n ? '-' : ''}${yuan}.${fraction}`;
}

/** Validate JSON money before it can reach a component or chart. */
export function assertMoneyFields(value: unknown): void {
  if (Array.isArray(value)) {
    for (const child of value) assertMoneyFields(child);
    return;
  }
  if (typeof value !== 'object' || value === null) return;
  for (const [key, child] of Object.entries(value)) {
    if (key.endsWith('_cents') && child !== null && !Number.isSafeInteger(child)) {
      throw new Error('资金响应含无法精确显示的金额');
    }
    if (key.includes('_cents_by_') && typeof child === 'object' && child !== null) {
      for (const amount of Object.values(child)) {
        if (amount !== null && !Number.isSafeInteger(amount)) {
          throw new Error('资金分项含无法精确显示的金额');
        }
      }
    }
    assertMoneyFields(child);
  }
}
