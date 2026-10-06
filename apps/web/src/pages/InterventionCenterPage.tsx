import { useEffect, useState } from 'react';
import { errorMessage } from '../api/http';
import { getIntervention, getOriginalInterventionResponse, interventionCanonicalJson, listInterventions, lookupIntervention, postIntervention, readQuestionObservation } from '../api/interventions';
import type { Intervention, InterventionIntent, InterventionList } from '../api/interventions';
import { beginInterventionOperation, clearInterventionAfterRead, endInterventionAttempt, prepareInterventionIntent, recoverInterventionOperation, useInterventionOperation } from '../features/intervention-operation';
import { recoverOneQuestionOperation } from '../features/one-question-operation';

function Raw({ value, title }: { value: object; title: string }) { return <details><summary>{title}</summary><pre className="readonly-raw">{getOriginalInterventionResponse(value) ?? JSON.stringify(value, null, 2)}</pre></details>; }
const stateNames: Record<string, string> = { PENDING: '待收阅', ACKNOWLEDGED: '已收阅', INVALIDATED: '已失效', RECORDED_ONLY: '仅记录', DEFERRED: '延后', UNKNOWN: '来源未知', ARCHIVED: '已归档' };
const bindingNames: Record<Intervention['current_source_binding'], string> = { ORIGINAL_MESSAGE: '原消息来源仍为当前', CURRENT_OBSERVATION: '新原观察绑定当前问题', LEGACY_TERMINAL_SOURCE: '历史终态不可复活', UNVERIFIED: '当前来源未证明' };

