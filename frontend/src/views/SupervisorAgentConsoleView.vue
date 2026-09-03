<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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

type Mode = 'analysis' | 'proxy'
const mode = ref<Mode>('analysis')
const customers = ref<any[]>([])
const selectedCustomer = ref('')
const query = ref('')
const loading = ref(false)
const error = ref('')
const messages = ref<any[]>([])
const conversationId = ref(sessionStorage.getItem('supervisor_agent_conversation_id') || genId())
sessionStorage.setItem('supervisor_agent_conversation_id', conversationId.value)

const canView = computed(() => auth.loggedIn.value && !auth.me.value?.roles?.includes('customer') && (isAdmin.value || isAdvisor.value))

function formatMoney(value: any) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) }
function responseError(e: any) { return e?.response?.data?.detail?.message || e?.response?.data?.detail || e?.message || '助手服务暂时不可用' }

async function loadCustomers() {
  try {
    const source = isAdmin.value ? profileApi.customers('', '', '', 200, 0) : profileApi.advisorCustomers('', '', '', 200, 0)
    customers.value = (await source).data.data.items || []
  } catch (e: any) { error.value = responseError(e) }
}

function statusText(status: string) {
  return ({ awaiting_customer_confirmation: '待客户确认', needs_clarification: '需要补充信息', precheck_failed: '受控校验未通过' } as Record<string, string>)[status] || status
}

function currentCustomerName(id: string) {
  const row = customers.value.find((c: any) => c.id === id)
  return row ? (row.display_name || row.username || id) : (id || '（未选择客户）')
}

function hasAmountInTail() {
  const last = messages.value[messages.value.length - 1]
  return !!last && last.proxyMode === true && last.nldraft?.status === 'needs_clarification' && (last.nldraft?.missing_fields || []).includes('amount')
}

async function send(modeOverride?: Mode, textOverride?: string) {
  const text = (textOverride ?? query.value).trim()
  const target = selectedCustomer.value
  if (!text || loading.value) return
  const useMode: Mode = modeOverride ?? mode.value
  query.value = ''
  error.value = ''
  loading.value = true
  messages.value.push({ role: 'user', text })
  try {
    if (useMode === 'analysis') {
      // 数据分析按权限执行：不强制绑定具体客户（权限范围内聚合），也可聚焦到所选目标客户做逐客户分析。
      const data = (await customerServiceApi.executeTargeted(text, conversationId.value, route.path, { targetCustomerId: target || undefined })).data.data
      const analysisResults = (data.results || []).filter((item: any) => item.intent === 'data_analysis' && item.data)
      const message: any = { role: 'assistant', answer: data.answer || '', analysis_results: analysisResults, clarification: data.clarification || '', overall_status: data.overall_status || '' }
      messages.value.push(message)
      return
    }
    if (!target) { messages.value.push({ role: 'assistant', answer: '投顾代客下单必须先选择被服务的授权目标客户。', proxyMode: true, nldraft: { status: 'needs_clarification', clarification: '请选择授权目标客户后重试。', missing_fields: ['target_customer'], product_options: [] } }); return }
    const result = (await profileApi.advisorNaturalLanguageOperationDraft({ customer_id: target, query: text })).data.data
    messages.value.push({ role: 'assistant', proxyMode: true, nldraft: result, parsedAmount: result.amount })
  } catch (e: any) {
    error.value = responseError(e)
  } finally {
    loading.value = false
  }
}

function pickOption(product: any) {
  const amountHint = hasAmountInTail() ? '10万元' : ''
  send('proxy', `代客户申购“${product.name}”${amountHint}`)
}

onMounted(() => { if (canView.value) loadCustomers() })
</script>

