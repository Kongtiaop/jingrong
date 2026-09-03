<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { profileApi } from '../api/profile'
import PaginationBar from '../components/PaginationBar.vue'

const authorizations = ref<any[]>([])
const drafts = ref<any[]>([])
const advisors = ref<any[]>([])
const selectedAdvisor = ref('')
const error = ref('')
const loading = ref(true)
const working = ref('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

// 授权时由客户设定的额度（元），默认放宽以支持大额代客申购演示
const singleLimit = ref<number>(5000000)   // 单笔上限：默认 500 万
const dailyLimit = ref<number>(10000000)   // 当日累计上限：默认 1000 万
const riskCeiling = ref<string>('C5')      // 产品风险上限：默认放行到最高

const RISK_OPTIONS = ['C1', 'C2', 'C3', 'C4', 'C5']

async function load() {
  loading.value = true
  try {
    const a = (await profileApi.advisorAuthorizations(pageSize.value, (page.value - 1) * pageSize.value)).data.data
    authorizations.value = a.items
    total.value = a.total
    const d = (await profileApi.operationDrafts(pageSize.value, (page.value - 1) * pageSize.value)).data.data
    drafts.value = d.items
    const staff = (await profileApi.staffAdvisors(pageSize.value, (page.value - 1) * pageSize.value)).data.data
    advisors.value = staff.items
  } catch (e: any) {
    error.value = e.response?.data?.detail?.message || e.message
  } finally {
    loading.value = false
  }
}

async function chooseAdvisor() {
  if (!selectedAdvisor.value) return
  working.value = 'advisor'
  try {
    await profileApi.createAdvisorAuthorization({
      advisor_id: selectedAdvisor.value,
      allowed_actions: 'buy',
      product_scope: '*',
      max_single_amount: Number(singleLimit.value) || 5000000,
      max_daily_amount: Number(dailyLimit.value) || 10000000,
      risk_level_ceiling: riskCeiling.value,
      requires_customer_confirmation: true,
    })
    await load()
  } catch (e: any) {
    error.value = e.response?.data?.detail?.message || e.message
  } finally {
    working.value = ''
  }
}

async function act(id: string, action: 'confirm' | 'reject' | 'submit') {
  if (working.value) return
  working.value = id + action
  try {
    if (action === 'confirm') await profileApi.confirmOperationDraft(id)
    if (action === 'reject') await profileApi.rejectOperationDraft(id)
    if (action === 'submit') await profileApi.submitOperationDraftOrder(id)
    await load()
  } catch (e: any) {
    error.value = e.response?.data?.detail?.message || e.message
  } finally {
    working.value = ''
  }
}

function go(n: number) { page.value = Math.max(1, n); load() }
function changeSize(n: number) { pageSize.value = n; page.value = 1; load() }
onMounted(load)
</script>

<template>
  <section class="workspace-page">
    <div class="row-actions">
      <button class="button secondary" @click="$router.push('/customer-center')">返回客户中心</button>
      <button class="button secondary" @click="$router.push('/')">返回前台空间</button>
    </div>
    <div class="section-heading">
      <div><span class="eyebrow">ADVISOR FLOW</span><h2>投顾服务</h2><p>当前可选工作人员：{{ advisors.length }} 人。</p></div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="empty">正在读取投顾信息…</div>
    <template v-else>
      <div class="panel-card">
        <h3>选择投顾并设定授权额度</h3>
        <p class="hint">选定后，所选工作人员将可在你设定的上限内代为申购（不会自动成交，草稿仍需你确认）。</p>
        <div class="advisor-form">
          <label>投顾（工作人员）
            <select v-model="selectedAdvisor" class="advisor-select">
              <option value="">请选择工作人员</option>
              <option v-for="a in advisors" :key="a.id" :value="a.id">{{ a.display_name }}（{{ a.username }}）</option>
            </select>
          </label>
          <label>单笔上限（元）
            <input v-model.number="singleLimit" type="number" min="0" step="10000" class="advisor-select" />
          </label>
          <label>当日累计上限（元）
            <input v-model.number="dailyLimit" type="number" min="0" step="10000" class="advisor-select" />
          </label>
          <label>产品风险上限
            <select v-model="riskCeiling" class="advisor-select">
              <option v-for="r in RISK_OPTIONS" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <button class="button" :disabled="!selectedAdvisor || working === 'advisor'" @click="chooseAdvisor">
            {{ working === 'advisor' ? '授权中…' : '确认选择并授权' }}
          </button>
        </div>
      </div>

      <div class="panel-card">
        <h3>当前授权</h3>
        <div v-if="!authorizations.length" class="empty">暂无 active 投顾授权</div>
        <div v-for="a in authorizations" :key="a.id" class="list-row">
          <div>
            <strong>{{ a.advisor_name || a.advisor_id }}</strong>
            <small>{{ a.allowed_actions }} · 单笔上限 ¥{{ a.max_single_amount }} · 当日 ¥{{ a.max_daily_amount }} · 风险上限 {{ a.risk_level_ceiling }}</small>
          </div>
          <span class="status-badge active">{{ a.status }}</span>
        </div>
      </div>

      <div class="panel-card">
        <h3>操作草稿</h3>
        <div v-if="!drafts.length" class="empty">暂无操作草稿</div>
        <div v-for="d in drafts" :key="d.id" class="list-row">
          <div>
            <strong>{{ d.side }} · ¥{{ d.amount }}</strong>
            <small>{{ d.product_name || d.product_id }} · 顾问：{{ d.advisor_name || d.advisor_id }} · {{ d.status }}</small>
          </div>
          <div class="row-actions">
            <button v-if="d.status === 'pending_customer_confirmation'" class="button secondary" @click="act(d.id, 'reject')">拒绝</button>
            <button v-if="d.status === 'pending_customer_confirmation'" class="button" @click="act(d.id, 'confirm')">确认草稿</button>
            <button v-if="d.status === 'customer_confirmed'" class="button" @click="act(d.id, 'submit')">提交订单</button>
          </div>
        </div>
      </div>
      <PaginationBar :page="page" :page-size="pageSize" :total="total" @change="go" @size-change="changeSize" />
    </template>
  </section>
</template>

<style scoped>
.hint { color: #71869a; font-size: 13px; margin: 6px 0 12px; }
.advisor-form { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); align-items: end; }
.advisor-form label { display: grid; gap: 6px; font-weight: 600; font-size: 13px; color: #334155; }
.advisor-form .advisor-select { width: 100%; }
.advisor-form input.advisor-select { font-weight: 400; }
</style>
