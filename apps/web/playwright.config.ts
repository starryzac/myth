import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '../..');

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:15173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{
    name: 'edge',
    use: { ...devices['Desktop Edge'], channel: process.env.PLAYWRIGHT_CHANNEL ?? 'msedge' },
  }],
  webServer: [
    {
      command: 'uv run --frozen uvicorn --no-access-log --app-dir apps/api app.main:app --host 127.0.0.1 --port 18000',
      cwd: repositoryRoot,
      env: { UV_CACHE_DIR: resolve(repositoryRoot, '.uv-cache') },
      url: 'http://127.0.0.1:18000/api/v1/health',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'pnpm --dir apps/web dev --port 15173',
      cwd: repositoryRoot,
      env: { API_PROXY_TARGET: 'http://127.0.0.1:18000' },
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
