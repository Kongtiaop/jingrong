<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { genId } from '../utils/id'
import { useRoute } from 'vue-router'
import { useAuth } from '../stores/auth'
import { customerServiceApi } from '../api/customer-rag'
import { profileApi } from '../api/profile'
import AnalysisResultCard from '../components/AnalysisResultCard.vue'

const auth = useAuth()
const route = useRoute()
const isAdmin = computed(() => auth.me.value?.is_super_admin === true || auth.me.value?.roles?.includes('super_admin') === true)
const isAdvisor = computed(() => auth.me.value?.roles?.includes('advisor') === true)

const customers = ref<any[]>([])
// 对话中自动识别的“正在服务的目标客户”（信息性：仅用于未点名的后续指令与代客下单；可随时取消）
const targetId = ref('')
const query = ref('')
const loading = ref(false)
const messages = ref<any[]>([])
const listEl = ref<HTMLElement | null>(null)
const conversationId = ref(sessionStorage.getItem('supervisor_agent_conversation_id') || genId())
sessionStorage.setItem('supervisor_agent_conversation_id', conversationId.value)
// 代客下单已发起但尚未点明目标客户时，暂存原始指令，等待用户选择/补充客户
let pendingProxy = ''

const canView = computed(() => auth.loggedIn.value && !auth.me.value?.roles?.includes('customer') && (isAdmin.value || isAdvisor.value))

function formatMoney(value: any) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) }
function responseError(e: any) { return e?.response?.data?.detail?.message || e?.response?.data?.detail || e?.message || '助手服务暂时不可用' }
function customerById(id: string) { return customers.value.find((c: any) => c.id === id) }
function customerLabel(c: any) { return c ? (c.display_name || c.username || c.id) : '' }
function targetLabel() { return customerLabel(customerById(targetId.value)) }

function scrollToBottom() { nextTick(() => { if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight }) }
function pushBubble(msg: any) { messages.value.push(msg); scrollToBottom() }

async function loadCustomers() {
  try {
    const source = isAdmin.value ? profileApi.customers('', '', '', 200, 0) : profileApi.advisorCustomers('', '', '', 200, 0)
    customers.value = (await source).data.data.items || []
  } catch (e: any) {
    pushBubble({ role: 'assistant', kind: 'error', text: responseError(e) })
  }
}

function statusText(status: string) {
  return ({ awaiting_customer_confirmation: '待客户确认', needs_clarification: '需要补充信息', precheck_failed: '受控校验未通过' } as Record<string, string>)[status] || status
}

// —— 自然语言意图识别 ——
function hasBuyVerb(text: string) { return /(申购|购买|买入|认购|下单)/.test(text) }
// 明显是数据分析问句时优先走分析，避免“申购…万元”之类的统计问句被误判为代客下单
function isAnalysisPhrase(text: string) { return /(分析|统计|汇总|查看|查询|多少|占比|分布|排名|走势|趋势|对比|拆分|透视|合计|数量|人数|笔数|占比率|金额)/.test(text) }
function isProxyIntent(text: string) {
  if (isAnalysisPhrase(text)) return false
  if (!hasBuyVerb(text)) return false
  // 明确的“代/替/为客户”操作
  if (/(代|替)/.test(text)) return true
  if (/为|给|帮/.test(text) && /客户/.test(text)) return true
  // 代客申购必然涉及金额或“万元/元”
  if (/(万|元|¥|￥)/.test(text)) return true
  return false
}
// 在已授权客户中匹配当前消息里点名的客户
function customerInText(text: string) {
  return customers.value.find((c: any) => ((c.display_name && text.includes(c.display_name)) || (c.username && text.includes(c.username)))) || null
}

function hasAmountInTail() {
  const last = messages.value[messages.value.length - 1]
  return !!last && last.proxyMode === true && last.nldraft?.status === 'needs_clarification' && (last.nldraft?.missing_fields || []).includes('amount')
}

async function runAnalysis(text: string, focusedId?: string) {
  loading.value = true
  try {
    const data = (await customerServiceApi.executeTargeted(text, conversationId.value, route.path, { targetCustomerId: focusedId })).data.data
    const analysisResults = (data.results || []).filter((item: any) => item.intent === 'data_analysis' && item.data)
    pushBubble({
      role: 'assistant',
      answer: data.answer || '',
      analysis_results: analysisResults,
      clarification: data.clarification || '',
      overall_status: data.overall_status || '',
    })
  } catch (e: any) {
    pushBubble({ role: 'assistant', kind: 'error', text: responseError(e) })
  } finally {
    loading.value = false
  }
}

