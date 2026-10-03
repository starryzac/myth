import { expect, test } from '@playwright/test';

test('Web 经代理连接真实模拟 API 并展示连接状态', async ({ page }) => {
  const healthResponse = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/health') && response.request().method() === 'GET',
  );
  await page.goto('/');
  const response = await healthResponse;
  expect(response.ok()).toBe(true);
  expect(await response.json()).toEqual({
    status: 'ok', service: 'bounded-funds-api', simulation: true,
  });
  await expect(page.getByRole('heading', { name: '钱途有界' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('API 已连接');
  await expect(page.getByText(/所有资金动作均为模拟/)).toBeVisible();
});
