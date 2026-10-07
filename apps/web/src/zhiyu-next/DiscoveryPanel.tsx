import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { LocalActorSession } from '../api/local-actor';
import type { NextState } from './api';
import { object } from '../features/policy-form';
import { businessText, money, userError } from './display';
import { canConfirmDiscovery, getDiscovery, type DiscoveryUpdate, type DiscoveredProposal } from './discovery';
import type { PaymentPerform } from './PaymentPanel';

export default function DiscoveryPanel({ environment, session, blocked, update, perform, retryOriginal }: { environment: NextState; session: LocalActorSession | null; blocked: boolean; update: DiscoveryUpdate | null; perform: PaymentPerform; retryOriginal?: () => Promise<void> }) {
  const [open, setOpen] = useState(false); useEffect(() => { if (update) setOpen(true); }, [update]);
  const current = useQuery({ queryKey: ['zhiyu-next-policy-discovery', environment.environment_id, environment.epoch_id], queryFn: () => getDiscovery(environment), enabled: open && !blocked, retry: false, staleTime: Infinity });
  const read = current.data ?? (update?.kind === 'DISCOVER' ? update.read : null);
  const signed = !!session && session.principal.user_id === environment.dashboard.user_id && session.principal.role === 'USER' && Date.parse(session.principal.expires_at) > Date.now();
  function confirm(p: DiscoveredProposal) { if (blocked || !signed || !canConfirmDiscovery(p)) return; void perform('/zhiyu-next/policy-discovery/confirm', { proposal_id: p.proposal_id, reviewed_hash: p.configuration_hash, accepted: true, expected_epoch_id: environment.epoch_id }); }
  return <section className="zy-card" aria-label="核实历史候选"><div className="zy-section-heading"><h2>从真实历史核实候选</h2><button className="zy-link" disabled={blocked} onClick={() => setOpen((v) => !v)}>{open ? '收起历史候选' : '查看历史候选'}</button></div><p>相邻账期的历史事实可帮助识别房租和还款规律。历史规律不保证未来义务，候选须由你明确审核；自动付款默认关闭。</p>
    {open && <><button className="zy-secondary" disabled={blocked} onClick={() => void perform('/zhiyu-next/policy-discovery', { expected_epoch_id: environment.epoch_id })}>核实历史并生成候选</button><p className="zy-muted">服务器只读取本轮可信历史。此步骤保存观察与候选，不确认规则或授予银行权限。</p>
      {current.isFetching && <p role="status">正在读取历史来源与候选原件…</p>}{current.isError && <p role="alert">{userError(current.error)}</p>}
      {read?.statistics && <p>本次新增 {read.statistics.created_proposal_ids.length} 项，复用 {read.statistics.reused_proposal_ids.length} 项；另有 {read.statistics.skipped.length} 项未形成候选。</p>}
      {read && !read.proposals.length && <p>本轮没有已核实的历史候选。没有可信规律时，系统不会补造规则或义务。</p>}
      {read?.proposals.map((p) => {
        const observation = p.observations[0]; const amount = p.configuration.amount_rule; const sources = observation ? [...new Set(observation.sources.map((s) => environment.dashboard.account_facts.facts.accounts.find((a) => a.id === s.account_id)?.name ?? (observation.rule === 'credit_card_cycle' ? '历史信用卡账户' : '原历史账户')))] : [];
        return <article key={p.proposal_id} className="zyn-discovered"><h3>{businessText(typeof p.configuration.name === 'string' ? p.configuration.name : observation?.rule === 'credit_card_cycle' ? '信用卡预留候选' : '周期预留候选')}</h3><p>{p.status === 'CONFIRMED' ? '原规则确认已保存，当前授权仍须读取现行规则。' : p.status === 'EXPIRED' ? '此候选已到期，不能确认。' : p.source_status === 'UNKNOWN' ? '历史来源尚未完整核实，不能确认。' : '历史观察已核实，等待你审核范围。'}</p>
          <dl className="zy-facts"><div><dt>来源</dt><dd>{sources.map(businessText).join('、') || '尚未完整核实'}</dd></div><div><dt>每期金额</dt><dd>{object(amount) && amount.kind === 'range' ? `${money(Number(amount.min_cents))} 至 ${money(Number(amount.max_cents))}` : '按本期实际未还账单核实，历史金额不当作未来应付'}</dd></div><div><dt>应付日</dt><dd>每月 {Number(p.configuration.due_day)} 日</dd></div><div><dt>自动付款</dt><dd>关闭；实际执行另核实范围与必要同意</dd></div></dl>
          {observation && <><p>{businessText(observation.matched_rule)}；使用 {observation.sources.length} 条历史原事实，观察窗口 {observation.observation_window.start_date} 至 {observation.observation_window.end_date}。</p><p className="zy-muted">{businessText(observation.limitation)}</p></>}
          {canConfirmDiscovery(p) && <button className="zy-primary" disabled={blocked || !signed} onClick={() => confirm(p)}>确认这个预留规则</button>}
        </article>;
      })}
      {update?.kind === 'CONFIRM' && <p className="zy-result">原规则确认已独立核实。此回执只证明规则结果，未证明付款或授予银行权限。</p>}
      {!signed && <p>确认候选前，请在上方建立本轮本地用户身份。</p>}
      {retryOriginal && signed && <button className="zy-secondary" onClick={() => void retryOriginal()}>按原请求恢复会话后的续接</button>}
    </>}
  </section>;
}
