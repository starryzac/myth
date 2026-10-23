import { Client } from './api';
import { nfClosed, nfHash, nfInt, nfList, nfObject, nfOwner, nfText, nfTime, nfUnique } from './native-financial-api';
import { templateID } from './template-schema';

export interface BillSource { id: string; state: 'CONFIGURED_NOT_OBSERVED' | 'SCHEMA_UNAVAILABLE' | 'OBSERVED_CAPTURE'; capture_id?: string; observed_at?: string; intake_state?: string }
export interface BillIntake { capture_id: string; capture_hash: string; state: 'CAPTURED_PENDING' | 'IMPORTED'; pending_reason?: string; imported_count?: number; recorded_at?: string }
export interface PendingBill { protocol: 'zhiyu-browser-bill-source-v1'; environment: string; user_id: string; key: string; command: { source_id: string }; capture_id?: string; capture_hash?: string }
export function parseBillSources(raw: unknown, client: Client): BillSource[] {
  const r = nfObject(raw); nfClosed(r, ['protocol', 'scope', 'sources', 'bank_authority']); nfOwner(r.scope, client);
  if (r.protocol !== 'native-bill-source-inventory-v1' || r.bank_authority !== false) throw new Error('账单来源清单不属于当前只读范围');
  const sources = nfList(r.sources, (raw): BillSource => {
    const s = nfObject(raw); nfClosed(s, ['id', 'state', 'capture_id', 'capture_hash', 'observed_at', 'intake_state']);
    if (!['CONFIGURED_NOT_OBSERVED', 'SCHEMA_UNAVAILABLE', 'OBSERVED_CAPTURE'].includes(String(s.state))) throw new Error('账单来源状态不受支持');
    const base = { id: nfText(s.id, 128), state: s.state as BillSource['state'] };
    if (s.state !== 'OBSERVED_CAPTURE') { if (s.capture_id != null || s.capture_hash != null || s.observed_at != null || s.intake_state != null) throw new Error('账单来源出现未观察原件'); return base; }
    nfHash(s.capture_hash); if (!['CAPTURED_PENDING', 'IMPORTED'].includes(String(s.intake_state))) throw new Error('账单导入状态不受支持');
    return { ...base, capture_id: templateID(s.capture_id), observed_at: nfTime(s.observed_at), intake_state: s.intake_state as string };
  }, 1); nfUnique(sources.map((s) => s.id)); return sources;
}
export function parseBillIntake(raw: unknown, client: Client): BillIntake {
  const r = nfObject(raw); nfClosed(r, ['protocol', 'capture_id', 'capture_hash', 'state', 'pending_reason', 'result', 'bank_authority']);
  if (r.protocol !== 'native-bill-intake-view-v1' || r.bank_authority !== false || !['CAPTURED_PENDING', 'IMPORTED'].includes(String(r.state))) throw new Error('账单观察原件不完整');
  const base: BillIntake = { capture_id: templateID(r.capture_id), capture_hash: nfHash(r.capture_hash), state: r.state as BillIntake['state'] };
  if (base.state === 'CAPTURED_PENDING') { if (r.result != null) throw new Error('待核对账单出现已入账结果'); return { ...base, pending_reason: nfText(r.pending_reason) }; }
  const result = nfObject(r.result); nfClosed(result, ['protocol', 'scope', 'capture_id', 'imported_bill_ids', 'resolved_capture_ids', 'funds_version', 'source_version', 'protection_hash', 'recorded_at', 'bank_authority']); nfOwner(result.scope, client);
  if (result.protocol !== 'native-bill-import-result-v1' || result.bank_authority !== false || r.pending_reason != null && r.pending_reason !== '') throw new Error('账单入账结果范围不符');
  templateID(result.capture_id); nfInt(result.funds_version, 1); nfInt(result.source_version, 1); nfHash(result.protection_hash);
  const bills = nfList(result.imported_bill_ids, templateID, 1000), resolved = nfList(result.resolved_capture_ids, templateID, 1000); nfUnique(bills); nfUnique(resolved);
  if (!resolved.includes(base.capture_id)) throw new Error('账单结果未包含原观察');
  return { ...base, imported_count: bills.length, recorded_at: nfTime(result.recorded_at) };
}
export function billPendingKey(client: Client) { if (!client.session) throw new Error('请先登录'); return `zhiyu-go:bill-source:v1:${client.session.environment}:${client.session.user_id}`; }
export function loadBillPending(client: Client): PendingBill | null {
  const text = localStorage.getItem(billPendingKey(client)); if (!text) return null;
  try { const p = nfObject(JSON.parse(text)); nfClosed(p, ['protocol', 'environment', 'user_id', 'key', 'command', 'capture_id', 'capture_hash']); const c = nfObject(p.command); nfClosed(c, ['source_id']); if (p.protocol !== 'zhiyu-browser-bill-source-v1' || p.environment !== client.session?.environment || p.user_id !== client.session?.user_id) throw new Error('scope'); templateID(p.key); nfText(c.source_id, 128); if (p.capture_id != null) { templateID(p.capture_id); nfHash(p.capture_hash); } else if (p.capture_hash != null) throw new Error('original'); return p as unknown as PendingBill; }
  catch { throw new Error('保存的账单观察原请求不完整，已保留资料并停止新观察'); }
}
export function saveBillPending(client: Client, p: PendingBill) { const key = billPendingKey(client), raw = JSON.stringify(p); localStorage.setItem(key, raw); if (localStorage.getItem(key) !== raw) throw new Error('未能保留账单原请求，尚未发送'); return loadBillPending(client)!; }
export class BillsClient {
  constructor(readonly client: Client) {}
  async sources() { return parseBillSources(await this.client.request('/bill-sources'), this.client); }
  async original(p: PendingBill) { const v = parseBillIntake(await this.client.request(`/bill-import-commands/${encodeURIComponent(p.key)}`), this.client); if (p.capture_id && (v.capture_id !== p.capture_id || v.capture_hash !== p.capture_hash)) throw new Error('账单观察原件发生替换'); return v; }
  async observe(p: PendingBill) { await this.client.request('/bill-imports', { method: 'POST', headers: { 'Idempotency-Key': p.key }, body: JSON.stringify(p.command) }); return this.original(p); }
  async intake(id: string) { return parseBillIntake(await this.client.request(`/bill-imports/${templateID(id)}`), this.client); }
  async resume(v: BillIntake) { const next = parseBillIntake(await this.client.request(`/bill-imports/${templateID(v.capture_id)}/resume`, { method: 'POST', body: '{}' }), this.client); if (next.capture_id !== v.capture_id || next.capture_hash !== v.capture_hash) throw new Error('账单续传替换了原观察'); return next; }
}
