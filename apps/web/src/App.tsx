import { useEffect, useRef, useSyncExternalStore } from 'react';
import DashboardPage from './pages/DashboardPage';
import PolicyCenterPage from './pages/PolicyCenterPage';
import GoalsPage from './pages/GoalsPage';
import DecisionTracePage from './pages/DecisionTracePage';
import DemoConsolePage from './pages/DemoConsolePage';
import AnnualPlanningPage from './pages/AnnualPlanningPage';
import EvidencePage from './pages/EvidencePage';
import DeliveryPage from './pages/DeliveryPage';
import FullPoliciesPanel from './components/FullPoliciesPanel';
import MultiTemplateFinancialPreviewPanel from './components/MultiTemplateFinancialPreviewPanel';
import RegisteredActionSetPanel from './components/RegisteredActionSetPanel';
import OnboardingPage from './pages/OnboardingPage';
import OneQuestionPage from './pages/OneQuestionPage';
import SpendingEvidencePage from './pages/SpendingEvidencePage';
import FullProductsPage from './pages/FullProductsPage';
import ReconciliationPage from './pages/ReconciliationPage';
import ScenarioSimulationPage from './pages/ScenarioSimulationPage';
import ScenarioRiskReviewPage from './pages/ScenarioRiskReviewPage';
import InterventionCenterPage from './pages/InterventionCenterPage';
import LocalActorSessionPanel from './components/LocalActorSessionPanel';
import { FutureIncomeOriginalRecoveryPanel } from './components/FutureIncomePlanningPanel';
import { SeasonalAdoptionOriginalRecoveryPanel } from './components/SeasonalReserveAdoptionPanel';
import RecoveryComposedObservationPanel, { RecoveryComposedObservationOriginalRecoveryPanel } from './components/RecoveryComposedObservationPanel';
import { FullMaturityOriginalRecoveryPanel } from './components/FullMaturityExecutionPanel';
import FullJointGoalExecutionPanel, { FullJointGoalOriginalRecoveryPanel } from './components/FullJointGoalExecutionPanel';
import { recoverDemoOperation, useDemoOperation } from './features/demo-operation';
import { useWriteInFlight } from './features/write-flight';
import { recoverFullPolicyOperation, useFullPolicyOperation } from './features/full-policy-operation';
import { recoverOnboardingDraft, useOnboardingDraft } from './features/onboarding-draft';
import { recoverFullGoalOperation, useFullGoalOperation } from './features/full-goal-operation';
import { recoverOneQuestionOperation, useOneQuestionOperation } from './features/one-question-operation';
import { recoverSpendingEvidenceOperation, useSpendingEvidenceOperation } from './features/spending-evidence-operation';
import { recoverInterventionOperation, useInterventionOperation } from './features/intervention-operation';
import { recoverGoalReleaseAuthorizationOperation, useGoalReleaseAuthorizationOperation } from './features/goal-release-authorization-operation';
import { recoverGoalCashReleaseOperation, useGoalCashReleaseOperation } from './features/goal-cash-release-operation';
import { recoverFullAssetExecutionOperation, useFullAssetExecutionOperation } from './features/full-asset-execution-operation';
import { recoverFixedPaymentOperation, useFixedPaymentOperation } from './features/fixed-payment-operation';
import { isDynamicGoalWorkspaceUnresolved, recoverDynamicGoalOperation, useDynamicGoalOperation } from './features/full-dynamic-goal-operation';
import { isFullRecoveryWorkspaceUnresolved, recoverFullRecoveryOperation, useFullRecoveryOperation } from './features/full-recovery-execution-operation';
import { isFutureIncomeWorkspaceUnresolved, recoverFutureIncomeOperation, useFutureIncomeOperation } from './features/future-income-operation';
import { recoverSeasonalAdoptionOperation, useSeasonalAdoptionOperation } from './features/seasonal-adoption-operation';
import { recoverRecoveryComposedObservationOperation, useRecoveryComposedObservationOperation } from './features/recovery-composed-observation-operation';
import { isFullMaturityWorkspaceUnresolved, recoverFullMaturityOperation, useFullMaturityOperation } from './features/full-maturity-execution-operation';
import { recoverFullJointGoalOperation, useFullJointGoalOperation } from './features/full-joint-goal-operation';

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback);
  return () => window.removeEventListener('hashchange', callback);
}
export default function App() {
  const operation = useDemoOperation(); const writing = useWriteInFlight();
  const fullOperation = useFullPolicyOperation();
  const onboarding = useOnboardingDraft();
  const goalOperation = useFullGoalOperation();
  const questionOperation = useOneQuestionOperation();
  const spendingOperation = useSpendingEvidenceOperation();
  const interventionOperation = useInterventionOperation();
  const releaseOperation = useGoalReleaseAuthorizationOperation();
  const cashReleaseOperation = useGoalCashReleaseOperation();
  const assetOperation = useFullAssetExecutionOperation();
  const paymentOperation = useFixedPaymentOperation();
  const dynamicOperation = useDynamicGoalOperation();
  const recoveryOperation = useFullRecoveryOperation();
  const futureOperation = useFutureIncomeOperation();
  const seasonalOperation = useSeasonalAdoptionOperation();
  const observationOperation = useRecoveryComposedObservationOperation();
  const maturityOperation = useFullMaturityOperation();
  const jointOperation = useFullJointGoalOperation();
  useEffect(() => { void recoverFullJointGoalOperation(); }, []);
  useEffect(() => { void recoverFullMaturityOperation(); }, []);
  useEffect(() => { void recoverRecoveryComposedObservationOperation(); }, []);
  useEffect(() => { void recoverSeasonalAdoptionOperation(); }, []);
  useEffect(() => { void recoverFutureIncomeOperation(); }, []);
  useEffect(() => { void recoverFullRecoveryOperation(); }, []);
  useEffect(() => { void recoverDynamicGoalOperation(); }, []);
  useEffect(() => { void recoverFullAssetExecutionOperation(); void recoverFixedPaymentOperation(); }, []);
  useEffect(() => { recoverDemoOperation(); recoverFullPolicyOperation(); recoverOnboardingDraft(); recoverFullGoalOperation(); recoverOneQuestionOperation(); recoverSpendingEvidenceOperation(); recoverInterventionOperation(); recoverGoalReleaseAuthorizationOperation(); void recoverGoalCashReleaseOperation(); }, []);
  const onboardingBlocked = !!onboarding.draft.pending || onboarding.busy || !!onboarding.storage_error;
  const observationBlocked = !!observationOperation.pending || observationOperation.busy || observationOperation.recovering || !!observationOperation.storage_error;
  const maturityBlocked = !!maturityOperation.pending || maturityOperation.busy || maturityOperation.recovering || !!maturityOperation.storage_error || (!!maturityOperation.workspace && isFullMaturityWorkspaceUnresolved(maturityOperation.workspace));
  const jointBlocked = !!jointOperation.pending || !!jointOperation.workspace_reference || jointOperation.busy || jointOperation.recovering || !!jointOperation.storage_error;
  const primaryBlocked = !!operation.pending || operation.busy || !!operation.storage_error || writing || onboardingBlocked;
  const otherBlocked = primaryBlocked || observationBlocked || maturityBlocked || jointBlocked;
  const fullBlocked = !!fullOperation.pending || fullOperation.busy || !!fullOperation.storage_error;
  const goalBlocked = !!goalOperation.pending || goalOperation.busy || !!goalOperation.storage_error;
  const questionBlocked = !!questionOperation.pending || questionOperation.busy || !!questionOperation.storage_error;
  const spendingBlocked = !!spendingOperation.pending || spendingOperation.busy || !!spendingOperation.storage_error;
  const interventionBlocked = !!interventionOperation.pending || interventionOperation.busy || !!interventionOperation.storage_error;
  const releaseBlocked = !!releaseOperation.pending || releaseOperation.busy || !!releaseOperation.storage_error;
  const cashReleaseBlocked = !!cashReleaseOperation.pending || cashReleaseOperation.busy || cashReleaseOperation.recovering || !!cashReleaseOperation.storage_error;
  const assetBlocked = !!assetOperation.pending || assetOperation.busy || assetOperation.recovering || !!assetOperation.storage_error;
  const paymentBlocked = !!paymentOperation.pending || paymentOperation.busy || paymentOperation.recovering || !!paymentOperation.storage_error;
  const dynamicBlocked = !!dynamicOperation.pending || dynamicOperation.busy || dynamicOperation.recovering || !!dynamicOperation.storage_error || (!!dynamicOperation.workspace && isDynamicGoalWorkspaceUnresolved(dynamicOperation.workspace));
  const recoveryBlocked = !!recoveryOperation.pending || recoveryOperation.busy || recoveryOperation.recovering || !!recoveryOperation.storage_error || (!!recoveryOperation.workspace && isFullRecoveryWorkspaceUnresolved(recoveryOperation.workspace));
  const futureBlocked = !!futureOperation.pending || futureOperation.busy || futureOperation.recovering || !!futureOperation.storage_error || (!!futureOperation.workspace && isFutureIncomeWorkspaceUnresolved(futureOperation.workspace));
  const seasonalBlocked = !!seasonalOperation.pending || seasonalOperation.busy || seasonalOperation.recovering || !!seasonalOperation.storage_error;
  const executionBlocked = assetBlocked || paymentBlocked || dynamicBlocked || recoveryBlocked || futureBlocked || seasonalBlocked;
  const observationMutationBlocked = primaryBlocked || maturityBlocked || jointBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked;
  const maturityMutationBlocked = primaryBlocked || observationBlocked || jointBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked;
  const jointMutationBlocked = primaryBlocked || observationBlocked || maturityBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked;
  const financialPreviewBlocked = otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked;
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => '');
  const content = useRef<HTMLDivElement>(null);
  const previousRoute = useRef(hash);
  useEffect(() => { if (previousRoute.current !== hash) { previousRoute.current = hash; content.current?.focus(); } }, [hash]);
  const page = hash === '#simulation-risk' ? 'simulation-risk' : hash === '#simulation' ? 'simulation' : hash === '#interventions' ? 'interventions' : hash === '#reconciliation' ? 'reconciliation' : hash === '#products' ? 'products' : hash === '#spending-evidence' ? 'spending-evidence' : hash === '#questions' ? 'questions' : hash === '#onboarding' ? 'onboarding' : hash === '#delivery' ? 'delivery' : hash === '#annual' ? 'annual' : hash === '#evidence' || hash.startsWith('#evidence/') ? 'evidence' : hash === '#demo' ? 'demo' : hash === '#policies' ? 'policies' : hash === '#goals' ? 'goals' : hash === '#decisions' || hash.startsWith('#decisions/') ? 'decisions' : 'overview';
  const runId = hash.startsWith('#decisions/') ? hash.slice('#decisions/'.length) : undefined;
  const evidenceParts = hash.startsWith('#evidence/') ? hash.slice('#evidence/'.length).split('/') : [];
  return <main className="app-shell">
    <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); content.current?.focus(); }}>跳到当前页面内容</a>
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">知</span>
      <div><h1>知余</h1><p>ZHIYU</p></div></div><span className="simulation-badge">模拟环境</span></header>
    <details><summary>本地用户身份会话</summary><LocalActorSessionPanel allowSameUserReauthentication mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /></details>
    <nav className="main-nav" aria-label="页面导航">
      <a href="#overview" aria-current={page === 'overview' ? 'page' : undefined}>资金总览</a>
      <a href="#onboarding" aria-current={page === 'onboarding' ? 'page' : undefined}>首次引导</a>
      <a href="#policies" aria-current={page === 'policies' ? 'page' : undefined}>策略中心</a>
      <a href="#goals" aria-current={page === 'goals' ? 'page' : undefined}>目标储备</a>
      <a href="#products" aria-current={page === 'products' ? 'page' : undefined}>产品与期限</a>
      <a href="#annual" aria-current={page === 'annual' ? 'page' : undefined}>年度规划</a>
      <a href="#questions" aria-current={page === 'questions' ? 'page' : undefined}>一次一问</a>
      <a href="#interventions" aria-current={page === 'interventions' ? 'page' : undefined}>介入中心</a>
      <a href="#spending-evidence" aria-current={page === 'spending-evidence' ? 'page' : undefined}>消费证据</a>
      <a href="#evidence" aria-current={page === 'evidence' ? 'page' : undefined}>事实与证据</a>
      <a href="#delivery" aria-current={page === 'delivery' ? 'page' : undefined}>投递记录</a>
      <a href="#reconciliation" aria-current={page === 'reconciliation' ? 'page' : undefined}>对账中心</a>
      <a href="#simulation-risk" aria-current={page === 'simulation-risk' ? 'page' : undefined}>分项反事实评估</a>
      <a href="#simulation" aria-current={page === 'simulation' ? 'page' : undefined}>场景模拟器</a>
      <a href="#decisions" aria-current={page === 'decisions' ? 'page' : undefined}>决策轨迹</a>
      <a href="#demo" aria-current={page === 'demo' ? 'page' : undefined}>演示控制台</a>
    </nav>
    <p className="simulation-note">所有资金动作均为模拟，未接入真实银行账户、支付或理财交易接口。</p>
    {(operation.busy || operation.pending || operation.storage_error || writing) && page !== 'demo' && <p className="notice">{operation.busy || writing ? '原资金或策略请求正在处理。' : '演示原命令结果尚待核对。'}其他资金表单暂不可提交；可读取总览、轨迹或前往控制台核对原项。</p>}
    {fullBlocked && <p className="notice">完整版策略原请求正在处理或待核对，资金表单暂不可提交；可前往策略中心只读核对原命令。</p>}
    {onboardingBlocked && <p className="notice">引导原候选请求正在处理或待核对，资金表单暂不可提交；可前往首次引导核对原候选。</p>}
    {goalBlocked && <p className="notice">完整目标原确认正在处理或待核对，资金表单暂不可提交；可前往目标储备只读核对原请求。</p>}
    {questionBlocked && <p className="notice">一次一问原请求正在处理或待核对，资金表单暂不可提交；可前往一次一问只读核对原键。</p>}
    {spendingBlocked && <p className="notice">消费分类原请求正在处理或待核对，资金表单暂不可提交；可前往消费证据只读核对原分类请求。</p>}
    {interventionBlocked && <p className="notice">通知原请求正在处理或待核对，其他变更暂不可提交；可前往介入中心只读核对原通知。</p>}
    {cashReleaseBlocked && <p className="notice">原现金回拨请求正在处理或待核对，其他资金变更暂不可提交；可前往目标储备只读核对原行动与回执。</p>}
    {releaseBlocked && <p className="notice">专用回拨授权原请求正在处理或待核对，其他变更暂不可提交；可前往目标储备只读核对原授权。</p>}
    {assetBlocked && <p className="notice">原资产组合或固定批次正在处理或待核对，其他资金变更暂不可提交；可前往产品与期限独立读取原结果。</p>}
    {paymentBlocked && <p className="notice">固定付款原请求正在处理或待核对，其他资金变更暂不可提交；可前往策略中心独立读取原请求。</p>}
    {recoveryBlocked && <p className="notice">原安全恢复请求或固定动作尚未终局，其他资金变更暂不可提交；可前往策略中心独立核对原body、键、确认与回执。</p>}
    {dynamicBlocked && <p className="notice">动态目标原请求或固定动作尚未终局，其他资金变更暂不可提交；可前往目标储备独立核对原键与回执。</p>}
    {futureBlocked && <p className="notice">未来收入原声明或复核工作区尚未核对，其他资金变更暂不可提交；可前往年度规划继续复核，原请求始终可独立读取。</p>}
    {seasonalBlocked && <p className="notice">季节采纳原请求正在处理或待核对，其他资金变更暂不可提交；完整原键GET始终可独立核对。</p>}
    {observationBlocked && <p className="notice">组合观察原请求正在处理或待核对，其他变更暂不可提交；完整原观察仍可独立读取。</p>}
    <FutureIncomeOriginalRecoveryPanel />
    <SeasonalAdoptionOriginalRecoveryPanel />
    <RecoveryComposedObservationOriginalRecoveryPanel mutationBlocked={observationMutationBlocked} />
    {maturityBlocked && <p className="notice">到期原请求或银行结果仍待核对，其他变更暂不可提交；固定原键只读核对始终可达。</p>}
    <FullMaturityOriginalRecoveryPanel />
    {jointBlocked && <p className="notice">联合目标原计划或固定子动作仍待核对，其他变更暂不可提交；完整原计划只读核对始终可达。</p>}
    <FullJointGoalOriginalRecoveryPanel />
    <div key={operation.read_generation} ref={content} id="main-content" className="page-content" role="region" aria-label="当前页面内容" tabIndex={-1}>{page === 'policies' ? <><fieldset className="page-operation-gate" disabled={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked}><PolicyCenterPage /></fieldset><MultiTemplateFinancialPreviewPanel mutationBlocked={financialPreviewBlocked} /><RegisteredActionSetPanel /><RecoveryComposedObservationPanel mutationBlocked={observationMutationBlocked} showOriginalRecovery={false} /><FullPoliciesPanel maturityMutationBlocked={maturityMutationBlocked} paymentMutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || assetBlocked || dynamicBlocked || recoveryBlocked || futureBlocked || seasonalBlocked} recoveryMutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || assetBlocked || paymentBlocked || dynamicBlocked || futureBlocked || seasonalBlocked} seasonalMutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || assetBlocked || paymentBlocked || dynamicBlocked || recoveryBlocked || futureBlocked} blocked={otherBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /></>
      : page === 'goals' ? <><GoalsPage dynamicExecutionBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || assetBlocked || paymentBlocked || recoveryBlocked || futureBlocked || seasonalBlocked} cashExecutionBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || executionBlocked} mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} modelBlocked={otherBlocked || fullBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /><FullJointGoalExecutionPanel mutationBlocked={jointMutationBlocked} /></>
        : page === 'questions' ? <OneQuestionPage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} />
          : page === 'spending-evidence' ? <SpendingEvidencePage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} />
            : page === 'interventions' ? <InterventionCenterPage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} />
              : page === 'simulation-risk' ? <ScenarioRiskReviewPage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /> : page === 'simulation' ? <ScenarioSimulationPage /> : page === 'reconciliation' ? <ReconciliationPage /> : page === 'products' ? <FullProductsPage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || paymentBlocked || dynamicBlocked || recoveryBlocked || futureBlocked || seasonalBlocked} /> : page === 'onboarding' ? <OnboardingPage mutationBlocked={observationBlocked || maturityBlocked || jointBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /> : page === 'annual' ? <AnnualPlanningPage mutationBlocked={otherBlocked || fullBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || assetBlocked || paymentBlocked || dynamicBlocked || recoveryBlocked || seasonalBlocked} /> : page === 'evidence' ? <EvidencePage kind={evidenceParts[0]} identity={evidenceParts.length === 2 ? evidenceParts[1] : undefined} /> : page === 'delivery' ? <DeliveryPage /> : page === 'demo' ? <DemoConsolePage mutationBlocked={observationBlocked || maturityBlocked || jointBlocked || fullBlocked || onboardingBlocked || goalBlocked || questionBlocked || spendingBlocked || interventionBlocked || releaseBlocked || cashReleaseBlocked || executionBlocked} /> : page === 'decisions' ? <DecisionTracePage runId={runId} /> : <DashboardPage />}</div>
    <footer className="app-footer"><span>知余 · 竞赛模拟原型</span><span>财务计算、策略权限与原操作核验分别展示</span></footer>
  </main>;
}
