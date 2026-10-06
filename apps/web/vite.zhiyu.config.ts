import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react(), {
    name: 'zhiyu-entry',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url === '/' || req.url?.startsWith('/?')) {
          res.writeHead(302, { Location: '/zhiyu.html' });
          res.end();
        } else next();
      });
    },
  }],
  server: { strictPort: true, proxy: { '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:19000' } },
  build: { outDir: 'dist-zhiyu', rollupOptions: { input: resolve(import.meta.dirname, 'zhiyu.html') } },
});
