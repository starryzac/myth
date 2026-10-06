import type { DemoAction } from '../api/demo';
import { formatMoneyCents } from '../features/money';
function money(value: number | null | undefined) { return value === undefined ? '未记录' : value === null ? '待核验' : `¥${formatMoneyCents(value)}`; }
export default function ActionEffectReview({ action }: { action: DemoAction }) {
  const effect = action.effect;
  return <section className="state-review" aria-label={`原动作经济后果 ${action.action_id}`}><h4>本次具体经济后果</h4>
    <p>原动作 {action.action_id} · {effect.action_type} · 状态 {action.status}</p><p>原自主等级 {action.autonomy_level} · 财务校验 {action.prepared_validation.status}</p>
    <p className="caption">这是服务端实际准备的原经济后果，不能用确认跳过当前权限、报价有效期或原银行操作核验。</p>
    <dl className="goal-metrics"><div><dt>{effect.action_type === 'REDEEM_ASSET' ? '原本金' : '动作金额'}</dt><dd>{money(effect.amount_cents)}</dd></div>
      <div><dt>费用</dt><dd>{money(effect.fee_cents)}</dd></div><div><dt>损失</dt><dd>{money(effect.loss_cents)}</dd></div><div><dt>净到账</dt><dd>{money(effect.net_cents)}</dd></div></dl>
    <dl className="demo-identities"><div><dt>原持仓</dt><dd>{effect.position_id ?? '未关联'}</dd></div><div><dt>持仓账户</dt><dd>{effect.position_account_id ?? '未关联'}</dd></div>
      <div><dt>到账账户</dt><dd>{effect.destination_account_id ?? effect.return_account_id ?? '未关联'}</dd></div><div><dt>目标归属</dt><dd>{effect.goal_id ?? '未关联'}</dd></div>
      <div><dt>产品 / 版本</dt><dd>{effect.product_id ?? '未关联'} / {effect.product_version_number ?? '未记录'}</dd></div><div><dt>原报价</dt><dd>{effect.quote_id ?? '未关联'}</dd></div>
      <div><dt>最迟到账时点</dt><dd>{effect.latest_arrival_at ?? '未记录'}</dd></div><div><dt>结算等待</dt><dd>{effect.settlement_delay_days ?? '未记录'} 天</dd></div>
      <div><dt>生效时点</dt><dd>{effect.valid_from}</dd></div><div><dt>结束时点（不含）</dt><dd>{effect.expires_at}</dd></div>
      <div><dt>原购入授权版本</dt><dd>{effect.original_policy_version_id ?? '未关联'}</dd></div><div><dt>当前策略版本</dt><dd>{effect.policy_version_id ?? '未关联'}</dd></div></dl>
    <p className="caption">报价到期或服务端校验失败时保留此原动作身份；本页不重新准备新价格。</p>
    <details><summary>查看全部原资金来源与其他经济约束</summary>
      <p>原业务键 {effect.business_key} · 原用户 {effect.user_id}</p><p>原策略 {effect.policy_id ?? '未关联'}；关联版本 {(effect.policy_version_ids ?? []).join('、') || '未关联'}</p>
      <p>收款人 {effect.payee_id ?? '未关联'}；收款人原证据 {effect.payee_evidence_id ?? '未关联'}；产品条款摘要 {effect.terms_digest ?? '未关联'}</p>
      <h5>原现金来源</h5>{(effect.cash_uses ?? []).length === 0 ? <p>未关联现金来源</p> : (effect.cash_uses ?? []).map((use) => <p key={use.account_id}>账户 {use.account_id} · {money(use.amount_cents)}</p>)}
      <h5>原收入归属</h5>{(effect.income_uses ?? []).length === 0 ? <p>未关联收入份额</p> : (effect.income_uses ?? []).map((use) => <p key={use.fragment_id}>原份额 {use.fragment_id} · 原交易 {use.origin_transaction_id} · 账户 {use.account_id} · {money(use.amount_cents)}</p>)}
      {effect.purchase_exit && <p>原退出计划 {effect.purchase_exit.kind} · 请求时点 {effect.purchase_exit.request_at ?? '未指定'} · 本金可用时点 {effect.purchase_exit.principal_available_at} · 计息 {effect.purchase_exit.earning_days} 天 · 流动性占用 {effect.purchase_exit.liquidity_days} 天 · 条款摘要 {effect.purchase_exit.terms_digest}</p>}
      {effect.liability && <p>原义务 {effect.liability.kind === 'bill' ? `账单 ${effect.liability.bill_id}` : `策略 ${effect.liability.policy_id} · 期间 ${effect.liability.period} · 最终金额 ${money(effect.liability.final_total_cents)}`} · 原证据 {effect.liability.evidence_ids.join('、')}</p>}
    </details>
    <p className="trace-hash caption">本次经济后果摘要 {action.effect_hash}</p>
    {(action.prepared_validation.reasons ?? []).map((reason, index) => <p className="caption" key={index}>{reason}</p>)}
    <a href={`#decisions/${action.decision_run_id}`}>查看此原动作的决策依据</a>
  </section>;
}
