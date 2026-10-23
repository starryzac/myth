import { useEffect, useId, useRef, useState } from 'react';
import type { Dashboard } from './api';
import type { TemplatePolicyView } from './template-api';
import { assetClasses, assetLabels, editYuan, interventionLabels, interventionReasons, mandatoryInterventions, templateLabels, templateYuan } from './template-schema';
import type { TemplateConfiguration, TemplateName } from './template-schema';

type Option = { value: string; label: string };
type Field = { path: string; label: string; kind: 'money' | 'integer' | 'text' | 'date' | 'check' | 'select' | 'multi' | 'words'; options?: Option[]; min?: number; max?: number; optional?: boolean; locked?: string[] };
const option = (value: string, label: string): Option => ({ value, label });
function naturalDate(asOf: string): string { const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(asOf)); return ['year', 'month', 'day'].map((kind) => parts.find((part) => part.type === kind)!.value).join('-'); }

export function defaultTemplate(name: TemplateName, data: Dashboard): TemplateConfiguration {
  const today = naturalDate(data.as_of), end = new Date(Date.UTC(Number(today.slice(0, 4)) + 1, Number(today.slice(5, 7)) - 1, Number(today.slice(8)))).toISOString().slice(0, 10);
  const priority = { importance: 50, minimum_cents: '0', reducible: false, deferrable: false };
  const contribution = { min_cents: '0', target_cents: '10000', max_cents: '10000' };
  switch (name) {
    case 'RecurringObligationPolicy': return { type: 'recurring_obligation', payee_id: '', amount_rule: { kind: 'exact', amount_cents: '10000' }, due_day: 1, prepare_days_before: 0, auto_execute: false, priority };
    case 'LivingReservePolicy': return { type: 'living_reserve', horizon_days: 30, method: { name: 'rolling_window_quantile', lookback_days: 90, quantile: '0.9', essential_categories: ['餐饮', '交通'], exclude_one_off: true }, extra_buffer_cents: '0', reconfirm_on_boundary_crossing: true };
    case 'EmergencyBufferPolicy': return { type: 'emergency_buffer', amount_cents: '10000' };
    case 'DatedExpensePolicy': return { type: 'dated_expense', window: { start: today, end: today }, amount: contribution, priority, must_not_reduce_policy_ids: [] };
    case 'LongTermGoalPolicy': return { type: 'long_term_goal', target_cents: data.goals[0]?.target_cents ?? '10000', deadline: end, monthly_contribution: contribution, importance: 50, minimum_guarantee_cents: '0', allow_partial: true, allow_deferral: false, deferral_cost_cents_per_day: '0', cross_goal_reallocation_allowed: false };
    case 'PeriodicTransferPolicy': return { type: 'periodic_transfer', source_account_id: data.accounts[0]?.id ?? '', payee_id: '', amount_rule: { kind: 'exact', amount_cents: '10000' }, due_day: 1, prepare_days_before: 0, single_action_cap_cents: '10000', auto_execute: false };
    case 'AssetAuthorizationPolicy': return { type: 'asset_authorization', scope: 'general_idle_funds', allowed_asset_classes: ['CASH'], max_auto_managed_cents: '10000', single_action_cap_cents: '10000', max_redemption_delay_days: 0, max_lock_days: 0, max_principal_risk_level: 0, allow_auto_recovery_without_penalty: false, allow_early_withdrawal_with_penalty: false };
    case 'RecoveryPolicy': return { type: 'recovery', scope: 'general_idle_funds', asset_policy_id: '', triggers: ['BOUNDARY_SHRINK'], single_action_cap_cents: '10000', max_redemption_delay_days: 0, allow_auto_recovery_without_penalty: true, max_fee_cents: '0', max_loss_cents: '0' };
    case 'GoalAllocationPolicy': return { type: 'goal_allocation', goal_ids: [], method: 'lexicographic_v1', funds_scope: 'NEW_UNASSIGNED_SAFE_FUNDS', max_single_allocation_cents: '10000' };
    case 'CrossGoalReallocationPolicy': return { type: 'cross_goal_reallocation', scope_version: 'GOAL_TRANSFER_V1', destination_scope: 'GOAL', enabled: false, source_goal_ids: [], target_goal_ids: [], single_action_cap_cents: '10000', total_cap_cents: '10000', valid_from: today, valid_until: end };
    case 'SeasonalReservePolicy': return { type: 'seasonal_reserve', holiday_code: '', window: { start: today, end: today }, lookback_days: 365, minimum_historical_windows: 1, quantile: '0.9', essential_categories: ['节庆必要支出'], adjustment_cap_cents: '10000', requires_confirmation: true, advice_only: true };
    case 'InterventionPolicy': return { type: 'intervention', must_ask_on: [...mandatoryInterventions], deduplicate_by_boundary_event: true, minimum_reask_interval_seconds: 0, safety_events_bypass_throttle: true, silent_when_action_set_unchanged: true };
  }
}

