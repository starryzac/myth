import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { getDashboard, type Dashboard } from './api/dashboard';
import { calendarDate, label, snapshotTime } from './features/dashboard-labels';
import { formatMoneyCents } from './features/money';

const protectionReasons = [
  ['obligations', '账单与周期义务'], ['living', '生活预留'], ['emergency', '应急金'],
  ['goal_cash', '目标现金保护（含待归属）'], ['goal_minimum', '目标最低承诺'],
] as const;

function Money({ cents, large = false }: { cents: number | null; large?: boolean }) {
  const formatted = formatMoneyCents(cents);
  return <span className={`money ${large ? 'money-large' : ''} ${cents === null ? 'money-unknown' : ''} ${formatted.length > 18 ? 'money-long' : ''}`}>
    {cents === null ? '待核验' : <><span className="currency">¥</span>{formatted}</>}
  </span>;
}

function Badge({ state }: { state: string }) {
  const positive = ['PROVEN', 'READY', 'MATCHED', 'VALID'].includes(state);
  return <span className={`badge ${positive ? 'badge-positive' : 'badge-neutral'}`}>{label(state)}</span>;
}

function Metric({ name, cents, hint }: { name: string; cents: number | null; hint?: string }) {
  return <div className="metric"><dt>{name}</dt><dd><Money cents={cents} /></dd>
    {hint && <p className="caption">{hint}</p>}</div>;
}

function Card({ title, subtitle, state, children, className = '' }: {
  title: string; subtitle?: string; state?: string; children: ReactNode; className?: string;
}) {
  return <section className={`card ${className}`} aria-label={title}>
    <div className="card-heading"><h3>{title}</h3>{state && <Badge state={state} />}</div>
    {subtitle && <p className="card-subtitle">{subtitle}</p>}{children}
  </section>;
}

function Issues({ issues }: { issues: { code: string; message: string; source_ref: string }[] }) {
  if (!issues.length) return null;
  return <details className="details issues"><summary>查看 {issues.length} 条待核验来源</summary>
    <ul>{issues.map((issue, index) => <li key={`${issue.code}-${issue.source_ref}-${index}`}>
      <p>{issue.message}</p><code>{issue.code}</code>
    </li>)}</ul></details>;
}

function BoundaryOverview({ data }: { data: Dashboard }) {
  const { boundary } = data;
  const proven = boundary.state === 'PROVEN' && boundary.status !== 'INSUFFICIENT_EVIDENCE';
  const risk = boundary.status === 'LIQUIDITY_RISK';
  return <section className={`boundary-overview ${risk ? 'boundary-risk' : ''}`} aria-label="91日资金边界">
    <div className="boundary-main">
      <p className="eyebrow">91 日资金边界 <span>· 财务计算</span></p>
      <div className="boundary-title"><h3>安全闲置资金</h3><Badge state={boundary.status} /></div>
      <Money cents={proven ? boundary.safe_idle_cents : null} large />
      <p className="boundary-explanation">{risk ? '窗口内存在资金缺口，请先查看限制点与待处理事项。' :
        proven ? '已考虑窗口内的现金、义务、预留与目标承诺。未到账收入不计入。' :
          '可靠来源尚不足，当前不能给出安全闲置金额。请查看待核验来源。'}</p>
      <p className="caption">此金额只反映财务边界，执行仍需满足对应策略和确认权限。</p>
    </div>
    <div className="boundary-side">
      <p className="window-label">{calendarDate(boundary.window_start)} — {calendarDate(boundary.window_end)}</p>
      <dl className="boundary-metrics">
        <Metric name="窗口最低余量" cents={proven ? boundary.minimum_margin_cents : null}
          hint="保留正负号；负值表示保护资金不足" />
        <Metric name="窗口资金缺口" cents={proven ? boundary.deficit_cents : null} />
      </dl>
      <p className="caption">限制日期：{proven && boundary.constraining_date ? calendarDate(boundary.constraining_date) : '待核验'}</p>
    </div>
  </section>;
}

