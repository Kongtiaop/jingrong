import { test, expect, request as playwrightRequest } from '@playwright/test'

const apiBase='http://127.0.0.1:8010/api/v1'
const e2eEnabled = process.env.E2E_REAL_DATA === '1'
const customer={username:'customer_demo',password:'Demo@2026Customer'}
const agent={username:'customer_service_e2e',password:'Demo@2026CustomerService'}
async function login(page:any,account:any){await page.goto('/login');await page.locator('input[autocomplete="username"]').fill(account.username);await page.locator('input[autocomplete="current-password"]').fill(account.password);await page.getByRole('button',{name:/登录|login/i}).click();await page.waitForTimeout(700);await page.reload();await page.waitForTimeout(700)}
async function token(api:any,account:any){const r=await api.post(`${apiBase}/auth/login`,{data:account});expect(r.ok()).toBeTruthy();return (await r.json()).data.access_token}
async function createSession(api:any,t:string){const r=await api.post(`${apiBase}/customer-service/sessions`,{headers:{Authorization:`Bearer ${t}`},data:{note:'playwright_e2e'}});expect(r.status()).toBe(201);return (await r.json()).data}
async function cleanup(api:any, customerToken:string, agentToken:string|undefined, session:any){if(!session?.id)return;if(agentToken)await api.post(`${apiBase}/customer-service/sessions/${session.id}/resolve`,{headers:{Authorization:`Bearer ${agentToken}`},data:{note:'e2e_cleanup'}}).catch(()=>{});await api.post(`${apiBase}/customer-service/sessions/${session.id}/customer-close`,{headers:{Authorization:`Bearer ${customerToken}`},data:{note:'e2e_cleanup'}}).catch(()=>{})}

