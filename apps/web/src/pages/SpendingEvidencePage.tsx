import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '../api/http';
import { categoryOptions, getCategoryCommand, getCategoryReview, getOriginalSpendingResponse, getPeriodicSuggestions, getSeasonalSuggestions, getSpendingAccounts, getSpendingTransactions, postCategoryConfirmation } from '../api/spending-evidence';
import type { CategoryBody, CategoryResult, CategoryReview, Periodic, Seasonal } from '../api/spending-evidence';
import { beginSpendingEvidenceOperation, clearSpendingEvidenceAfterLookup, endSpendingEvidenceAttempt, getSessionTransactionReadonlyReference, prepareSpendingIntent, recoverSpendingEvidenceOperation, useSpendingEvidenceOperation } from '../features/spending-evidence-operation';
import { formatMoneyCents } from '../features/money';

const categoryNames: Record<CategoryBody['category'], string> = { food: '食品', transport: '交通', daily_necessities: '日常必需品', rent: '房租', utilities: '水电', education: '教育', healthcare: '医疗', other: '其他' };
function Raw({ value, title }: { value: object; title: string }) { return <details><summary>{title}</summary><pre className="readonly-raw">{getOriginalSpendingResponse(value) ?? JSON.stringify(value, null, 2)}</pre></details>; }
function History({ value }: { value: Periodic | Seasonal }) { return <><p>服务时点 {value.as_of} · 原历史 {value.history_proof.period_start ?? 'UNKNOWN'} → {value.history_proof.period_end ?? 'UNKNOWN'} · 覆盖 {value.history_proof.verified ? 'VERIFIED' : 'UNKNOWN'}</p><p>覆盖账户 {value.history_proof.account_ids.length}，覆盖证据 {value.history_proof.evidence_ids.length}。不足：{value.history_proof.reason_codes.join('、') || '服务未报告覆盖不足'}</p><ul>{value.source_issues.map((issue, index) => <li key={index}>{issue.code} · {issue.source_ref} · {issue.message}</li>)}</ul><p>原 source_digest <code>{value.source_digest}</code></p></>; }
export default function SpendingEvidencePage({ mutationBlocked = false }: { mutationBlocked?: boolean }) {
  const operation = useSpendingEvidenceOperation(); const client = useQueryClient();
  const [account, setAccount] = useState(''); const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(() => { const original = recoverSpendingEvidenceOperation(); return original.pending?.transaction_id ?? getSessionTransactionReadonlyReference()?.transaction_id ?? ''; });
  const [review, setReview] = useState<CategoryReview | null>(null); const [category, setCategory] = useState<CategoryBody['category'] | ''>(''); const [reason, setReason] = useState(''); const [accepted, setAccepted] = useState(false);
  const [windowId, setWindowId] = useState(''); const [seasonal, setSeasonal] = useState<Seasonal | null>(null); const [receipt, setReceipt] = useState<CategoryResult | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const accounts = useQuery({ queryKey: ['spending-accounts'], queryFn: getSpendingAccounts, retry: false });
  const user = accounts.data?.user_id;
  const transactions = useQuery({ queryKey: ['spending-transactions', user, account, offset], queryFn: () => getSpendingTransactions(accounts.data!, account, offset), enabled: !!accounts.data && !!account, retry: false });
  const periodic = useQuery({ queryKey: ['spending-periodic', user], queryFn: () => getPeriodicSuggestions(user!), enabled: !!user, retry: false });
  const blocked = mutationBlocked || operation.busy || !!operation.pending || !!operation.storage_error || busy;
  useEffect(() => { recoverSpendingEvidenceOperation(); }, []);
  async function readTransaction(id = selected) {
    if (!user || !id || busy) return; setBusy(true); setError(''); setReview(null); setAccepted(false); setCategory(''); setReason('');
    try { const original = await getCategoryReview(user, id); setSelected(id); setReview(original); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function submit() {
    if (!review || !category || !accepted || !reason.trim() || blocked || !user) return;
    setBusy(true); setError(''); setNotice(''); setReceipt(null);
    try {
      const current = await getCategoryReview(user, review.transaction_id);
      if (current.reviewed_transaction_hash !== review.reviewed_transaction_hash || current.epoch_id !== review.epoch_id) throw new Error('原交易或模拟期已变化，请重新读取并明确复核');
      const body: CategoryBody = { category, accepted: true, reason: reason.trim(), reviewed_transaction_hash: review.reviewed_transaction_hash, expected_epoch_id: review.epoch_id, idempotency_key: `category-${crypto.randomUUID()}` };
      const intent = await prepareSpendingIntent(current, body); await beginSpendingEvidenceOperation(intent, mutationBlocked);
      try { await postCategoryConfirmation(intent); setNotice('确认请求已返回；原请求仍待单独按原键读回核对。'); } finally { endSpendingEvidenceAttempt(); }
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function recover() {
    const intent = operation.pending; if (!intent || busy || operation.busy) return; setBusy(true); setError(''); setNotice('');
    try {
      const original = await getCategoryCommand(intent); setReceipt(original);
      if (original.status === 'NOT_FOUND_NOT_FINAL') { setNotice('原键未找到不是终局；保留原命令，不允许换键重提。'); return; }
      await clearSpendingEvidenceAfterLookup(intent, original); setReview(null); setAccepted(false); setCategory(''); setSelected(intent.transaction_id);
      setNotice('已按原键核对原命令、交易与银行原件回执。分类回执不授予资金权限。'); await client.invalidateQueries({ queryKey: ['spending-transactions'] });
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function readSeasonal() {
    if (!user || !windowId || busy) return; setBusy(true); setError(''); setSeasonal(null);
    try { setSeasonal(await getSeasonalSuggestions(user, windowId)); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <div className="spending-evidence-page"><header><h1>支出证据与历史规律</h1><p>模拟银行原件与用户类别声明分别展示。规律候选只供复核；本页不会确认策略或执行资金动作。</p></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{operation.storage_error && <p role="alert">{operation.storage_error}</p>}
    {operation.pending && <section aria-label="待核对分类原请求"><h2>待核对分类原请求</h2><p>交易 {operation.pending.transaction_id} · 原键 {operation.pending.body.idempotency_key} · hash {operation.pending.request_hash}</p><Raw value={operation.pending} title="保存的完整原分类命令" /><button type="button" disabled={busy || operation.busy} onClick={() => void recover()}>只读核对原分类请求</button><p>HTTP 成功、拒绝和响应丢失均保留原请求。原键未找到不表示允许替换。</p></section>}
    {receipt && <section aria-label="分类原回执"><h2>分类原件 · {receipt.status}</h2><Raw value={receipt} title="原命令与原银行引用回执" /></section>}
    <section aria-label="账户与原银行交易"><h2>读取原银行消费交易</h2>{accounts.isError && <p role="alert">{errorMessage(accounts.error)}</p>}
      <label>交易账户<select value={account} disabled={busy} onChange={(event) => { setAccount(event.target.value); setOffset(0); setReview(null); setAccepted(false); setSelected(''); }}><option value="">明确选择账户</option>{accounts.data?.accounts.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.account_type}</option>)}</select></label>
      {transactions.isError && <p role="alert">{errorMessage(transactions.error)}</p>}{transactions.data && <><p>实际交易总数 {transactions.data.total} · 本页 {transactions.data.items.length} · offset {transactions.data.offset}</p><ul>{transactions.data.items.map((item) => <li key={item.id}><time>{item.occurred_at}</time> · {item.direction} · {formatMoneyCents(item.amount_cents)} 元 · 原类别 {item.category} · {item.category_confirmed ? '已确认类别；更正尚未支持' : '未确认类别'} <button type="button" disabled={busy || item.direction !== 'DEBIT'} onClick={() => void readTransaction(item.id)}>读取交易 {item.id}</button></li>)}</ul><button type="button" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页交易</button><button type="button" disabled={busy || offset + transactions.data.items.length >= transactions.data.total} onClick={() => setOffset(offset + 50)}>下一页交易</button></>}
      {selected && <button type="button" disabled={!user || busy} onClick={() => void readTransaction()}>重读所选交易原件</button>}
    </section>
    {review && <section aria-label="原消费交易类别复核"><h2>首次类别确认复核</h2><p>银行消费 {formatMoneyCents(Number(review.transaction.amount_cents))} 元 · 原类别 {String(review.transaction.category)} · 服务时点 {review.as_of}</p><p>原银行 evidence {String(review.bank_fact.evidence_id)} · hash {String(review.bank_fact.evidence_hash)}</p><Raw value={review} title="原银行事实与完整交易复核响应" />
      {review.first_confirmation_supported ? <><label>用户明确选择类别<select value={category} disabled={blocked} onChange={(event) => { setCategory(event.target.value as CategoryBody['category'] | ''); setAccepted(false); }}><option value="">请选择类别</option>{categoryOptions.map((item) => <option value={item} key={item}>{categoryNames[item]}</option>)}</select></label><label>分类确认原因<textarea value={reason} maxLength={500} disabled={blocked} onChange={(event) => { setReason(event.target.value); setAccepted(false); }} /></label><label><input type="checkbox" checked={accepted} disabled={blocked || !category || !reason.trim()} onChange={(event) => setAccepted(event.target.checked)} />我已复核原银行消费，并明确确认此类别声明</label><button type="button" disabled={blocked || !accepted || !category || !reason.trim()} onClick={() => void submit()}>提交首次类别确认</button></> : <p>该交易类别已经确认。类别更正尚未支持，本页只读。</p>}<p>银行金额、方向、收付对象和经济角色保持原件；类别声明不授予策略或银行权限。</p>
    </section>}
    <section aria-label="周期规律建议"><h2>周期规律 · 只读候选</h2>{periodic.isError && <p role="alert">{errorMessage(periodic.error)}</p>}<button type="button" disabled={!user || periodic.isFetching || busy} onClick={() => void periodic.refetch()}>重读周期规律</button>{periodic.data && <><History value={periodic.data} /><p>原窗口 {periodic.data.history_start} → {periodic.data.history_end} · 排除交易 {periodic.data.excluded_transaction_count}</p>{periodic.data.patterns.length === 0 && <p>服务未返回周期候选；这不表示未来没有义务。</p>}{periodic.data.patterns.map((item) => <article key={item.pattern_id}><h3>{item.kind} · {item.status}</h3><p>{item.payee_ref} · 账户 {item.account_id} · 月份 {item.months.join('、')} · 周期 {item.cycle_count} / 样本 {item.sample_count}</p><p>金额范围 {formatMoneyCents(item.amount_min_cents)} → {formatMoneyCents(item.amount_max_cents)} 元；日期跨度 {item.day_spread} 天；建议日 {item.suggested_due_day ?? 'UNKNOWN'}</p><p>原均值 {item.mean_fraction_cents} 分 · 方差 {item.variance_fraction_cents_squared} 分²。具体不足：{item.reason_codes.join('、') || '服务未报告不足'}</p><p>原来源 {item.sources.length} 条；候选 hash {item.candidate_configuration_hash ?? '未提供候选'}。尚未确认，未来义务未保证。</p></article>)}<Raw value={periodic.data} title="完整周期样本、来源与候选原响应" /></>}</section>
    <section aria-label="节日储备建议"><h2>节日储备 · 只读建议</h2><label>用户指定官方窗口 ID<input value={windowId} maxLength={160} disabled={busy} onChange={(event) => { setWindowId(event.target.value); setSeasonal(null); }} /></label>
      {seasonal && <label>选择本次服务返回的登记窗口<select value={windowId} disabled={busy} onChange={(event) => { setWindowId(event.target.value); setSeasonal(null); }}><option value={windowId}>{windowId}</option>{seasonal.public_windows.filter((item) => item.window_id !== windowId).map((item) => <option value={item.window_id} key={item.window_id}>{item.window_id} · {item.start} → {item.end}</option>)}</select></label>}
      <button type="button" disabled={!user || !windowId || busy} onClick={() => void readSeasonal()}>读取指定节日建议</button><p>窗口由服务端官方通知登记，未登记或尚未发布的窗口保持 UNKNOWN；本页不推定全年安排。</p>
      {seasonal && <><History value={seasonal} /><h3>{seasonal.suggestion.status}</h3><p>历史窗口 {seasonal.suggestion.window_count} / 至少 {seasonal.suggestion.required_window_count} · 具体不足：{seasonal.suggestion.reason_codes.join('、') || '服务未报告不足'}</p>{seasonal.suggestion.target && <p>实际窗口 {seasonal.suggestion.target.start} → {seasonal.suggestion.target.end} · {seasonal.suggestion.target.notice_reference} · <a href={seasonal.suggestion.target.source_url} target="_blank" rel="noreferrer">官方通知原来源</a></p>}<p>有效窗口 {seasonal.suggestion.effective_window_start ?? 'UNKNOWN'} → {seasonal.suggestion.effective_window_end ?? 'UNKNOWN'}；登记核对日期 {seasonal.calendar_verified_on}。</p><p>原建议额 {seasonal.suggestion.proposed_adjustment_cents === null ? 'UNKNOWN（未补零）' : `${formatMoneyCents(seasonal.suggestion.proposed_adjustment_cents)} 元`} · 所需额 {seasonal.suggestion.required_adjustment_cents === null ? 'UNKNOWN' : `${formatMoneyCents(seasonal.suggestion.required_adjustment_cents)} 元`} · 原rank {seasonal.suggestion.rank ?? 'UNKNOWN'}</p><ul>{seasonal.suggestion.comparisons.map((item) => <li key={item.window.window_id}>{item.window.window_id} · {item.status} · 基线 {item.baseline_start} → {item.baseline_end} · 交易分母 {item.transaction_ids.length} · {item.reason_codes.join('、')}</li>)}</ul><p>候选尚未确认；本页没有改变硬保护或银行权限。</p><Raw value={seasonal} title="官方窗口、全部比较样本与节日建议原响应" /></>}
    </section>
  </div>;
}