function FactsAndProtection({ data }: { data: Dashboard }) {
  const { account_facts: accounts, boundary, goal_ownership: goals, managed_assets: assets } = data;
  const boundaryProven = boundary.state === 'PROVEN' && boundary.status !== 'INSUFFICIENT_EVIDENCE';
  const goalProven = goals.state === 'PROVEN';
  const assetsProven = assets.state === 'PROVEN';
  return <div className="facts-grid">
    <Card title="账面现金" subtitle="原始账户事实 · 包含目标账户现金" state={accounts.state}>
      <Money cents={accounts.facts.cash_balance_cents} large />
      <p className="caption">{label(accounts.bank_projection_state)}。账面余额不等同于可自主使用额度。</p>
      <dl className="compact-metrics"><Metric name="账面在途与持有本金" cents={accounts.facts.position_principal_cents} />
        <Metric name="未还信用卡账单" cents={accounts.facts.credit_card_unpaid_cents} /></dl>
      <details className="details"><summary>查看 {accounts.facts.accounts.length} 个账户</summary>
        <ul className="amount-list">{accounts.facts.accounts.map((account) =>
          <li key={account.id}><span>{account.name}{account.account_type === 'GOAL' && <small>目标账户</small>}</span>
            <Money cents={account.balance_cents} /></li>)}</ul>
      </details><Issues issues={accounts.issues ?? []} />
    </Card>
    <Card title="当前保护资金" subtitle="今天付款前的保护分层" state={boundary.state}>
      <Money cents={boundaryProven ? boundary.current_protected_cents : null} large />
      <dl className="protection-list">{protectionReasons.map(([key, title]) =>
        <Metric key={key} name={title} cents={boundaryProven ? boundary.current_protected_cents_by_reason?.[key] ?? null : null} />)}</dl>
      <div className="card-divider"><span>当前余量</span><Money cents={boundaryProven ? boundary.current_margin_cents : null} /></div>
      <p className="caption">当前余量与窗口最低余量口径不同，安全闲置取整个窗口的限制。</p>
    </Card>
    <Card title="目标资金归属" subtitle="现金与本金共同构成已归属金额" state={goals.state}>
      <Money cents={goalProven ? goals.allocated_cents : null} large />
      <dl className="compact-metrics"><Metric name="目标现金" cents={goalProven ? goals.cash_owned_cents : null} />
        <Metric name="目标本金" cents={goalProven ? goals.principal_owned_cents : null} />
        <Metric name="待归属的目标账户现金" cents={goalProven ? goals.unassigned_goal_cash_cents : null} /></dl>
      <p className="caption">目标现金已包含在账面现金；目标本金可能也包含在已自主配置中，请勿重复相加。</p>
      {goalProven && goals.items.length > 0 && <details className="details"><summary>查看 {goals.items.length} 个目标归属</summary>
        <ul className="goal-list">{goals.items.map((goal) => <li key={goal.goal_id}><strong>{goal.name}</strong>
          <dl><Metric name="现金" cents={goal.cash_owned_cents} /><Metric name="本金" cents={goal.principal_owned_cents} />
            <Metric name="合计归属" cents={goal.allocated_cents} /></dl></li>)}</ul>
      </details>}<Issues issues={goals.issues ?? []} />
    </Card>
    <Card title="已自主配置" subtitle="有正式策略与来源证据的当前本金" state={assets.state}>
      <Money cents={assetsProven ? assets.managed_current_principal_cents : null} large />
      <dl className="compact-metrics"><Metric name="一般闲置资金本金" cents={assetsProven ? assets.general_principal_cents : null} />
        <Metric name="持有或已到期本金" cents={assetsProven ? assets.held_or_matured_cents : null} />
        <Metric name="赎回处理中本金" cents={assetsProven ? assets.redeeming_cents : null} />
        <Metric name="待买入金额" cents={assetsProven ? assets.pending_purchase_cents : null} /></dl>
      <p className="caption">已排除 {assets.excluded_manual_count} 笔手工持仓。
        {assets.unknown_position_count > 0 && `另有 ${assets.unknown_position_count} 笔持仓状态待核验。`}待买入金额单列，不计入当前本金。</p>
      {assetsProven && assets.by_goal.length > 0 && <details className="details"><summary>查看目标策略本金</summary>
        <ul className="amount-list">{assets.by_goal.map((item) => <li key={item.goal_id}>
          <span>{goals.items.find((goal) => goal.goal_id === item.goal_id)?.name ?? '目标归属待说明'}</span>
          <Money cents={item.principal_cents} /></li>)}</ul></details>}
      <Issues issues={assets.issues ?? []} />
    </Card>
  </div>;
}

