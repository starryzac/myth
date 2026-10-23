const MAX = 9223372036854775807n;
const MAX_COMMAND_CENTS = 9000000000000000n;

export function cents(value: unknown): string {
  if (typeof value !== 'string' || !/^-?(0|[1-9]\d*)$/.test(value)) throw new Error('服务端金额必须是整数分字符串');
  const amount = BigInt(value);
  if (amount < -MAX - 1n || amount > MAX) throw new Error('金额超出支持范围');
  return value;
}

export function yuanToCents(value: string): string {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/.exec(value.trim());
  if (!match) throw new Error('请输入最多两位小数的人民币金额');
  const amount = BigInt(match[1]!) * 100n + BigInt((match[2] ?? '').padEnd(2, '0'));
  if (amount <= 0n || amount > MAX_COMMAND_CENTS) throw new Error('金额必须大于零且不超过 90,000,000,000,000.00 元');
  return amount.toString();
}

export function money(value: string): string {
  const amount = BigInt(cents(value));
  const absolute = amount < 0n ? -amount : amount;
  return `${amount < 0n ? '−' : ''}¥${(absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${(absolute % 100n).toString().padStart(2, '0')}`;
}
