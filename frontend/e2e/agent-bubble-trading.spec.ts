import { test, expect } from '@playwright/test'

const customer = { username: 'customer_demo', password: 'Demo@2026Customer' }

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill(customer.username)
  await page.locator('input[autocomplete="current-password"]').fill(customer.password)
  await page.getByRole('button', { name: /登录|login/i }).click()
  await expect(page).toHaveURL(/\/customer-center/)
}

async function openAgent(page: import('@playwright/test').Page) {
  await page.getByTestId('agent-bubble-launcher').click()
  await expect(page.getByTestId('agent-bubble-panel')).toBeVisible()
}

test('agent bubble clarifies a purchase request with missing parameters', async ({ page }) => {
  await login(page)
  await openAgent(page)
  await page.getByTestId('agent-bubble-input').fill('我要申购')
  await page.getByTestId('agent-bubble-send').click()
  await expect(page.getByTestId('agent-bubble-messages')).toContainText(/请补充.*产品名称.*申购金额/)
  await expect(page.getByTestId('agent-bubble-messages')).toContainText('交易草稿需要补充或暂不能提交')
})

test('agent bubble creates a draft and a pending-confirmation order only after explicit acknowledgement', async ({ page }) => {
  await login(page)
  await openAgent(page)
  await page.getByTestId('agent-bubble-input').fill('我要申购“现金管理保本计划”1000元')
  await page.getByTestId('agent-bubble-send').click()

  const draft = page.getByTestId('agent-trade-draft')
  await expect(draft).toBeVisible()
  await expect(draft).toContainText('现金管理保本计划')
  await expect(draft).toContainText('预检通过')
  await expect(page.getByTestId('agent-trade-confirm')).toBeDisabled()

  await page.getByTestId('agent-trade-ack').check()
  await expect(page.getByTestId('agent-trade-confirm')).toBeEnabled()
  await page.getByTestId('agent-trade-confirm').click()
  await expect(page.getByTestId('agent-bubble-messages')).toContainText(/已创建，当前为待确认状态/, { timeout: 20_000 })
})


test('agent bubble shows suitability recommendations and carries a product into purchase drafting', async ({ page }) => {
  await login(page)
  await openAgent(page)
  await page.getByTestId('agent-bubble-input').fill('推荐适合我的产品')
  await page.getByTestId('agent-bubble-send').click()
  const recommendations = page.getByTestId('agent-recommendations')
  await expect(recommendations).toBeVisible({ timeout: 20_000 })
  await expect(recommendations).toContainText('适当性产品推荐')
  const prepare = recommendations.getByTestId('agent-recommendation-buy').first()
  await expect(prepare).toBeVisible()
  await prepare.click()
  await expect(page.getByTestId('agent-bubble-input')).toHaveValue(/我要申购“.+”/)
})


test('agent bubble accepts amount-before-product purchase wording', async ({ page }) => {
  await login(page)
  await openAgent(page)
  await page.getByTestId('agent-bubble-input').fill('我要申购1000元现金管理保本计划')
  await page.getByTestId('agent-bubble-send').click()
  const draft = page.getByTestId('agent-trade-draft')
  await expect(draft).toBeVisible({ timeout: 20_000 })
  await expect(draft).toContainText('现金管理保本计划')
  await expect(draft).toContainText('1,000.00')
})


test('agent bubble handles recommend-and-purchase request as dependent tasks without auto-selecting', async ({ page }) => {
  await login(page)
  await openAgent(page)
  await page.getByTestId('agent-bubble-input').fill('推荐一个产品并申购1000元')
  await page.getByTestId('agent-bubble-send').click()
  const recommendations = page.getByTestId('agent-recommendations')
  await expect(recommendations).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('agent-bubble-messages')).toContainText('系统不会自动替您选择产品或创建订单')
  await recommendations.getByTestId('agent-recommendation-buy').first().click()
  await expect(page.getByTestId('agent-bubble-input')).toHaveValue(/我要申购“.+”1000元/)
})
