import { useSyncExternalStore } from 'react';
import DashboardPage from './pages/DashboardPage';
import PolicyCenterPage from './pages/PolicyCenterPage';
import GoalsPage from './pages/GoalsPage';

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback);
  return () => window.removeEventListener('hashchange', callback);
}
export default function App() {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => '');
  const page = hash === '#policies' ? 'policies' : hash === '#goals' ? 'goals' : 'overview';
  return <main className="app-shell">
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">界</span>
      <div><h1>钱途有界</h1><p>BOUNDED FUNDS</p></div></div><span className="simulation-badge">模拟环境</span></header>
    <nav className="main-nav" aria-label="页面导航">
      <a href="#overview" aria-current={page === 'overview' ? 'page' : undefined}>资金总览</a>
      <a href="#policies" aria-current={page === 'policies' ? 'page' : undefined}>策略中心</a>
      <a href="#goals" aria-current={page === 'goals' ? 'page' : undefined}>目标储备</a>
    </nav>
    <p className="simulation-note">所有资金动作均为模拟，未接入真实银行账户、支付或理财交易接口。</p>
    {page === 'policies' ? <PolicyCenterPage /> : page === 'goals' ? <GoalsPage /> : <DashboardPage />}
    <footer className="app-footer"><span>钱途有界 · 竞赛模拟原型</span><span>财务计算、策略权限与原操作核验分别展示</span></footer>
  </main>;
}