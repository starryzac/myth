import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ZhiyuApp from './zhiyu/ZhiyuApp';

const root = document.getElementById('root');
if (!root) throw new Error('未找到应用挂载节点');
const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
createRoot(root).render(<StrictMode><QueryClientProvider client={queryClient}><ZhiyuApp /></QueryClientProvider></StrictMode>);
