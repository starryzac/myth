import { useEffect, useRef, useState } from 'react';
import { getRecoveryComposedActionSet } from '../api/recovery-composed-action-set';
import { createRecoveryObservationIntent, getRecoveryObservation, originalRecoveryObservation, postRecoveryObservation } from '../api/recovery-composed-observations';
import type { RecoveryObservation } from '../api/recovery-composed-observations';
import { ApiError, errorMessage } from '../api/http';
import { acceptRecoveryObservationRead, beginRecoveryComposedObservation, beginRecoveryObservationSameKeyRetry, endRecoveryComposedObservationAttempt, noteRecoveryObservationAbsent, recoverRecoveryComposedObservationOperation, useRecoveryComposedObservationOperation } from '../features/recovery-composed-observation-operation';

function OriginalView({ value }: { value: RecoveryObservation }) {
  return <><p role="status">{value.kind === 'BoundaryCrossed' ? '原登记动作边界发生变化，需要复核' : value.kind === 'BoundaryObserved' ? '原观察已核对' : '原观察已记录，来源比较仍未知'}</p><p>{value.snapshot.as_of} · {value.global_action_set_complete ? '登记来源完整' : '完整性未证明'} · 原运行 {value.observation_run_id}</p><p>只记录观察元数据；没有赎回、到账、权限或通知投递。恢复动作仍需新的明确确认。</p><details><summary>完整原观察响应</summary><pre className="readonly-raw">{originalRecoveryObservation(value)}</pre></details></>;
}
/** GET recovery is independent of current lists, owner login, and other write gates. */
export function RecoveryComposedObservationOriginalRecoveryPanel({ mutationBlocked = false }: { mutationBlocked?: boolean }) {
  const op = useRecoveryComposedObservationOperation(); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [value, setValue] = useState<RecoveryObservation | null>(null);
  const writeBlocked = useRef(mutationBlocked); writeBlocked.current = mutationBlocked;
  useEffect(() => { void recoverRecoveryComposedObservationOperation(); }, []);
  async function read(retry = false) {
    const intent = op.pending; if (busy || op.busy || !intent && !op.original_run_id) return; setBusy(true); setError(''); setValue(null);
    try {
      if (!intent) { setValue(await getRecoveryObservation(op.original_run_id!)); return; }
      let original: RecoveryObservation;
      try { original = await getRecoveryObservation(intent.expected_run_id, intent); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        noteRecoveryObservationAbsent(intent);
        if (!retry) throw new Error('原记录尚未找到，不能视为最终失败；可手动同body/key再提交。');
        await beginRecoveryObservationSameKeyRetry(intent, () => writeBlocked.current);
        try { await postRecoveryObservation(intent); } finally { endRecoveryComposedObservationAttempt(); }
        original = await getRecoveryObservation(intent.expected_run_id, intent);
      }
      await acceptRecoveryObservationRead(intent, original); setValue(original);
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  if (!op.pending && !op.original_run_id && !op.storage_error && !value) return null;
  return <section className="card" aria-label="原组合观察恢复"><h3>原组合观察恢复</h3>
    {op.pending && <><p>保留原运行 {op.pending.expected_run_id}、原body/hash/key；GET可独立核对。未找到不允许换键。</p><button type="button" disabled={busy || op.busy} onClick={() => void read()}>核对原观察</button><button type="button" disabled={busy || op.busy || mutationBlocked || !!op.storage_error} onClick={() => void read(true)}>先核对，再同键重试原观察</button></>}
    {!op.pending && op.original_run_id && <button type="button" disabled={busy || op.busy} onClick={() => void read()}>重新读取历史原观察</button>}
    {(error || op.storage_error) && <p role="alert">{error || op.storage_error}</p>}{value && <OriginalView value={value} />}
  </section>;
}
export default function RecoveryComposedObservationPanel({ mutationBlocked = false, showOriginalRecovery = true }: { mutationBlocked?: boolean; showOriginalRecovery?: boolean }) {
  const op = useRecoveryComposedObservationOperation(); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [value, setValue] = useState<RecoveryObservation | null>(null);
  const writeBlocked = useRef(mutationBlocked); writeBlocked.current = mutationBlocked;
  const [comparePrevious, setComparePrevious] = useState(true);
  useEffect(() => { void recoverRecoveryComposedObservationOperation(); }, []);
  async function record() {
    if (busy || mutationBlocked || op.pending || op.busy || op.recovering || op.storage_error) return; setBusy(true); setError(''); setValue(null);
    try {
      // Current owner/epoch always comes from a verified actual GET, never an input box.
      const current = await getRecoveryComposedActionSet();
      const previous = comparePrevious && op.original_run_id ? await getRecoveryObservation(op.original_run_id) : null;
      const intent = await createRecoveryObservationIntent(current, previous);
      await beginRecoveryComposedObservation(intent, () => writeBlocked.current);
      try { await postRecoveryObservation(intent); } finally { endRecoveryComposedObservationAttempt(); }
      const original = await getRecoveryObservation(intent.expected_run_id, intent);
      await acceptRecoveryObservationRead(intent, original); setValue(original);
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <>{showOriginalRecovery && <RecoveryComposedObservationOriginalRecoveryPanel mutationBlocked={mutationBlocked} />}<section className="card" aria-label="记录组合动作观察"><h3>记录组合动作观察</h3><p>服务器重新核对当前周期划款、整仓恢复和全部登记动作。明确点击只持久记录一条观察；没有金融执行或通知。</p>
    {op.original_run_id && <label><input type="checkbox" checked={comparePrevious} disabled={busy || op.busy || !!op.pending} onChange={event => setComparePrevious(event.target.checked)} />与上一条原观察比较；取消选择明确建立新观察基点</label>}
    <button type="button" disabled={busy || mutationBlocked || op.busy || op.recovering || !!op.pending || !!op.storage_error} onClick={() => void record()}>记录并核对当前组合观察</button>
    {error && <p role="alert">{error}；原请求保留，未使用先前完整结论。</p>}{!error && value && <OriginalView value={value} />}
  </section></>;
}
