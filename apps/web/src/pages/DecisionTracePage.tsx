import DecisionSearchPanel from '../components/DecisionSearchPanel';
import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { getDecision, getDecisions, getOriginalTraceResponse, getTraceReceipt, hasUnsafeDeclarationNumbers, isRunId } from '../api/decisions';
import type { ActionLink, TraceResponse } from '../api/decisions';
import { ApiError, errorMessage } from '../api/http';
import { formatMoneyCents } from '../features/money';
import { object } from '../features/policy-form';

const completeness: Record<string, string> = { COMPLETE: '冻结记录完整', LEGACY_PARTIAL: '历史仅保存部分内容', UNSUPPORTED_VERSION: '当前版本不能完整解释' };
const phaseNames: Record<string, string> = { EVALUATION: '只读评估', PREPARE: '动作准备', CONFIRM: '确认', RESERVE: '资金预留', BANK_ACCEPT: '模拟银行受理', RECOVERY_PLAN: '恢复规划', CONTRACT_SETTLEMENT: '合约结算' };
const levels: Record<string, string> = { AUTO_EXECUTE: '可自主执行', ASK_ONCE: '需要一次具体确认', ADVISE_ONLY: '仅提供建议', BLOCKED: '已阻止' };
const auditNames: Record<string, string> = { VALID: '已核验', INTEGRITY_ERROR: '完整性错误', UNSUPPORTED_VERSION: '版本不支持', LEGACY_UNAUDITED: '历史记录未建立审计链', INCOMPLETE: '核验范围不完整' };
const evidenceNames: Record<string, string> = { BANK_CONFIRMED: '银行确认事实', BANK_OBSERVED: '银行观察事实', USER_DECLARED: '用户声明', MODEL_INFERRED: '模型推断', USER_CONFIRMED_POLICY: '用户确认策略', USER_CONFIRMED_ACTION: '用户确认动作' };
const collectionNames: Record<string, string> = { constraints: '约束', candidates: '候选', sources: '证据', policies: '策略', reasons: '原因', actions: '关联动作', current_references: '当前关联状态' };
const fieldNames: Record<string, string> = { cash_cents: '现金', safe_idle_cents: '安全闲置', minimum_margin_cents: '最低余量', margin_cents: '余量', protected_cents_by_reason: '保护分层',
  amount_cents: '金额', amount_options_cents: '候选金额', principal_cents: '本金', fee_cents: '费用', loss_cents: '损失', retained_cash_cents: '保留现金', suggested_cents: '建议金额',
  required_cents: '需求', available_cents: '可用金额', deficit_cents: '缺口', max_allocatable_cents: '最大可配置金额', financial_cap_cents: '财务上限', net_simulated_yield_cents: '净模拟收益',
  snapshot: '冻结快照', positions: '持仓', products: '产品', versions: '版本', source_issues: '来源问题', autonomy_boundary: '自主权限边界输入', asset_boundary: '资产边界输入',
  planning: '规划输入', validation: '校验结果', baseline: '原资金边界', projected: '预计资金边界', reservation_adjusted: '扣除预留后的边界', decision: '自主决策', recovery: '恢复结果',
  level: '自主等级', financial_evaluation: '财务评估', confirmation_required: '需要确认', confirmation_satisfied: '已确认', reasons: '原因', exit_plan: '退出计划', principal_available_at: '本金可用时点' };
