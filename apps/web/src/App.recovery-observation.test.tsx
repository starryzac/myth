import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import App from './App';

const pendingGates = vi.hoisted(() => ({ observation: true, maturity: false, joint: false }));
beforeEach(() => { pendingGates.observation = true; pendingGates.maturity = false; pendingGates.joint = false; });
vi.mock('./features/full-joint-goal-operation', () => ({
  useFullJointGoalOperation: () => ({ pending: null, workspace_reference: pendingGates.joint ? { synthetic: 'restored original locator gate only' } : null, busy: false, recovering: false, storage_error: null }),
  recoverFullJointGoalOperation: async () => undefined,
}));
vi.mock('./components/FullJointGoalExecutionPanel', () => ({
  default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>联合目标族外部门</button>,
  FullJointGoalOriginalRecoveryPanel: () => <button>独立原联合GET</button>,
}));
vi.mock('./features/recovery-composed-observation-operation', () => ({
  useRecoveryComposedObservationOperation: () => ({ pending: pendingGates.observation ? { synthetic: 'identity gate only' } : null, busy: false, recovering: false, storage_error: null }),
  recoverRecoveryComposedObservationOperation: async () => undefined,
}));
vi.mock('./features/full-maturity-execution-operation', () => ({
  useFullMaturityOperation: () => ({ pending: pendingGates.maturity ? { synthetic: 'identity gate only' } : null, busy: false, recovering: false, storage_error: null, workspace: null }),
  recoverFullMaturityOperation: async () => undefined,
  isFullMaturityWorkspaceUnresolved: () => false,
}));
vi.mock('./components/FullMaturityExecutionPanel', () => ({
  FullMaturityOriginalRecoveryPanel: () => <button>独立原到期GET</button>,
}));
vi.mock('./components/RecoveryComposedObservationPanel', () => ({
  default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>观察族外部门</button>,
  RecoveryComposedObservationOriginalRecoveryPanel: () => <button>独立原观察GET</button>,
}));
vi.mock('./components/LocalActorSessionPanel', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>身份变更</button> }));
vi.mock('./pages/PolicyCenterPage', () => ({ default: () => <button>原策略变更</button> }));
vi.mock('./components/MultiTemplateFinancialPreviewPanel', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>多模板财务预览</button> }));
vi.mock('./components/RegisteredActionSetPanel', () => ({ default: () => <button>注册动作集合只读GET</button> }));
vi.mock('./components/FullPoliciesPanel', () => ({ default: (props: { blocked?: boolean; recoveryMutationBlocked?: boolean; paymentMutationBlocked?: boolean; seasonalMutationBlocked?: boolean; maturityMutationBlocked?: boolean }) => <>
  <button disabled={props.blocked}>完整版策略变更</button><button disabled={props.recoveryMutationBlocked}>恢复资金</button><button disabled={props.paymentMutationBlocked}>周期付款</button><button disabled={props.seasonalMutationBlocked}>季节采纳</button><button disabled={props.maturityMutationBlocked}>到期族外部门</button>
</> }));
vi.mock('./pages/GoalsPage', () => ({ default: (props: { mutationBlocked?: boolean; dynamicExecutionBlocked?: boolean; cashExecutionBlocked?: boolean; modelBlocked?: boolean }) => <>
  <button disabled={props.mutationBlocked}>目标变更</button><button disabled={props.dynamicExecutionBlocked}>动态归属</button><button disabled={props.cashExecutionBlocked}>现金回拨</button><button disabled={props.modelBlocked}>模型变更</button>
</> }));
vi.mock('./pages/FullProductsPage', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>资产购买</button> }));
vi.mock('./pages/AnnualPlanningPage', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>未来收入变更</button> }));
vi.mock('./pages/OnboardingPage', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>首次候选变更</button> }));
vi.mock('./pages/DemoConsolePage', () => ({ default: ({ mutationBlocked }: { mutationBlocked?: boolean }) => <button disabled={mutationBlocked}>演示重置</button> }));
vi.mock('./pages/DashboardPage', () => ({ default: () => <p>只读总览</p> }));
vi.mock('./components/FutureIncomePlanningPanel', () => ({ FutureIncomeOriginalRecoveryPanel: () => null }));
vi.mock('./components/SeasonalReserveAdoptionPanel', () => ({ SeasonalAdoptionOriginalRecoveryPanel: () => null }));

