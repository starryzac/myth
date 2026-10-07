import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NextState } from './api';
import type { AssetsState } from './assets-state';
import { getPermissionSchema, parseNativePermissionConfiguration, type PermissionUpdate } from './asset-permissions';
import { initialForm, buildFromSchema, configurationSummary, type FormValues } from './policy-schema';
import PolicySchemaForm from './PolicySchemaForm';
import type { AssetPerform } from './AssetsPanel';
import { businessText, userError } from './display';

export default function AssetPermissionPanel({ environment, state, blocked, signed, update, edited, perform, recovering = false, retryOriginal, openGoalLink }: { environment: NextState; state?: AssetsState; blocked: boolean; signed: boolean; update: PermissionUpdate | null; edited: () => void; perform: AssetPerform; recovering?: boolean; retryOriginal?: () => Promise<void>; openGoalLink?: () => void }) {
  const [open, setOpen] = useState(false); const [fullId, setFullId] = useState(''); const [values, setValues] = useState<FormValues>({}); const [error, setError] = useState('');
  useEffect(() => { if (update || recovering) setOpen(true); }, [update, recovering]);
  const schema = useQuery({ queryKey: ['zhiyu-next-native-asset-schema', environment.environment_id, environment.epoch_id], queryFn: () => getPermissionSchema(environment), enabled: open && !blocked, retry: false, staleTime: Infinity });
  useEffect(() => { if (schema.data) setValues(initialForm(schema.data.configuration_schema)); }, [schema.data]);
  const policies = state?.policies.filter((p) => p.source_kind === 'FULL_POLICY' && p.template_name === 'AssetAuthorizationPolicy' && p.planning_confirmation_valid && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status)) ?? [];
  const full = policies.find((p) => p.policy_id === fullId) ?? (policies.length === 1 ? policies[0] : undefined);
  const choices = { goal_id: environment.goals.map((g) => ({ value: g.id, label: businessText(g.name) })) };
  const candidate = update?.candidate; const relationship = candidate?.full_relationship;
  const reviewedFull = policies.find((p) => p.policy_id === relationship?.policy_id && p.current_version_id === relationship.version_id && p.configuration_hash === relationship.configuration_hash);
  async function propose() {
    if (blocked || !signed || !schema.data || !full) return; setError(''); edited();
    try { const configuration = parseNativePermissionConfiguration(buildFromSchema(schema.data.configuration_schema, values, choices)); await perform('/zhiyu-next/assets/mvp-permissions/candidates', { expected_epoch_id: environment.epoch_id, full_policy_id: full.policy_id, expected_full_policy_version_id: full.current_version_id, configuration }); }
    catch (cause) { setError(userError(cause)); }
  }
  function change(path: string, value: string | string[]) { setValues((previous) => ({ ...previous, [path]: value })); edited(); }
  return <section className="zy-card" aria-label="独立原购买权限"><div className="zy-section-heading"><h3>独立原购买权限</h3><button className="zy-secondary" disabled={blocked} onClick={() => setOpen((value) => !value)}>{open ? '收起权限审阅' : '设置原购买权限'}</button></div><p>先确认资产规划，再独立审阅购买权限的类别、额度、期限和罚金开关。两次确认各有自己的范围；此处不购买产品、不生成逐笔执行同意。</p>
    {openGoalLink && <button className="zy-secondary" disabled={blocked} onClick={openGoalLink}>重审已有目标的资产关联</button>}
    {open && <>{(schema.isError || error) && <p role="alert" className="zy-message zy-error">{error || userError(schema.error)}</p>}
      <label className="zy-field" htmlFor="zyn-permission-full">已确认资产规划<select id="zyn-permission-full" disabled={blocked} value={full?.policy_id ?? ''} onChange={(e) => { setFullId(e.target.value); edited(); }}><option value="">请选择</option>{policies.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label>
      {!policies.length && <p>先在“我的规则”保存并确认资产安排范围。本页不会把规划自动转换为购买权限。</p>}
      {schema.data && !candidate && <form onSubmit={(e) => { e.preventDefault(); void propose(); }}><PolicySchemaForm schema={schema.data.configuration_schema} values={values} choices={choices} disabled={blocked} update={change} /><p>金额以元填写。允许有罚金支取仅保存明确权限；每次实际损失仍需核实原报价和一次必要同意。</p><button className="zy-secondary" type="submit" disabled={blocked || !signed || !full}>生成原权限候选</button></form>}
      {candidate && <section aria-label="原权限候选范围"><h4>审阅原购买权限</h4><p>{businessText(candidate.summary)}</p><ul>{configurationSummary(candidate.canonical_configuration, choices).map((line, i) => <li key={i}>{businessText(line)}</li>)}</ul><p>关联规划：{businessText(reviewedFull?.name ?? '原规划版本当前未核实')}。关联本身不授予资金执行权限。</p><div className="zy-buttons"><button className="zy-primary" disabled={blocked || !signed || !candidate.can_confirm || !reviewedFull} onClick={() => void perform('/zhiyu-next/assets/mvp-permissions/confirm', { expected_epoch_id: environment.epoch_id, candidate_id: candidate.candidate_id, reviewed_hash: candidate.configuration_hash, accepted: true })}>确认这份原购买权限</button><button className="zy-link" disabled={blocked} onClick={edited}>取消原权限候选</button></div></section>}
      {update?.confirmation && <p role="status">原购买权限的原声明、真实用户签署和原版本已独立核实。这是建权历史回执；当前是否可用仍由执行时的规则核实决定。</p>}
      {recovering && <p>原权限命令正在独立核实；没有原件时保留原请求，不改键、不假定拒绝。</p>}
      {recovering && retryOriginal && signed && <button className="zy-secondary" onClick={() => void retryOriginal()}>恢复会话后续接原权限命令</button>}
    </>}
  </section>;
}
