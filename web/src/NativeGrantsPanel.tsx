import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError } from './api';
import type { Client, Dashboard } from './api';
import { money, yuanToCents } from './money';
import { timestamp, userError } from './display';
import { nextPoll } from './recovery';
import { financialOptions, productLabel } from './native-financial-api';
import type { FinancialOptions } from './native-financial-api';
import { templateLabels } from './template-schema';
import type { TemplatePolicyView } from './template-api';
import { clearNativeGrantStateIntent, loadNativeGrantLocators, loadNativeGrantStateIntent, nativeFamilies, nativeFamilyLabels, NativeGrantsClient, saveNativeGrantLocator, saveNativeGrantStateIntent } from './native-grants-api';
import type { NativeCapability, NativeFamily, NativeGrantAction, NativeGrantLocator, NativeGrantPreview, NativeGrantStateIntent, NativeGrantTerms, NativeGrantView } from './native-grants-api';

const actionLabels: Record<NativeGrantAction, string> = { PAUSE: '暂停', RESUME: '恢复', REVOKE: '撤销' };
const stateLabels = { ACTIVE: '有效', PAUSED: '已暂停', REVOKED: '已撤销' };
const defaultExpiry = () => { const date = new Date(Date.now() + 30 * 86400000); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`; };
function eligible(view: TemplatePolicyView, family: NativeFamily) {
  if (view.effective_state !== 'ACTIVE' || !view.version.current) return false;
  const cfg = view.version.compiled.configuration;
  if (family === 'SINGLE_GOAL' || family === 'JOINT_GOALS') return cfg.type === 'long_term_goal';
  if (family === 'PERIODIC_PAYMENT') return (cfg.type === 'recurring_obligation' || cfg.type === 'periodic_transfer') && cfg.auto_execute;
  return cfg.type === 'asset_authorization' || (family === 'MATURITY_REINVEST' && cfg.type === 'recovery');
}

export default function NativeGrantsPanel({ client, dashboard, onChanged }: { client: Client; dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const formID = useId(), api = useMemo(() => new NativeGrantsClient(client), [client]), scope = `${client.session?.environment}:${client.session?.user_id}`;
  const [family, setFamily] = useState<NativeFamily>('SINGLE_GOAL'), [versions, setVersions] = useState<string[]>([]), [accounts, setAccounts] = useState<string[]>([]), [products, setProducts] = useState<string[]>([]);
  const [single, setSingle] = useState('10.00'), [monthly, setMonthly] = useState('50.00'), [lifetime, setLifetime] = useState('100.00'), [expires, setExpires] = useState(defaultExpiry), [timezone, setTimezone] = useState<'UTC' | 'Asia/Shanghai'>('Asia/Shanghai');
  const [capabilities, setCapabilities] = useState<NativeCapability[]>([]), [options, setOptions] = useState<FinancialOptions | null>(null), [grants, setGrants] = useState<NativeGrantView[]>([]), [loadError, setLoadError] = useState(''), [loading, setLoading] = useState(true), [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState<NativeGrantPreview | null>(null), [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [records, setRecords] = useState<NativeGrantLocator[]>([]), [selectedKey, setSelectedKey] = useState(''), [storageError, setStorageError] = useState(''), [ready, setReady] = useState(false), [keyResult, setKeyResult] = useState<NativeGrantView | null>(null), [verifiedKey, setVerifiedKey] = useState(''), [notFound, setNotFound] = useState(false), [independent, setIndependent] = useState(false), [checking, setChecking] = useState(false), [readError, setReadError] = useState(''), [replayConsent, setReplayConsent] = useState(false), [recovery, setRecovery] = useState(0);
  const [stateIntent, setStateIntent] = useState<NativeGrantStateIntent | null>(null), [stateRetryReady, setStateRetryReady] = useState(false), [stateChecks, setStateChecks] = useState<string[]>([]);
  const submitting = useRef(false), callbacks = useRef(onChanged); callbacks.current = onChanged;
  const selected = records.find((r) => r.key === selectedKey), frontier = records.at(-1), unresolved = !!frontier && verifiedKey !== frontier.key;
  const blocked = busy || !ready || !!storageError || unresolved || !!stateIntent;
  const candidates = options?.policies.filter((v) => eligible(v, family)) ?? [];
  const assetFamily = family === 'ASSET_ALLOCATION' || family === 'MATURITY_REINVEST';
  const capability = capabilities.find((c) => c.family === family);
  const goalName = (id: string) => dashboard.goals.find((g) => g.id === id)?.name ?? '原确认目标';
  const accountName = (id: string) => dashboard.accounts.find((a) => a.id === id)?.name ?? '原确认账户';
  const policyName = (view: TemplatePolicyView) => view.version.compiled.configuration.name || `${templateLabels[view.version.compiled.template_name]}${view.goal_id ? ` · ${goalName(view.goal_id)}` : ''}`;
  function clearReview() { setPreview(null); setConsent(false); }
  function toggle(values: string[], value: string, checked: boolean) { return checked ? [...values.filter((v) => v !== value), value] : values.filter((v) => v !== value); }
  useEffect(() => {
    setReady(false); setStorageError(''); setRecords([]); setSelectedKey(''); setVerifiedKey(''); setStateIntent(null); clearReview(); setStateChecks([]); setVersions([]); setAccounts([]); setProducts([]);
    try { const saved = loadNativeGrantLocators(client); setRecords(saved); setSelectedKey(saved.at(-1)?.key ?? ''); setStateIntent(loadNativeGrantStateIntent(client)); }
    catch (cause) { setStorageError(userError(cause)); }
    setReady(true);
  }, [client, scope]);
  useEffect(() => {
    const controller = new AbortController(); let cancelled = false; let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      setLoading(true);
      try { const [caps, available, current] = await Promise.all([api.capabilities(controller.signal), financialOptions(client, controller.signal), api.list(controller.signal)]); if (cancelled) return; setCapabilities(caps); setOptions(available); setGrants(current); setLoadError(''); }
      catch (cause) { if (!cancelled) { setCapabilities([]); setLoadError(userError(cause)); } }
      finally { if (!cancelled) { setLoading(false); timer = setTimeout(() => { void read(); }, 30000); } }
    }
    void read(); return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, client, refresh]);
  useEffect(() => {
    setKeyResult(null); setNotFound(false); setIndependent(false); setReadError(''); setReplayConsent(false);
    if (!selected) return;
    const record = selected, controller = new AbortController(); let cancelled = false, verified = false, attempts = 0; let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      setChecking(true); let again = false;
      try { if (!verified) { await api.original(record, controller.signal); if (cancelled) return; verified = true; setIndependent(true); } const result = await api.byKey(record, controller.signal); if (cancelled) return; setKeyResult(result); setNotFound(false); setReadError(''); if (record.key === frontier?.key) setVerifiedKey(record.key); setRefresh((v) => v + 1); }
      catch (cause) { if (cancelled) return; attempts++; if (cause instanceof ApiError && cause.status === 404) { setNotFound(verified); setReadError('原键暂未查到，不能据此另建授权；只可明确沿同一原键继续提交。'); } else { setReadError(userError(cause)); again = !(cause instanceof ApiError && [401, 403].includes(cause.status)); } }
      finally { if (!cancelled) { setChecking(false); if (again) timer = setTimeout(() => { void read(); }, nextPoll(attempts)); } }
    }
    void read(); return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, selected?.key, frontier?.key, recovery]);
  useEffect(() => {
    setStateRetryReady(false); if (!stateIntent) return;
    const intent = stateIntent, controller = new AbortController(); let cancelled = false;
    void api.grant(intent.grant_id, controller.signal).then((view) => {
      if (cancelled) return;
      if (view.state_sequence > intent.expected_sequence) { clearNativeGrantStateIntent(client); setStateIntent(null); setNotice('已独立核对当前授权状态。原请求后的状态变化已保留；已签名资金操作仍须沿原键恢复。'); setRefresh((v) => v + 1); }
      else if (view.state_sequence === intent.expected_sequence) setStateRetryReady(true);
      else throw new Error('授权状态版本倒退，请继续读取原件');
    }).catch((cause) => { if (!cancelled) setError(userError(cause)); });
    return () => { cancelled = true; controller.abort(); };
  }, [api, client, stateIntent, recovery]);
  async function review() {
    if (blocked || submitting.current || loading || !options || !capability) return;
    setBusy(true); setError(''); setNotice(''); clearReview();
    try { const caps = await api.capabilities(); setCapabilities(caps); const value = await api.preview({ family, policy_version_ids: versions, account_ids: accounts, product_ids: assetFamily ? products : [], single_cap_cents: yuanToCents(single), monthly_cap_cents: yuanToCents(monthly), lifetime_cap_cents: yuanToCents(lifetime), timezone, expires_at: new Date(expires).toISOString() }); setPreview(value); }
    catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (blocked || !preview || !consent || submitting.current) return;
    submitting.current = true; setBusy(true); setError('');
    try {
      const session = client.session; if (!session) throw new Error('请登录当前本人会话');
      if (Date.now() >= Date.parse(preview.expires_at)) { clearReview(); throw new Error('原持续授权审阅已过期，请重新预览'); }
      const original: NativeGrantLocator = { environment: session.environment, user_id: session.user_id, key: crypto.randomUUID(), command: { preview_id: preview.id, reviewed_hash: preview.reviewed_hash }, preview };
      const saved = saveNativeGrantLocator(client, original); setRecords(saved); setSelectedKey(original.key); setVerifiedKey(''); clearReview();
      await api.confirm(original); setRecovery((v) => v + 1); await callbacks.current();
    } catch (cause) { setError(`${userError(cause)} 已保存的原授权只沿原键核对，断连不自动创建另一份授权。`); setRecovery((v) => v + 1); }
    finally { submitting.current = false; setBusy(false); }
  }
  async function replay() {
    if (busy || checking || !selected || !independent || !notFound || !replayConsent || submitting.current || storageError) return;
    submitting.current = true; setBusy(true); setReplayConsent(false); setError('');
    try { const record = loadNativeGrantLocators(client).find((v) => v.key === selected.key); if (!record) throw new Error('原授权本机资料缺失'); await api.confirm(record); setRecovery((v) => v + 1); await callbacks.current(); }
    catch (cause) { setError(`${userError(cause)} 原键与完整授权载荷保留。`); } finally { submitting.current = false; setBusy(false); }
  }
  async function changeState(view: NativeGrantView, action: NativeGrantAction) {
    if (busy || submitting.current || storageError || stateIntent || !stateChecks.includes(view.grant.id)) return;
    const session = client.session; if (!session) return;
    const intent = { environment: session.environment, user_id: session.user_id, grant_id: view.grant.id, expected_sequence: view.state_sequence, action };
    submitting.current = true; setBusy(true); setError(''); setStateChecks([]); clearReview();
    try { saveNativeGrantStateIntent(client, intent); setStateIntent(intent); await api.state(intent); setRecovery((v) => v + 1); await callbacks.current(); }
    catch (cause) { setError(`${userError(cause)} 保留原状态动作和原版本，先独立读取当前状态。`); setRecovery((v) => v + 1); }
    finally { submitting.current = false; setBusy(false); }
  }
  async function retryState() {
    if (!stateIntent || !stateRetryReady || busy || submitting.current) return;
    submitting.current = true; setBusy(true); setError('');
    try { await api.state(stateIntent); setRecovery((v) => v + 1); await callbacks.current(); }
    catch (cause) { setError(userError(cause)); setRecovery((v) => v + 1); } finally { submitting.current = false; setBusy(false); }
  }
  function describe(terms: NativeGrantTerms) {
    return <><p>{nativeFamilyLabels[terms.family]}；每笔最高 {money(terms.single_cap_cents)}，自然月最高 {money(terms.monthly_cap_cents)}，累计最高 {money(terms.lifetime_cap_cents)}。月预算时区：{terms.timezone}。</p><p>授权窗口：{timestamp(terms.effective_from)} 至 {timestamp(terms.expires_at)}。账户：{terms.account_ids.map(accountName).join('、')}。{terms.goal_ids.length ? `目标：${terms.goal_ids.map(goalName).join('、')}。` : ''}</p><ul>{terms.policies.map((p) => { const view = options?.policies.find((v) => v.version.version_id === p.version_id); return <li key={p.version_id}>{view ? policyName(view) : templateLabels[p.template_name]} · 第 {p.version} 版原规则{p.goal_id ? ` · ${goalName(p.goal_id)}` : ''}</li>; })}</ul>{terms.product_ids.length > 0 && <p>限定产品：{terms.product_ids.map((id) => { const product = options?.products.find((p) => p.terms.product_id === id); return product ? productLabel(product.terms) : '原授权限定产品（当前供给待核实）'; }).join('、')}。</p>}{terms.payee_ids.length > 0 && <p>收款对象严格沿原支付规则已确认的对象，不能通过本表增加收款人。</p>}</>;
  }
  return <section className="zy-template-card" aria-label="原生持续授权"><h3>持续授权与执行范围</h3><p>规则确认与持续执行授权分别保存。只有明确确认的范围、当前服务端资格与每笔确定性检查都通过，系统才可安排；模型不能替本人授权。</p>
    <button className="zy-secondary" disabled={busy || loading} onClick={() => { setRefresh((v) => v + 1); setRecovery((v) => v + 1); }}>重新读取当前授权与资格</button>
    {loading && <p role="status">正在读取本人原规则、产品与当前执行资格…</p>}{loadError && <p role="alert">{loadError} 执行资格尚待核实，请重新读取。</p>}{storageError && <p role="alert">{storageError}</p>}
    <ul aria-label="服务端当前执行资格">{capabilities.map((c) => <li key={c.family}>{nativeFamilyLabels[c.family]}：{c.qualified ? '服务端已开放此执行族；每笔仍须重新核对' : '此执行族尚未开放；保存授权不会放开执行'}{c.family === 'JOINT_GOALS' && c.reason.startsWith('SAME_ACCOUNT') ? '（当前仅同账户）' : ''}</li>)}</ul>
    <form onSubmit={(event) => { event.preventDefault(); void review(); }} onChange={() => clearReview()}>
      <label className="zy-field" htmlFor={`${formID}-family`}>持续执行类型<select id={`${formID}-family`} disabled={blocked} value={family} onChange={(event) => { setFamily(event.target.value as NativeFamily); setVersions([]); setProducts([]); }}>{nativeFamilies.map((f) => <option key={f} value={f}>{nativeFamilyLabels[f]}</option>)}</select></label>
      <fieldset disabled={blocked || !options}><legend>选择当前本人原规则</legend>{candidates.map((view) => <label className="zy-check" key={view.version.version_id}><input type="checkbox" checked={versions.includes(view.version.version_id)} onChange={(event) => setVersions(toggle(versions, view.version.version_id, event.target.checked))} />{policyName(view)} · 第 {view.version.version} 版</label>)}{!candidates.length && <p>当前没有适用于此族的有效规则，请先在规则表单中明确确认。</p>}</fieldset>
      <fieldset disabled={blocked}><legend>限定实际账户</legend>{dashboard.accounts.map((account) => <label className="zy-check" key={account.id}><input type="checkbox" checked={accounts.includes(account.id)} onChange={(event) => setAccounts(toggle(accounts, account.id, event.target.checked))} />{account.name}</label>)}</fieldset>
      {assetFamily && <fieldset disabled={blocked || !options}><legend>限定原产品</legend>{options?.products.filter((p) => p.availability.status === 'AVAILABLE').map((product) => <label className="zy-check" key={product.terms.product_id}><input type="checkbox" checked={products.includes(product.terms.product_id)} onChange={(event) => setProducts(toggle(products, product.terms.product_id, event.target.checked))} />{productLabel(product.terms)}</label>)}<p>费用或损失需要独立本人确认；本授权不能自动覆盖新的费用损失。</p></fieldset>}
      <div className="zy-template-grid">{[['单次最高金额（元）', single, setSingle, 'single'], ['自然月累计最高金额（元）', monthly, setMonthly, 'month'], ['整个授权累计最高金额（元）', lifetime, setLifetime, 'life']].map(([label, value, setter, id]) => <label className="zy-field" htmlFor={`${formID}-${id as string}`} key={id as string}>{label as string}<input id={`${formID}-${id as string}`} inputMode="decimal" value={value as string} disabled={blocked} onChange={(event) => (setter as (v: string) => void)(event.target.value)} required /></label>)}</div>
      <label className="zy-field" htmlFor={`${formID}-expiry`}>授权截止（本机当地时间）<input id={`${formID}-expiry`} type="datetime-local" value={expires} disabled={blocked} onChange={(event) => setExpires(event.target.value)} required /></label><label className="zy-field" htmlFor={`${formID}-zone`}>自然月预算时区<select id={`${formID}-zone`} value={timezone} disabled={blocked} onChange={(event) => setTimezone(event.target.value as 'UTC' | 'Asia/Shanghai')}><option value="Asia/Shanghai">中国标准时间</option><option value="UTC">协调世界时</option></select></label>
      <button className="zy-secondary" disabled={blocked || loading || !options || !capability || !versions.length || !accounts.length || (assetFamily && !products.length)}>预览完整持续授权</button>
    </form>
    {preview && <div aria-label="完整持续授权原审阅"><h4>逐项核对持续授权</h4>{describe(preview.terms)}<p>本次审阅至 {timestamp(preview.expires_at)} 有效；最终授权从实际本人确认时刻生效，不能追授此前收入。</p>{capabilities.find((c) => c.family === preview.terms.family)?.qualified === false && <p>当前此执行族尚未开放。本次只保存限定授权，不能使该族开始执行。</p>}<label className="zy-check"><input type="checkbox" checked={consent} disabled={blocked} onChange={(event) => setConsent(event.target.checked)} />本人已逐项核对原规则版本、实际对象、单次/月/累计金额和期限，明确确认此持续授权。</label><button className="zy-primary" disabled={blocked || !consent} onClick={() => { void confirm(); }}>本人确认并保存持续授权</button></div>}
    {records.length > 0 && <div aria-label="持续授权原键恢复"><label className="zy-field" htmlFor={`${formID}-original`}>已保存的原持续授权<select id={`${formID}-original`} value={selectedKey} disabled={busy} onChange={(event) => setSelectedKey(event.target.value)}>{records.map((r, i) => <option value={r.key} key={r.key}>第 {i + 1} 份 · {nativeFamilyLabels[r.preview.terms.family]}</option>)}</select></label><button className="zy-secondary" disabled={busy || checking} onClick={() => setRecovery((v) => v + 1)}>核对原持续授权</button>{checking && <p role="status">正在独立读取原审阅与原键结果…</p>}{readError && <p role="alert">{readError}</p>}{keyResult && <p>原键授权已核对：{stateLabels[keyResult.state]}。授权确认本身不代表任何款项已安排或结清。</p>}{notFound && independent && !keyResult && <><label className="zy-check"><input type="checkbox" disabled={busy || checking} checked={replayConsent} onChange={(event) => setReplayConsent(event.target.checked)} />本人仅沿原键与完整原审阅继续提交这一份授权。</label><button className="zy-secondary" disabled={busy || checking || !replayConsent} onClick={() => { void replay(); }}>沿原键继续保存原授权</button></>}</div>}
    {unresolved && <p>最新原授权尚未核实，不能自动新建另一份授权。</p>}
    {stateIntent && <div aria-label="原授权状态请求恢复"><p>正在核对原“{actionLabels[stateIntent.action]}”请求的当前状态，原版本与动作已保留。</p><button className="zy-secondary" disabled={busy} onClick={() => setRecovery((v) => v + 1)}>读取原状态请求结果</button>{stateRetryReady && <button className="zy-secondary" disabled={busy} onClick={() => { void retryState(); }}>明确沿原版本重试{actionLabels[stateIntent.action]}</button>}</div>}
    <div aria-label="当前本人持续授权">{grants.length === 0 && !loading && !loadError && <p>当前没有已保存的本人持续授权。</p>}{grants.map((view, i) => <article className="zy-template-review" key={view.grant.id}><h4>第 {i + 1} 份 · {nativeFamilyLabels[view.grant.terms.family]} · {Date.now() >= Date.parse(view.grant.terms.expires_at) ? '已到期' : stateLabels[view.state]}</h4>{describe(view.grant.terms)}<p>本人原确认：{timestamp(view.grant.confirmed_at)}；当前状态核对时间：{timestamp(view.state_at)}。</p><label className="zy-check"><input type="checkbox" disabled={busy || !!stateIntent || view.state === 'REVOKED'} checked={stateChecks.includes(view.grant.id)} onChange={(event) => setStateChecks(toggle(stateChecks, view.grant.id, event.target.checked))} />本人明确调整第 {i + 1} 份授权的后续执行状态。</label>{(view.state === 'ACTIVE' ? ['PAUSE', 'REVOKE'] as const : view.state === 'PAUSED' ? ['RESUME', 'REVOKE'] as const : []).map((action) => <button className="zy-secondary" key={action} disabled={busy || !!stateIntent || !stateChecks.includes(view.grant.id) || (action === 'RESUME' && Date.now() >= Date.parse(view.grant.terms.expires_at))} onClick={() => { void changeState(view, action); }}>{actionLabels[action]}第 {i + 1} 份授权</button>)}</article>)}</div>
    <p>暂停、撤销或到期只限制后续未签名安排。已经签名的 UNKNOWN、待结清操作继续保留原预留、原键与原回执恢复，不能据此判断无效果。</p>{busy && <p role="status">正在保存本人原请求或核对当前状态…</p>}{notice && <p role="status">{notice}</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
