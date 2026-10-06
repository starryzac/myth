import { useEffect, useState } from 'react';
import { errorMessage } from '../api/http';
import { lookupDynamicGoal } from '../api/full-dynamic-goal-execution';
import { acceptDynamicGoalRead, acceptDynamicGoalWorkspaceRead, dynamicGoalWorkspaceIntent, recoverDynamicGoalOperation, useDynamicGoalOperation } from '../features/full-dynamic-goal-operation';

/** Original GET remains reachable without inventing a missing policy or loading current lists. */
export default function DynamicGoalOriginalRecoveryPanel() {
  const operation = useDynamicGoalOperation();
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { void recoverDynamicGoalOperation(); }, []);
  async function readOriginal() {
    if (busy || operation.busy || operation.recovering || (!operation.pending && !operation.workspace)) return;
    const pending = operation.pending; setBusy(true); setError(''); setNotice('');
    try {
      const intent = pending ?? await dynamicGoalWorkspaceIntent(); const value = await lookupDynamicGoal(intent);
      if (pending) { const result = await acceptDynamicGoalRead(intent, value); setNotice(result.complete ? '完整原请求与原结果已核对；固定动作未终局时继续阻挡其他资金变更。' : '原结果尚未终局，保留原body、动作与键。NOT_FOUND不是最终未提交证明。'); }
      else { await acceptDynamicGoalWorkspaceRead(intent, value); setNotice('独立GET已核对固定原动作；历史确认与回执不提供当前银行授权。'); }
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  if (!operation.pending && !operation.workspace && !operation.storage_error && !notice && !error) return null;
  const original = operation.pending?.prepare_request ?? operation.workspace?.original_request;
  return <section className="card readonly-section" aria-label="动态目标独立原件恢复"><h4>动态目标原件 · 独立只读恢复</h4>
    <p>当前目标、用户或资金列表未读取时仍保留原请求。这里仅核对原件，继续固定动作需要当前目标页面重新验源。</p>
    {operation.storage_error && <p role="alert">{operation.storage_error}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {original && <><p>原目标 {original.goal_id} · 原版本 {original.expected_policy_version_id} · 原周期 {original.expected_epoch_id} · 原键 {original.idempotency_key}</p>
      <button type="button" disabled={busy || operation.busy || operation.recovering} onClick={() => void readOriginal()}>独立只读核对原动态目标原件</button>
      <details><summary>完整原动态请求或固定工作区</summary><pre>{JSON.stringify(operation.pending ?? operation.workspace, null, 2)}</pre></details></>}
  </section>;
}
