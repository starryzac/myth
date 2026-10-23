import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Client } from './api';
import { timestamp, userError } from './display';
import { NativeActivityClient, nativeActivityKindText } from './native-activity-api';
import NativeActivityDetails from './NativeActivityDetails';

export default function ActivityHistory({ client, scope, onRead }: { client: Client; scope: string; onRead: (id: string) => void }) {
  const [cursors, setCursors] = useState(['']);
  const [batch, setBatch] = useState(0);
  const cursor = cursors[cursors.length - 1];
  const page = useQuery({ queryKey: ['activities', scope, batch, cursor], queryFn: ({ signal }) => new NativeActivityClient(client).page(cursor, signal), staleTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const restart = () => { setCursors(['']); setBatch((value) => value + 1); };
  return <section className="zy-card" aria-label="资金活动分页">
    <div className="zy-section-heading"><h2>资金活动</h2><button className="zy-link" onClick={restart}>重新开始读取活动</button></div>
    {page.isPending && <p role="status">正在读取这一页活动…</p>}
    {page.isError && <p className="zy-message zy-error" role="alert">{userError(page.error)} 请重新开始读取活动。</p>}
    {page.data && <>
      <p className="zy-muted">本批读取于 {timestamp(page.data.as_of)}，共 {page.data.total} 条；第 {cursors.length} 页显示 {page.data.returned} 条。</p>
      {!page.data.items.length && <p>当前还没有资金活动。</p>}
      {page.data.items.map((item) => <article key={item.id} className="zy-event">
        <h3>{item.operation_kind ? `${nativeActivityKindText[item.operation_kind]}${item.kind === 'NATIVE_OPERATION_SETTLED' ? '已结清' : '活动已记录'}` : item.kind === 'OPERATION_SETTLED' ? '安排已结清' : item.kind === 'INCOME_IMPORTED' ? '可信到账已记录' : '活动已记录'}</h3>
        <p>{timestamp(item.created_at)}</p>
        {item.kind === 'OPERATION_SETTLED' && <button className="zy-link" onClick={() => onRead(item.aggregate_id)}>读取关联的原操作</button>}
        {item.operation_family === 'NATIVE' && item.receipt_id && <NativeActivityDetails key={`${scope}:${item.id}`} client={client} event={item} scope={scope} />}
      </article>)}
      <div className="zy-actions"><button className="zy-secondary" disabled={cursors.length === 1 || page.isFetching} onClick={() => setCursors((value) => value.slice(0, -1))}>上一页活动</button>
        <button className="zy-secondary" disabled={!page.data.has_more || page.isFetching} onClick={() => { const next = page.data?.next_cursor; if (next) setCursors((value) => [...value, next]); }}>下一页活动</button></div>
    </>}
  </section>;
}
