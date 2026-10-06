import { useEffect, useSyncExternalStore } from 'react';
import DashboardPage from './pages/DashboardPage';
import PolicyCenterPage from './pages/PolicyCenterPage';
import GoalsPage from './pages/GoalsPage';
import DecisionTracePage from './pages/DecisionTracePage';
import DemoConsolePage from './pages/DemoConsolePage';
import { recoverDemoOperation, useDemoOperation } from './features/demo-operation';
import { useWriteInFlight } from './features/write-flight';

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback);
  return () => window.removeEventListener('hashchange', callback);
}
export default function App() {
  const operation = useDemoOperation(); const writing = useWriteInFlight();
  useEffect(() => { recoverDemoOperation(); }, []);
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => '');
  const page = hash === '#demo' ? 'demo' : hash === '#policies' ? 'policies' : hash === '#goals' ? 'goals' : hash === '#decisions' || hash.startsWith('#decisions/') ? 'decisions' : 'overview';
  const runId = hash.startsWith('#decisions/') ? hash.slice('#decisions/'.length) : undefined;
  return <main className="app-shell">
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">界</span>
      <div><h1>钱途有界</h1><p>BOUNDED FUNDS</p></div></div><span className="simulation-badge">模拟环境</span></header>
    <nav className="main-nav" aria-label="页面导航">
      <a href="#overview" aria-current={page === 'overview' ? 'page' : undefined}>资金总览</a>
      <a href="#policies" aria-current={page === 'policies' ? 'page' : undefined}>策略中心</a>
      <a href="#goals" aria-current={page === 'goals' ? 'page' : undefined}>目标储备</a>
      <a href="#decisions" aria-current={page === 'decisions' ? 'page' : undefined}>决策轨迹</a>
      <a href="#demo" aria-current={page === 'demo' ? 'page' : undefined}>演示控制台</a>
    </nav>
    <p className="simulation-note">所有资金动作均为模拟，未接入真实银行账户、支付或理财交易接口。</p>
    {(operation.busy || operation.pending || operation.storage_error || writing) && page !== 'demo' && <p className="notice">{operation.busy || writing ? '原资金或策略请求正在处理。' : '演示原命令结果尚待核对。'}其他资金表单暂不可提交；可读取总览、轨迹或前往控制台核对原项。</p>}
    <div key={operation.read_generation}>{page === 'policies' ? <fieldset className="page-operation-gate" disabled={!!operation.pending || operation.busy || !!operation.storage_error || writing}><PolicyCenterPage /></fieldset>
      : page === 'goals' ? <fieldset className="page-operation-gate" disabled={!!operation.pending || operation.busy || !!operation.storage_error || writing}><GoalsPage /></fieldset>
        : page === 'demo' ? <DemoConsolePage /> : page === 'decisions' ? <DecisionTracePage runId={runId} /> : <DashboardPage />}</div>
    <footer className="app-footer"><span>钱途有界 · 竞赛模拟原型</span><span>财务计算、策略权限与原操作核验分别展示</span></footer>
  </main>;
}
