import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ZhiyuNextApp from './zhiyu-next/ZhiyuNextApp';

const root = document.getElementById('root');
if (!root) throw new Error('未找到应用挂载节点');
const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
createRoot(root).render(<StrictMode><QueryClientProvider client={client}><ZhiyuNextApp /></QueryClientProvider></StrictMode>);