export default function InterventionCenterPage({ mutationBlocked = false }: { mutationBlocked?: boolean }) {
  const operation = useInterventionOperation();
  const [inventory, setInventory] = useState<InterventionList | null>(null);
  const [selected, setSelected] = useState<Intervention | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [sessionId, setSessionId] = useState(() => recoverOneQuestionOperation().session?.session_id ?? '');
  const [busy, setBusy] = useState(false); const [reading, setReading] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const blocked = mutationBlocked || operation.busy || !!operation.pending || !!operation.storage_error || busy || reading;

  useEffect(() => {
    recoverInterventionOperation(); let active = true;
    setReading(true); setInventory(null);
    void listInterventions().then((value) => { if (active) setInventory(value); }).catch((cause: unknown) => { if (active) setError(errorMessage(cause)); }).finally(() => { if (active) setReading(false); });
    return () => { active = false; };
  }, []);

  async function readInventory() {
    if (busy || reading) return; setReading(true); setError(''); setInventory(null); setSelected(null); setAccepted(false);
    try { setInventory(await listInterventions()); } catch (cause) { setError(errorMessage(cause)); } finally { setReading(false); }
  }
  async function readMessage(id: string) {
    if (busy || reading) return; setReading(true); setError(''); setSelected(null); setAccepted(false);
    try { setSelected(await getIntervention(id)); } catch (cause) { setError(errorMessage(cause)); } finally { setReading(false); }
  }
  async function send(intent: InterventionIntent) {
    await beginInterventionOperation(intent, mutationBlocked);
    try { await postIntervention(intent); setNotice('请求已返回；仍保留完整原命令，需单独只读核对。'); }
    finally { endInterventionAttempt(); }
  }
  async function deliver(value: Intervention) {
    if (blocked || !value.pending || value.previously_claimed) return; setBusy(true); setError(''); setNotice(''); setAccepted(false);
    try {
      const current = await getIntervention(value.original_message.message_id);
      if (!current.pending || current.previously_claimed || current.payload_hash !== value.payload_hash || current.original_message.epoch_id !== value.original_message.epoch_id) throw new Error('通知或来源已变化，请重读当前原件。');
      const intent = await prepareInterventionIntent({ kind: 'DELIVER', user_id: current.original_message.user_id, message_id: current.original_message.message_id, body: { expected_epoch_id: current.original_message.epoch_id, reviewed_payload_hash: current.payload_hash } });
      await send(intent);
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function acknowledge() {
    if (!selected || !accepted || blocked || !selected.pending || !selected.previously_claimed) return; setBusy(true); setError(''); setNotice('');
    try {
      const current = await getIntervention(selected.original_message.message_id);
      if (!current.pending || !current.previously_claimed || current.payload_hash !== selected.payload_hash || current.original_message.epoch_id !== selected.original_message.epoch_id || interventionCanonicalJson(current.original_inbox_claim) !== interventionCanonicalJson(selected.original_inbox_claim)) throw new Error('通知、收件原件或来源已变化，请重读并明确收阅。');
      const intent = await prepareInterventionIntent({ kind: 'ACKNOWLEDGE', user_id: current.original_message.user_id, message_id: current.original_message.message_id, body: { expected_epoch_id: current.original_message.epoch_id, reviewed_payload_hash: current.payload_hash, idempotency_key: `intervention-ack-${crypto.randomUUID()}`, acknowledged: true } });
      await send(intent); setAccepted(false);
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function observe() {
    if (blocked || !sessionId) return; setBusy(true); setError(''); setNotice('');
    try {
      const source = await readQuestionObservation(sessionId, `intervention-observe-${crypto.randomUUID()}`);
      await send(await prepareInterventionIntent({ kind: 'OBSERVE', user_id: source.user_id, message_id: null, body: source.body }));
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function recover() {
    const original = operation.pending; if (!original || busy || reading || operation.busy) return; setBusy(true); setError(''); setNotice('');
    try {
      const value = original.kind === 'DELIVER' ? await getIntervention(original.message_id!) : await lookupIntervention(original);
      await clearInterventionAfterRead(original, value);
      const message = original.kind === 'DELIVER' ? value as Intervention : (value as Awaited<ReturnType<typeof lookupIntervention>>).message;
      setSelected(message); setAccepted(false);
      setNotice(original.kind === 'DELIVER' ? '原固定收件身份已核对；是否曾呈现或被人看到仍未知，不重新投递。' : '原键、完整命令和原回执已核对。收阅与通知记录均不回答或确认金融动作。');
      setInventory(null); setInventory(await listInterventions());
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <div className="intervention-center-page"><header><h1>介入中心</h1><p>读取实际持久通知。收阅只记录通知回执，问题回答与金融确认仍需到各自页面明确完成。</p></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="待核对通知原请求"><h2>待核对通知原请求</h2><p>原操作 {operation.pending.kind} · {'idempotency_key' in operation.pending.body ? `原键 ${operation.pending.body.idempotency_key}` : `原消息 ${operation.pending.message_id}`}</p><Raw value={operation.pending} title="发送前保存的完整原通知命令" /><button type="button" disabled={busy || reading || operation.busy} onClick={() => void recover()}>只读核对原通知请求</button><p>未找到或原件不匹配时继续保留；任何 HTTP 结果不会直接解除原请求。</p></section>}
    <section aria-label="持久通知列表"><h2>持久通知</h2><button type="button" disabled={busy || reading} onClick={() => void readInventory()}>重读通知列表</button>{reading && <p role="status">正在只读核对通知原件…</p>}
      {inventory && <><p>实际消息总数 {inventory.actual_message_count} · 本页 {inventory.items.length} · {inventory.presentation_truncated ? '展示有截断，保留实际总数' : '完整库存已读取'}</p>{inventory.items.length === 0 && <p>服务未返回通知；这不表示所有边界变化已经覆盖。</p>}<ul>{inventory.items.map((value) => <li key={value.original_message.message_id}><strong>{value.original_message.source_kind === 'QUESTION' ? '一次一问' : '单动作边界观察'}</strong> · {stateNames[value.effective_state]} · 当前来源 {value.source_status} / {bindingNames[value.current_source_binding]} · {value.previously_claimed ? '已有固定收件记录' : '尚无收件记录'} <button type="button" disabled={busy || reading} onClick={() => void readMessage(value.original_message.message_id)}>读取通知 {value.original_message.message_id}</button><button type="button" disabled={blocked || !value.pending || value.previously_claimed} onClick={() => void deliver(value)}>领取通知 {value.original_message.message_id}</button></li>)}</ul><Raw value={inventory} title="完整通知列表原响应" /></>}
    </section>
    {selected && <section aria-label="通知原件"><h2>通知原件 · {stateNames[selected.effective_state]}</h2><p>当前来源状态 {selected.source_status} · {bindingNames[selected.current_source_binding]} · 创建 {selected.original_message.created_at} · 可用时点 {selected.available_at}</p>
      <section aria-label="不可变消息来源"><h3>不可变消息的原来源</h3><dl><dt>原周期</dt><dd>{selected.original_message.epoch_id}</dd><dt>原会话</dt><dd>{selected.original_message.session_id ?? '非问答来源'}</dd><dt>原问答版本</dt><dd>{selected.original_message.question_revision ?? '不适用'}</dd><dt>原来源 run</dt><dd>{selected.original_message.source_run_id}</dd><dt>原来源轨迹 hash</dt><dd>{selected.original_message.source_trace_hash}</dd><dt>原消息 payload hash</dt><dd>{selected.payload_hash}</dd></dl><p>原 payload 保留历史身份；新观察不会改写原题目、来源或哈希。</p></section>
      {selected.current_question_observation && <section aria-label={selected.current_source_binding === 'CURRENT_OBSERVATION' ? '服务端当前问题观察' : '历史终态观察原件'}><h3>{selected.current_source_binding === 'CURRENT_OBSERVATION' ? '当前问题的新原观察' : '历史终态的观察原件'}</h3><dl><dt>观察 run</dt><dd>{selected.current_question_observation.observation_run_id}</dd><dt>观察轨迹 hash</dt><dd>{selected.current_question_observation.observation_trace_hash}</dd><dt>观察会话</dt><dd>{selected.current_question_observation.session_id}</dd><dt>观察问答版本</dt><dd>{selected.current_question_observation.revision}</dd><dt>观察来源 run</dt><dd>{selected.current_question_observation.source_run_id}</dd><dt>观察来源轨迹 hash</dt><dd>{selected.current_question_observation.source_trace_hash}</dd><dt>原观察键</dt><dd>{selected.current_question_observation.original_receipt.idempotency_key}</dd><dt>原观察请求 hash</dt><dd>{selected.current_question_observation.original_receipt.request_hash}</dd><dt>服务端完整语义 key</dt><dd>{selected.current_question_observation.semantic_key}</dd></dl><p>浏览器核对字段绑定与原命令 SHA；完整世界语义、原轨迹和审计由服务器核验。本观察不回答问题、不确认金融动作、不证明真人已看到。</p><Raw value={selected.current_question_observation} title="新原观察回执与当前问题完整字段" /></section>}
      {selected.source_status === 'CURRENT' && selected.pending && ['ORIGINAL_MESSAGE', 'CURRENT_OBSERVATION'].includes(selected.current_source_binding) && selected.original_message.source_kind === 'QUESTION' && selected.current_question && <><p>当前待澄清：{selected.current_question.variable_id} · 选项 {selected.current_question.choices.map((choice) => choice.key).join('、')}</p><p>当前问题 ID {selected.current_question.question_id}；到原问答页面核对会话与版本后再明确回答。</p><a href="#questions">到一次一问页面读取当前问题并回答</a></>}
      {selected.current_source_binding === 'LEGACY_TERMINAL_SOURCE' && <p>原 INVALIDATED 终态和收件状态保留；即使服务核到新观察，也不会复活此消息、领取或收阅。</p>}
      {selected.source_status !== 'CURRENT' && <p>原来源 {selected.source_status}，此通知不再提供可提交的问题或确认；请在原业务页面重新读取。</p>}
      {selected.original_message.source_kind === 'SINGLE_ACTION_BOUNDARY' && <p>仅为登记的单动作 before/after 观察，不代表全局边界订阅或全部动作集合。</p>}
      <p>{selected.original_inbox_claim ? `固定收件原件 ${selected.original_inbox_claim.inbox_id} · ${selected.original_inbox_claim.received_at} · ${selected.original_inbox_claim.state}` : '尚无固定收件原件'}。收件记录不证明页面曾呈现或用户已看到。</p>
      {selected.original_acknowledgment && <p>原收阅键 {selected.original_acknowledgment.idempotency_key} · 记录 {selected.original_acknowledgment.recorded_at}；不会回答问题或授予银行权限。</p>}
      {selected.pending && selected.previously_claimed && <><label><input type="checkbox" checked={accepted} disabled={blocked} onChange={(event) => setAccepted(event.target.checked)} />我确认已收阅此通知</label><button type="button" disabled={blocked || !accepted} onClick={() => void acknowledge()}>提交明确收阅</button></>}
      <Raw value={selected} title="原消息、固定收件身份与原收阅回执" />
    </section>}
    <section aria-label="同步当前问题"><h2>登记当前问题通知</h2><label>当前问答会话 ID<input value={sessionId} maxLength={36} disabled={blocked} onChange={(event) => setSessionId(event.target.value)} /></label><button type="button" disabled={blocked || !sessionId} onClick={() => void observe()}>读取当前问题并登记通知</button><p>先读取当前 revision 和完整原轨迹，再提交其原身份；本页不回答、不继承旧候选确认。</p></section>
    <p>一次一问提交后的通知登记由服务器处理，是否有消息以实际原件为准。全局边界订阅仍未实现；未知、归档和不足保持原状态。本页所有资金动作均未执行。</p>
  </div>;
}