test('未决观察跨页阻挡其它变更，原观察 GET 在页面门之外仍可达', () => {
  window.location.hash = '#policies';
  render(<App />);
  for (const name of ['身份变更', '原策略变更', '完整版策略变更', '恢复资金', '周期付款', '季节采纳', '多模板财务预览']) expect(screen.getByRole('button', { name })).toBeDisabled();
  expect(screen.getByRole('button', { name: '注册动作集合只读GET' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '观察族外部门' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '到期族外部门' })).toBeDisabled();
  const original = screen.getByRole('button', { name: '独立原观察GET' });
  expect(original).toBeEnabled(); expect(original.closest('fieldset')).toBeNull();
  for (const [hash, names] of [
    ['#goals', ['目标变更', '动态归属', '现金回拨', '模型变更', '联合目标族外部门']],
    ['#products', ['资产购买']], ['#annual', ['未来收入变更']],
    ['#onboarding', ['首次候选变更']], ['#demo', ['演示重置']],
  ] as const) {
    window.location.hash = hash; fireEvent(window, new HashChangeEvent('hashchange'));
    for (const name of names) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.getByRole('button', { name: '独立原观察GET' })).toBeEnabled();
  }
});

test('未决到期跨页阻挡其它变更，自己的恢复入口和原件GET保持可达', () => {
  pendingGates.observation = false; pendingGates.maturity = true;
  window.location.hash = '#policies'; render(<App />);
  for (const name of ['身份变更', '原策略变更', '完整版策略变更', '恢复资金', '周期付款', '季节采纳', '观察族外部门', '多模板财务预览']) expect(screen.getByRole('button', { name })).toBeDisabled();
  expect(screen.getByRole('button', { name: '注册动作集合只读GET' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '到期族外部门' })).toBeEnabled();
  const original = screen.getByRole('button', { name: '独立原到期GET' });
  expect(original).toBeEnabled(); expect(original.closest('fieldset')).toBeNull();
  expect(screen.getByRole('button', { name: '独立原观察GET' })).toBeEnabled();
  for (const [hash, names] of [
    ['#goals', ['目标变更', '动态归属', '现金回拨', '模型变更', '联合目标族外部门']],
    ['#products', ['资产购买']], ['#annual', ['未来收入变更']],
    ['#onboarding', ['首次候选变更']], ['#demo', ['演示重置']],
  ] as const) {
    window.location.hash = hash; fireEvent(window, new HashChangeEvent('hashchange'));
    for (const name of names) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.getByRole('button', { name: '独立原到期GET' })).toBeEnabled();
  }
});

test('恢复的联合原工作区跨页阻挡其它变更，自己的手动恢复和原件GET保持可达', () => {
  pendingGates.observation = false; pendingGates.joint = true;
  window.location.hash = '#goals'; render(<App />);
  for (const name of ['身份变更', '目标变更', '动态归属', '现金回拨', '模型变更']) expect(screen.getByRole('button', { name })).toBeDisabled();
  expect(screen.getByRole('button', { name: '联合目标族外部门' })).toBeEnabled();
  const original = screen.getByRole('button', { name: '独立原联合GET' });
  expect(original).toBeEnabled(); expect(original.closest('fieldset')).toBeNull();
  for (const [hash, names] of [
    ['#policies', ['原策略变更', '完整版策略变更', '恢复资金', '周期付款', '季节采纳', '观察族外部门', '到期族外部门', '多模板财务预览']],
    ['#products', ['资产购买']], ['#annual', ['未来收入变更']],
    ['#onboarding', ['首次候选变更']], ['#demo', ['演示重置']],
  ] as const) {
    window.location.hash = hash; fireEvent(window, new HashChangeEvent('hashchange'));
    for (const name of names) expect(screen.getByRole('button', { name })).toBeDisabled();
    if (hash === '#policies') expect(screen.getByRole('button', { name: '注册动作集合只读GET' })).toBeEnabled();
    for (const name of ['独立原联合GET', '独立原到期GET', '独立原观察GET']) {
      expect(screen.getByRole('button', { name })).toBeEnabled(); expect(screen.getByRole('button', { name }).closest('fieldset')).toBeNull();
    }
  }
});
