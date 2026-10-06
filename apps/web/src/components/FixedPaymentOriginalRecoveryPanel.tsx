import { useEffect, useState } from 'react';
import { errorMessage } from '../api/http';
import { lookupPaymentIntent } from '../api/full-payment-relations';
import { acceptFixedPaymentRead, recoverFixedPaymentOperation, useFixedPaymentOperation } from '../features/fixed-payment-operation';

/** Always reachable outside selected policy details. This component has no POST. */
export default function FixedPaymentOriginalRecoveryPanel() {
  const operation = useFixedPaymentOperation();
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { void recoverFixedPaymentOperation(); }, []);
  async function readOriginal() {
    const intent = operation.pending;
    if (!intent || busy || operation.busy || operation.recovering) return;
    setBusy(true); setError(null); setNotice(null);
    try { const value = await lookupPaymentIntent(intent); const cleared = await acceptFixedPaymentRead(intent, value); setNotice(cleared ? '完整原件已匹配原请求与身份，待核对门已解除。历史记录不转为当前资金授权。' : '原键未终局或付款未取得完整结算回执，原请求继续保留。'); }
    catch (cause) { setError(`${errorMessage(cause)}；没有删除或替换原请求。`); }
    finally { setBusy(false); }
  }
  if (!operation.pending && !operation.storage_error && !notice && !error) return null;
  return <section className="card readonly-section" aria-label="固定付款独立原件恢复"><h4>固定付款原请求 · 独立只读恢复</h4><p>当前策略列表、详情或身份读取失败也可查询原件。这里只读，不重发、确认、执行或创建替代付款。</p>
    {operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {operation.pending && <><p>原用户 {operation.pending.user_id} · 原周期 {operation.pending.epoch_id} · 原步骤 {operation.pending.kind} · 原Full关系 {operation.pending.full_policy_id}。</p><button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void readOriginal()}>独立只读核对固定付款原件</button><details><summary>保存的完整原请求</summary><pre className="readonly-raw">{JSON.stringify(operation.pending, null, 2)}</pre></details></>}
  </section>;
}
