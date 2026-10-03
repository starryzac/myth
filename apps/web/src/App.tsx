import { useQuery } from '@tanstack/react-query';
import type { components } from '../../../packages/contracts/schema';

type HealthResponse = components['schemas']['HealthResponse'];

async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/health`);
  if (!response.ok) throw new Error('API 暂时无法连接');

  const health: unknown = await response.json();
  if (
    typeof health !== 'object' || health === null ||
    !('status' in health) || health.status !== 'ok' ||
    !('service' in health) || health.service !== 'bounded-funds-api' ||
    !('simulation' in health) || health.simulation !== true
  ) {
    throw new Error('API 响应未通过模拟环境校验');
  }
  return health as HealthResponse;
}

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false });

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-6 py-10 sm:px-10 sm:py-16">
      <header className="flex items-center justify-between gap-4 border-b border-slate-200 pb-6">
        <span className="text-sm font-semibold tracking-wide text-slate-700">BOUNDED FUNDS</span>
        <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">模拟环境</span>
      </header>

      <section className="max-w-2xl py-14 sm:py-20" aria-labelledby="product-title">
        <p className="mb-4 text-sm font-medium text-teal-700">面向青年的可审计分级自主资金 Agent</p>
        <h1 id="product-title" className="text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">钱途有界</h1>
        <p className="mt-6 text-lg leading-8 text-slate-600">让每一步资金安排，都在你确认的边界之内。</p>
        <p className="mt-4 text-sm leading-7 text-slate-500">所有资金动作均为模拟，未接入真实银行账户、支付或理财交易接口。</p>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8" aria-labelledby="connection-title">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id="connection-title" className="text-base font-semibold text-slate-900">系统连接</h2>
          <p role="status" aria-live="polite" className={`text-sm font-medium ${health.isSuccess ? 'text-teal-700' : health.isError ? 'text-amber-800' : 'text-slate-500'}`}>
            {health.isSuccess ? 'API 已连接' : health.isError ? 'API 暂未连接' : '正在连接 API…'}
          </p>
        </div>
        <p className="mt-3 text-sm leading-6 text-slate-500">连接状态来自模拟服务的实时健康检查。</p>
        {health.isError && (
          <button type="button" onClick={() => void health.refetch()} disabled={health.isFetching} className="mt-5 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:opacity-50">
            {health.isFetching ? '正在重试…' : '重新连接'}
          </button>
        )}
      </section>

      <footer className="mt-auto pt-16 text-xs leading-6 text-slate-500">钱途有界 · 竞赛模拟原型</footer>
    </main>
  );
}