function readPath(value: unknown, path: string): unknown { return path.split('.').reduce<unknown>((current, key) => current && typeof current === 'object' ? (current as Record<string, unknown>)[key] : undefined, value); }
function replacePath(config: TemplateConfiguration, path: string, value: unknown): TemplateConfiguration {
  const copy = structuredClone(config) as unknown as Record<string, unknown>; const keys = path.split('.'); let cursor = copy;
  for (const key of keys.slice(0, -1)) cursor = cursor[key] as Record<string, unknown>;
  if (value === undefined) delete cursor[keys.at(-1)!]; else cursor[keys.at(-1)!] = value;
  return copy as unknown as TemplateConfiguration;
}

function MoneyInput({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  const controlID = useId();
  const [text, setText] = useState(() => editYuan(value)); const last = useRef(value);
  useEffect(() => { if (value !== last.current) { last.current = value; setText(editYuan(value)); } }, [value]);
  return <label className="zy-field" htmlFor={controlID}>{label}（元）<input id={controlID} aria-label={`${label}（元）`} inputMode="decimal" required disabled={disabled} value={text} onChange={(event) => {
    setText(event.target.value);
    try { const parsed = templateYuan(event.target.value); event.target.setCustomValidity(''); last.current = parsed; onChange(parsed); }
    catch { event.target.setCustomValidity('请填写支持范围内、最多两位小数的人民币金额'); }
  }} /></label>;
}

function WordsInput({ label, value, disabled, onChange }: { label: string; value: string[]; disabled: boolean; onChange: (value: string[]) => void }) {
  const controlID = useId();
  const [text, setText] = useState(value.join('，')); const last = useRef(JSON.stringify(value));
  useEffect(() => { const encoded = JSON.stringify(value); if (encoded !== last.current) { last.current = encoded; setText(value.join('，')); } }, [value]);
  return <label className="zy-field" htmlFor={controlID}>{label}<input id={controlID} aria-label={label} value={text} maxLength={2000} required disabled={disabled} onChange={(event) => { setText(event.target.value); const values = event.target.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean); last.current = JSON.stringify(values); onChange(values); }} /></label>;
}