test('customer and agent complete pure-page messaging and customer exit',async({browser})=>{ test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
 const api=await playwrightRequest.newContext();const ct=await token(api,customer);const at=await token(api,agent);const session=await createSession(api,ct);
 const cc=await browser.newContext();const ac=await browser.newContext();const cp=await cc.newPage();const ap=await ac.newPage();
 try{await login(cp,customer);await login(ap,agent);await ap.goto('/backoffice');await ap.getByRole('button',{name:/实时客服/}).click();await expect(ap.getByTestId('customer-service-desk-title')).toBeVisible();await expect(ap.getByTestId('customer-service-queue-item').filter({hasText:session.id.slice(0,8)})).toBeVisible({timeout:10000});await ap.getByTestId('customer-service-queue-item').filter({hasText:session.id.slice(0,8)}).click();const [claimResponse]=await Promise.all([ap.waitForResponse(r=>r.url().includes(`/customer-service/sessions/${session.id}/claim`)&&r.request().method()==='POST'),ap.getByRole('button',{name:'接入',exact:true}).click()]);expect(claimResponse.status()).toBe(200);const claimBody=await claimResponse.json();expect(claimBody.data.status).toBe('human_active');await expect(ap.getByTestId('customer-service-compose')).toBeVisible();
  await cp.goto('/customer-center/service');await expect(cp.getByTestId('customer-rag-title')).toBeVisible();
  const customerMsg='页面客户消息-'+Date.now();await cp.evaluate(async({base,id,msg})=>{const t=sessionStorage.getItem('access_token');await fetch(`${base}/customer-service/sessions/${id}/messages`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${t}`},body:JSON.stringify({content:msg})})},{base:apiBase,id:session.id,msg:customerMsg});await expect(ap.getByTestId('customer-service-messages').getByText(customerMsg, { exact: true })).toBeVisible({timeout:10000});
  const reply='页面人工回复-'+Date.now();await ap.getByTestId('customer-service-reply-input').fill(reply);await ap.getByTestId('customer-chat-modal').getByTestId('customer-service-reply-input-modal').fill(reply);await ap.getByTestId('customer-chat-modal').getByRole('button',{name:'发送'}).click();
  await expect.poll(async()=>{const r=await api.get(`${apiBase}/customer-service/sessions/${session.id}/messages`,{headers:{Authorization:`Bearer ${ct}`}});return (await r.json()).data.some((x:any)=>x.content===reply)}).toBeTruthy();
  await cp.evaluate(()=>document.querySelector('[data-testid="customer-human-state"] button')?.click());
 }finally{await cleanup(api,ct,at,session);await cc.close();await ac.close();await api.dispose()}
})

test('two agents cannot claim the same waiting session',async({browser})=>{ test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
 const api=await playwrightRequest.newContext();const ct=await token(api,customer);const at=await token(api,agent);const session=await createSession(api,ct);const first=await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`,{headers:{Authorization:`Bearer ${at}`}});expect(first.status()).toBe(200);const second=await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`,{headers:{Authorization:`Bearer ${at}`}});expect(second.status()).toBe(409);await cleanup(api,ct,at,session);await api.dispose()
})


test('customer service page reports recovery after API interruption', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const api = await playwrightRequest.newContext()
  const ct = await token(api, customer)
  const at = await token(api, agent)
  const session = await createSession(api, ct)
  const cc = await browser.newContext()
  const ac = await browser.newContext()
  const cp = await cc.newPage()
  const ap = await ac.newPage()
  try {
    await login(cp, customer)
    await login(ap, agent)
    await ap.goto('/backoffice')
    await ap.getByRole('button', { name: /实时客服/ }).click()
    const item = ap.getByTestId('customer-service-queue-item').filter({ hasText: session.id.slice(0, 8) })
    await expect(item).toBeVisible({ timeout: 10_000 })
    await item.click()
    await ap.getByRole('button', { name: '接入', exact: true }).click()
    await expect(ap.getByTestId('customer-service-compose')).toBeVisible()
    await ap.route(`${apiBase}/api/v1/customer-service/sessions/${session.id}/heartbeat`, async route => {
      await route.abort('failed')
    })
    await ap.waitForTimeout(3500)
    await expect(ap.getByTestId('customer-service-compose')).toBeVisible({ timeout: 8_000 })
    await ap.unroute(`${apiBase}/api/v1/customer-service/sessions/${session.id}/heartbeat`)
  } finally {
    await cleanup(api, ct, at, session)
    await cc.close()
    await ac.close()
    await api.dispose()
  }
})

test('customer service page recovers from browser offline mode', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const api = await playwrightRequest.newContext()
  const ct = await token(api, customer)
  const at = await token(api, agent)
  const session = await createSession(api, ct)
  const ac = await browser.newContext()
  const ap = await ac.newPage()
  try {
    await login(ap, agent)
    await ap.goto('/backoffice')
    await ap.getByRole('button', { name: /实时客服/ }).click()
    const item = ap.getByTestId('customer-service-queue-item').filter({ hasText: session.id.slice(0, 8) })
    await expect(item).toBeVisible({ timeout: 10_000 })
    await item.click()
    await ap.getByRole('button', { name: '接入', exact: true }).click()
    await expect(ap.getByTestId('customer-service-compose')).toBeVisible()
    await ap.context().setOffline(true)
    await ap.waitForTimeout(3500)
    await expect(ap.locator('p.muted').filter({ hasText: /连接恢复中|会话已暂停/ })).toBeVisible({ timeout: 8_000 })
    await ap.context().setOffline(false)
    await expect(ap.getByTestId('customer-service-compose')).toBeVisible({ timeout: 10_000 })
  } finally {
    await cleanup(api, ct, at, session)
    await ac.close()
    await api.dispose()
  }
})


test('customer sees multi-intent task details', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const api = await playwrightRequest.newContext()
  const ct = await token(api, customer)
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('帮我查订单状态、账户风险限制和资产情况')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-task-results')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('customer-task-results')).toContainText('order_status')
    await expect(page.getByTestId('customer-task-results')).toContainText('risk_restriction')
    await expect(page.getByTestId('customer-task-results')).toContainText('asset_summary')
    await expect(page.locator('.status-badge').first()).toBeVisible()
  } finally {
    await context.close()
    await api.dispose()
  }
})


test('customer is sent to the existing operations page without executing the operation', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const context = await browser.newContext()
  const page = await context.newPage()
  const operationRequests: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && /\/operations\/(redeem|transfer|order)/.test(request.url())) operationRequests.push(request.url())
  })
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('请帮我赎回')
    await page.getByTestId('customer-rag-send').click()
    const action = page.getByTestId('customer-operation-action').first()
    await expect(action).toBeVisible({ timeout: 30000 })
    await action.click()
    await expect(page).toHaveURL(/\/customer-center\?tab=operations&action=redeem/)
    await expect(page.locator('h1').filter({ hasText: '业务办理' })).toBeVisible()
    expect(operationRequests).toEqual([])
  } finally {
    await context.close()
  }
})




test('customer service notification event is visible to the intended user only', async () => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const api = await playwrightRequest.newContext()
  const customerToken = await token(api, customer)
  const agentToken = await token(api, agent)
  const session = await createSession(api, customerToken)
  try {
    const before = await api.get(`${apiBase}/notifications?unread_only=true`, { headers: { Authorization: `Bearer ${customerToken}` } })
    expect(before.ok()).toBeTruthy()
    const customerMessage = await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, {
      headers: { Authorization: `Bearer ${customerToken}` }, data: { content: '通知 E2E 客户消息' },
    })
    expect(customerMessage.status()).toBe(201)
    const agentNotificationsBeforeClaim = await api.get(`${apiBase}/notifications?unread_only=true`, { headers: { Authorization: `Bearer ${agentToken}` } })
    expect(agentNotificationsBeforeClaim.ok()).toBeTruthy()
    expect((await agentNotificationsBeforeClaim.json()).data.items.some((item: any) => item.resource_type === 'customer_service_session' && item.resource_id === session.id)).toBeFalsy()
    const claim = await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`, { headers: { Authorization: `Bearer ${agentToken}` } })
    expect(claim.status()).toBe(200)
    const customerNotifications = await api.get(`${apiBase}/notifications?unread_only=true`, { headers: { Authorization: `Bearer ${customerToken}` } })
    expect(customerNotifications.ok()).toBeTruthy()
    const claimedVisible = (await customerNotifications.json()).data.items.some((item: any) => item.notification_type === 'customer_service_claimed' && item.resource_id === session.id)
    expect(typeof claimedVisible).toBe('boolean')
  } finally {
    await cleanup(api, customerToken, agentToken, session)
    await api.dispose()
  }
})


test('staff opens a customer service notification, marks it read, and focuses the matching session', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API session routes')
  const api = await playwrightRequest.newContext()
  const customerToken = await token(api, customer)
  const agentToken = await token(api, agent)
  const session = await createSession(api, customerToken)
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    const claim = await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`, { headers: { Authorization: `Bearer ${agentToken}` } })
    expect(claim.status()).toBe(200)
    const customerMessage = await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, {
      headers: { Authorization: `Bearer ${customerToken}` }, data: { content: '请通过通知进入本次客服会话' },
    })
    expect(customerMessage.status()).toBe(201)
    let row: any
    await expect.poll(async () => {
      const notifications = await api.get(`${apiBase}/notifications?unread_only=true`, { headers: { Authorization: `Bearer ${agentToken}` } })
      expect(notifications.ok()).toBeTruthy()
      row = (await notifications.json()).data.items.find((item: any) => item.notification_type === 'customer_service_message' && item.resource_id === session.id)
      return Boolean(row)
    }, { timeout: 10_000 }).toBeTruthy()

    await login(page, agent)
    await page.goto('/backoffice')
    await page.getByRole('button', { name: /通知/ }).click()
    const notification = page.getByTestId('notification-row').filter({ hasText: '客户发送了新消息' }).first()
    await expect(notification).toBeVisible({ timeout: 10_000 })
    await notification.click()
    await expect(page.getByTestId('customer-service-desk-title')).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`customer_service_session_id=${session.id}`))
    await expect(page.getByTestId('customer-service-selected-session')).toContainText(session.id.slice(0, 8))

    const after = await api.get(`${apiBase}/notifications?unread_only=true`, { headers: { Authorization: `Bearer ${agentToken}` } })
    expect(after.ok()).toBeTruthy()
    expect((await after.json()).data.items.some((item: any) => item.id === row.id)).toBeFalsy()
  } finally {
    await cleanup(api, customerToken, agentToken, session)
    await context.close()
    await api.dispose()
  }
})




test('customer receives realtime human session lifecycle updates', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API/Redis WebSocket lifecycle')
  const api = await playwrightRequest.newContext()
  const customerToken = await token(api, customer)
  const agentToken = await token(api, agent)
  const session = await createSession(api, customerToken)
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('[cancel-test] 请查询订单状态')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-rag-cancel')).toBeVisible({ timeout: 5_000 })
    await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`, { headers: { Authorization: `Bearer ${agentToken}` } }).then(response => expect(response.status()).toBe(200))
    await expect(page.getByTestId('customer-human-lifecycle')).toContainText('human_active')
    await api.post(`${apiBase}/customer-service/sessions/${session.id}/pause`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { note: 'e2e pause' } }).then(response => expect(response.status()).toBe(200))
    await expect(page.getByTestId('customer-human-lifecycle')).toContainText('paused')
    await api.post(`${apiBase}/customer-service/sessions/${session.id}/resolve`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { note: 'e2e resolve' } }).then(response => expect(response.status()).toBe(200))
    await expect(page.getByTestId('customer-human-lifecycle')).toContainText('resolved')
    await expect(page.getByTestId('customer-human-state')).toHaveCount(0)
  } finally {
    await cleanup(api, customerToken, agentToken, session)
    await context.close()
    await api.dispose()
  }
})


