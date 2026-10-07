import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { request, ApiError } from '../api/http';
import { getPresets, statusText, terminal } from '../zhiyu/api';
import type { Action } from '../zhiyu/api';
import { getNextState, messageAgent, readOperation } from './api';
import type { AgentReply, NextState } from './api';
import { completePending, isPolicyChangePath, makePending, restorePending, retainPending, replacePending } from './operation';
import type { PendingOperation } from './operation';
import { businessText, money, ruleNames, ruleSummary, userError } from './display';
import ModelPanel from './ModelPanel';
import PolicyWorkspace from './PolicyWorkspace';
import { context, isCatalogUserCommand, parsePolicyCandidate, readPolicyCommand, type PolicyCandidate } from './policies';
import { readPolicyChange, type ChangeReview, type ChangeCommit } from './policy-change';
import { isPaymentPath, type PaymentRecovery } from './payment-recovery';
import { paymentIntent, readPaymentOriginal, samePaymentUpdate, type PaymentUpdate } from './payments';
import { isJointPath, type JointRecovery } from './joint-recovery';
import { nextJointCommand, readJointOriginal, sameJointUpdate, validateJointOriginal, rememberJointView, type JointUpdate } from './joint-operations';
import GoalsPanel from './GoalsPanel';
import { isGoalModelPath, goalModelIntent, readGoalModelOriginal, type GoalModelUpdate } from './goal-repairs';
import { isDiscoveryPath, readDiscoveryOriginal, type DiscoveryUpdate } from './discovery';
import { isAssetPath, type AssetRecovery } from './asset-recovery';
import { readAssetOriginal, validateAssetOriginal, sameAssetUpdate, rememberAssetView, type AssetUpdate } from './assets';
import AssetsPanel from './AssetsPanel';
import { isMaturityPath, type MaturityRecovery } from './maturity-recovery';
import { readMaturityOriginal, validateMaturityOriginal, sameMaturityUpdate, rememberMaturityView, type MaturityUpdate } from './maturity';
import { isPermissionPath, readPermissionOriginal, type PermissionUpdate } from './asset-permissions';
import { isReinvestmentPrepare, type ReinvestmentRecovery } from './reinvestment-recovery';
import { readReinvestmentOriginal, validateReinvestmentOriginal, sameReinvestmentUpdate, rememberReinvestmentView, type ReinvestmentUpdate } from './reinvestments';
import { isLossPath, type LossRecovery } from './loss-recovery';
import { readLossOriginal, validateLossOriginal, sameLossUpdate, rememberLossView, type LossUpdate } from './loss';
import { isLossRevisionPath, revisionBase, type LossRevisionRecovery } from './loss-revision-recovery';
import { readLossRevisionOriginal, validateRevisionOriginal, sameRevisionUpdate, rememberRevisionUpdate, type LossRevisionUpdate } from './loss-revisions';
import { isQuestionPath, type QuestionRecovery } from './question-recovery';
import { readQuestionOriginal, rememberQuestionView, type QuestionUpdate } from './questions';
import { isStandingPath, type StandingRecovery } from './standing-recovery';
import { readStandingOriginal, validateStandingOriginal, standingAcknowledgment, rememberStandingUpdate, type StandingUpdate } from './standing';
import StandingPanel from './StandingPanel';
import '../zhiyu/zhiyu.css';
import './zhiyu-next.css';

const pages = ['总览', '我的规则', '目标与执行', '活动记录'];
const nextStatusText: Record<string, string> = { ...statusText, PAUSED: '已暂停' };
const protection: Record<string, string> = { obligations: '房租与账单', living: '生活准备金', emergency: '应急金', goal_cash: '已有目标资金', goal_minimum: '目标最低承诺' };
function Timestamp({ value }: { value: string }) { return <time dateTime={value}>{new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))}</time>; }
function Status({ value }: { value: string }) { return <span className={`zy-status zy-status-${['SUCCEEDED', 'RECONCILED', 'COMPLETED', 'SETTLED', 'ACTIVE'].includes(value) ? 'good' : ['UNKNOWN', 'SUBMITTED', 'PENDING'].includes(value) ? 'wait' : 'plain'}`}>{nextStatusText[value] ?? (value === 'PENDING' ? '正在处理' : '等待核实')}</span>; }

function ActionResult({ action, environment, confirm, blocked }: { action: Action; environment: NextState; confirm: () => void; blocked: boolean }) {
  const isCompleted = ['SUCCEEDED', 'RECONCILED'].includes(action.status) && terminal(action);
  const unresolved = ['UNKNOWN', 'SUBMITTED'].includes(action.status);
  const askAvailable = environment.capabilities.some((capability) => capability.id === 'ask_once_execution' && ['AVAILABLE', 'VERIFIED', 'ENABLED'].includes(capability.status));
  const goal = environment.goals.find((item) => item.id === action.effect.goal_id);
  const source = [...new Set((action.effect.income_uses ?? []).map((item) => environment.dashboard.account_facts.facts.accounts.find((account) => account.id === item.account_id)?.name ?? '原来源账户'))];
  return <article className="zy-card" aria-label="资金安排结果"><div className="zy-section-heading"><h2>{isCompleted ? '已完成的安排' : unresolved ? '正在核实这笔安排' : '当前安排'}</h2><Status value={!isCompleted && ['SUCCEEDED', 'RECONCILED'].includes(action.status) ? 'UNKNOWN' : action.status} /></div>
    <strong className="zy-amount zy-amount-small">{money(isCompleted ? action.receipt!.executed_cents : action.effect.amount_cents)}</strong>
    <dl className="zy-facts"><div><dt>目标</dt><dd>{businessText(goal?.name ?? '已确认的原目标')}</dd></div><div><dt>来源</dt><dd>{source.length ? source.map(businessText).join('、') : '等待服务端核实'}</dd></div><div><dt>费用</dt><dd>{money(action.effect.fee_cents)}</dd></div><div><dt>损失</dt><dd>{money(action.effect.loss_cents)}</dd></div></dl>
    {isCompleted ? <p className="zy-result">回执与账本已核实，目标进度以当前服务端读数为准。</p> : unresolved ? <p role="status">银行结果暂未返回。后台会核对同一原动作，在核实前不会重复安排。</p> : action.autonomy_level === 'AUTO_EXECUTE' ? <p>该安排由现行授权覆盖，后台将重检资金与规则后自动处理。</p> : action.autonomy_level === 'ASK_ONCE' && ['PLANNED', 'AUTHORIZED'].includes(action.status) ? askAvailable ? <><p>这次安排需要你明确同意。金额、目标、费用与损失如上，确认后由系统完成后续步骤。</p><button className="zy-primary" disabled={blocked} onClick={confirm}>确认并执行</button></> : <p>此类逐笔执行尚未开放，请保留计划。已授权的单目标安全安排可继续自动处理。</p> : <p>{businessText((action.prepared_validation.reasons ?? []).join('；')) || '当前条件不允许执行，请先查看规则与资金限制。'}</p>}
  </article>;
}