async function runProxyDraft(text: string, customerId: string) {
  loading.value = true
  try {
    const result = (await profileApi.advisorNaturalLanguageOperationDraft({ customer_id: customerId, query: text })).data.data
    pushBubble({ role: 'assistant', proxyMode: true, nldraft: result, parsedAmount: result.amount })
  } catch (e: any) {
    pushBubble({ role: 'assistant', kind: 'error', text: responseError(e) })
  } finally {
    loading.value = false
  }
}

async function send(textOverride?: string) {
  const text = (textOverride ?? query.value).trim()
  if (!text || loading.value) return
  query.value = ''
  pushBubble({ role: 'user', text })

  // ① 正在等待“目标客户”补充时：普通回答视为点名客户
  if (pendingProxy && !isProxyIntent(text)) {
    const picked = customerInText(text)
    if (picked) { applyCustomer(picked); return }
    if (text.length <= 16) {
      pushBubble({ role: 'assistant', askCustomer: true, text: '', clarification: '还没识别到目标客户，请直接说出客户姓名（例如“张三”），或从下方选择：' })
      return
    }
    pendingProxy = '' // 转向了新的分析请求，放弃此前待补的代客草稿
  }

  // ② 代客申购（自动识别意图）
  if (isProxyIntent(text)) {
    pendingProxy = ''
    const picked = customerInText(text)
    const customerId = (picked && picked.id) || targetId.value || ''
    if (customerId) {
      targetId.value = customerId
      await runProxyDraft(text, customerId)
    } else {
      pendingProxy = text
      pushBubble({
        role: 'assistant',
        askCustomer: true,
        text: '',
        clarification: '已识别为代客申购指令，但未识别到目标客户。请直接说出客户姓名（例如“张三”），或从下方选择：',
      })
    }
    return
  }

  // ③ 数据分析 / 一般自然语言
  pendingProxy = ''
  const picked = customerInText(text)
  if (picked) targetId.value = picked.id
  await runAnalysis(text, picked ? picked.id : undefined)
}

// 点击候选客户：补一条用户气泡再应用
function onPickCustomer(c: any) {
  pushBubble({ role: 'user', text: `目标客户：${customerLabel(c)}` })
  applyCustomer(c)
}

// 应用目标客户；若存在待补的代客草稿则用该客户重跑
function applyCustomer(c: any) {
  targetId.value = c.id
  const pending = pendingProxy
  pendingProxy = ''
  if (pending) {
    runProxyDraft(pending, c.id)
  } else {
    pushBubble({ role: 'assistant', text: `已将目标客户设为 ${customerLabel(c)}，请直接下达代客申购或分析指令。` })
  }
}

function pickOption(product: any) {
  const amountHint = hasAmountInTail() ? '10万元' : ''
  if (!targetId.value) { pushBubble({ role: 'assistant', askCustomer: true, text: '', clarification: '请选择该草稿要服务的客户：' }); return }
  send(`代客户申购“${product.name}”${amountHint}`)
}

function clearTarget() {
  targetId.value = ''
  pushBubble({ role: 'assistant', text: '已取消当前目标客户，后续指令将按权限范围执行。' })
}

onMounted(() => { if (canView.value) loadCustomers() })
</script>

