import { object } from '../features/policy-form';
import { businessText } from './display';
import { choicesAt, fieldLabel, isOptional, resolveSchema, unionOptions, valueLabel, type Schema, type FormValues, type ReferenceChoices } from './policy-schema';

export default function PolicySchemaForm({ schema, values, choices, disabled, update }: { schema: Schema; values: FormValues; choices: ReferenceChoices; disabled: boolean; update: (path: string, value: string | string[]) => void }) {
  function fields(input: unknown, path: string, depth: number): React.ReactNode {
    if (depth > 5) throw new Error('模板嵌套范围尚未支持。');
    const node = resolveSchema(schema, input); const branches = unionOptions(schema, node); const id = `zyn-policy-${path.replaceAll('.', '-')}`;
    if (branches.length) {
      const selected = branches.find((branch) => branch.value === values[`${path}.kind`]);
      return <fieldset key={path} className="zyn-policy-group"><legend>{fieldLabel(path)}</legend><label className="zy-field" htmlFor={id}>选择金额方式<select id={id} value={String(values[`${path}.kind`] ?? '')} disabled={disabled} onChange={(event) => update(`${path}.kind`, event.target.value)}><option value="">请选择</option>{branches.map((branch) => <option key={branch.value} value={branch.value}>{valueLabel(branch.value)}</option>)}</select></label>{selected && fields(selected.schema, path, depth + 1)}</fieldset>;
    }
    if (node.type === 'object') {
      if (!object(node.properties)) throw new Error('模板字段结构尚未核实。');
      const children = Object.entries(node.properties).map(([name, child]) => fields(child, path ? `${path}.${name}` : name, depth + 1));
      return path ? <fieldset key={path} className="zyn-policy-group"><legend>{fieldLabel(path)}</legend><div className="zyn-form-grid">{children}</div></fieldset> : <div className="zyn-form-grid">{children}</div>;
    }
    if (node.const !== undefined) return null;
    const label = fieldLabel(path); const optional = isOptional(object(input) ? input : {}); const references = choicesAt(choices, path); const array = node.type === 'array';
    const item = array ? resolveSchema(schema, node.items) : node;
    const options = references ?? (Array.isArray(item.enum) ? item.enum.map((value) => ({ value: String(value), label: valueLabel(String(value)) })) : node.type === 'boolean' ? [{ value: 'false', label: '不允许 / 否' }, { value: 'true', label: '允许 / 是' }] : null);
    const needsReference = item.format === 'uuid' || /(?:_id|_ids)$/.test(path);
    const value = values[path] ?? (array ? [] : '');
    return <label className="zy-field" key={path} htmlFor={id}>{label}{/_cents(?:_per_day)?$/.test(path) ? '（元）' : ''}{optional ? '（可选）' : ''}
      {options || needsReference ? <><select id={id} multiple={array} value={array ? Array.isArray(value) ? value : [] : String(value)} disabled={disabled || needsReference && !references?.length} onChange={(event) => update(path, array ? [...event.target.selectedOptions].map((option) => option.value) : event.target.value)}>{!array && <option value="">{optional ? '不关联' : '请选择'}</option>}{(options ?? []).map((option) => <option key={option.value} value={option.value}>{businessText(option.label)}</option>)}</select>{array && <small>可选择多项；按住 Ctrl 或 Command 增选。</small>}{needsReference && !references?.length && <small>当前没有可核实的{label}，先保留其他字段。</small>}</> : <input id={id} type={node.format === 'date' ? 'date' : 'text'} inputMode={node.type === 'integer' || node.type === 'number' ? 'decimal' : undefined} value={Array.isArray(value) ? value.join('，') : value} disabled={disabled} maxLength={typeof node.maxLength === 'number' ? node.maxLength : 500} onChange={(event) => update(path, event.target.value)} placeholder={array ? '用逗号分隔，如餐饮，交通' : undefined} />}
    </label>;
  }
  return <>{fields(schema, '', 0)}</>;
}
