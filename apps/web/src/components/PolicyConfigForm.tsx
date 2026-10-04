import { useState } from 'react';
import { buildConfiguration, fieldsFor, formValues, policyTypes, readPath, validateForm } from '../features/policy-form';
import type { Configuration } from '../features/policy-form';
import { formatMoneyCents } from '../features/money';

const choiceLabels: Record<string, string> = { exact: '固定金额', range: '金额区间', bill_balance: '真实账单余额', general_idle_funds: '一般安全闲置资金', goal: '指定目标', rolling_window_quantile: '滚动窗口分位数', CASH: '现金', CASH_MGMT_T0: '当日到账现金管理', CASH_MGMT_T1: '次日到账现金管理', FIXED_DEPOSIT: '定期存款' };
export function ConfigurationReview({ configuration }: { configuration: Configuration }) {
  return <div className="config-review"><p className="caption">类型：{policyTypes[String(configuration.type)] ?? '待核验'}</p>
    <dl>{fieldsFor(configuration).map((field) => {
      const value = readPath(configuration, field.path);
      const display = value == null ? (field.optional ? '未设置' : '待补齐') : field.kind === 'money'
        ? `¥${formatMoneyCents(value as number)}` : typeof value === 'boolean' ? value ? '允许' : '不允许'
          : Array.isArray(value) ? value.map((item) => choiceLabels[String(item)] ?? String(item)).join('、') : choiceLabels[String(value)] ?? String(value);
      return <div key={field.path}><dt>{field.label.replace('（元）', '')}</dt><dd>{display}</dd></div>;
    })}</dl></div>;
}
export default function PolicyConfigForm({ initial, onChange }: { initial: Configuration; onChange: (config: Configuration | null, issues: string[]) => void }) {
  const [values, setValues] = useState(() => formValues(initial));
  const [shape, setShape] = useState(initial);
  const [issues, setIssues] = useState<string[]>([]);
  function change(path: string, value: string | boolean) {
    const next = { ...values, [path]: value };
    let nextShape = shape;
    if (path === 'amount_rule.kind') nextShape = { ...shape, amount_rule: { kind: value } };
    if (path === 'scope') nextShape = { ...shape, scope: value, goal_id: null };
    if (nextShape !== shape) {
      for (const [key, fallback] of Object.entries(formValues(nextShape))) if (!(key in next)) next[key] = fallback;
      setShape(nextShape);
    }
    setValues(next);
    try {
      const config = buildConfiguration(nextShape, next); const errors = validateForm(config);
      setIssues(errors); onChange(errors.length ? null : config, errors);
    } catch (error) {
      const errors = [error instanceof Error ? error.message : '配置待补齐'];
      setIssues(errors); onChange(null, errors);
    }
  }
  return <div className="policy-form" aria-label="策略配置表单"><p className="caption">{policyTypes[String(initial.type)]} · 金额单位：元；确认与规范化以服务端为准。</p>
    <div className="form-grid">{fieldsFor(shape).map((field) => field.kind === 'classes'
      ? <fieldset key={field.path}><legend>{field.label}</legend>{field.choices?.map((item) => { const selected = String(values[field.path] ?? '').split(/,\s*/).filter(Boolean); return <label className="checkbox-field" key={item}>
        <input type="checkbox" checked={selected.includes(item)} onChange={(e) => change(field.path, (e.target.checked ? [...selected, item] : selected.filter((value) => value !== item)).join(', '))} />{choiceLabels[item]}</label>; })}</fieldset>
      : <label key={field.path} className={field.kind === 'boolean' ? 'checkbox-field' : ''}>
      {field.kind === 'boolean' ? <><input type="checkbox" checked={values[field.path] === true} onChange={(e) => change(field.path, e.target.checked)} />{field.label}</>
        : <><span>{field.label}{field.optional && '（可选）'}</span>{field.kind === 'select'
          ? <select value={String(values[field.path] ?? '')} onChange={(e) => change(field.path, e.target.value)}><option value="">请选择</option>{field.choices?.map((item) => <option key={item} value={item}>{choiceLabels[item] ?? item}</option>)}</select>
          : <input type={field.kind === 'date' ? 'date' : 'text'} inputMode={['money', 'integer', 'ratio'].includes(field.kind) ? 'decimal' : undefined}
            value={String(values[field.path] ?? '')} onChange={(e) => change(field.path, e.target.value)} />}</>}
    </label>)}</div>{issues.length > 0 && <ul className="form-issues" role="alert">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}</div>;
}
