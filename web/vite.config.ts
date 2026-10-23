import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5178, strictPort: true, proxy: { '/api/v2': { target: process.env.ZHIYU_API_TARGET ?? 'http://127.0.0.1:19310', changeOrigin: false } } },
});
