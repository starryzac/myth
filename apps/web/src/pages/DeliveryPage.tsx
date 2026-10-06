import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getDeliveries, getDelivery, getOriginalDeliveryResponse } from '../api/delivery';
import type { Deliveries, Delivery, DeliveryAttempt, DeliveryIdentity } from '../api/delivery';
import { errorMessage } from '../api/http';

const inboxLabels: Record<string, string> = { RECEIVED: '已收件，尚未完成处理', PROCESSING: '原消息正在处理', WAITING_CONFIRMATION: '等待原动作确认', UNRESOLVED: '结果尚未解决', SERVICE_RECEIPT_VERIFIED: '收件记录标记服务回执已核验', STOPPED: '处理已停止', FAILED: '尝试发生错误' };
const outboxLabels: Record<string, string> = { PENDING: '原消息待处理或待核对', DELIVERED: '原消息已确认投递', STOPPED: '原消息投递已停止' };
const status = (value: string | null, names: Record<string, string>) => value === null ? '未提供 / 尚未记录' : names[value] ? `${names[value]} · ${value}` : value;
function OriginalReads({ data, name }: { data: Delivery | Deliveries | undefined; name: string }) {
  const seen = useRef(new WeakSet<object>()); const [reads, setReads] = useState<{ identity: string; text: string }[]>([]);
  useEffect(() => {
    if (data === undefined || seen.current.has(data)) return;
    const original = getOriginalDeliveryResponse(data); if (original === null) return;
    seen.current.add(data); setReads((previous) => [...previous, { identity: 'outbox_id' in data ? data.outbox_id : '最近原消息列表', text: original }]);
  }, [data]);
  return <details className="delivery-originals"><summary>本页捕获的{name}原 JSON 响应 · {reads.length} 次</summary>
    <p className="caption">保留本页读取前后的响应文本，不是传输层字节或资金核验；关闭页面后不保留。历史原文本不能代替当前读取状态。</p>
    {reads.map((read, index) => <details key={index}><summary>读取 {index + 1} · {read.identity}</summary><pre className="readonly-raw">{read.text}</pre></details>)}</details>;
}
function Attempt({ attempt }: { attempt: DeliveryAttempt }) {
  const result = attempt.result;
  return <li className="delivery-attempt"><h4>尝试 {attempt.attempt_number} · {status(attempt.state, inboxLabels)}</h4><dl className="delivery-fields">
    <div><dt>原尝试编号</dt><dd><code>{attempt.attempt_id}</code></dd></div><div><dt>开始时间</dt><dd>{attempt.started_at}</dd></div><div><dt>结束时间</dt><dd>{attempt.finished_at ?? '尚未记录，不能视为已完成'}</dd></div>
    <div><dt>该次动作状态</dt><dd>{attempt.source_action_status ?? '尚未记录'}</dd></div><div><dt>该次银行状态</dt><dd>{typeof result?.bank_status === 'string' ? result.bank_status : '尚未记录'}</dd></div>
    <div><dt>该次服务回执核验</dt><dd>{result === null ? '尚未记录' : result.service_receipt_verified === true ? '该次原服务报告已核验' : '该次原服务未报告核验通过'}</dd></div>
    <div><dt>原推迟原因</dt><dd>{typeof result?.deferred_reason === 'string' ? result.deferred_reason : '未记录推迟原因'}</dd></div><div><dt>原错误</dt><dd>{attempt.error ?? '未记录错误'}</dd></div>
  </dl><p className="caption">这是原尝试的历史结果，不替代下方当前只读核对，也不表示独立资金验真。</p></li>;
}
function DeliveryDetails({ data }: { data: Delivery }) {
  const unresolved = ['UNKNOWN', 'SUBMITTED'].includes(data.source_action_status) || data.inbox_state === 'UNRESOLVED';
  return <><h3>原消息当前核对</h3><dl className="delivery-fields">
    <div><dt>消息原编号（Outbox）</dt><dd><code>{data.outbox_id}</code></dd></div><div><dt>原 root</dt><dd><code>{data.root_id}</code></dd></div><div><dt>原 action</dt><dd><code>{data.action_id}</code></dd></div><div><dt>原审计周期</dt><dd><code>{data.epoch_id}</code></dd></div>
    <div><dt>消息 payload 哈希</dt><dd><code>{data.payload_hash}</code></dd></div><div><dt>原请求哈希</dt><dd>当前读取合同未提供，未推断</dd></div><div><dt>原经济后果 / effect 哈希</dt><dd>当前读取合同未提供，未推断</dd></div><div><dt>原银行幂等键</dt><dd>当前读取合同未提供，未生成新键</dd></div>
    <div><dt>Outbox 状态</dt><dd>{status(data.outbox_state, outboxLabels)}</dd></div><div><dt>Inbox 状态</dt><dd>{status(data.inbox_state, inboxLabels)}</dd></div><div><dt>原动作当前状态</dt><dd>{data.source_action_status}</dd></div><div><dt>银行当前状态</dt><dd>{data.bank_status ?? '服务未提供银行状态，不推断未受理'}</dd></div>
    <div><dt>原动作当前可用</dt><dd>{data.current_action_available ? '可读取当前原动作' : '当前周期无可用原动作，历史消息仍保留'}</dd></div><div><dt>原阻止原因</dt><dd>{data.blocking_reason ?? '服务未列出阻止原因，不等于已授权执行'}</dd></div><div><dt>投递服务是否繁忙</dt><dd>{data.busy ? '原投递互斥锁繁忙，请稍后只读核对' : '此读取未报告繁忙'}</dd></div><div><dt>最近原错误</dt><dd>{data.last_error ?? '未记录错误'}</dd></div>
  </dl>
  {unresolved && <p className="notice delivery-unresolved">UNKNOWN / 未解决需要核对同一原动作与银行操作。银行可能已受理或已结算，不能据此断言现金未变；超时不能生成新消息、行动或银行键。</p>}
  {!data.current_action_available && <p className="notice">原动作已不在当前可用范围。仅保留原消息和尝试历史，本页不重建动作或恢复旧周期。</p>}
  {data.inbox_state === 'WAITING_CONFIRMATION' && <p className="notice">收件不提供新权限。需要原动作明确确认，投递不能代替用户确认。</p>}
  {data.inbox_state === 'SERVICE_RECEIPT_VERIFIED' && !data.service_receipt_verified && <p role="alert">收件历史标记与当前服务回执核对不一致；当前未报告核验通过。</p>}
  <section className="delivery-verification" aria-label="服务回执与资金验真"><h4>服务回执与资金验真</h4>
    <p>{data.service_receipt_verified ? '原服务本次读取报告：SERVICE_RECEIPT_VERIFIED。只表示原服务回执核验。' : '原服务本次读取未报告回执核验通过。'}</p>
    <p>独立资金验真：未验证 · economic_verified=false。服务回执、Outbox 状态或消息哈希均不能替代外部资金核验。</p></section>
  <section aria-label="原投递尝试历史"><h4>原投递尝试历史 · {data.attempts.length} 次</h4><p className="caption">按服务返回的完整连续编号显示，没有裁剪或重新编号。</p>
    {data.attempts.length === 0 ? <p>服务尚未记录收件尝试，不能推定消息已完成。</p> : <ol className="delivery-attempts" aria-label="投递尝试列表">{data.attempts.map((attempt) => <Attempt key={attempt.attempt_id} attempt={attempt} />)}</ol>}</section>
  </>;
}
export default function DeliveryPage() {
  const [selected, setSelected] = useState<DeliveryIdentity | null>(null); const focus = useRef<HTMLElement>(null);
  const list = useQuery({ queryKey: ['delivery-list', 50], queryFn: () => getDeliveries(50), retry: false, structuralSharing: false });
  const detail = useQuery({ queryKey: ['delivery-detail', selected?.outbox_id, selected?.root_id, selected?.action_id, selected?.epoch_id, selected?.payload_hash], queryFn: () => getDelivery(selected!.outbox_id, selected!), enabled: selected !== null, retry: false, structuralSharing: false });
  function select(data: Delivery) {
    setSelected({ outbox_id: data.outbox_id, root_id: data.root_id, action_id: data.action_id, epoch_id: data.epoch_id, payload_hash: data.payload_hash }); focus.current?.focus();
  }
  return <div className="delivery-page"><section className="page-intro"><div><p className="eyebrow">核对同一原消息与原行动</p><h2>命令投递</h2></div><button type="button" disabled={list.isFetching} onClick={() => void list.refetch()}>刷新原消息列表</button></section>
    <p className="simulation-note">持久模拟命令只读视图。收件和再次投递均不授予资金权限；本页未启用写入或自动重试。</p>
    <section className="card readonly-section" aria-label="持久原消息列表"><h3>持久原消息列表</h3><p className="caption">只读取最近最多50条原消息；接口未提供总量或分页，不能称完整历史总量。列表与详情是分别进行的只读查询。</p>
      {list.isPending && <p role="status">正在读取原消息…</p>}{list.isError && <p role="alert">{errorMessage(list.error)}；未将旧列表当本次读取成功。</p>}
      {!list.isError && list.data && <>{list.data.items.length === 0 ? <p>当前读取范围没有原消息，不表示全部历史为空。</p> : <ul className="delivery-messages" aria-label="实际持久消息">{list.data.items.map((item) => <li key={item.outbox_id}><button type="button" aria-pressed={selected?.outbox_id === item.outbox_id} onClick={() => select(item)}>
        <span>查看原消息 <code>{item.outbox_id}</code></span><span>{status(item.inbox_state, inboxLabels)}</span><span>原动作 {item.source_action_status} · 银行 {item.bank_status ?? '尚未提供'}</span><span>尝试 {item.attempts.length} 次 · {status(item.outbox_state, outboxLabels)}</span></button></li>)}</ul>}
        </>}
      <OriginalReads data={list.data} name="列表" />
    </section><section ref={focus} tabIndex={-1} className="card readonly-section delivery-detail" aria-label="原消息详情">
      {!selected && <p>请选择原消息后读取详情。</p>}{selected && <><p>选定原消息 <code>{selected.outbox_id}</code></p><button type="button" disabled={detail.isFetching} onClick={() => void detail.refetch()}>只读核对同一原消息</button>
        {detail.isPending && <p role="status">正在核对同一原消息…</p>}{detail.isError && <p role="alert">{errorMessage(detail.error)}；保留选定消息编号，只读恢复核对，不发起新投递。</p>}
        {detail.isFetching && detail.data && <p role="status">正在重新读取；下方仍为上次详情，尚未完成本次核对。</p>}
        {!detail.isError && detail.data && <DeliveryDetails data={detail.data} />}</>}
      <OriginalReads data={detail.data} name="详情" />
    </section><p className="notice">手动再次投递尚未启用：需要先接入跨页面共享的待核对写入门，读取原消息确认后再由用户明确投递。本页不创建新 enqueue、行动或幂等键。</p></div>;
}
