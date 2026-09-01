# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: agent-bubble-trading.spec.ts >> agent bubble handles recommend-and-purchase request as dependent tasks without auto-selecting
- Location: e2e\agent-bubble-trading.spec.ts:73:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('agent-recommendations')
Expected: visible
Timeout: 20000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 20000ms
  - waiting for getByTestId('agent-recommendations')

```

```yaml
- banner:
  - text: WEALTH MANAGER
  - heading "智能财富管家" [level=1]
  - navigation:
    - link "首页":
      - /url: /
    - link "产品展示":
      - /url: /products
  - button "客户演示账号 客户 ⌄"
- main:
  - complementary:
    - text: W
    - strong: 财富管理平台
    - text: 客户服务端 客
    - strong: 客户演示账号
    - text: 客户
    - navigation:
      - button "⌂ 返回前台"
      - button "▦ 我的看板"
      - button "◉ 个人信息"
      - button "▤ 我的订单"
      - button "☷ 我的工单"
      - button "⇄ 业务办理"
      - button "◇ 资产与持仓"
      - button "▣ 企业认证"
      - button "△ 风险测评"
      - button "♙ 投顾服务"
    - button "↪ 退出登录"
  - main:
    - paragraph: 当前位置 / 我的看板
    - heading "我的看板" [level=1]
    - text: 客户中心
    - article:
      - text: 总资产
      - strong: ¥302,118.96
    - article:
      - text: 可用余额
      - strong: ¥200,000.00
    - article:
      - text: 持仓市值
      - strong: ¥102,118.96
    - strong: 资产数据说明
    - text: 总资产 = 可用余额 + 冻结余额 + 当前持仓市场价值；每次进入客户中心会按最新持仓估值刷新。 账户风控状态
    - strong: 风险关注
    - paragraph: 当前账户处于风险关注状态（RW-001），交易服务可能受到额外审核。
    - text: 当前规则：RW-001 · 预警等级：medium
- text: 智能助手 统一 Agent 入口 · 客服、推荐与受控交易在同一窗口完成
- button "×"
- article:
  - paragraph: 推荐一个产品并申购1000元
- article:
  - paragraph: 未找到唯一匹配的在售产品，请从候选产品中选择或补充完整产品名称。
  - strong: 交易草稿需要补充或暂不能提交
  - paragraph: 未找到唯一匹配的在售产品，请从候选产品中选择或补充完整产品名称。
- textbox "例如：我要申购“现金管理保本计划”1000元"
- button "发送" [disabled]
- button "打开统一智能助手": × 智能助手
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test'
  2  | 
  3  | const customer = { username: 'customer_demo', password: 'Demo@2026Customer' }
  4  | 
  5  | async function login(page: import('@playwright/test').Page) {
  6  |   await page.goto('/login')
  7  |   await page.locator('input[autocomplete="username"]').fill(customer.username)
  8  |   await page.locator('input[autocomplete="current-password"]').fill(customer.password)
  9  |   await page.getByRole('button', { name: /登录|login/i }).click()
  10 |   await expect(page).toHaveURL(/\/customer-center/)
  11 | }
  12 | 
  13 | async function openAgent(page: import('@playwright/test').Page) {
  14 |   await page.getByTestId('agent-bubble-launcher').click()
  15 |   await expect(page.getByTestId('agent-bubble-panel')).toBeVisible()
  16 | }
  17 | 
  18 | test('agent bubble clarifies a purchase request with missing parameters', async ({ page }) => {
  19 |   await login(page)
  20 |   await openAgent(page)
  21 |   await page.getByTestId('agent-bubble-input').fill('我要申购')
  22 |   await page.getByTestId('agent-bubble-send').click()
  23 |   await expect(page.getByTestId('agent-bubble-messages')).toContainText(/请补充.*产品名称.*申购金额/)
  24 |   await expect(page.getByTestId('agent-bubble-messages')).toContainText('交易草稿需要补充或暂不能提交')
  25 | })
  26 | 
  27 | test('agent bubble creates a draft and a pending-confirmation order only after explicit acknowledgement', async ({ page }) => {
  28 |   await login(page)
  29 |   await openAgent(page)
  30 |   await page.getByTestId('agent-bubble-input').fill('我要申购“现金管理保本计划”1000元')
  31 |   await page.getByTestId('agent-bubble-send').click()
  32 | 
  33 |   const draft = page.getByTestId('agent-trade-draft')
  34 |   await expect(draft).toBeVisible()
  35 |   await expect(draft).toContainText('现金管理保本计划')
  36 |   await expect(draft).toContainText('预检通过')
  37 |   await expect(page.getByTestId('agent-trade-confirm')).toBeDisabled()
  38 | 
  39 |   await page.getByTestId('agent-trade-ack').check()
  40 |   await expect(page.getByTestId('agent-trade-confirm')).toBeEnabled()
  41 |   await page.getByTestId('agent-trade-confirm').click()
  42 |   await expect(page.getByTestId('agent-bubble-messages')).toContainText(/已创建，当前为待确认状态/, { timeout: 20_000 })
  43 | })
  44 | 
  45 | 
  46 | test('agent bubble shows suitability recommendations and carries a product into purchase drafting', async ({ page }) => {
  47 |   await login(page)
  48 |   await openAgent(page)
  49 |   await page.getByTestId('agent-bubble-input').fill('推荐适合我的产品')
  50 |   await page.getByTestId('agent-bubble-send').click()
  51 |   const recommendations = page.getByTestId('agent-recommendations')
  52 |   await expect(recommendations).toBeVisible({ timeout: 20_000 })
  53 |   await expect(recommendations).toContainText('适当性产品推荐')
  54 |   const prepare = recommendations.getByTestId('agent-recommendation-buy').first()
  55 |   await expect(prepare).toBeVisible()
  56 |   await prepare.click()
  57 |   await expect(page.getByTestId('agent-bubble-input')).toHaveValue(/我要申购“.+”/)
  58 | })
  59 | 
  60 | 
  61 | test('agent bubble accepts amount-before-product purchase wording', async ({ page }) => {
  62 |   await login(page)
  63 |   await openAgent(page)
  64 |   await page.getByTestId('agent-bubble-input').fill('我要申购1000元现金管理保本计划')
  65 |   await page.getByTestId('agent-bubble-send').click()
  66 |   const draft = page.getByTestId('agent-trade-draft')
  67 |   await expect(draft).toBeVisible({ timeout: 20_000 })
  68 |   await expect(draft).toContainText('现金管理保本计划')
  69 |   await expect(draft).toContainText('1,000.00')
  70 | })
  71 | 
  72 | 
  73 | test('agent bubble handles recommend-and-purchase request as dependent tasks without auto-selecting', async ({ page }) => {
  74 |   await login(page)
  75 |   await openAgent(page)
  76 |   await page.getByTestId('agent-bubble-input').fill('推荐一个产品并申购1000元')
  77 |   await page.getByTestId('agent-bubble-send').click()
  78 |   const recommendations = page.getByTestId('agent-recommendations')
> 79 |   await expect(recommendations).toBeVisible({ timeout: 20_000 })
     |                                 ^ Error: expect(locator).toBeVisible() failed
  80 |   await expect(page.getByTestId('agent-bubble-messages')).toContainText('系统不会自动替您选择产品或创建订单')
  81 |   await recommendations.getByTestId('agent-recommendation-buy').first().click()
  82 |   await expect(page.getByTestId('agent-bubble-input')).toHaveValue(/我要申购“.+”1000元/)
  83 | })
  84 | 
```