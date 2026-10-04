import { expect, test } from '@playwright/test';

test('Web 经代理读取真实模拟资金总览并展示同一快照', async ({ page }) => {
  const dashboardResponse = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/dashboard') && response.request().method() === 'GET',
  );
  await page.goto('/');
  const response = await dashboardResponse;
  expect(response.ok()).toBe(true);
  const snapshot = await response.json();
  expect(snapshot.schema_version).toBe('dashboard-v1');
  expect(snapshot.simulation).toBe(true);
  expect(snapshot.account_facts.facts.user_id).toBe(snapshot.user_id);
  await expect(page.getByRole('heading', { name: '钱途有界' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('资金总览已连接');
  await expect(page.getByText(/所有资金动作均为模拟/)).toBeVisible();
  await expect(page.getByRole('region', { name: '91日资金边界' })).toBeVisible();
  await expect(page.getByRole('region', { name: '账面现金' })).toBeVisible();
  await expect(page.getByRole('region', { name: '待处理事项' })).toBeVisible();
});