export default function ZhiyuNextApp() {
  const [page, setPage] = useState(0); const [modelOpen, setModelOpen] = useState(false); const [diagnosticOpen, setDiagnosticOpen] = useState(false);
  const [text, setText] = useState(''); const [engine, setEngine] = useState<'llm' | 'rules'>('llm'); const [reply, setReply] = useState<AgentReply | null>(null);
  const [pending, setPending] = useState<PendingOperation | null>(null); const [storageError, setStorageError] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [policyCandidate, setPolicyCandidate] = useState<PolicyCandidate | null>(null); const [policyCompleted, setPolicyCompleted] = useState<string | null>(null);
  const [changeReview, setChangeReview] = useState<ChangeReview | null>(null); const [changeCommit, setChangeCommit] = useState<ChangeCommit | null>(null); const [authBlocked, setAuthBlocked] = useState(false); const [operationChecking, setOperationChecking] = useState(false);
  const [paymentUpdate, setPaymentUpdate] = useState<PaymentUpdate | null>(null);
  const [jointUpdate, setJointUpdate] = useState<JointUpdate | null>(null);
  const [goalModelUpdate, setGoalModelUpdate] = useState<GoalModelUpdate | null>(null);
  const [discoveryUpdate, setDiscoveryUpdate] = useState<DiscoveryUpdate | null>(null);
  const [assetUpdate, setAssetUpdate] = useState<AssetUpdate | null>(null);
  const [maturityUpdate, setMaturityUpdate] = useState<MaturityUpdate | null>(null);
  const [permissionUpdate, setPermissionUpdate] = useState<PermissionUpdate | null>(null);
  const [reinvestmentUpdate, setReinvestmentUpdate] = useState<ReinvestmentUpdate | null>(null);
  const [lossUpdate, setLossUpdate] = useState<LossUpdate | null>(null);
  const [revisionUpdate, setRevisionUpdate] = useState<LossRevisionUpdate | null>(null);
  const [questionUpdate, setQuestionUpdate] = useState<QuestionUpdate | null>(null);
  const [openGoalLink, setOpenGoalLink] = useState(false);
  const [standingUpdate, setStandingUpdate] = useState<StandingUpdate | null>(null);
  const queryClient = useQueryClient();
  const content = useRef<HTMLDivElement>(null); const recovering = useRef(false);
  const stateQuery = useQuery({ queryKey: ['zhiyu-next-state'], queryFn: getNextState, structuralSharing: false, refetchInterval: (query) => {
    if (busy || pending) return false;
    const value = query.state.data;
    return value?.autonomy.state === 'ACTIVE' || value?.actions.some((action) => !terminal(action)) ? 15000 : false;
  } });
  const data = stateQuery.isSuccess ? stateQuery.data : undefined;
  const presets = useQuery({ queryKey: ['zhiyu-next-presets'], queryFn: getPresets, enabled: page === 1 });
  const blocked = !data || busy || !!pending || !!storageError;
  useEffect(() => { document.title = '知余扩展版 · 开发验证'; }, []);
  useEffect(() => {
    if (!data) return;
    try { setPending(restorePending(data)); setStorageError(''); }
    catch (cause) { setStorageError(userError(cause)); }
  }, [data]);
  async function refresh() {
    const result = await stateQuery.refetch();
    if (result.isError || !result.data) throw result.error ?? new Error('当前状态未能核实，请稍后再试。');
    await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-policy-records', result.data.environment_id, result.data.epoch_id] });
    return result.data;
  }
  async function verifyOriginal(original: PendingOperation, environment: NextState): Promise<boolean> {
    const policyRequest = ['/zhiyu-next/policy-candidates', '/zhiyu-next/policy-commands/confirm', '/zhiyu-next/policy-commands/lifecycle'].includes(original.path);
    const paymentRequest = isPaymentPath(original.path);
    const jointRequest = isJointPath(original.path);
    const goalModelResult = isGoalModelPath(original.path) ? await readGoalModelOriginal(original, environment) : null;
    const discoveryRequest = isDiscoveryPath(original.path);
    const reinvestmentRequest = isReinvestmentPrepare(original.path) || !!original.reinvestment_recovery;
    const assetRequest = isAssetPath(original.path) && !reinvestmentRequest;
    const maturityRequest = isMaturityPath(original.path);
    const permissionRequest = isPermissionPath(original.path);
    const lossRequest = isLossPath(original.path);
    const revisionResult = isLossRevisionPath(original.path) ? await readLossRevisionOriginal(original, environment) : null;
    const standingRequest = isStandingPath(original.path);
    const standingResult = standingRequest ? await readStandingOriginal(original, environment) : null;
    const questionRequest = isQuestionPath(original.path);
    const questionResult = questionRequest ? await readQuestionOriginal(original, environment) : null;
    const jointResult = jointRequest ? await readJointOriginal(original, environment) : null;
    const assetResult = assetRequest ? await readAssetOriginal(original, environment) : null;
    const maturityResult = maturityRequest ? await readMaturityOriginal(original, environment) : null;
    const permissionResult = permissionRequest ? await readPermissionOriginal(original, environment) : null;
    const reinvestmentResult = reinvestmentRequest ? await readReinvestmentOriginal(original, environment) : null;
    const lossResult = lossRequest ? await readLossOriginal(original, environment) : null;
    const result = goalModelResult ?? jointResult ?? assetResult ?? maturityResult ?? permissionResult ?? reinvestmentResult ?? lossResult ?? revisionResult ?? questionResult ?? standingResult ?? await (paymentRequest ? readPaymentOriginal(original, environment) : discoveryRequest ? readDiscoveryOriginal(original, environment) : isPolicyChangePath(original.path) ? readPolicyChange(original, environment) : policyRequest ? readPolicyCommand(original, environment) : readOperation(original.client_request_id, environment));
    if (paymentRequest && result.result) setPaymentUpdate(result.result as unknown as PaymentUpdate);
    if (jointResult?.joint) { rememberJointView(jointResult.joint, environment); setJointUpdate(jointResult.joint); }
    if (assetResult?.asset) { rememberAssetView(assetResult.asset, environment); setAssetUpdate(assetResult.asset); }
    if (maturityResult?.maturity) { rememberMaturityView(maturityResult.maturity, environment); setMaturityUpdate(maturityResult.maturity); }
    if (permissionResult?.permission) setPermissionUpdate(permissionResult.permission);
    if (reinvestmentResult?.reinvestment) { rememberReinvestmentView(reinvestmentResult.reinvestment, environment); setReinvestmentUpdate(reinvestmentResult.reinvestment); }
    if (lossResult?.loss) { rememberLossView(lossResult.loss, environment); setLossUpdate(lossResult.loss); }
    if (revisionResult?.revision) { rememberRevisionUpdate(revisionResult.revision, environment); setRevisionUpdate(revisionResult.revision); }
    if (questionResult?.question) { rememberQuestionView(questionResult.question, environment); setQuestionUpdate(questionResult.question); }
    if (standingResult?.standing) { rememberStandingUpdate(standingResult.standing, environment); setStandingUpdate(standingResult.standing); }
    if (goalModelResult?.goal_model) setGoalModelUpdate(goalModelResult.goal_model);
    if (result.status === 'PENDING') return false;
    setBusy(true);
    try {
      const following = jointResult?.joint && result.status === 'COMPLETED' ? nextJointCommand(jointResult.joint) : null;
      if (following) {
        const next = makePending(environment, following.path, following.body, undefined, following.recovery);
        await validateJointOriginal(next, environment); replacePending(original, next); setPending(next);
        try { await request(next.path, 'POST', next.body, (value) => { context(value, environment); return value; }); }
        catch (cause) { setError(userError(cause)); if (cause instanceof ApiError && cause.status === 401) setAuthBlocked(true); }
        return true;
      }
      completePending(original); setPending(null);
      setAuthBlocked(false);
      if (result.status === 'REJECTED') {
        setError('服务端已核实这次请求被拒绝，请根据最新条件调整后再提交。');
        if (original.path.endsWith('/authorizations/confirm')) setReply((value) => value?.candidate ? { ...value, candidate: { ...value.candidate, can_confirm: false } } : value);
        if (original.path === '/zhiyu-next/policy-commands/confirm') setPolicyCandidate((value) => value ? { ...value, can_confirm: false } : value);
        if (isPolicyChangePath(original.path)) setChangeReview(null);
        if (discoveryRequest) setDiscoveryUpdate(null);
        if (permissionRequest) setPermissionUpdate(null);
      }
      else {
        setError(''); setNotice('原请求已独立核实，结果已更新。'); if (original.path.endsWith('/authorizations/confirm')) setReply(null);
        if (original.path === '/zhiyu-next/policy-candidates') setPolicyCandidate(parsePolicyCandidate(result.result, original));
        if (original.path === '/zhiyu-next/policy-commands/confirm') { setPolicyCandidate(null); setPolicyCompleted(original.client_request_id); }
        if (original.path === '/zhiyu-next/policy-commands/lifecycle') { setPolicyCandidate(null); setPolicyCompleted(null); setNotice('当前规则状态已独立核实，现行版本已更新。'); }
        if (original.path.endsWith('/change-review')) setChangeReview(result.result as unknown as ChangeReview);
        if (original.path.endsWith('/change-confirm')) { const commit = result.result as unknown as ChangeCommit; setChangeCommit(commit); setChangeReview(null); setNotice(commit.status === 'COMMITTED_BUT_FINANCIAL_UNKNOWN' ? '规则版本已提交，资金影响待核实；原命令已独立定位，不会重复提交。' : '当前规则已修改，已审阅范围完成独立核对。'); }
        if (discoveryRequest) { setDiscoveryUpdate(result.result as unknown as DiscoveryUpdate); setNotice(original.path.endsWith('/confirm') ? '历史候选规则确认已独立核实，未授予银行权限。' : '历史候选已独立核实，请审核必要规则范围。'); }
        if (permissionRequest) setNotice(original.path.endsWith('/confirm') ? '原购买权限声明、用户签署与真实版本已独立核实，尚未执行资金。' : '原购买权限候选已独立核实，请审阅完整范围。');
        if (questionRequest) setNotice('偏好原命令和当前来源已独立核实；回答没有确认或执行资金。');
        if (standingRequest) setNotice('持续范围或原控制已独立核实；声明没有确认或执行银行资金。');
      }
      if (goalModelResult) {
        setNotice('该目标的新版本、完整配置与原用户确认已独立核实；其它目标需重新读取基线。');
        await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-goal-conflicts', environment.environment_id, environment.epoch_id] });
        await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-goal-planning', environment.environment_id, environment.epoch_id] });
      }
      if (standingRequest) await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-standing', environment.environment_id, environment.epoch_id] });
      if (paymentRequest) await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-payments', environment.environment_id, environment.epoch_id] });
      if (discoveryRequest) await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-policy-discovery', environment.environment_id, environment.epoch_id] });
      if (assetRequest || maturityRequest || permissionRequest || reinvestmentRequest || lossRequest || !!revisionResult?.revision?.view?.action.receipt) await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-assets-state', environment.environment_id, environment.epoch_id] });
      if (permissionRequest && original.path.endsWith('/confirm')) await queryClient.invalidateQueries({ queryKey: ['zhiyu-next-policy-records', environment.environment_id, environment.epoch_id] });
      if (jointRequest && jointResult?.joint?.execution.children.some((row) => row.state === 'ORIGINAL_RECEIPT_VERIFIED')) await refresh();
      else if (!jointRequest && !permissionRequest && !questionRequest && !standingRequest && (!lossRequest || !!lossResult?.loss?.view?.action.receipt) && (!revisionResult || !!revisionResult.revision?.view?.action.receipt) && (!reinvestmentRequest || original.path.endsWith('/confirm-and-execute') || original.path.endsWith('/continue-original')) && (!maturityRequest || maturityResult?.maturity?.action.service_receipt_verified) && (!assetRequest || original.path.endsWith('/confirm-and-execute') || original.path.endsWith('/continue-original')) && (!paymentRequest || original.path.endsWith('/confirm-and-execute') || original.path.endsWith('/execute-original')) && original.path !== '/zhiyu-next/policy-candidates' && original.path !== '/zhiyu-next/policy-discovery' && !original.path.endsWith('/change-review')) {
        await refresh();
      }
      return true;
    } finally { setBusy(false); }
  }
  useEffect(() => {
    if (!pending || !data || busy || storageError) return;
    let mounted = true;
    let timer: number | undefined;
    const check = async () => {
      if (!mounted) return;
      if (!recovering.current) {
        recovering.current = true;
        setOperationChecking(true);
        try { await verifyOriginal(pending, data); }
        catch { /* Unresolved reads retain the original; terminal refresh failures never replay it. */ }
        finally { recovering.current = false; if (mounted) setOperationChecking(false); }
      }
      if (mounted) timer = window.setTimeout(() => void check(), 5000);
    };
    void check();
    return () => { mounted = false; window.clearTimeout(timer); };
    // One serial original-operation reader owns recovery and the terminal refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, data, busy, storageError]);
  async function perform(path: string, body: Record<string, unknown>, payment_recovery?: PaymentRecovery, joint_recovery?: JointRecovery, asset_recovery?: AssetRecovery, maturity_recovery?: MaturityRecovery, reinvestment_recovery?: ReinvestmentRecovery, loss_recovery?: LossRecovery, question_recovery?: QuestionRecovery, standing_recovery?: StandingRecovery, loss_revision_recovery?: LossRevisionRecovery) {
    if (blocked || !data) return;
    setBusy(true); setError(''); setNotice(''); setAuthBlocked(false);
    let original: PendingOperation | null = null;
    try {
      original = makePending(data, path, body, payment_recovery, joint_recovery, asset_recovery, maturity_recovery, reinvestment_recovery, loss_recovery, question_recovery, standing_recovery, loss_revision_recovery);
      if (isPaymentPath(path)) await paymentIntent(original, data);
      if (isJointPath(path)) await validateJointOriginal(original, data);
      if (isGoalModelPath(path)) await goalModelIntent(original, data);
      if (isReinvestmentPrepare(path) || reinvestment_recovery) await validateReinvestmentOriginal(original, data);
      else if (isAssetPath(path)) await validateAssetOriginal(original, data);
      if (isMaturityPath(path)) await validateMaturityOriginal(original, data);
      if (isLossPath(path)) await validateLossOriginal(original, data);
      if (isLossRevisionPath(path)) await validateRevisionOriginal(original, data);
      // Establish the thin route contract before creating a new preference write gate.
      // Missing native originals remain nonfinal; an unavailable route sends no POST.
      if (isQuestionPath(path)) await readQuestionOriginal(original, data);
      if (isStandingPath(path)) await validateStandingOriginal(original, data);
      retainPending(original); setPending(original);
      // Signed-review responses contain typed money maps. The POST only acknowledges
      // delivery; its financial result is never displayed or used to clear the locator.
      await request(path, 'POST', original.body, isStandingPath(path) ? standingAcknowledgment : isGoalModelPath(path) || isPolicyChangePath(path) || isPaymentPath(path) || isJointPath(path) || isDiscoveryPath(path) || isAssetPath(path) || isMaturityPath(path) || isPermissionPath(path) || isReinvestmentPrepare(path) || isLossPath(path) || isLossRevisionPath(path) ? (value) => { context(value, data); return value; } : undefined);
    } catch (cause) {
      setError(userError(cause));
      if (cause instanceof ApiError && cause.status === 401 && (isCatalogUserCommand(path) || isGoalModelPath(path) || isPolicyChangePath(path) || isPaymentPath(path) || isJointPath(path) || isDiscoveryPath(path) || isAssetPath(path) || isMaturityPath(path) || isPermissionPath(path) || isReinvestmentPrepare(path) || isLossPath(path) || isLossRevisionPath(path) || isQuestionPath(path) || isStandingPath(path))) setAuthBlocked(true);
    } finally { setBusy(false); }
  }
  async function runRead<T>(work: () => Promise<T>): Promise<T> { if (blocked) throw new Error('原请求尚未核实，请先保留当前内容。'); setBusy(true); setError(''); try { return await work(); } finally { setBusy(false); } }
  async function retryOriginal() {
    if (!pending || !data || busy || recovering.current || !authBlocked || (!isCatalogUserCommand(pending.path) && !isGoalModelPath(pending.path) && !isPolicyChangePath(pending.path) && !isPaymentPath(pending.path) && !isJointPath(pending.path) && !isDiscoveryPath(pending.path) && !isAssetPath(pending.path) && !isMaturityPath(pending.path) && !isPermissionPath(pending.path) && !isReinvestmentPrepare(pending.path) && !isLossPath(pending.path) && !isLossRevisionPath(pending.path) && !isQuestionPath(pending.path) && !isStandingPath(pending.path))) return;
    setBusy(true); setError('');
    try { if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('浏览器原请求已变化，请保留记录。'); await request(pending.path, 'POST', pending.body, isStandingPath(pending.path) ? standingAcknowledgment : (value) => { context(value, data); return value; }); setAuthBlocked(false); }
    catch (cause) { setError(userError(cause)); }
    finally { setBusy(false); }
  }
  async function resumeJointOriginal() {
    if (!pending || !data || busy || recovering.current || !isJointPath(pending.path) || !sameJointUpdate(jointUpdate, pending) || !jointUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原联合安排定位已变化，请保留记录。');
      const read = await readJointOriginal(pending, data);
      if (read.status !== 'PENDING' || !read.joint?.resume_original || !sameJointUpdate(read.joint, pending)) throw new Error('同一原安排只能继续核实，请勿重新发起。');
      await request(pending.path, 'POST', pending.body, (value) => { context(value, data); return value; });
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeAssetOriginal() {
    if (!pending || !data || busy || recovering.current || !isAssetPath(pending.path) || !sameAssetUpdate(assetUpdate, pending) || !assetUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原资产定位已变化，请保留记录。');
      const read = await readAssetOriginal(pending, data); if (read.status !== 'PENDING' || !read.asset?.resume_original || !sameAssetUpdate(read.asset, pending)) throw new Error('当前原组合只能继续核实，不会新建动作。');
      await request(`/zhiyu-next/assets/portfolios/${read.asset.recovery.portfolio_id}/continue-original`, 'POST', { expected_epoch_id: data.epoch_id, reviewed_portfolio_hash: read.asset.recovery.reviewed_portfolio_hash }, (value) => { context(value, data); return value; });
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumePaymentOriginal() {
    if (!pending || !data || busy || recovering.current || !isPaymentPath(pending.path) || !samePaymentUpdate(paymentUpdate, pending) || !paymentUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原付款定位已变化，请保留记录。');
      const read = await readPaymentOriginal(pending, data); const update = read.result as unknown as PaymentUpdate | undefined;
      if (read.status !== 'PENDING' || !update?.resume_original || !samePaymentUpdate(update, pending)) throw new Error('当前原付款只能继续核实，请勿重新发起。');
      if (update.partial_preparation) await request(pending.path, 'POST', pending.body, (value) => { context(value, data); return value; });
      else await request(`/zhiyu-next/payments/actions/${update.action!.action_id}/execute-original`, 'POST', { expected_epoch_id: data.epoch_id }, (value) => { context(value, data); return value; });
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeMaturityOriginal() {
    if (!pending || !data || busy || recovering.current || !isMaturityPath(pending.path) || !sameMaturityUpdate(maturityUpdate, pending) || !maturityUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原到期定位已变化，请保留记录。');
      const read = await readMaturityOriginal(pending, data); if (read.status !== 'PENDING' || !read.maturity?.resume_original || !sameMaturityUpdate(read.maturity, pending)) throw new Error('同一原到期动作只能继续核实，不会新建动作。');
      const action = read.maturity.action;
      await request(`/zhiyu-next/assets/maturity/actions/${action.action_id}/execute-original`, 'POST', { expected_epoch_id: action.epoch_id, reviewed_command_hash: action.reviewed_command_hash }, (value) => { context(value, data); return value; });
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeReinvestmentOriginal() {
    if (!pending || !data || busy || recovering.current || !sameReinvestmentUpdate(reinvestmentUpdate, pending) || !reinvestmentUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原再投定位已变化，请保留记录。');
      const read = await readReinvestmentOriginal(pending, data); const update = read.reinvestment;
      if (read.status !== 'PENDING' || !update?.resume_original || !sameReinvestmentUpdate(update, pending)) throw new Error('同一原再投只能继续核实，不会替换组合。');
      if (update.partial_preparation) await request(pending.path, 'POST', pending.body, (value) => { context(value, data); return value; });
      else await request(`/zhiyu-next/assets/portfolios/${update.recovery.portfolio_id}/continue-original`, 'POST', { expected_epoch_id: data.epoch_id, reviewed_portfolio_hash: update.recovery.reviewed_portfolio_hash }, (value) => { context(value, data); return value; });
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeLossOriginal() {
    if (!pending || !data || busy || recovering.current || !sameLossUpdate(lossUpdate, pending) || !lossUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原有损支取定位已变化，请保留记录。');
      const read = await readLossOriginal(pending, data); const update = read.loss;
      if (read.status !== 'PENDING' || !update?.resume_original || !sameLossUpdate(update, pending)) throw new Error('同一原有损支取只能继续核实，不会换报价或动作。');
      if (update.partial_preparation || update.view?.action.status === 'PLANNED' && pending.path.endsWith('/confirm-and-execute')) await request(pending.path, 'POST', pending.body, (value) => { context(value, data); return value; });
      else {
        if (!update.view || !['AUTHORIZED', 'SUBMITTED', 'UNKNOWN'].includes(update.view.action.status)) throw new Error('原动作确认尚未完成，请保留原请求。');
        await request(`/zhiyu-next/assets/lossy/actions/${update.recovery.action_id}/execute-original`, 'POST', { expected_epoch_id: data.epoch_id, reviewed_quote_hash: update.recovery.reviewed_quote_hash, reviewed_effect_hash: update.recovery.reviewed_effect_hash }, (value) => { context(value, data); return value; });
      }
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeRevisionOriginal() {
    if (!pending || !data || busy || recovering.current || !sameRevisionUpdate(revisionUpdate, pending) || !revisionUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原续报支取定位已变化，请保留记录。');
      const read = await readLossRevisionOriginal(pending, data); const update = read.revision;
      if (read.status !== 'PENDING' || !update?.resume_original || !sameRevisionUpdate(update, pending)) throw new Error('同一原新报价动作只能继续核实，不会再次续报或换键。');
      if (update.partial || update.view?.action.status === 'PLANNED' && pending.path.endsWith('/confirm-and-execute')) await request(pending.path, 'POST', pending.body, (value) => { context(value, data); return value; });
      else {
        if (!update.view || !['AUTHORIZED', 'SUBMITTED', 'UNKNOWN'].includes(update.view.action.status)) throw new Error('新报价原同意尚未完整，请保留原请求。');
        await request(`${revisionBase}/actions/${update.recovery.action_id}/execute-original`, 'POST', { expected_epoch_id: data.epoch_id, reviewed_quote_hash: update.recovery.reviewed_quote_hash, reviewed_effect_hash: update.recovery.reviewed_effect_hash }, (value) => { context(value, data); return value; });
      }
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function resumeStandingOriginal() {
    if (!pending || !data || busy || recovering.current || !isStandingPath(pending.path) || !standingUpdate?.resume_original) return;
    setBusy(true); setError('');
    try {
      if (JSON.stringify(restorePending(data)) !== JSON.stringify(pending)) throw new Error('原持续请求已变化，请保留定位。');
      const read = await readStandingOriginal(pending, data);
      if (read.status !== 'PENDING' || !read.standing?.partial || !read.standing.resume_original) throw new Error('原控制未证明可续接，请保留原请求。');
      await request(pending.path, 'POST', pending.body, standingAcknowledgment);
    } catch (cause) { setError(userError(cause)); } finally { setBusy(false); }
  }
  async function ask() {
    if (blocked || !text.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const parent = reply?.questions.length && reply.candidate?.can_confirm === false ? reply.client_request_id : undefined;
      setReply(await messageAgent(text.trim(), engine, crypto.randomUUID(), parent)); setText('');
    }
    catch (cause) { setError(userError(cause)); }
    finally { setBusy(false); }
  }
  const candidate = reply?.candidate;
  const unresolved = data?.actions.some((action) => ['UNKNOWN', 'SUBMITTED'].includes(action.status)) ?? false;
  const active = data?.autonomy.state === 'ACTIVE';
  const current = data?.actions.find((action) => !terminal(action)) ?? data?.actions.at(-1);
  const lastResult = typeof data?.autonomy.last_result === 'string' ? data.autonomy.last_result : data?.autonomy.last_result?.summary;
  const candidateCard = candidate && !reply?.questions.length && <section className="zy-card zyn-candidate" aria-label="待确认规则"><span className="zy-kicker">只需确认这个范围</span><h2>{businessText(String(candidate.compilation.configuration?.name ?? ruleNames[String(candidate.compilation.configuration?.type)] ?? '新的规则'))}</h2><p>{businessText(candidate.summary)}</p><ul>{candidate.compilation.configuration && ruleSummary(candidate.compilation.configuration).map((row) => <li key={row}>{row}</li>)}</ul>
    <p className="zy-muted">规则确认后，系统完成目标关联与必要准备。只有已到账资金可以安排，保护底线始终保留。</p><div className="zy-buttons"><button className="zy-primary" disabled={blocked || !candidate.can_confirm || !candidate.compilation.proposal_id || unresolved} onClick={() => void perform('/zhiyu-next/authorizations/confirm', { proposal_id: candidate.compilation.proposal_id, reviewed_hash: candidate.compilation.configuration_hash, accepted: true, expected_epoch_id: data!.epoch_id })}>{['goal_saving', 'long_term_goal'].includes(String(candidate.compilation.configuration?.type)) ? '确认并开启自动安排' : '确认规则'}</button><button className="zy-link" disabled={busy || !!pending} onClick={() => setReply(null)}>取消候选</button></div></section>;
  function navigate(index: number) { setPage(index); content.current?.focus(); }
  return <main className="zy-shell zyn-shell"><a className="zy-skip" href="#zyn-content">跳到当前内容</a>
    <header className="zy-header"><a className="zy-brand" href="#" onClick={(event) => { event.preventDefault(); navigate(0); }}><span className="zy-mark">知</span><span><strong>知余扩展版</strong><small>授权内持续安排 · 结果有据可查</small></span></a><div className="zyn-header-actions"><span className="zy-simulation">开发验证 · 模拟资金</span><button className="zy-link" onClick={() => setModelOpen((value) => !value)}>模型设置</button></div></header>
    <nav className="zy-nav" aria-label="知余页面导航">{pages.map((name, index) => <button key={name} aria-current={page === index ? 'page' : undefined} onClick={() => navigate(index)}>{name}</button>)}</nav>
    <div className="zy-toolbar"><span>{data ? <>最近核实 <Timestamp value={data.dashboard.as_of} /></> : '正在连接扩展版独立环境'}</span><button className="zy-link" disabled={stateQuery.isFetching || busy || !!pending} onClick={() => void refresh().catch((cause: unknown) => setError(userError(cause)))}>刷新当前状态</button></div>
    {modelOpen && <ModelPanel close={() => setModelOpen(false)} />}
    <div aria-live="polite">{stateQuery.isPending && <p className="zy-message" role="status">正在核实账户与现行规则…</p>}{busy && <p className="zy-message" role="status">正在处理，系统会完成后续步骤…</p>}{(error || stateQuery.isError || storageError) && <p className="zy-message zy-error" role="alert">{storageError || error || userError(stateQuery.error)}</p>}{notice && <p className="zy-message">{notice}</p>}{pending && <p className="zy-message" role="status">正在后台核对原请求。刷新页面仍会保留同一操作，不会重新安排一笔。</p>}</div>
    <div id="zyn-content" ref={content} tabIndex={-1} className="zy-content">
      {page === 0 && <><section className="zy-hero zyn-hero"><span className="zy-kicker">先保护生活，再自动安排</span><h1>告诉我你的目标，<br />其余在授权内持续完成。</h1><p>你确定范围，知余持续核对到账事实、资金边界和真实结果。</p></section>
        {data && <><section className="zy-boundary"><div><span className="zy-kicker">今天可安心安排</span><h2>可自主使用上限</h2><strong className="zy-amount">{money(data.dashboard.boundary.state === 'PROVEN' && data.dashboard.boundary.status !== 'INSUFFICIENT_EVIDENCE' ? data.dashboard.boundary.safe_idle_cents : null)}</strong><p>未来收入仅用于预测，到账前不扩大今天的额度。</p></div><div className="zy-boundary-next"><span className={`zyn-autonomy-indicator ${active ? 'is-active' : ''}`}>{active ? '自动安排已开启' : data.autonomy.state === 'PAUSED' ? '自动安排已暂停' : '等待首次授权'}</span><p>{businessText(data.autonomy.summary ?? (active ? '授权内的安全动作由后台处理。' : '先描述并确认你的规则。'))}</p>{active ? <button className="zy-secondary" disabled={blocked} onClick={() => void perform('/zhiyu-next/autonomy/pause', {})}>暂停自动安排</button> : data.autonomy.state === 'PAUSED' && data.autonomy.authorization_id ? <button className="zy-primary" disabled={blocked} onClick={() => void perform('/zhiyu-next/autonomy/resume', { authorization_id: data.autonomy.authorization_id, accepted: true })}>恢复自动安排</button> : <button className="zy-primary" onClick={() => document.getElementById('zyn-intent')?.focus()}>设置自动安排</button>}</div></section>
        <section className="zy-metrics"><article><span>账户现金</span><strong>{money(data.dashboard.account_facts.state === 'PROVEN' ? data.dashboard.account_facts.facts.cash_balance_cents : null)}</strong><p>以当前账户事实为准</p></article><article><span>已保护资金</span><strong>{money(data.dashboard.boundary.state === 'PROVEN' ? data.dashboard.boundary.current_protected_cents : null)}</strong><p>保护分项已处理重叠</p></article><article><span>正在核实</span><strong>{data.autonomy.pending_count} 项</strong><p>原结果未决时阻挡冲突动作</p></article></section>
        {unresolved && <section className="zy-card zyn-todo" aria-label="当前待处理事项"><h2>银行结果正在核实</h2><p>这一步由后台继续处理，你无需再次确认或重建操作。</p></section>}</>}
        <section className="zy-card zyn-agent" aria-label="Agent 对话"><div className="zy-section-heading"><h2>和知余说说你的安排</h2><span>{engine === 'llm' ? '模型理解 · 服务端核实' : '离线规则模板'}</span></div><label className="zy-field" htmlFor="zyn-intent">{reply?.questions.length ? '补充这个条件' : '你的需求'}<textarea id="zyn-intent" rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder={reply?.questions.length ? '只需补充当前这个问题的答案，原需求会保留。' : '例如：先留出三千元应急金，再按旅行目标安排。'} /></label><div className="zyn-agent-tools"><label>理解方式<select aria-label="理解方式" value={engine} onChange={(event) => setEngine(event.target.value as 'llm' | 'rules')}><option value="llm">已配置模型</option><option value="rules">离线模板</option></select></label><div className="zy-buttons">{reply && <button className="zy-link" disabled={busy || !!pending} onClick={() => { setReply(null); setText(''); }}>开始新的需求</button>}<button className="zy-primary" disabled={blocked || !text.trim()} onClick={() => void ask()}>{reply?.questions.length ? '补充回答' : '发送给知余'}</button></div></div>{reply && <div className="zyn-reply"><p>{businessText(reply.reply)}</p><span className="zy-muted">{reply.model_used ? '已使用实际模型，候选仍需服务端核实。' : '本次由离线规则处理。'}</span>{reply.questions[0] && <p className="zyn-question">{businessText(reply.questions[0])}</p>}</div>}</section>{candidateCard}
        {current && data && <ActionResult action={current} environment={data} blocked={blocked} confirm={() => void perform(`/zhiyu-next/actions/${current.action_id}/confirm-and-execute`, { accepted: true, effect_hash: current.effect_hash, expected_epoch_id: data.epoch_id })} />}
        {lastResult && <p className="zy-message">{businessText(lastResult)}</p>}
        {data && <section className="zy-card"><h2>这些钱先被守住</h2><div className="zy-protection">{Object.entries(data.dashboard.boundary.current_protected_cents_by_reason ?? {}).map(([reason, value]) => <div key={reason}><span>{protection[reason] ?? '已确认保护'}</span><strong>{money(value)}</strong></div>)}</div><p className="zy-muted">分项可能重叠，请以保护总额为准。</p></section>}</>}
      {page === 1 && <><section className="zy-page-title"><span className="zy-kicker">只确认必要范围</span><h1>我的规则</h1><p>先预览保护与安排范围，再一次确认。规则有效且范围足够时，不再逐笔询问。</p></section><section className="zy-card"><h2>从模板开始</h2><div className="zy-buttons">{presets.data?.intents.map((preset) => <button className="zy-secondary" disabled={blocked} key={preset.id} onClick={() => { setReply(null); setText(preset.text); setEngine('rules'); navigate(0); }}>{businessText(preset.title)}</button>)}</div>{presets.isError && <p role="alert">{userError(presets.error)}</p>}<p className="zy-muted">选模板只填写需求，不会直接产生权限或资金动作。</p></section>{candidateCard}
        {data && <PolicyWorkspace environment={data} blocked={blocked} candidate={policyCandidate} completed={policyCompleted} perform={perform} runRead={runRead} review={changeReview} commit={changeCommit} paymentUpdate={paymentUpdate} discoveryUpdate={discoveryUpdate} openGoalLink={openGoalLink} resumePaymentOriginal={!operationChecking && !busy && paymentUpdate?.resume_original ? resumePaymentOriginal : undefined} editChange={() => { setChangeReview(null); setChangeCommit(null); }} retryOriginal={authBlocked && !operationChecking && !busy ? retryOriginal : undefined} editCandidate={() => { setPolicyCandidate(null); setPolicyCompleted(null); }} />}
        {data && <StandingPanel environment={data} blocked={blocked} update={standingUpdate} recovering={!!pending && isStandingPath(pending.path)} perform={perform} runRead={runRead} edited={() => setStandingUpdate(null)} retryOriginal={authBlocked && !operationChecking && !busy ? retryOriginal : undefined} resumeOriginal={!operationChecking && !busy && standingUpdate?.resume_original ? resumeStandingOriginal : undefined} />}
        {data?.capabilities.length ? <section className="zy-card"><h2>本轮开放能力</h2><div className="zyn-capabilities">{data.capabilities.map((capability) => <article key={capability.id}><strong>{businessText(capability.title)}</strong><span className="zy-muted">{['AVAILABLE', 'VERIFIED', 'ENABLED'].includes(capability.status) ? '已开放' : '尚未开放'}</span>{capability.reason && <p>{businessText(capability.reason)}</p>}</article>)}</div></section> : null}</>}
      {page === 2 && <><section className="zy-page-title"><span className="zy-kicker">目标、进度与实际结果</span><h1>目标与执行</h1><p>确认规则后，系统完成必要关联与准备。已到账资金和实际回执决定当前进度。</p></section>
        {data && <GoalsPanel environment={data} blocked={blocked} update={jointUpdate} recovering={!!pending && isJointPath(pending.path)} repairUpdate={goalModelUpdate} repairRecovering={!!pending && isGoalModelPath(pending.path)} perform={perform} runRead={runRead} retryOriginal={authBlocked && !operationChecking && !busy ? retryOriginal : undefined} resumeOriginal={!operationChecking && !busy && jointUpdate?.resume_original ? resumeJointOriginal : undefined} />}
        {data && <AssetsPanel environment={data} blocked={blocked} update={assetUpdate} recovering={!!pending && isAssetPath(pending.path) && !pending.reinvestment_recovery} perform={perform} runRead={runRead} retryOriginal={authBlocked && !operationChecking && !busy ? retryOriginal : undefined} resumeOriginal={!operationChecking && !busy && assetUpdate?.resume_original ? resumeAssetOriginal : undefined} maturityUpdate={maturityUpdate} maturityRecovering={!!pending && isMaturityPath(pending.path)} resumeMaturityOriginal={!operationChecking && !busy && maturityUpdate?.resume_original ? resumeMaturityOriginal : undefined} permissionUpdate={permissionUpdate} permissionEdited={() => setPermissionUpdate(null)} permissionRecovering={!!pending && isPermissionPath(pending.path)} reinvestmentUpdate={reinvestmentUpdate} reinvestmentRecovering={!!pending && !!pending.reinvestment_recovery} resumeReinvestmentOriginal={!operationChecking && !busy && reinvestmentUpdate?.resume_original ? resumeReinvestmentOriginal : undefined} revisionUpdate={revisionUpdate} revisionRecovering={!!pending && isLossRevisionPath(pending.path)} resumeRevisionOriginal={!operationChecking && !busy && revisionUpdate?.resume_original ? resumeRevisionOriginal : undefined} lossUpdate={lossUpdate} lossRecovering={!!pending && isLossPath(pending.path)} resumeLossOriginal={!operationChecking && !busy && lossUpdate?.resume_original ? resumeLossOriginal : undefined} questionUpdate={questionUpdate} questionRecovering={!!pending && isQuestionPath(pending.path)} openGoalLink={() => { setOpenGoalLink(true); navigate(1); }} />}
        {!data?.goals.length && <section className="zy-card"><h2>还没有已关联的目标</h2><p>描述你的目标并确认规则，系统将完成关联。</p><button className="zy-primary" onClick={() => navigate(0)}>向知余描述目标</button></section>}
        {data?.goals.map((goal) => <section key={goal.id} className="zy-card"><div className="zy-section-heading"><h2>{businessText(goal.name)}</h2><span>截止 {goal.deadline}</span></div><strong className="zy-amount zy-amount-small">{money(goal.allocated_cents)} <small>/ {money(goal.target_cents)}</small></strong><progress value={goal.allocated_cents} max={goal.target_cents} aria-label={`${businessText(goal.name)}进度`} /><p>月度上限 {money(goal.monthly_max_cents)}，本轮进度来自服务端真实读数。</p></section>)}
        {data?.actions.map((action) => <ActionResult key={action.action_id} action={action} environment={data} blocked={blocked} confirm={() => void perform(`/zhiyu-next/actions/${action.action_id}/confirm-and-execute`, { accepted: true, effect_hash: action.effect_hash, expected_epoch_id: data.epoch_id })} />)}
        {data && <section className="zy-card"><h2>当前持有资产</h2><strong className="zy-event-amount">{money(data.dashboard.managed_assets.state === 'PROVEN' ? data.dashboard.managed_assets.managed_current_principal_cents : null)}</strong><p className="zy-muted">多目标联合执行、年度预测和资产梯度按本轮能力声明逐项开放。</p></section>}</>}
      {page === 3 && <><section className="zy-page-title"><span className="zy-kicker">每次决定有据可查</span><h1>活动记录</h1><p>允许、拒绝、暂停与恢复均来自服务端记录。结果未决会保持正在核实。</p></section><section className="zy-timeline">{!data?.activity.length && <p className="zy-card">暂时没有活动。确认规则后，知余会持续处理授权内的到账事件。</p>}{data?.activity.slice().reverse().map((activity) => <article key={activity.id} className="zy-timeline-item"><div className="zy-timeline-time"><Timestamp value={activity.at} /></div><div className="zy-card"><div className="zy-section-heading"><h2>{businessText(activity.intent)}</h2><Status value={activity.status} /></div><p>{businessText(activity.decision)}</p>{activity.amount_cents !== null && <strong className="zy-event-amount">{money(activity.amount_cents)}</strong>}<p className="zy-muted">依据：{businessText(activity.authorization)}</p></div></article>)}</section></>}
    </div>
    <footer className="zy-footer"><span>知余扩展版 · 开发验证 · 所有资金动作均为模拟</span><button className="zy-link" onClick={() => setDiagnosticOpen((value) => !value)}>{diagnosticOpen ? '关闭开发诊断' : '开发诊断'}</button></footer>
    {diagnosticOpen && <section className="zy-card zyn-diagnostic" aria-label="开发诊断"><h2>开发诊断与模拟事实控制</h2><p>仅用于独立扩展环境验证，普通用户流程不需要这些控制。到账金额与日期由服务端预置。</p><div className="zy-buttons"><button className="zy-secondary" disabled={blocked} onClick={() => void perform('/zhiyu-next/demo/income', { expected_epoch_id: data!.epoch_id, event: 'PAYROLL_A' })}>发送第一笔预置到账事件</button><button className="zy-secondary" disabled={blocked} onClick={() => void perform('/zhiyu-next/demo/income', { expected_epoch_id: data!.epoch_id, event: 'PAYROLL_B' })}>发送第二笔预置到账事件</button></div><pre>{JSON.stringify({ environment_id: data?.environment_id, epoch_id: data?.epoch_id, autonomy: data?.autonomy, pending, actions: data?.actions.map((action) => ({ action_id: action.action_id, effect_hash: action.effect_hash, status: action.status })), capabilities: data?.capabilities }, null, 2)}</pre></section>}
  </main>;
}