function Obligations({ data }: { data: Dashboard }) {
  const next = data.next_obligations;
  return <Card title="下一组义务" subtitle="按原到期日选取最近一组，含已逾期的保护承诺" state={next.status}>
    {next.status === 'NOT_PROVEN' ? <p className="empty-state">义务来源待核验，不能据此判断已无待付义务。</p> :
      next.next_count === 0 ? <p className="empty-state">窗口内暂无已知待保护义务。</p> : <>
        <div className="obligation-summary"><div><span className="caption">原到期日</span>
          <p className="due-date">{calendarDate(next.next_due_date)}</p>
          <p className="caption">同日 {next.next_count} 笔 · {next.basis_summary && label(next.basis_summary)}</p></div>
          <div><span className="caption">本组剩余保护金额</span><p><Money cents={next.next_remaining_protection_cents} /></p></div>
        </div>
        <ul className="obligation-list">{next.items.map((item) => <li key={item.occurrence_id}>
          <div><strong>{item.kind === 'CREDIT_CARD_BILL' ? '信用卡账单' : item.payee_id ? `周期义务 · ${item.payee_id}` : '周期义务'}</strong>
            {item.overdue && <span className="badge badge-warning">已逾期</span>}
            <p className="caption">{label(item.total_basis)}{item.period && ` · ${item.period}`}</p>
            {item.overdue && <p className="caption">原到期日保留；测算支付日为 {calendarDate(item.projection_payment_date)}</p>}
          </div><Money cents={item.remaining_protection_cents} /></li>)}</ul>
        {!next.items_complete && <p className="notice">展示前 {next.items.length} 笔；本组笔数与金额覆盖完整同日组。</p>}
      </>}
    <p className="caption">保护金额用于边界计算。区间上限不代表已确认的实际扣款额；本页不会发起支付。</p>
  </Card>;
}

function PendingActions({ data }: { data: Dashboard }) {
  const actions = data.pending_actions;
  const recoveries = data.recovery_proposals;
  return <Card title="待处理事项" subtitle="查看原动作与结果状态，不在总览页产生新的资金动作" state={actions.state}>
    <div className={`intervention ${data.intervention.status === 'NONE' ? '' : 'intervention-attention'}`}>
      <strong>{label(data.intervention.status)}</strong>
      <span>{data.intervention.complete ? `已知需介入 ${data.intervention.known_required_count} 项` : '范围尚未完整核验，不能判断没有待介入事项'}</span>
    </div>
    <h4>原资金动作 <span>{actions.total}</span></h4>
    {actions.total === 0 ? <p className="empty-state">{actions.state === 'PROVEN' && actions.list_complete ? '暂无原动作待处理。' : '列表待核验。'}</p> :
      <ul className="action-list">{actions.items.map((action) => <li key={action.action_id}>
        <details><summary><span><strong>{label(action.action_type)}</strong><small>{label(action.status)}</small></span>
          <Money cents={action.amount_cents} /><span className="expand-label">查看原项</span></summary>
          <dl className="action-details"><div><dt>准备时分级</dt><dd>{label(action.prepared_level)}</dd></div>
            <div><dt>当前评估</dt><dd>{action.current_decision ? label(action.current_decision.level) : '保持原动作状态，未重新分级'}</dd></div>
            <div><dt>模拟银行状态</dt><dd>{action.bank_status ? label(action.bank_status) : '尚无银行操作'}{!action.bank_state_proven && ' · 待核验'}</dd></div>
            <div><dt>回执</dt><dd>{action.receipt_status ? label(action.receipt_status) : '尚无回执'}{action.receipt_id && (action.receipt_verified ? ' · 已核验' : ' · 待核验')}</dd></div>
            <div><dt>该原项审计</dt><dd>{label(action.audit_status)}</dd></div>
            <Metric name="费用" cents={action.fee_cents} /><Metric name="退出损失" cents={action.loss_cents} />
            <div><dt>原动作编号</dt><dd><code>{action.action_id}</code></dd></div>
            <div><dt>原决策编号</dt><dd><code>{action.decision_run_id}</code></dd></div>
            {action.effect_hash && <div><dt>原经济效果摘要</dt><dd><code>{action.effect_hash}</code></dd></div>}
          </dl>{action.reason_codes.length > 0 && <p className="caption">原因：{action.reason_codes.join(' · ')}</p>}
          {action.status === 'UNKNOWN' && <p className="notice">银行结果未知时，应查询与核对原操作；本页不会创建重试扣款。</p>}
        </details></li>)}</ul>}
    {!actions.list_complete && <p className="notice">已展示 {actions.items.length} / {actions.total} 个原动作，待处理范围未完整展示。</p>}
    <h4>恢复建议 <span>{recoveries.total}</span><Badge state={recoveries.state} /></h4>
    <p className="caption">历史单次确认建议保留为待复核原项，不能替代当前的具体确认。</p>
    {recoveries.total === 0 ? <p className="empty-state">{recoveries.state === 'PROVEN' && recoveries.list_complete ? '暂无恢复建议待复核。' : '恢复建议列表待核验。'}</p> :
      <ul className="recovery-list">{recoveries.items.map((item) => <li key={item.run_id}><details>
        <summary><strong>{label(item.status)}</strong><span className="caption">{snapshotTime(item.as_of, data.timezone)}</span><span className="expand-label">查看建议</span></summary>
        <dl className="action-details"><div><dt>原建议状态</dt><dd>{label(item.original_status)}</dd></div>
          <div><dt>审计状态</dt><dd>{label(item.audit_status)}</dd></div>
          <Metric name="费用" cents={item.fee_cents} /><Metric name="退出损失" cents={item.loss_cents} />
          <div><dt>原建议编号</dt><dd><code>{item.run_id}</code></dd></div></dl>
        {item.reason_codes.length > 0 && <p className="caption">原因：{item.reason_codes.join(' · ')}</p>}
      </details></li>)}</ul>}
    {!recoveries.list_complete && <p className="notice">已展示 {recoveries.items.length} / {recoveries.total} 条恢复建议，范围未完整展示。</p>}
  </Card>;
}