<template>
  <section v-if="canView" class="workspace-page nl-console" data-testid="supervisor-console">
    <div class="section-heading">
      <div>
        <span class="eyebrow">SUPERVISOR AGENT CONSOLE</span>
        <h2 data-testid="supervisor-console-title">后台 Agent 控制台</h2>
        <p>直接用一句话下达指令：数据分析用自然语言即可（如"分析各风险等级客户数量占比"）；代客户下单可点名客户（如"为张三申购"稳健增值计划"10万元"）。系统自动识别意图与目标客户。</p>
      </div>
      <span class="pill">工作人员</span>
    </div>

    <div class="nl-card nl-chat">
      <div ref="listEl" class="nl-messages" data-testid="supervisor-console-messages">
        <p v-if="!messages.length" class="nl-welcome">
          我是后台 Agent 助手。请直接输入想做的事，例如：<br />
          · 分析：<span>各风险等级客户数量占比</span> / <span>分析张三的资产分布</span><br />
          · 代客申购：<span>代张三申购“稳健增值计划”10万元</span>
        </p>

        <article v-for="(message, index) in messages" :key="index" :class="'nl-bubble nl-' + message.role">
          <div class="nl-sender">{{ message.role === 'user' ? '我' : 'Agent' }}</div>

          <template v-if="message.kind === 'error'">
            <p class="nl-error">{{ message.text }}</p>
          </template>

          <template v-else>
            <p v-if="message.text || message.answer" class="nl-text">{{ message.text || message.answer }}</p>

            <div v-if="message.askCustomer" class="nl-clarify nl-customer-pick">
              <strong>目标客户</strong>
              <p>{{ message.clarification }}</p>
              <div v-if="customers.length" class="nl-chip-list">
                <button v-for="c in customers" :key="c.id" class="nl-chip" :disabled="loading" @click="onPickCustomer(c)">
                  <span>{{ c.display_name || c.username }}</span><small>{{ c.username || c.id }}</small>
                </button>
              </div>
              <p v-else class="nl-muted">当前账号暂无可选客户。数据分析可继续直接在下方输入；如需代客下单请确认账号的授权范围。</p>
            </div>

            <div v-else-if="message.clarification" class="nl-clarify"><strong>需要补充信息</strong><p>{{ message.clarification }}</p></div>

            <AnalysisResultCard v-for="(item, aIndex) in message.analysis_results || []" :key="'a' + aIndex" :data="item.data" />

            <template v-if="message.proxyMode && message.nldraft">
              <div class="nl-proxy" :data-status="message.nldraft.status">
                <div class="nl-proxy-head">
                  <strong>投顾代客下单</strong>
                  <span class="nl-status" :class="message.nldraft.status">{{ statusText(message.nldraft.status) }}</span>
                </div>
                <template v-if="message.nldraft.status === 'awaiting_customer_confirmation' && message.nldraft.draft">
                  <p class="nl-proxy-ok">
                    草稿已生成：{{ message.nldraft.draft.product_name || message.nldraft.draft.product_id }} · ¥{{ formatMoney(message.nldraft.draft.amount) }}。<br />
                    {{ message.nldraft.clarification || '客户需在客户中心“投顾服务”中确认草稿后方可提交订单。' }}
                  </p>
                </template>
                <template v-else-if="message.nldraft.status === 'needs_clarification'">
                  <p class="nl-proxy-clarify">{{ message.nldraft.clarification }}</p>
                  <div v-if="message.nldraft.product_options?.length" class="nl-options">
                    <button v-for="p in message.nldraft.product_options" :key="p.id" class="nl-option" :disabled="loading" @click="pickOption(p)">
                      <span>{{ p.name }}</span><small>{{ p.risk_level }} · 起购 ¥{{ formatMoney(p.minimum_amount) }}</small>
                    </button>
                  </div>
                </template>
                <template v-else>
                  <p class="nl-proxy-fail">{{ message.nldraft.clarification }}</p>
                  <ul v-if="message.nldraft.precheck_reasons?.length"><li v-for="r in message.nldraft.precheck_reasons" :key="r">{{ r }}</li></ul>
                </template>
              </div>
            </template>
          </template>
        </article>

        <p v-if="loading" class="nl-typing">正在处理…</p>
      </div>

      <div class="nl-footer">
        <div v-if="targetId" class="nl-target-line">
          <span class="nl-target-chip">当前目标客户：{{ targetLabel() }} <button title="取消目标客户" @click="clearTarget">✕</button></span>
        </div>
        <div class="nl-input-row">
          <textarea v-model="query" rows="1" class="nl-input" data-testid="supervisor-console-input"
            placeholder="输入自然语言指令…（回车发送，Shift+回车换行）" :disabled="loading"
            @keydown.enter.exact.prevent="send()" @keydown.shift.enter.prevent="query += '\n'" />
          <button class="button" data-testid="supervisor-console-send" :disabled="loading || !query.trim()" @click="send()">
            {{ loading ? '处理中…' : '发送' }}
          </button>
        </div>
      </div>
    </div>
  </section>
  <section v-else class="workspace-page">
    <p class="empty">该控制台仅对具备投顾/管理员权限的工作账号开放。请使用 advisor 或 super_admin 账号登录。</p>
  </section>
</template>