test('human realtime subscription stays connected after the agent request closes', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and API/Redis WebSocket lifecycle')
  const api = await playwrightRequest.newContext()
  const customerToken = await token(api, customer)
  const agentToken = await token(api, agent)
  const session = await createSession(api, customerToken)
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('[cancel-test] 请查询订单状态')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-rag-cancel')).toBeVisible({ timeout: 5_000 })
    await api.post(`${apiBase}/customer-service/sessions/${session.id}/claim`, { headers: { Authorization: `Bearer ${agentToken}` } }).then(response => expect(response.status()).toBe(200))
    await expect(page.getByTestId('customer-human-lifecycle')).toContainText('human_active')
    await expect(page.getByTestId('customer-agent-reconnecting')).toHaveCount(0)
    await page.waitForTimeout(1_500)
    await expect(page.getByTestId('customer-agent-reconnecting')).toHaveCount(0)
    const reply = `独立订阅回复-${Date.now()}`
    await api.post(`${apiBase}/customer-service/sessions/${session.id}/messages`, { headers: { Authorization: `Bearer ${agentToken}` }, data: { content: reply } }).then(response => expect(response.status()).toBe(201))
    await expect(page.getByText(reply)).toBeVisible({ timeout: 10_000 })
  } finally {
    await cleanup(api, customerToken, agentToken, session)
    await context.close()
    await api.dispose()
  }
})
test('customer agent websocket cancel stops the active request', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and WebSocket API')
  const context = await browser.newContext()
  const page = await context.newPage()
  const browserEvents: Array<{ kind: string; data: string }> = []
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket
    class InstrumentedWebSocket extends NativeWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        this.addEventListener('message', event => console.debug('__agent_cancel_ws__' + JSON.stringify({ kind: 'message', data: String(event.data) })))
      }
      send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        console.debug('__agent_cancel_ws__' + JSON.stringify({ kind: 'send', data: typeof data === 'string' ? data : '[binary]' }))
        return super.send(data)
      }
    }
    window.WebSocket = InstrumentedWebSocket as typeof WebSocket
  })
  page.on('console', message => {
    const text = message.text()
    if (text.startsWith('__agent_cancel_ws__')) browserEvents.push(JSON.parse(text.slice('__agent_cancel_ws__'.length)))
  })
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('[cancel-test] 请查询订单状态并取消本次请求')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-rag-cancel')).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('customer-rag-cancel').click()
    await expect(page.getByTestId('customer-agent-cancel-requested')).toBeVisible()
    await expect.poll(() => browserEvents.some(event => event.kind === 'message' && event.data.includes('agent.cancelled'))).toBeTruthy()
    await expect(page.getByText('cancelled')).toBeVisible()
  } finally {
    await context.close()
  }
})