<template>
  <section v-if="canView" class="workspace-page supervisor-console" data-testid="supervisor-console">
    <div class="section-heading">
      <div>
        <span class="eyebrow">SUPERVISOR AGENT CONSOLE</span>
        <h2 data-testid="supervisor-console-title">后台 Agent 控制台</h2>
        <p>面向已授权目标客户：① 数据分析 NL2SQL（柱/饼图）；② 投顾代客下单（自然语言 → 草稿 → 客户确认）。投顾仅见自己客户，数据分析按权限聚焦。</p>
      </div>
      <span class="pill">工作人员</span>
    </div>

    <div class="panel-card console-context">
      <label>目标客户（授权范围）</label>
      <select v-model="selectedCustomer" class="advisor-select" data-testid="supervisor-target-customer">
        <option value="">请选择已授权目标客户</option>
        <option v-for="c in customers" :key="c.id" :value="c.id">{{ c.display_name }}（{{ c.username }}）</option>
      </select>
      <p v-if="!customers.length" class="muted">暂无可用客户。投顾需先由客户在客户中心授权；数据分析将在权限范围内执行。</p>
    </div>

    <div class="panel-card console-tools">
      <div class="console-mode-switch">
        <button class="mode-tab" :class="{ active: mode === 'analysis' }" @click="mode = 'analysis'">① 数据分析 NL2SQL</button>
        <button class="mode-tab" :class="{ active: mode === 'proxy' }" @click="mode = 'proxy'">② 投顾代客下单</button>
      </div>
      <p v-if="mode === 'analysis'" class="console-hint">数据分析按权限执行：不选目标客户则做权限范围内聚合分析；选中 {{ selectedCustomer ? currentCustomerName(selectedCustomer) : '目标客户' }} 则聚焦到该客户逐项分析。例如：“{{ selectedCustomer ? ('分析 ' + currentCustomerName(selectedCustomer) + ' 的资产分布') : '各风险等级客户数量占比' }}”。系统返回只读分析并用柱状/饼图展示。</p>
      <p v-else class="console-hint">例如：“代客户申购‘稳健增值计划’10万元”。系统先生成代客草稿，经授权/适当性/风控校验后交客户确认；不会直接成交。</p>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="panel-card console-chat" data-testid="supervisor-console-messages">
      <article v-for="(message, index) in messages" :key="index" :class="'console-message ' + message.role">
        <p v-if="message.text || message.answer">{{ message.text || message.answer }}</p>

        <div v-if="message.clarification" class="agent-clarification"><strong>需要补充信息</strong><p>{{ message.clarification }}</p></div>

        <AnalysisResultCard v-for="(item, aIndex) in message.analysis_results || []" :key="'a' + aIndex" :data="item.data" />

        <template v-if="message.proxyMode && message.nldraft">
          <div class="proxy-card" :data-status="message.nldraft.status">
            <div class="proxy-head">
              <strong>投顾代客下单</strong>
              <span class="status-chip" :class="message.nldraft.status">{{ statusText(message.nldraft.status) }}</span>
            </div>
            <template v-if="message.nldraft.status === 'awaiting_customer_confirmation' && message.nldraft.draft">
              <p class="proxy-ok">草稿已生成（{{ message.nldraft.draft.product_name || message.nldraft.draft.product_id }} · ¥{{ formatMoney(message.nldraft.draft.amount) }}）。状态：{{ message.nldraft.draft.status }}。客户需在客户中心“投顾服务”中确认草稿后方可提交订单。</p>
            </template>
            <template v-else-if="message.nldraft.status === 'needs_clarification'">
              <p class="proxy-clarify">{{ message.nldraft.clarification }}</p>
              <div v-if="message.nldraft.product_options?.length" class="proxy-options">
                <button v-for="p in message.nldraft.product_options" :key="p.id" class="product-option" @click="pickOption(p)">
                  <span>{{ p.name }}</span><small>{{ p.risk_level }} · 起购 ¥{{ formatMoney(p.minimum_amount) }}</small>
                </button>
              </div>
            </template>
            <template v-else>
              <p class="proxy-fail">{{ message.nldraft.clarification }}</p>
              <ul v-if="message.nldraft.precheck_reasons?.length"><li v-for="r in message.nldraft.precheck_reasons" :key="r">{{ r }}</li></ul>
            </template>
          </div>
        </template>
      </article>
      <p v-if="!messages.length" class="console-empty">选择能力与目标客户后，用自然语言发起请求。</p>
    </div>

    <div class="console-input-row">
      <select v-model="selectedCustomer" class="advisor-select compact" title="目标客户" @change="error=''">
        <option value="">目标客户</option>
        <option v-for="c in customers" :key="c.id" :value="c.id">{{ c.display_name }}</option>
      </select>
      <textarea v-model="query" rows="2" placeholder="输入自然语言指令…" :disabled="loading" @keydown.ctrl.enter.prevent="send()" />
      <button class="button" :disabled="loading || !query.trim()" @click="send()">{{ loading ? '处理中…' : '发送' }}</button>
    </div>
  </section>
  <section v-else class="workspace-page">
    <p class="empty">该控制台仅对具备投顾/管理员权限的工作账号开放。请使用 advisor 或 super_admin 账号登录。</p>
  </section>