function Evidence({ data }: { data: Dashboard }) {
  return <section className="evidence-panel" aria-label="来源与审计">
    <div><h3>来源与审计</h3><p className="caption">全部卡片来自同一时点、同一次资金总览查询。</p></div>
    <Badge state={data.audit.status} />
    <details className="details evidence-details"><summary>查看计算与核验说明</summary>
      <div className="evidence-content"><p>核验范围：当前有效审计纪元。{data.audit.complete ? '范围完整。' : '范围未完整核验。'}</p>
        <dl className="action-details"><div><dt>可信查询时点</dt><dd>{data.as_of} · {data.timezone}</dd></div>
          <div><dt>输入摘要</dt><dd><code>{data.boundary.input_digest}</code></dd></div>
          <div><dt>资金边界摘要</dt><dd><code>{data.boundary.boundary_hash}</code></dd></div>
          <div><dt>审计纪元</dt><dd><code>{data.audit.epoch_id ?? '尚未建立'}</code></dd></div>
          <div><dt>采用的来源证据</dt><dd>{data.source_evidence_ids.length} 条</dd></div></dl>
        <Issues issues={data.boundary.issues ?? []} />
        {data.boundary.blocking_constraints.length > 0 && <><h4>边界限制</h4><ul>{data.boundary.blocking_constraints.map((item, index) =>
          <li key={`${item.code}-${index}`}><code>{item.code}</code>{item.date && ` · ${calendarDate(item.date)}`}
            {item.required_cents != null && <> · 保护要求 <Money cents={item.required_cents} /></>}
            {item.available_cents != null && <> · 可用 <Money cents={item.available_cents} /></>}</li>)}</ul></>}
        {data.boundary.calculation_notes.length > 0 && <><h4>计算说明</h4><ul>{data.boundary.calculation_notes.map((note, index) => <li key={index}>{note}</li>)}</ul></>}
      </div></details>
  </section>;
}

export default function App() {
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: getDashboard, retry: false, refetchOnWindowFocus: false });
  const data = dashboard.data;
  // Hide a stale snapshot after a failed refresh rather than claim it is current.
  const showData = data && !dashboard.isError;
  return <main className="app-shell">
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">界</span>
      <div><h1>钱途有界</h1><p>BOUNDED FUNDS</p></div></div><span className="simulation-badge">模拟环境</span></header>
    <section className="page-intro"><div><p className="eyebrow">每一步安排，都在确认的边界之内</p><h2>资金总览</h2></div>
      <div className="snapshot-controls"><p role="status" aria-live="polite">
        {dashboard.isError ? '资金总览暂未连接' : dashboard.isPending ? '正在获取资金总览…' : dashboard.isFetching ? '正在刷新资金总览…' : '资金总览已连接'}</p>
        {showData && <p className="caption">查询时点 {snapshotTime(data.as_of, data.timezone)} · {data.timezone}</p>}
        <button type="button" className="refresh-button" disabled={dashboard.isFetching} onClick={() => void dashboard.refetch()}>
          {dashboard.isFetching ? '获取中…' : dashboard.isError ? '重新连接' : '刷新总览'}</button></div>
    </section>
    <p className="simulation-note">所有资金动作均为模拟，未接入真实银行账户、支付或理财交易接口。</p>
    {dashboard.isError && <section className="load-error" role="alert"><h3>暂时无法展示资金总览</h3>
      <p>{dashboard.error instanceof Error ? dashboard.error.message : '读取失败，请重新连接。'}</p>
      <p className="caption">连接恢复后将重新获取全部卡片，当前不显示缺失金额为零。</p></section>}
    {dashboard.isPending && <section className="loading-panel" aria-label="加载资金总览"><div className="loading-line" />
      <p>正在读取同一时点的账户、边界与待处理事项…</p></section>}
    {showData && <><BoundaryOverview data={data} /><FactsAndProtection data={data} />
      <div className="detail-grid"><Obligations data={data} /><PendingActions data={data} /></div><Evidence data={data} /></>}
    <footer className="app-footer"><span>钱途有界 · 竞赛模拟原型</span><span>财务计算、策略权限与原操作核验分别展示</span></footer>
  </main>;
}
