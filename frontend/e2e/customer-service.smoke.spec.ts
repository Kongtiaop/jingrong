import { test, expect } from '@playwright/test'

async function loginAsCustomer(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('账号').fill('customer_demo')
  await page.getByLabel('密码').fill('Demo@2026Customer')
  await page.getByRole('button', { name: '登录' }).click()
}

test('customer login opens the personal center without a blank screen and preserves public navigation', async ({ page }) => {
  await loginAsCustomer(page)
  await expect(page).toHaveURL(/\/customer-center/)
  await expect(page.getByRole('heading', { name: '我的看板' })).toBeVisible()
  await expect(page.getByRole('link', { name: '首页' })).toBeVisible()
  await expect(page.getByRole('link', { name: '产品展示' })).toBeVisible()

  await page.getByRole('link', { name: '产品展示' }).click()
  await expect(page).toHaveURL(/\/products/)
  await expect(page.getByRole('heading', { name: '产品展示' })).toBeVisible()
})
