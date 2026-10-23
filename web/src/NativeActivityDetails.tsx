import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { Client } from './api';
import { timestamp, userError } from './display';
import { money } from './money';
import { NativeActivityClient, nativeActivityKindText } from './native-activity-api';
import type { NativeActivityItem, NativeActivityPosting } from './native-activity-api';

const dimensions: Record<NativeActivityPosting['dimension'], string> = { ECONOMIC: '现金与本金', GOAL_OWNERSHIP: '目标归属', INCOME_LOCATION: '原收入位置', LIABILITY: '负债余额' };
export default function NativeActivityDetails({ client, event, scope }: { client: Client; event: NativeActivityItem; scope: string }) {
  const [opened, setOpened] = useState(false);
  const original = useQuery({ queryKey: ['native-activity-original', scope, event.id], queryFn: ({ signal }) => new NativeActivityClient(client).original(event, signal), enabled: opened, retry: false, staleTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false });
  return <div>
    <button className="zy-link" aria-expanded={opened} onClick={() => setOpened((value) => !value)}>{opened ? '收起原银行回执核对' : '独立核对原银行回执'}</button>
    {opened && <section aria-label="保留原件独立核对">
      <p className="zy-muted">此处读取保留的原银行结果，核对原权限证据及本地记账分录；本次没有重新查询银行，也不会发起资金操作。</p>
      {original.isPending && <p role="status">正在读取并核对保留原件…</p>}
      {original.isError && <><p role="alert" className="zy-message zy-error">{userError(original.error)} 原件核对尚未完成，不能据此判断原效果不存在。</p><button className="zy-secondary" onClick={() => { void original.refetch(); }} disabled={original.isFetching}>重新读取保留原件</button></>}
      {original.data && <>
        <h4>{nativeActivityKindText[original.data.kind]} · 原回执已结清</h4>
        <p>原经济金额 {money(original.data.amount_cents)}；原银行结清于 {timestamp(original.data.settled_at)}。</p>
        <p>本地记账于 {timestamp(original.data.recorded_at)}；本次核对于 {timestamp(original.data.verified_at)}。</p>
        <p>原银行受理于 {timestamp(original.data.accepted_at)}，原约定可用时点 {timestamp(original.data.available_at)}。</p>
        {['REDEEM_ASSET', 'MATURE_ASSET'].includes(original.data.kind) && <p>本金 {money(original.data.amount_cents)}，费用 {money(original.data.fee_cents)}，损失 {money(original.data.loss_cents)}，实际返本 {money(original.data.net_cents)}。返本不构成新收入。</p>}
        {original.data.historical_cost_consent && <p>原件包含当时本人对该笔费用或损失的独立确认。</p>}
        {original.data.funding.income_uses.length > 0 && <p>原收入资格用途 {money(original.data.funding.income_uses.reduce((sum, use) => sum + BigInt(use.amount_cents), 0n).toString())}；仅保留既有来源身份。</p>}
        {original.data.funding.return_uses.length > 0 && <p>原返本用途 {money(original.data.funding.return_uses.reduce((sum, use) => sum + BigInt(use.amount_cents), 0n).toString())}；不新增收入来源。</p>}
        <table><caption>与原银行回执逐笔一致的本地分录</caption><thead><tr><th scope="col">分录</th><th scope="col">维度</th><th scope="col">方向</th><th scope="col">金额</th><th scope="col">发生时间</th></tr></thead><tbody>{original.data.postings.map((posting, index) => <tr key={posting.id}><td>{index + 1}</td><td>{dimensions[posting.dimension]}</td><td>{posting.direction === 'DEBIT' ? '借方' : '贷方'}</td><td>{money(posting.amount_cents)}</td><td>{timestamp(posting.occurred_at)}</td></tr>)}</tbody></table>
      </>}
    </section>}
  </div>;
}
