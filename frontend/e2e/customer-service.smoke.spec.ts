import { test, expect } from '@playwright/test'

test('customer service page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page).toHaveURL(/login/)
})
