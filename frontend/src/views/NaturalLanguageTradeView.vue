<script setup lang="ts">
import { computed, ref } from 'vue'
import { tradingApi } from '../api/trading'

const query = ref('')
const drafting = ref(false)
const confirming = ref(false)
const error = ref('')
const success = ref('')
const draft = ref<any>(null)
const riskAcknowledged = ref(false)

const currency = (value: unknown) => Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const canConfirm = computed(() => draft.value?.status === 'awaiting_user_confirmation' && !!draft.value?.draft_id && !!draft.value?.confirmation_token && riskAcknowledged.value && !confirming.value)

function messageOf(errorValue: any) {
  return errorValue?.response?.data?.detail?.message || errorValue?.response?.data?.detail || errorValue?.message || '请求失败'
}

async function createDraft() {
  if (!query.value.trim() || drafting.value) return
  drafting.value = true
  error.value = ''
  success.value = ''
  riskAcknowledged.value = false
  try {
    draft.value = (await tradingApi.createNaturalLanguageDraft(query.value.trim())).data.data
  } catch (e: any) {
    error.value = messageOf(e)
  } finally {
    drafting.value = false
  }
}

async function confirmDraft() {
  if (!canConfirm.value) return
  confirming.value = true
  error.value = ''
  try {
    const order = (await tradingApi.confirmNaturalLanguageDraft(draft.value.draft_id, {
      confirmation_token: draft.value.confirmation_token,
      idempotency_key: `customer-nl-confirm-${draft.value.draft_id}-v${draft.value.version}`,
    })).data.data
    success.value = `订单 ${order.order_no || order.id} 已创建，当前状态为“${order.status}”。请前往“我的订单”完成后续确认。`
    draft.value = null
    query.value = ''
    riskAcknowledged.value = false
  } catch (e: any) {
    error.value = messageOf(e)
  } finally {
    confirming.value = false
  }
}

function reset() {
  draft.value = null
  query.value = ''
  error.value = ''
  success.value = ''
  riskAcknowledged.value = false
}
</script>

<template>
  <section class="nl-trade">
    <div class="section-heading">
      <div>
        <span class="eyebrow">AI-ASSISTED TRADE</span>
        <h2>AI 辅助申购</h2>
        <p>用自然语言准备申购草稿。系统会先完成预检；未经您明确确认，不会创建订单、冻结资金或执行交易。</p>
      </div>
      <span class="pill">仅支持申购</span>
    </div>

    <div class="nl-trade-grid">
      <div class="panel-card nl-entry-card">
        <label for="nl-trade-query">您的申购意图</label>
        <textarea id="nl-trade-query" v-model="query" rows="4" :disabled="drafting || confirming" placeholder="例如：我要申购“稳健精选基金”10万元"></textarea>
        <small>请明确提供产品名称与申购金额。AI 不会猜测关键交易参数。</small>
        <div class="action-row">
          <button class="button" :disabled="!query.trim() || drafting || confirming" @click="createDraft">{{ drafting ? '正在预检…' : '生成申购草稿' }}</button>
          <button class="text-button" :disabled="drafting || confirming" @click="reset">清空</button>
        </div>
      </div>

      <aside class="nl-guardrails">
        <strong>交易保护机制</strong>
        <ul>
          <li>先校验产品、最低金额、余额、风险限制和适当性。</li>
          <li>确认令牌仅在短时间内有效，且与本次草稿绑定。</li>
          <li>确认后只创建待确认订单，仍需走既有订单与风控流程。</li>
        </ul>
      </aside>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="success" class="success">{{ success }}</p>

    <div v-if="draft" class="panel-card nl-draft-card">
      <div class="nl-draft-title">
        <div><span class="card-kicker">ORDER DRAFT</span><h3>申购草稿预览</h3></div>
        <span class="status-chip" :class="draft.status">{{ draft.status }}</span>
      </div>

      <template v-if="draft.status === 'awaiting_user_confirmation'">
        <div class="nl-summary-grid">
          <div><span>产品</span><strong>{{ draft.product_name }}</strong></div>
          <div><span>申购金额</span><strong>¥{{ currency(draft.amount) }}</strong></div>
          <div><span>适当性</span><strong class="positive">已通过</strong></div>
          <div><span>草稿有效期</span><strong>{{ new Date(draft.expires_at).toLocaleString('zh-CN') }}</strong></div>
        </div>
        <div class="nl-note"><strong>适当性说明</strong><ul><li v-for="reason in draft.suitability_reasons" :key="reason">{{ reason }}</li></ul></div>
        <div v-if="draft.warnings?.length" class="nl-warning"><strong>请注意</strong><ul><li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li></ul></div>
        <label class="nl-confirm-check"><input v-model="riskAcknowledged" type="checkbox" :disabled="confirming" />我已核对产品、金额、适当性结果和风险提示，并确认提交本次申购申请。</label>
        <div class="action-row"><button class="button" :disabled="!canConfirm" @click="confirmDraft">{{ confirming ? '正在确认…' : '确认并创建订单' }}</button><button class="text-button" :disabled="confirming" @click="reset">放弃本次草稿</button></div>
      </template>

      <template v-else>
        <p class="nl-result-message">{{ draft.clarification || '当前草稿不能确认。' }}</p>
        <div v-if="draft.product_options?.length" class="nl-options"><strong>候选产品</strong><button v-for="product in draft.product_options" :key="product.id" class="nl-product-option" @click="query=`我要申购“${product.name}”${draft.amount || ''}元`"><span>{{ product.name }}</span><small>{{ product.risk_level }} · 起购 ¥{{ currency(product.minimum_amount) }}</small></button></div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.nl-trade{display:grid;gap:18px}.nl-trade-grid{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:18px}.nl-entry-card{display:grid;gap:10px}.nl-entry-card textarea{width:100%;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font:inherit;resize:vertical}.nl-entry-card small{color:#64748b}.nl-guardrails{background:#eff6ff;border:1px solid #bfdbfe;border-radius:14px;padding:18px;color:#1e3a5f}.nl-guardrails ul{margin:10px 0 0;padding-left:18px;display:grid;gap:8px;font-size:13px}.nl-draft-card{display:grid;gap:16px}.nl-draft-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.nl-draft-title h3{margin:3px 0 0}.nl-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.nl-summary-grid>div{border:1px solid #e2e8f0;border-radius:12px;padding:13px;background:#f8fafc;display:grid;gap:5px}.nl-summary-grid span{font-size:12px;color:#64748b}.nl-summary-grid strong{font-size:15px;color:#0f172a}.positive{color:#15803d!important}.nl-note,.nl-warning{border-radius:12px;padding:13px}.nl-note{background:#f0fdf4;border:1px solid #bbf7d0}.nl-warning{background:#fff7ed;border:1px solid #fed7aa}.nl-note ul,.nl-warning ul{margin:8px 0 0;padding-left:18px;display:grid;gap:5px}.nl-confirm-check{display:flex;gap:9px;align-items:flex-start;font-weight:600;line-height:1.55}.nl-confirm-check input{margin-top:4px}.nl-result-message{margin:0}.nl-options{display:grid;gap:8px}.nl-product-option{background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;text-align:left;display:grid;gap:4px;cursor:pointer}.nl-product-option:hover{border-color:#2563eb}.nl-product-option small{color:#64748b}.awaiting_user_confirmation{background:#e0f2fe;color:#075985}@media(max-width:860px){.nl-trade-grid{grid-template-columns:1fr}.nl-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.nl-summary-grid{grid-template-columns:1fr}}
</style>
