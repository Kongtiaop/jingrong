import { test, expect, request as playwrightRequest } from '@playwright/test'

const apiBase = 'http://127.0.0.1:8010/api/v1'
const customer = { username: 'customer_demo', password: 'Demo@2026Customer' }
const agent = { username: 'customer_service_e2e', password: 'Demo@2026CustomerService' }

async function apiToken(api: any, account: any) {
  const response = await api.post(`${apiBase}/auth/login`, { data: account })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).data.access_token
}

async function browserLogin(page: any, account: any) {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill(account.username)
  await page.locator('input[autocomplete="current-password"]').fill(account.password)
  await page.getByRole('button', { name: /登录|login/i }).click()
  await page.waitForTimeout(350)
}

async function cleanup(api: any, customerToken: string, agentToken: string, sessionId: string) {
  await api.post(`${apiBase}/customer-service/sessions/${sessionId}/resolve`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { note: 'e2e_cleanup' } }).catch(() => {})
  await api.post(`${apiBase}/customer-service/sessions/${sessionId}/customer-close`, { headers: { Authorization: `Bearer ${customerToken}` }, data: { note: 'e2e_cleanup' } }).catch(() => {})
}

test('customer AgentBubble sends follow-up messages directly to an active human service session', async ({ browser }) => {
  const api = await playwrightRequest.newContext()
  const customerToken = await apiToken(api, customer)
  const agentToken = await apiToken(api, agent)
  const create = await api.post(`${apiBase}/customer-service/sessions`, { headers: { Authorization: `Bearer ${customerToken}` }, data: { note: 'agent_bubble_human_message_e2e' } })
  expect(create.status()).toBe(201)
  const session = (await create.json()).data
  const claim = await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`, { headers: { Authorization: `Bearer ${agentToken}` } })
  expect(claim.status()).toBe(200)

  const customerContext = await browser.newContext()
  const customerPage = await customerContext.newPage()
  const customerMessage = `气泡客户消息-${Date.now()}`
  const agentReply = `气泡人工回复-${Date.now()}`
  try {
    await browserLogin(customerPage, customer)
    await customerPage.evaluate((sessionId) => sessionStorage.setItem('customer_service_session_id', sessionId), session.id)
    await customerPage.reload()
    await customerPage.getByTestId('agent-bubble-launcher').click()
    await expect(customerPage.getByText('已接入人工客服：您的新消息将直接发送给客服。')).toBeVisible()
    await customerPage.getByTestId('agent-bubble-input').fill(customerMessage)
    await customerPage.getByTestId('agent-bubble-send').click()

    await expect.poll(async () => {
      const messages = await api.get(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${agentToken}` } })
      return (await messages.json()).data.some((item: any) => item.sender_type === 'customer' && item.content === customerMessage)
    }).toBeTruthy()

    const reply = await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { content: agentReply } })
    expect(reply.status()).toBe(201)
    await expect(customerPage.getByTestId('agent-bubble-messages')).toContainText(agentReply, { timeout: 12_000 })
  } finally {
    await cleanup(api, customerToken, agentToken, session.id)
    await customerContext.close()
    await api.dispose()
  }
})