</template>

<style scoped>
.supervisor-console { display: grid; gap: 16px; max-width: 980px; }
.panel-card { padding: 16px; border: 1px solid #dce4ec; border-radius: 12px; background: #fff; }
.section-heading h2 { font-size: 28px; margin: 8px 0 6px; }
.supervisor-console .muted { color: #71869a; font-size: 12px; }
.console-context { display: grid; gap: 8px; }
.console-context label, .console-tools .console-hint { font-weight: 600; }
.advisor-select, .console-input-row textarea { border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; font: inherit; background: #fff; }
.console-tools { display: grid; gap: 8px; }
.console-mode-switch { display: flex; gap: 10px; flex-wrap: wrap; }
.mode-tab { border: 1px solid #cbd5e1; border-radius: 999px; padding: 8px 14px; background: #fff; color: #334155; cursor: pointer; font-weight: 600; }
.mode-tab.active { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
.console-hint { color: #64748b; font-size: 13px; }
.console-chat { display: grid; gap: 12px; min-height: 180px; }
.console-message { border-radius: 12px; padding: 12px 14px; line-height: 1.6; }
.console-message.user { background: #eff6ff; border: 1px solid #bfdbfe; align-self: end; max-width: 90%; }
.console-message.assistant { background: #f8fafc; border: 1px solid #e2e8f0; align-self: start; max-width: 100%; }
.console-empty { color: #94a3b8; text-align: center; padding: 26px 0; }
.agent-clarification { margin-top: 8px; padding: 8px 10px; border-left: 3px solid #f59e0b; background: #fffbeb; color: #92400e; font-size: 13px; }
.agent-clarification p { margin: 4px 0 0; }
.proxy-card { margin-top: 8px; border: 1px solid #cbd5e1; border-radius: 12px; padding: 12px; }
.proxy-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 6px; }
.status-chip { font-size: 11px; border-radius: 999px; padding: 3px 8px; }
.status-chip.awaiting_customer_confirmation { background: #dcfce7; color: #166534; }
.status-chip.needs_clarification { background: #e0f2fe; color: #075985; }
.status-chip.precheck_failed { background: #fee2e2; color: #991b1b; }
.proxy-ok { color: #166534; }
.proxy-clarify { color: #334155; }
.proxy-fail { color: #991b1b; }
.proxy-card ul { margin: 6px 0 0; padding-left: 18px; color: #991b1b; font-size: 13px; }
.proxy-options { display: grid; gap: 8px; margin-top: 8px; }
.product-option { background: #fff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; text-align: left; display: grid; gap: 3px; cursor: pointer; }
.product-option:hover { border-color: #1d4ed8; }
.product-option small { color: #64748b; }
.console-input-row { display: grid; grid-template-columns: minmax(130px, 180px) 1fr auto; gap: 10px; align-items: end; }
.console-input-row textarea { resize: vertical; }
@media (max-width: 720px) { .console-input-row { grid-template-columns: 1fr; } }
</style>
