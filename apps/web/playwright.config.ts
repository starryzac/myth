import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '../..');
const w1 = process.env.BF_W1_BROWSER_RUN === '1';
function localPort(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1024 || value > 65535) throw new Error(`Invalid local port: ${name}`);
  return value;
}
const apiPort = localPort('BF_W1_API_PORT', 18000);
const webPort = localPort('BF_W1_WEB_PORT', 15173);
if (apiPort === webPort) throw new Error('API and Web require different ports');
if (w1) {
  const database = new URL(process.env.DATABASE_URL ?? '');
  if (database.protocol !== 'postgresql+psycopg:' || !['127.0.0.1', 'localhost', '[::1]'].includes(database.hostname) ||
    database.port !== '54329' || !/^\/bf_test_[0-9a-f]{32}$/.test(database.pathname) || !process.env.BF_W1_RUN_DIRECTORY ||
    process.env.BF_W1_SERVERS_EXTERNAL !== '1') throw new Error('W1 writes require the guarded local disposable-database coordinator');
}
const artifacts = w1 ? resolve(process.env.BF_W1_RUN_DIRECTORY!, 'playwright') : undefined;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: w1 ? 1 : undefined,
  timeout: w1 ? 1_800_000 : 30_000,
  expect: { timeout: w1 ? 600_000 : 5_000 },
  outputDir: artifacts ? resolve(artifacts, 'test-results') : undefined,
  reporter: artifacts ? [['list'], ['json', { outputFile: resolve(artifacts, 'results.json') }], ['html', { outputFolder: resolve(artifacts, 'html'), open: 'never' }]] : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: w1 ? 'on' : 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: w1 ? 'on' : 'off',
  },
  projects: [{
    name: 'edge',
    use: { ...devices['Desktop Edge'], channel: w1 ? 'msedge' : process.env.PLAYWRIGHT_CHANNEL ?? 'msedge' },
  }],
  webServer: w1 ? undefined : [
    {
      command: `uv run --frozen uvicorn --no-access-log --app-dir apps/api app.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: repositoryRoot,
      env: { UV_CACHE_DIR: resolve(repositoryRoot, '.uv-cache') },
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `pnpm --dir apps/web dev --port ${webPort}`,
      cwd: repositoryRoot,
      env: { API_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
