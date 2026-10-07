import type { PaymentScope } from '../api/full-payment-relations';
function localDay(asOf: string, scope: PaymentScope) { const parts = new Intl.DateTimeFormat('en', { timeZone: scope.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(asOf)); return ['year', 'month', 'day'].map((key) => parts.find((part) => part.type === key)!.value).join('-'); }
/** A conservative UI gate using the server's date and month. Execution authority
 * and actual finances are always independently rechecked by the server. */
export function dueInServerWindow(asOf: string, period: string | undefined, scope: PaymentScope | null): boolean {
  if (!scope || !period) return false; const first = new Date(`${period}-01T00:00:00Z`); first.setUTCMonth(first.getUTCMonth() + 1); first.setUTCDate(0);
  const due = `${period}-${String(Math.min(scope.due_day, first.getUTCDate())).padStart(2, '0')}`; const today = localDay(asOf, scope);
  return today.startsWith(period) && today >= due && due >= localDay(scope.valid_from, scope);
}
