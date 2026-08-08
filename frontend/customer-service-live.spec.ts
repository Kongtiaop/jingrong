import { test, expect, request as playwrightRequest } from '@playwright/test'

const apiBase = 'http://127.0.0.1:8010/api/v1'
const customer = { username: 'customer_demo', password: 'Demo@2026Customer' }
const agent = { username: 'customer_service_demo', password: 'Demo@2026CustomerService' }

async function login(page: any, account: typeof customer) {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill(account.username)
  await page.locator('input[autocomplete="current-password"]').fill(account.password)
  await page.getByRole('button', { name: /登录|login/i }).click()
}
async function token(api: any, account: typeof customer) {
  const response = await api.post(`${apiBase}/auth/login`, { data: account }); expect(response.ok()).toBeTruthy(); return (await response.json()).data.access_token
}

test('customer and agent complete live handoff messaging loop', async ({ browser }) => {
  const api = await playwrightRequest.newContext(); const customerToken = await token(api, customer)
  const created = await api.post(`${apiBase}/customer-service/sessions`, { headers: { Authorization: `Bearer ${customerToken}` }, data: { note: 'playwright_e2e' } }); expect(created.status()).toBe(201); const session = (await created.json()).data
  const customerContext = await browser.newContext(); const agentContext = await browser.newContext(); const customerPage = await customerContext.newPage(); const agentPage = await agentContext.newPage()
  try {
    await login(customerPage, customer); await login(agentPage, agent); await agentPage.goto('/backoffice'); await expect(agentPage.getByText('实时客服工作台')).toBeVisible({ timeout: 10_000 }); await expect(agentPage.getByText(session.id.slice(0,8))).toBeVisible({ timeout: 10_000 }); await agentPage.getByText(session.id.slice(0,8)).click(); await agentPage.getByRole('button', { name: '接入' }).click(); await expect(agentPage.getByRole('button', { name: '暂停' })).toBeVisible()
    const customerMessage='我需要人工帮助'; const agentMessage='您好，我来协助您。'; await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${customerToken}` }, data: { content: customerMessage } }); await expect(agentPage.getByText(customerMessage)).toBeVisible({ timeout: 10_000 }); const agentToken=await token(api,agent); await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { content: agentMessage } }); const listed=await api.get(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${customerToken}` } }); expect((await listed.json()).data.some((item:any)=>item.content===agentMessage)).toBeTruthy()
  } finally { await customerContext.close(); await agentContext.close(); await api.dispose() }
})
