import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react(), {
    name: 'zhiyu-next-entry',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url === '/' || req.url?.startsWith('/?')) {
          res.writeHead(302, { Location: '/zhiyu-next.html' }); res.end();
        } else next();
      });
    },
  }],
  server: {
    strictPort: true,
    proxy: { '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:19200' },
    fs: { deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/.runtime/**', '**/model.env'] },
  },
  build: { outDir: 'dist-zhiyu-next', rollupOptions: { input: resolve(import.meta.dirname, 'zhiyu-next.html') } },
});