function fieldsFor(c: TemplateConfiguration, data: Dashboard, policies: TemplatePolicyView[]): Field[] {
  const accounts = data.accounts.map((a) => option(a.id, a.name)), goals = data.goals.map((g) => option(g.id, g.name));
  const refs = policies.filter((p) => p.version.current).map((p) => option(p.version.policy_id, `${p.version.compiled.configuration.name ?? templateLabels[p.version.compiled.template_name]} · 第${p.version.version}版`));
  const assetRefs = refs.filter((ref) => policies.some((p) => p.version.policy_id === ref.value && p.version.compiled.template_name === 'AssetAuthorizationPolicy'));
  const crossRefs = refs.filter((ref) => policies.some((p) => p.version.policy_id === ref.value && p.version.compiled.template_name === 'CrossGoalReallocationPolicy'));
  const money = (path: string, label: string): Field => ({ path, label, kind: 'money' });
  const integer = (path: string, label: string, max = 3650, min = 0): Field => ({ path, label, kind: 'integer', min, max });
  const check = (path: string, label: string): Field => ({ path, label, kind: 'check' });
  const multi = (path: string, label: string, options: Option[], locked?: string[]): Field => ({ path, label, kind: 'multi', options, locked });
  const priority = (prefix: string): Field[] => [integer(`${prefix}.importance`, '重要程度（0—100）', 100), money(`${prefix}.minimum_cents`, '不可降低的最低金额'), check(`${prefix}.reducible`, '允许降低这项期望金额'), check(`${prefix}.deferrable`, '允许协商延期')];
  const contribution = (prefix: string): Field[] => [money(`${prefix}.min_cents`, '最低金额'), money(`${prefix}.target_cents`, '期望金额'), money(`${prefix}.max_cents`, '最高金额')];
  const window: Field[] = [{ path: 'window.start', label: '支出窗口开始', kind: 'date' }, { path: 'window.end', label: '支出窗口结束', kind: 'date' }];
  const amountRule = (bill: boolean): Field[] => [{ path: 'amount_rule.kind', label: '金额依据', kind: 'select', options: [option('exact', '固定金额'), option('range', '确认的金额范围'), ...(bill ? [option('bill_balance', '当前已核实账单余额')] : [])] }, ...(readPath(c, 'amount_rule.kind') === 'bill_balance' ? [{ path: 'amount_rule.account_id', label: '账单所属账户', kind: 'select' as const, options: accounts }] : readPath(c, 'amount_rule.kind') === 'range' ? [money('amount_rule.min_cents', '最低支出'), money('amount_rule.max_cents', '最高支出')] : [money('amount_rule.amount_cents', '固定支出')])];
  const scoped: Field[] = [{ path: 'scope', label: '适用资金范围', kind: 'select', options: [option('general_idle_funds', '一般未归属闲置资金'), option('goal', '一个明确目标的已归属资金')] }, ...('scope' in c && c.scope === 'goal' ? [{ path: 'goal_id', label: '所绑定目标', kind: 'select' as const, options: goals }] : [])];
  switch (c.type) {
    case 'recurring_obligation': return [{ path: 'payee_id', label: '收款对象名称或登记代码', kind: 'text' }, ...amountRule(true), integer('due_day', '每月到期日（无此日时按月末）', 31, 1), integer('prepare_days_before', '提前准备天数'), check('auto_execute', '规则允许周期支付候选；实际执行仍需对应授权'), ...priority('priority')];
    case 'living_reserve': return [integer('horizon_days', '保护未来天数', 3650, 1), integer('method.lookback_days', '真实支出回看天数', 3650, 1), { path: 'method.quantile', label: '支出分位数（大于0且不超过1）', kind: 'text' }, { path: 'method.essential_categories', label: '必要支出类别（用逗号分隔）', kind: 'words' }, check('method.exclude_one_off', '排除已标记的一次性支出'), money('extra_buffer_cents', '额外保护金额'), check('reconfirm_on_boundary_crossing', '保护边界跨越时重新确认')];
    case 'emergency_buffer': return [money('amount_cents', '应急保护金额')];
    case 'dated_expense': return [...window, ...contribution('amount'), ...priority('priority'), multi('must_not_reduce_policy_ids', '不得削减的现有规则', refs)];
    case 'long_term_goal': return [money('target_cents', '目标总额'), { path: 'deadline', label: '目标期限', kind: 'date' }, ...contribution('monthly_contribution'), integer('importance', '目标重要程度（0—100）', 100), money('minimum_guarantee_cents', '最低保证金额'), check('allow_partial', '允许目标只完成一部分'), check('allow_deferral', '允许明确协商延期'), ...(c.allow_deferral ? [money('deferral_cost_cents_per_day', '每延期一日的代价')] : []), { path: 'asset_policy_id', label: '可选资产安排范围', kind: 'select', options: assetRefs, optional: true }, check('cross_goal_reallocation_allowed', '允许使用独立的跨目标调配规则'), ...(c.cross_goal_reallocation_allowed ? [{ path: 'cross_goal_reallocation_policy_id', label: '独立跨目标调配规则', kind: 'select' as const, options: crossRefs }] : [])];
    case 'periodic_transfer': return [{ path: 'source_account_id', label: '付款账户', kind: 'select', options: accounts }, { path: 'payee_id', label: '收款对象名称或登记代码', kind: 'text' }, ...amountRule(false), integer('due_day', '每月到期日（无此日时按月末）', 31, 1), integer('prepare_days_before', '提前准备天数'), money('single_action_cap_cents', '单次支付上限'), check('auto_execute', '规则允许周期支付候选；实际执行仍需对应授权')];
    case 'asset_authorization': return [...scoped, multi('allowed_asset_classes', '允许的资产类别', assetClasses.map((asset) => option(asset, assetLabels[asset]))), money('max_auto_managed_cents', '管理总额上限'), money('single_action_cap_cents', '单次安排上限'), integer('max_redemption_delay_days', '赎回到账最多等待天数', 3660), integer('max_lock_days', '最多锁定天数', 3660), integer('max_principal_risk_level', '最高本金风险等级', 5), check('allow_auto_recovery_without_penalty', '允许在独立执行权限内无损回收'), check('allow_early_withdrawal_with_penalty', '允许提前取回产生已明确的费用或损失')];
    case 'recovery': return [...scoped, { path: 'asset_policy_id', label: '所依据的资产安排规则', kind: 'select', options: assetRefs }, multi('triggers', '允许回收的触发条件', [option('BOUNDARY_SHRINK', '保护边界收紧'), option('AUTHORIZATION_REVOKED', '原授权撤销'), option('POLICY_EXPIRED', '规则到期'), option('LIQUIDITY_SHORTFALL', '流动性不足')]), money('single_action_cap_cents', '单次回收上限'), integer('max_redemption_delay_days', '回收到账最多等待天数', 3660), check('allow_auto_recovery_without_penalty', '仅在独立执行权限内自动无损回收'), money('max_fee_cents', '允许费用上限'), money('max_loss_cents', '允许本金损失上限')];
    case 'goal_allocation': return [multi('goal_ids', '参与联合分配的目标（至少两项）', goals), money('max_single_allocation_cents', '单次分配总额上限')];
    case 'cross_goal_reallocation': return [{ path: 'destination_scope', label: '接收资金的范围', kind: 'select', options: [option('GOAL', '明确的另一个目标'), option('PROTECTED_CASH', '生活现金保护缺口')] }, check('enabled', '启用这项独立调配规则'), multi('source_goal_ids', '允许释放资金的目标', goals), ...(c.destination_scope === 'GOAL' ? [multi('target_goal_ids', '允许接收资金的目标', goals), multi('must_not_reduce_policy_ids', '不得削减的现有规则', refs)] : [multi('emergency_conditions', '允许响应的现金保护缺口', [option('HARD_OBLIGATION_SHORTFALL', '已确认支出缺口'), option('LIVING_RESERVE_SHORTFALL', '生活费保护缺口'), option('EMERGENCY_BUFFER_SHORTFALL', '应急备用金缺口')])]), money('single_action_cap_cents', '单次调配上限'), money('total_cap_cents', '整个规则周期累计上限')];
    case 'seasonal_reserve': return [{ path: 'holiday_code', label: '节庆或已登记场景名称', kind: 'text' }, ...window, integer('lookback_days', '真实历史回看天数', 3650, 1), integer('minimum_historical_windows', '至少需要的真实历史窗口数', 10, 1), { path: 'quantile', label: '历史分位数（大于0且不超过1）', kind: 'text' }, { path: 'essential_categories', label: '必要支出类别（用逗号分隔）', kind: 'words' }, money('adjustment_cap_cents', '建议调整上限')];
    case 'intervention': return [multi('must_ask_on', '需要向我确认的情形（安全项保留）', interventionReasons.map((reason) => option(reason, interventionLabels[reason])), [...mandatoryInterventions]), integer('minimum_reask_interval_seconds', '同一普通事件最小再问间隔（秒）', 86400)];
  }
}

