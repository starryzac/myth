import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { MaturityPreview } from '../api/full-maturity-execution';
import type { NextState } from './api';
import type { AssetsState } from './assets-state';
import { assetSourceOpen } from './assets-state';
import type { AssetPerform } from './AssetsPanel';
import { lookupNextMaturity, previewNextMaturity, readMaturityView, type MaturityUpdate } from './maturity';
import { businessText, money, userError } from './display';

const stages: Record<string, string> = { PLANNED: '到期原动作仅已准备', AUTHORIZED: '原兑付同意已核实，待执行', SUBMITTED: '同一原兑付正在核实', UNKNOWN: '同一原兑付正在核实', SUCCEEDED: '实际到期回执已核实', RECONCILED: '实际到期回执已核实', FAILED: '原兑付未完成，结果继续核实', CANCELLED: '原兑付已取消', INVALIDATED: '原兑付条件已变化' };
type Preview = { request: MaturityUpdate['recovery']['prepare_request']; value: MaturityPreview };
export default function MaturityPanel({ environment, state, blocked, signed, perform, runRead, update, recovering = false, resumeOriginal, retryOriginal }: { environment: NextState; state?: AssetsState; blocked: boolean; signed: boolean; perform: AssetPerform; runRead: <T>(work: () => Promise<T>) => Promise<T>; update: MaturityUpdate | null; recovering?: boolean; resumeOriginal?: () => Promise<void>; retryOriginal?: () => Promise<void> }) {
  const [positionId, setPositionId] = useState(''); const [policyId, setPolicyId] = useState(''); const [preview, setPreview] = useState<Preview | null>(null); const [working, setWorking] = useState(false); const [error, setError] = useState('');
  const original = useQuery({ queryKey: ['zhiyu-next-maturity-view', environment.environment_id, environment.epoch_id], queryFn: () => readMaturityView(environment), enabled: !!state && !blocked && !update, retry: false, staleTime: Infinity, refetchInterval: (query) => !blocked && !update && ['UNKNOWN', 'SUBMITTED'].includes(query.state.data?.action.status ?? '') ? 15000 : false });
  const current = update ?? original.data; const action = current?.action;
  const policies = state?.policies.filter((p) => p.template_name === 'RecoveryPolicy' && p.source_kind === 'FULL_POLICY' && p.planning_confirmation_valid && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status)) ?? [];
  const policy = policies.find((p) => p.policy_id === policyId) ?? (policies.length === 1 ? policies[0] : undefined);
  const positions = state?.positions.filter((p) => p.maturity_at !== null && p.status !== 'REDEEMED') ?? [];
  const position = positions.find((p) => p.position_id === positionId) ?? (positions.length === 1 ? positions[0] : undefined);
  const available = assetSourceOpen(state?.source_validation_status);
  const scope = !!policy && !!position;
  const exactPreview = !!preview && !!policy && preview.request.policy_id === policy.policy_id && preview.request.expected_version_id === policy.current_version_id && preview.request.position_id === position?.position_id;
  const prepared = !!action && !action.historical && action.status === 'PLANNED' && action.original_consent === null && policies.some((p) => p.policy_id === action.original_request.policy_id && p.current_version_id === action.original_request.expected_version_id) && !!state && Date.parse(state.server_as_of) < Date.parse(action.command.expires_at);
  async function inspect() {
    if (blocked || working || !scope || !policy || !position) return; setWorking(true); setError('');
    try { const request = { policy_id: policy.policy_id, expected_version_id: policy.current_version_id, expected_epoch_id: environment.epoch_id, position_id: position.position_id, idempotency_key: crypto.randomUUID() }; const value = await runRead(() => previewNextMaturity(request, environment)); setPreview({ request, value }); }
    catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function prepare() {
    if (blocked || working || !signed || !available || !exactPreview || preview?.value.proof.status !== 'VERIFIED_SCOPE') return; setWorking(true); setError('');
    try { const body = preview.request; await perform('/zhiyu-next/assets/maturity/prepare', body, undefined, undefined, undefined, { protocol: 'zhiyu-next-maturity-recovery-v1', user_id: environment.dashboard.user_id, prepare_request: body, action_id: null, reviewed_command_hash: null }); setPreview(null); }
    catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  async function confirm() {
    if (blocked || working || !signed || !available || !prepared || !current || !action) return; setWorking(true); setError('');
    try { const lookup = await runRead(() => lookupNextMaturity(current.recovery, environment)); if (!lookup.action || lookup.action.status !== 'PLANNED' || lookup.action.original_consent !== null || lookup.action.historical) throw new Error('原到期动作已变化，请保留原件核实。'); await perform(`/zhiyu-next/assets/maturity/actions/${action.action_id}/confirm-and-execute`, { expected_epoch_id: action.epoch_id, reviewed_command_hash: action.reviewed_command_hash, accepted: true }, undefined, undefined, undefined, current.recovery); }
    catch (cause) { setError(userError(cause)); } finally { setWorking(false); }
  }
  const accountName = action ? environment.dashboard.account_facts.facts.accounts.find((a) => a.id === action.command.destination_account_id)?.name ?? '原合同兑付账户' : '';
  const productName = action ? state?.positions.find((p) => p.position_id === action.command.position_id)?.product_name ?? '原到期产品' : '';
  return <section className="zy-card" aria-label="到期兑付"><h3>到期兑付</h3><p>合同到期与当前持仓仍需独立保护核实。准备仅保存原兑付动作，尚未扣减持仓或收到资金。</p>
    {(error || original.isError) && <p role="alert" className="zy-message zy-error">{error || userError(original.error)}</p>}
    <label className="zy-field" htmlFor="zyn-maturity-position">原持仓<select id="zyn-maturity-position" value={position?.position_id ?? ''} disabled={blocked || working} onChange={(e) => { setPositionId(e.target.value); setPreview(null); }}><option value="">请选择</option>{positions.map((p) => <option key={p.position_id} value={p.position_id}>{businessText(p.product_name)} · 到期 {p.maturity_at!.slice(0, 10)}</option>)}</select></label>
    <label className="zy-field" htmlFor="zyn-maturity-policy">回收保护范围<select id="zyn-maturity-policy" value={policy?.policy_id ?? ''} disabled={blocked || working} onChange={(e) => { setPolicyId(e.target.value); setPreview(null); }}><option value="">请选择</option>{policies.map((p) => <option key={p.policy_id} value={p.policy_id}>{businessText(p.name)}</option>)}</select></label>
    <div className="zy-buttons"><button className="zy-secondary" disabled={blocked || working || !scope} onClick={() => void inspect()}>核实到期保护</button><button className="zy-secondary" disabled={blocked || working || !signed || !available || !exactPreview || preview?.value.proof.status !== 'VERIFIED_SCOPE' || !!action && !action.service_receipt_verified} onClick={() => void prepare()}>保存可审阅到期原件</button></div>
    {preview && <p role="status">{preview.value.proof.status === 'VERIFIED_SCOPE' ? '当前保护范围已核实，可以准备原兑付动作；比较结果本身不授予执行权限。' : `当前不能准备兑付：${businessText(preview.value.proof.reasons.join('；'))}`}</p>}
    {recovering && !action && <p>正在核实同一原到期请求；尚未找到原件不等于拒绝，原准备键保持保留。</p>}
    {action && <section aria-label="到期原件必要后果"><h4>{stages[action.status] ?? '原兑付待核实'}</h4><strong className="zy-amount zy-amount-small">{money(action.command.principal_cents)}</strong><p>{businessText(productName)}；原本金返回 {businessText(accountName)}，费用 {money(0)}，损失 {money(0)}。本次只兑付原合同本金，已计提收益不作为当前到账资金。</p><p>{action.service_receipt_verified ? '原动作、银行结算和服务回执已核实；这笔真实收到的本金可以进入后续规划。' : action.original_consent ? '原同意已独立核实，继续查询同一原动作，不再要求重复确认。' : '尚未确认兑付，资金未到账。'}</p>
      {prepared && available && <button className="zy-primary" disabled={blocked || working || !signed} onClick={() => void confirm()}>确认并兑付原本金</button>}
      {resumeOriginal && available && signed && <button className="zy-secondary" disabled={working} onClick={() => void resumeOriginal()}>续接同一原兑付</button>}
    </section>}
    {retryOriginal && recovering && signed && <button className="zy-secondary" disabled={working} onClick={() => void retryOriginal()}>按原到期请求恢复会话后的续接</button>}
    {!available && <p className="zy-muted">本轮到期执行尚待真实闭环验收。</p>}
  </section>;
}