<style scoped>
.nl-console { max-width: 1000px; }
.nl-chat { display: flex; flex-direction: column; height: min(68vh, 680px); min-height: 420px; border: 1px solid #dce4ec; border-radius: 14px; background: #fff; overflow: hidden; }
.nl-messages { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 14px; background: #f7f9fb; }
.nl-welcome { color: #7c8ea0; font-size: 13px; line-height: 1.9; text-align: center; padding: 22px 8px; }
.nl-welcome span { color: #1d4ed8; }
.nl-bubble { display: flex; flex-direction: column; gap: 4px; max-width: 86%; }
.nl-bubble.user { align-self: flex-end; align-items: flex-end; }
.nl-bubble.assistant { align-self: flex-start; align-items: flex-start; }
.nl-sender { font-size: 11px; color: #8aa0b4; padding: 0 4px; }
.nl-bubble.user .nl-sender { display: none; }
.nl-text { margin: 0; padding: 10px 14px; border-radius: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; font-size: 14px; }
.nl-bubble.user .nl-text { background: #1d4ed8; color: #fff; border-bottom-right-radius: 4px; }
.nl-bubble.assistant .nl-text { background: #fff; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px; color: #1f2937; }
.nl-error { margin: 0; padding: 10px 14px; border-radius: 12px; background: #fee2e2; border: 1px solid #fecaca; color: #991b1b; font-size: 13px; }
.nl-clarify { margin-top: 4px; padding: 10px 12px; border-left: 3px solid #f59e0b; background: #fffbeb; color: #92400e; border-radius: 8px; max-width: 100%; font-size: 13px; }
.nl-clarify strong { display: block; margin-bottom: 4px; }
.nl-clarify p { margin: 0; line-height: 1.6; }
.nl-customer-pick, .nl-proxy { background: #fff; border: 1px solid #e2e8f0; border-left: 3px solid #1d4ed8; border-radius: 10px; }
.nl-customer-pick { margin-top: 4px; padding: 10px 12px; }
.nl-chip-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.nl-chip { border: 1px solid #bfdbfe; background: #eff6ff; color: #1e3a8a; border-radius: 999px; padding: 6px 12px; cursor: pointer; display: inline-flex; gap: 6px; align-items: baseline; font-size: 13px; }
.nl-chip:hover { background: #dbeafe; border-color: #1d4ed8; }
.nl-chip small { color: #64748b; font-size: 11px; }
.nl-chip:disabled { opacity: .55; cursor: not-allowed; }
.nl-muted { color: #7c8ea0; font-size: 12px; }
.nl-proxy { margin-top: 6px; padding: 12px; }
.nl-proxy-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; }
.nl-status { font-size: 11px; border-radius: 999px; padding: 3px 8px; }
.nl-status.awaiting_customer_confirmation { background: #dcfce7; color: #166534; }
.nl-status.needs_clarification { background: #e0f2fe; color: #075985; }
.nl-status.precheck_failed { background: #fee2e2; color: #991b1b; }
.nl-proxy p { margin: 4px 0; line-height: 1.6; font-size: 13px; }
.nl-proxy-ok { color: #166534; }
.nl-proxy-clarify { color: #334155; }
.nl-proxy-fail { color: #991b1b; }
.nl-proxy ul { margin: 6px 0 0; padding-left: 18px; color: #991b1b; font-size: 13px; }
.nl-options { display: grid; gap: 8px; margin-top: 10px; }
.nl-option { background: #fff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; text-align: left; display: grid; gap: 3px; cursor: pointer; }
.nl-option:hover { border-color: #1d4ed8; }
.nl-option small { color: #64748b; }
.nl-option:disabled { opacity: .55; cursor: not-allowed; }
.nl-typing { color: #94a3b8; font-size: 12px; padding: 4px 4px 0; }

.nl-footer { border-top: 1px solid #e2e8f0; background: #fff; padding: 10px 14px 12px; }
.nl-target-line { margin-bottom: 8px; }
.nl-target-chip { display: inline-flex; align-items: center; gap: 8px; background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; font-size: 12px; border-radius: 999px; padding: 4px 10px; }
.nl-target-chip button { border: 0; background: transparent; color: #64748b; cursor: pointer; font-size: 12px; line-height: 1; }
.nl-target-chip button:hover { color: #dc2626; }
.nl-input-row { display: flex; gap: 10px; align-items: flex-end; }
.nl-input { flex: 1; border: 1px solid #cbd5e1; border-radius: 12px; padding: 11px 14px; font: inherit; resize: none; line-height: 1.5; max-height: 160px; }
.nl-input:focus { outline: none; border-color: #1d4ed8; }
.nl-input-row .button { white-space: nowrap; }
</style>
