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
  const customerMsg='页面客户消息-'+Date.now();await cp.evaluate(async({base,id,msg})=>{const t=sessionStorage.getItem('access_token');await fetch(`${base}/customer-service/sessions/${id}/messages`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${t}`},body:JSON.stringify({content:msg})})},{base:apiBase,id:session.id,msg:customerMsg});await expect(ap.getByText(customerMsg)).toBeVisible({timeout:10000});
  const reply='页面人工回复-'+Date.now();await ap.getByTestId('customer-service-reply-input').fill(reply);await ap.getByTestId('customer-service-compose').getByRole('button',{name:'发送'}).click();
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