export default function TemplateEditor({ configuration, onChange, dashboard, policies, disabled = false }: { configuration: TemplateConfiguration; onChange: (value: TemplateConfiguration) => void; dashboard: Dashboard; policies: TemplatePolicyView[]; disabled?: boolean }) {
  const editorID = useId();
  const fields: Field[] = [{ path: 'name', label: '规则名称（可选）', kind: 'text', optional: true }, { path: 'valid_from', label: '生效日期（可选）', kind: 'date', optional: true }, { path: 'valid_until', label: '最后有效日期（含当日，可选）', kind: 'date', optional: true }, ...fieldsFor(configuration, dashboard, policies)];
  function change(path: string, value: unknown) {
    let next = replacePath(configuration, path, value);
    if (path === 'amount_rule.kind') next = replacePath(next, 'amount_rule', value === 'bill_balance' ? { kind: value, account_id: dashboard.accounts[0]?.id ?? '' } : value === 'range' ? { kind: value, min_cents: '0', max_cents: '10000' } : { kind: 'exact', amount_cents: '10000' });
    if (path === 'scope') next = replacePath(next, 'goal_id', value === 'goal' ? dashboard.goals[0]?.id ?? '' : undefined);
    if (path === 'allow_deferral' && !value) next = replacePath(next, 'deferral_cost_cents_per_day', '0');
    if (path === 'cross_goal_reallocation_allowed') next = replacePath(next, 'cross_goal_reallocation_policy_id', value ? '' : undefined);
    if (path === 'destination_scope') {
      next = replacePath(next, 'scope_version', value === 'GOAL' ? 'GOAL_TRANSFER_V1' : undefined);
      next = replacePath(next, 'target_goal_ids', value === 'GOAL' ? [] : undefined);
      next = replacePath(next, 'emergency_conditions', value === 'GOAL' ? undefined : []);
      if (value === 'PROTECTED_CASH') { next = replacePath(next, 'must_not_reduce_policy_ids', undefined); next = replacePath(next, 'enabled', false); next = replacePath(next, 'single_action_cap_cents', '0'); next = replacePath(next, 'total_cap_cents', '0'); }
    }
    onChange(next);
  }
  return <div className="zy-template-grid">{fields.map((field) => {
    const raw = readPath(configuration, field.path), label = field.label, controlID = `${editorID}-${field.path}`;
    if (field.kind === 'money') return <MoneyInput key={field.path} label={label} value={raw as string} disabled={disabled} onChange={(value) => change(field.path, value)} />;
    if (field.kind === 'words') return <WordsInput key={field.path} label={label} value={raw as string[]} disabled={disabled} onChange={(value) => change(field.path, value)} />;
    if (field.kind === 'check') return <label className="zy-check" htmlFor={controlID} key={field.path}><input id={controlID} aria-label={label} type="checkbox" checked={raw === true} disabled={disabled} onChange={(event) => change(field.path, event.target.checked)} />{label}</label>;
    if (field.kind === 'select') return <label className="zy-field" htmlFor={controlID} key={field.path}>{label}<select id={controlID} aria-label={label} required={!field.optional} value={(raw as string | undefined) ?? ''} disabled={disabled} onChange={(event) => change(field.path, event.target.value || undefined)}><option value="">{field.optional ? '不绑定此项' : '请选择'}</option>{field.options!.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{!field.options!.length && <small>当前没有可核实的对象；请先建立对应规则或目标。</small>}</label>;
    if (field.kind === 'multi') return <fieldset key={field.path}><legend>{label}</legend><div className="zy-template-option-group">{!field.options!.length && <p>当前没有可核实的对象。</p>}{field.options!.map((item) => <label key={item.value} htmlFor={`${controlID}-${item.value}`}><input id={`${controlID}-${item.value}`} aria-label={`${item.label}${field.locked?.includes(item.value) ? '（安全项）' : ''}`} type="checkbox" checked={Array.isArray(raw) && raw.includes(item.value)} disabled={disabled || field.locked?.includes(item.value)} onChange={(event) => { const values = Array.isArray(raw) ? raw as string[] : []; change(field.path, event.target.checked ? [...values, item.value] : values.filter((value) => value !== item.value)); }} />{item.label}{field.locked?.includes(item.value) ? '（安全项）' : ''}</label>)}</div></fieldset>;
    return <label className="zy-field" htmlFor={controlID} key={field.path}>{label}<input id={controlID} aria-label={label} required={!field.optional} type={field.kind === 'date' ? 'date' : field.kind === 'integer' ? 'number' : 'text'} min={field.min} max={field.max} step={field.kind === 'integer' ? 1 : undefined} maxLength={160} disabled={disabled} value={field.kind === 'integer' && !Number.isFinite(raw) ? '' : (raw as string | number | undefined) ?? ''} onChange={(event) => change(field.path, field.kind === 'integer' ? event.target.valueAsNumber : field.optional && !event.target.value ? undefined : event.target.value)} /></label>;
  })}</div>;
}