test('customer agent websocket amend submits supplemental information', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and WebSocket API')
  const context = await browser.newContext()
  const page = await context.newPage()
  const events: string[] = []
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket
    class InstrumentedWebSocket extends NativeWebSocket {
      send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        console.debug('__agent_amend_ws__' + (typeof data === 'string' ? data : '[binary]'))
        return super.send(data)
      }
    }
    window.WebSocket = InstrumentedWebSocket as typeof WebSocket
  })
  page.on('console', message => { const text=message.text(); if(text.startsWith('__agent_amend_ws__'))events.push(text.slice('__agent_amend_ws__'.length)) })
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('请帮我查这笔订单')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-task-results')).toBeVisible({ timeout: 20_000 })
    const amendInput=page.getByTestId('customer-rag-amend-input')
    if(await amendInput.count()){
      await amendInput.fill('订单号 123456')
      await page.getByTestId('customer-rag-amend-send').click()
      await expect.poll(()=>events.some(item=>item.includes('agent.amend'))).toBeTruthy()
      await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    }
  } finally { await context.close() }
})

test('customer agent websocket shows live progress stages', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and WebSocket API')
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('[cancel-test] 请查询订单状态')
    await page.getByTestId('customer-rag-send').click()
    const progress=page.getByTestId('customer-agent-progress')
    await expect(progress).toBeVisible({ timeout: 5_000 })
    await expect(progress).toContainText(/已收到您的问题|正在识别问题|正在整理回复|已完成一项任务|回复已生成/)
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
  } finally { await context.close() }
})
test('customer agent websocket streams chunks and final structured result', async ({ browser }) => {
  test.skip(!e2eEnabled, 'requires E2E_REAL_DATA=1 and WebSocket API')
  const context = await browser.newContext()
  const page = await context.newPage()
  const browserEvents: Array<{ kind: string; data: string }> = []
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket
    class InstrumentedWebSocket extends NativeWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        const record = (kind: string, data = '') => console.debug('__agent_ws__' + JSON.stringify({ kind, url: String(url), data }))
        this.addEventListener('open', () => record('open'))
        this.addEventListener('message', event => record('message', String(event.data)))
        this.addEventListener('close', event => record('close', `${event.code}:${event.reason}`))
        this.addEventListener('error', () => record('error'))
      }
      send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        console.debug('__agent_ws__' + JSON.stringify({ kind: 'send', data: typeof data === 'string' ? data : '[binary]' }))
        return super.send(data)
      }
    }
    window.WebSocket = InstrumentedWebSocket as typeof WebSocket
  })
  page.on('console', message => {
    const text = message.text()
    if (text.startsWith('__agent_ws__')) browserEvents.push(JSON.parse(text.slice('__agent_ws__'.length)))
  })
  try {
    await login(page, customer)
    await page.goto('/customer-center/service')
    await expect(page.getByTestId('customer-rag-title')).toBeVisible()
    await page.getByTestId('customer-rag-input').fill('帮我查订单状态、账户风险限制和资产情况')
    await page.getByTestId('customer-rag-send').click()
    await expect(page.getByTestId('customer-task-results')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('customer-task-results')).toContainText('order_status')
    await expect(page.getByTestId('customer-task-results')).toContainText('risk_restriction')
    await expect(page.getByTestId('customer-task-results')).toContainText('asset_summary')
    expect(browserEvents.some(event => event.kind === 'send' && event.data.includes('agent.request'))).toBeTruthy()
    expect(browserEvents.some(event => event.kind === 'message' && event.data.includes('agent.output.chunk'))).toBeTruthy()
    expect(browserEvents.some(event => event.kind === 'message' && event.data.includes('agent.final'))).toBeTruthy()
  } finally {
    await context.close()
  }
})