const label = (code: string, names: Record<string, string>) => names[code] ? `${names[code]} · ${code}` : code;
function Money({ cents }: { cents: number | null | undefined }) { return <span className="money">{cents === undefined ? '未记录' : cents === null ? '待核验' : `¥${formatMoneyCents(cents)}`}</span>; }
function Section({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return <section className="trace-section card" aria-label={`${number} ${title}`}><h3><span className="trace-step">{number}</span>{title}</h3>{children}</section>;
}
function Literal({ value }: { value: unknown }) {
  const [limit, setLimit] = useState(800); const rendered = value === null ? 'null' : String(value);
  return <><span className="trace-literal">{rendered.slice(0, limit)}</span>{rendered.length > limit && <><p className="caption">原文已显示 {limit} / {rendered.length} 字符</p><button onClick={() => setLimit(limit + 800)}>继续显示原文字符</button></>}</>;
}
function SavedFields({ value, path, raw = false, focusPath = '', moneyMap = false }: { value: unknown; path: string; raw?: boolean; focusPath?: string; moneyMap?: boolean }) {
  const [limit, setLimit] = useState(20);
  const entries: [string, unknown][] = Array.isArray(value) ? value.map((child, i) => [String(i), child]) : object(value) ? Object.entries(value) : [];
  if (!Array.isArray(value) && !object(value)) return <Literal value={value} />;
  const focusEntry = entries.findIndex(([key]) => {
    const childPath = `${path}${Array.isArray(value) ? `[${key}]` : `.${key}`}`;
    return focusPath === childPath || focusPath.startsWith(`${childPath}.`) || focusPath.startsWith(`${childPath}[`);
  });
  const visible = entries.map(([key, child], index) => ({ key, child, index })).filter(({ index }) => index < limit || index === focusEntry);
  return <div className={`trace-fields ${raw ? 'trace-raw' : ''}`}><dl>{visible.map(({ key, child }) => {
    const childPath = `${path}${Array.isArray(value) ? `[${key}]` : `.${key}`}`;
    const monetary = !raw && (moneyMap || key.endsWith('_cents')) && key !== 'amount_options_cents';
    return <div key={key}><dt>{raw ? key : fieldNames[key] ?? key}</dt><dd>
      {monetary ? <Money cents={child as number | null} /> : object(child) || Array.isArray(child)
        ? <SavedBranch title={`${raw ? key : fieldNames[key] ?? key} · ${Array.isArray(child) ? child.length : Object.keys(child).length} 项`} value={child} path={childPath} raw={raw} focusPath={focusPath} moneyMap={!raw && (key.includes('_cents_by_') || key === 'amount_options_cents')} />
        : child === null && !raw ? key === 'amount_options_cents' || key.includes('_cents_by_') ? '待核验' : '未记录' : typeof child === 'boolean' && !raw ? child ? '是' : '否' : <Literal value={child} />}
      <span className="caption trace-path">{childPath}</span></dd></div>;
  })}</dl>{entries.length > limit && <><p className="caption">已展示前 {Math.min(limit, entries.length)} / {entries.length} 项{focusEntry >= limit ? `，另定位第 ${focusEntry + 1} 项` : ''}；其余内容按需展开。</p><button onClick={() => setLimit(limit + 20)}>继续显示字段</button></>}
    {entries.length === 0 && <p className="caption">该分支没有保存字段。</p>}</div>;
}
function SavedBranch({ title, value, path, raw = false, focusPath = '', moneyMap = false }: { title: string; value: unknown; path: string; raw?: boolean; focusPath?: string; moneyMap?: boolean }) {
  const [open, setOpen] = useState(false); const forced = !!focusPath && (focusPath === path || focusPath.startsWith(`${path}.`) || focusPath.startsWith(`${path}[`));
  return <details open={open || forced} onToggle={(event) => setOpen(event.currentTarget.open)}><summary>{title}</summary>
    {(open || forced) && <>{raw && <p className="notice">未核验声明的解析字段，非完整原响应文本。声明金额未作为可信计算金额；内容哈希一致也不表示事实成立或拥有资金权限。</p>}
      <SavedFields value={value} path={path} raw={raw} focusPath={focusPath} moneyMap={moneyMap} /></>}</details>;
}
function Records<T>({ items, prefix, focusPath, render }: { items: T[]; prefix: string; focusPath: string; render: (item: T, index: number) => ReactNode }) {
  const [limit, setLimit] = useState(20); const match = focusPath.match(new RegExp(`^${prefix}\\[(\\d+)\\]`)); const target = match ? Number(match[1]) : -1;
  const visible = items.map((item, index) => ({ item, index })).filter(({ index }) => index < limit || index === target);
  return <><p className="caption">已展示前 {Math.min(limit, items.length)} / {items.length} 项{target >= limit && target < items.length ? `，另定位第 ${target + 1} 项` : ''}；这是所选阶段保存的集合。</p>
    {visible.map(({ item, index }) => <div key={index}>{render(item, index)}</div>)}
    {limit < items.length && <button onClick={() => setLimit(limit + 20)}>继续显示{collectionNames[prefix] ?? '条目'}</button>}</>;
}
function Receipt({ link }: { link: ActionLink }) {
  const [enabled, setEnabled] = useState(false);
  const query = useQuery({ queryKey: ['trace-receipt', link.action_id, link.bank_operation_id, link.receipt_id], queryFn: () => getTraceReceipt(link), enabled, retry: false });
  return <article className="trace-item"><h4>原动作 {link.action_id}</h4><p>当前动作状态 {link.status} · 模拟银行状态 {link.bank_status ?? '未关联'} · 回执状态 {link.receipt_status ?? '尚无已对账回执'}</p>
    <p className="caption">原决策记录 {link.decision_run_id} · 原银行操作 {link.bank_operation_id ?? '未关联'} · 回执编号 {link.receipt_id ?? '未关联'}</p>
    <p className="caption trace-hash">请求哈希 {link.request_hash}</p>
    {link.status === 'UNKNOWN' || link.bank_status === 'UNKNOWN' ? <p className="notice">原操作仍未完成核验，不能认定成功，也不能通过新扣款补齐。</p> : null}
    <button disabled={query.isFetching} onClick={() => { setEnabled(true); if (enabled) void query.refetch(); }}>{enabled ? '重读原动作回执' : '读取原动作回执'}</button>
    {enabled && query.isPending && <p role="status">正在读取原动作回执…</p>}
    {query.isError && <p role="alert">{errorMessage(query.error)} · {query.error instanceof ApiError ? query.error.code : 'INVALID_RESPONSE'}；回执核验仍未完成。</p>}
    {!query.isError && query.data && <section aria-label={`回执 ${query.data.receipt_id}`} className="state-review"><p>已读取同原操作回执 · {query.data.status}</p>
      <dl className="goal-metrics"><div><dt>执行金额</dt><dd><Money cents={query.data.executed_cents} /></dd></div><div><dt>费用</dt><dd><Money cents={query.data.fee_cents} /></dd></div><div><dt>损失</dt><dd><Money cents={query.data.loss_cents} /></dd></div></dl>
      <p>经济发生时点 {query.data.occurred_at}</p><p>对账时点 {query.data.reconciled_at ?? '尚未对账'}</p>
      <SavedBranch title={`查看记账条目 · ${query.data.posting_ids.length} 项`} value={query.data.posting_ids} path="receipt.posting_ids" /></section>}
  </article>;
}
function referenceTarget(trace: TraceResponse['trace'], path: string): string | null {
  if (!trace || !/^(sources|policies|constraints|candidates|outcome|inputs)(?:\.[A-Za-z0-9_]+|\[\d+\])*$/.test(path)) return null;
  let saved: unknown = trace;
  for (const segment of path.match(/[A-Za-z0-9_]+/g) ?? []) {
    if ((!object(saved) && !Array.isArray(saved)) || !Object.hasOwn(saved, segment)) return null;
    saved = (saved as Record<string, unknown>)[segment];
  }
  const match = path.match(/^(sources|policies|constraints|candidates)\[(\d+)\]/);
  if (match && trace) {
    const collection = match[1] as 'sources' | 'policies' | 'constraints' | 'candidates';
    const items = trace[collection] ?? [];
    if (Number(match[2]) >= items.length) return null;
    return `trace-${{ sources: 'evidence', policies: 'policy', constraints: 'constraint', candidates: 'candidate' }[collection]}-${match[2]}`;
  }
  return /^(outcome|inputs)(?:\.|$)/.test(path) ? `trace-${path.startsWith('inputs') ? 'inputs' : 'outcome'}` : null;
}
function OriginalResponse({ data }: { data: TraceResponse }) {
  const [open, setOpen] = useState(false); const [url, setUrl] = useState(''); const original = getOriginalTraceResponse(data);
  useEffect(() => {
    if (!open || !original || !URL.createObjectURL) return;
    const resource = URL.createObjectURL(new Blob([original], { type: 'application/json;charset=utf-8' })); setUrl(resource);
    return () => URL.revokeObjectURL(resource);
  }, [open, original]);
  return <div>{hasUnsafeDeclarationNumbers(data) && <p className="notice">声明含不能用 JavaScript 精确保真的数字，解析字段可能已舍入。请核对原响应文本的数字词法，不能将解析值作为精确金额。</p>}
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}><summary>查看原响应文本（保留原始数字词法）</summary>
      {open && <>{original === null ? <p>本次读取未保留原响应文本。</p> : <><p className="caption">服务原响应，含未核验声明。仅分段展示文字，不据此计算金额。</p>
        {url && <a href={url} download={`decision-${data.run_id}.json`}>下载所选记录原响应文本</a>}<Literal value={original} /></>}</>}</details>
  </div>;
}
function TraceDetail({ data }: { data: TraceResponse }) {
  const [focusPath, setFocusPath] = useState(''); const focusSerial = useRef(0); const [serial, setSerial] = useState(0); const host = useRef<HTMLDivElement>(null);
  const trace = data.trace; const supported = data.completeness === 'COMPLETE' && !!trace;
  function locate(path: string) { setFocusPath(path); setSerial(++focusSerial.current); }
  useEffect(() => { if (!serial) return; const id = referenceTarget(trace, focusPath); if (!id) return; const target = host.current?.querySelector<HTMLElement>(`#${id}`); target?.focus(); target?.scrollIntoView?.({ block: 'center', behavior: 'smooth' }); }, [focusPath, serial, trace]);
  const changed = data.current_references.filter((item) => item.status !== 'UNCHANGED');
  return <div ref={host} className="trace-detail"><div className="trace-summary state-review"><h3>{completeness[data.completeness]}</h3><p>所选记录 {data.run_id}</p>
    <p>决策依据时点 {data.as_of}</p><p>当前关联读取时点 {data.read_at}</p>
    <p className="caption">当时冻结的事实、策略版本与结论按原记录展示。当前关联动作和来源变化另列，不能替换当时依据。</p>
    {trace && <p>阶段 {label(trace.phase, phaseNames)}</p>}
    <OriginalResponse data={data} />
    <div className="button-row">{trace?.parent_run_id && <a href={`#decisions/${trace.parent_run_id}`}>查看父阶段 {trace.parent_run_id}</a>}{data.children.map((id) => <a href={`#decisions/${id}`} key={id}>查看子阶段 {id}</a>)}</div>
  </div>
    {!supported && <div className="notice"><p>{data.completeness === 'LEGACY_PARTIAL' ? '未保存完整冻结轨迹，不能补造当时事实、约束或自主结论。' : '该算法版本当前不能完整解释。已保存原文仅供核对，不宣称八层结论已核验。'}</p>
      <SavedBranch title="查看旧记录原文" value={{ legacy_snapshot: data.legacy_snapshot ?? null, legacy_result: data.legacy_result ?? null, saved_trace: data.trace ?? null }} path="legacy" raw /></div>}
    {supported && <>
      <Section number={1} title="银行事实与证据"><p className="caption">银行事实、用户声明和模型推断分别标注。以下来源均是当时冻结副本。</p>
        <Records items={trace.sources ?? []} prefix="sources" focusPath={focusPath} render={(item, index) => <article className="trace-item" id={`trace-evidence-${index}`} tabIndex={-1}>
          <h4>{label(item.evidence_level, evidenceNames)} · {item.source_type}</h4><p>当时状态 {item.status_at_decision} · 声明内容完整性 {item.content_integrity}</p>
          <p className="caption">证据 {item.id} · 来源 {item.source_ref}</p><p className="caption">观察 {item.observed_at} · 生效 {item.valid_from} · 结束 {item.valid_to ?? '未记录'}</p>
          {item.supersedes_evidence_id && <p className="caption">替代证据 {item.supersedes_evidence_id}</p>}
          <SavedBranch title="查看证据声明原文" value={item.content} path={`sources[${index}].content`} raw focusPath={focusPath} />
          <SavedBranch title="查看证据声明哈希" value={{ claimed: item.content_hash, captured: item.captured_content_hash }} path={`sources[${index}].hashes`} /></article>} />
        <div id="trace-inputs" tabIndex={-1}><SavedBranch title="查看当时冻结计算输入" value={trace.inputs} path="inputs" focusPath={focusPath} /></div>
        {changed.length > 0 && <details><summary>当前关联状态 · {changed.map((item) => item.current_status ?? 'MISSING').join(' / ')}</summary><p className="caption">以下是读取时点的变化，不覆盖上述当时状态。</p>
          <Records items={changed} prefix="current_references" focusPath="" render={(item) => <p>{item.entity_type} {item.entity_id} · 原状态 {item.original_status} → 当前 {item.current_status ?? '缺失'} · {item.status}</p>} /></details>}
      </Section>
      <Section number={2} title="当时用户策略"><p className="caption">使用当时确认的版本；配置声明完整性不等同于资金授权。</p>
        <Records items={trace.policies ?? []} prefix="policies" focusPath={focusPath} render={(item, index) => <article className="trace-item" id={`trace-policy-${index}`} tabIndex={-1}><h4>策略版本 {item.version_number} · 当时 {item.status_at_decision}</h4>
          <p className="caption">策略 {item.policy_id} · 版本 {item.id}</p><p>配置声明完整性 {item.configuration_integrity}</p><p className="caption">确认 {item.confirmed_at ?? '未记录'} · 生效 {item.valid_from ?? '未记录'} · 结束时点 {item.valid_to ?? '未记录'}</p>
          <SavedBranch title="查看当时配置声明" value={item.configuration} path={`policies[${index}].configuration`} raw focusPath={focusPath} />
          <SavedBranch title="查看配置声明哈希" value={{ claimed: item.configuration_hash, captured: item.captured_configuration_hash }} path={`policies[${index}].hashes`} /></article>} /></Section>
      <Section number={3} title="受保护金额与约束"><p className="caption">金额来自保存的约束与计算点；预计边界、预留后边界和阶段沿原键区分，页面不重新计算。</p>
        <Records items={trace.constraints ?? []} prefix="constraints" focusPath={focusPath} render={(item, index) => <article className="trace-item" id={`trace-constraint-${index}`} tabIndex={-1}><h4>{item.constraint_key}</h4>
          <p>{item.is_hard ? '硬约束' : '软约束'} · {item.satisfied == null ? '满足情况待核验' : item.satisfied ? '满足' : '未满足'} · 原因 {item.reason_code}</p>
          <dl className="goal-metrics"><div><dt>需求</dt><dd><Money cents={item.required_cents} /></dd></div><div><dt>可用金额</dt><dd><Money cents={item.available_cents} /></dd></div><div><dt>到期日期</dt><dd>{item.due_date ?? '未记录'}</dd></div></dl>
          {item.policy_version_id && <p className="caption">当时策略版本 {item.policy_version_id}</p>}
          <SavedBranch title="查看该约束计算点" value={item.calculation ?? {}} path={`constraints[${index}].calculation`} focusPath={focusPath} /></article>} /></Section>
      <Section number={4} title="候选动作"><p className="caption">展示该阶段实际保存的候选。需要确认或仅建议不等于被拒绝。</p>
        {!(trace.candidates ?? []).length && <p>该阶段未保存候选集合。</p>}
        <Records items={trace.candidates ?? []} prefix="candidates" focusPath={focusPath} render={(item, index) => <article className="trace-item" id={`trace-candidate-${index}`} tabIndex={-1}><h4>{item.candidate_key}</h4><p>类型 {item.kind} · 状态 {label(item.status, levels)}</p>
          {(item.reasons ?? []).map((reason, i) => <p key={i}>原原因代码 {reason}</p>)}
          <SavedBranch title="查看候选输入" value={item.inputs ?? {}} path={`candidates[${index}].inputs`} focusPath={focusPath} />
          <SavedBranch title="查看候选计算结果" value={item.result ?? {}} path={`candidates[${index}].result`} focusPath={focusPath} /></article>} /></Section>
      <Section number={5} title="拒绝与限制原因"><p className="caption">说明由服务端根据原记录提供；未知原因保留原代码。引用可定位到本页保存分支。</p>
        {!data.explanation?.reasons.length && <p>该阶段未保存原因说明。</p>}
        <Records items={data.explanation?.reasons ?? []} prefix="reasons" focusPath="" render={(reason) => <article className="trace-item"><h4>{reason.code}</h4><p>{reason.text}</p><div className="button-row">{reason.references.map((path, i) => referenceTarget(trace, path)
          ? <button key={i} onClick={() => locate(path)}>定位 {path}</button> : <span className="caption" key={i}>原引用路径 {path}（未映射到已保存分支）</span>)}</div></article>} /></Section>
      <Section number={6} title="当时自主等级"><p>当时等级 {data.explanation?.level ? label(data.explanation.level, levels) : '未记录'}</p><p>当时财务评估 {data.explanation?.financial_evaluation ?? '未记录'}</p>
        <p>需要具体确认：{data.explanation?.confirmation_required == null ? '未记录' : data.explanation.confirmation_required ? '是' : '否'} · 当时已确认：{data.explanation?.confirmation_satisfied == null ? '未记录' : data.explanation.confirmation_satisfied ? '是' : '否'}</p>
        {(data.explanation?.summary ?? []).map((line, index) => <p key={index}>{line}</p>)}
        <p className="caption">记录完整或记录状态 SUCCEEDED 不表示资金执行成功。财务结论不能代替策略权限和原操作核验。</p>
        <div id="trace-outcome" tabIndex={-1}><SavedBranch title="查看当时保存的完整结果分支" value={trace.outcome} path="outcome" focusPath={focusPath} /></div></Section>
    </>}
    <Section number={7} title="当前关联动作与回执"><p className="caption">以下关联在 {data.read_at} 读取并核对，可能晚于所选决策。经济发生与对账时点见原回执；不能用当前成功状态替换当时自主等级。</p>
      {!data.actions.length && <p>该记录没有关联执行动作；只读评估不产生资金回执。</p>}
      <Records items={data.actions} prefix="actions" focusPath="" render={(link) => <Receipt key={`${link.action_id}:${link.receipt_id}`} link={link} />} /></Section>
    <Section number={8} title="审计哈希状态"><p>所选记录审计链：{auditNames[data.audit_chain_status]} · {data.audit_chain_status}</p>
      <p className="caption">这是所选 run 的服务端核验结果；历史声明完整性、冻结轨迹完整性与资金执行结果分别展示。当前接口未返回锚定事件或完整诊断，不能据此扩称全部历史已核验。</p>
      {trace && <><p className="trace-hash">冻结输入哈希 {trace.input_hash}</p><p className="trace-hash">冻结轨迹哈希 {trace.trace_hash}</p><SavedBranch title="查看录制算法版本" value={trace.algorithm_versions} path="algorithm_versions" /></>}</Section>
  </div>;
}
function DecisionList() {
  const query = useInfiniteQuery({ queryKey: ['decision-list'], queryFn: ({ pageParam }) => getDecisions(pageParam), initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined, retry: false });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const searchOwner = query.isError ? undefined : query.data?.pages[0]?.user_id;
  return <><div className="page-intro"><div><p className="eyebrow">DECISION TRACE</p><h2>决策轨迹</h2><p>从当时依据到当前回执，逐层查看保存的决策。</p></div><button disabled={query.isFetching} onClick={() => void query.refetch()}>刷新记录列表</button></div>
    <p className="caption">记录状态与资金执行结果分别展示。列表按接口保存时点排序，游标分页；已展示数量不代表全部历史记录。</p>
    <DecisionSearchPanel key={searchOwner ?? 'unknown-owner'} ownerUserId={searchOwner} />
    {query.isPending && <p role="status">正在读取决策记录…</p>}{query.isError && <p role="alert">{errorMessage(query.error)} <button onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>重试读取列表</button></p>}
    {(!query.isError || query.isFetchNextPageError) && query.data && <><p className="caption">已读取 {items.length} 条{query.hasNextPage ? '，还有下一页' : '，当前分页范围读取完毕'}。</p>
      {!items.length && <p className="empty">暂无保存的决策轨迹。</p>}<div className="trace-list">{items.map((item, index) => <article key={`${item.run_id}:${index}`} className="card trace-item"><h3>{item.phase ? label(item.phase, phaseNames) : '阶段未记录'}</h3><p>{item.as_of}</p><p>触发 {item.trigger_type} · 记录状态 {item.status}</p><p>{completeness[item.completeness]}</p>
        <a href={`#decisions/${item.run_id}`}>查看 {item.run_id}</a>{item.parent_run_id && <p className="caption">父阶段 {item.parent_run_id}</p>}{item.action_id && <p className="caption">原动作 {item.action_id}</p>}</article>)}</div>
      {query.hasNextPage && <button disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>加载下一页</button>}</>}
  </>;
}
function DecisionRun({ runId }: { runId: string }) {
  const query = useQuery({ queryKey: ['decision', runId], queryFn: () => getDecision(runId), retry: false, enabled: isRunId(runId), structuralSharing: false });
  return <><div className="page-intro"><div><p className="eyebrow">HISTORICAL DECISION</p><h2>一次决策的八层依据</h2><a href="#decisions">返回决策列表</a></div><button disabled={query.isFetching || !isRunId(runId)} onClick={() => void query.refetch()}>刷新所选记录</button></div>
    {!isRunId(runId) ? <p role="alert">决策编号格式无效，请从真实记录列表进入。</p> : <>{query.isPending && <p role="status">正在读取并核验所选记录…</p>}
      {query.isError && <div role="alert">{errorMessage(query.error)} · {query.error instanceof ApiError ? `${query.error.status} ${query.error.code}` : 'INVALID_RESPONSE'}<p>本次读取未完成，历史成功面板已隐藏。</p><button onClick={() => void query.refetch()}>重试读取</button></div>}
      {!query.isError && query.data && <TraceDetail key={runId} data={query.data} />}</>}
  </>;
}
export default function DecisionTracePage({ runId }: { runId?: string }) { return runId === undefined ? <DecisionList /> : <DecisionRun key={runId} runId={runId} />; }
