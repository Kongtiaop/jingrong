<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { profileApi } from '../api/profile'
import { tradingApi } from '../api/trading'
import { useAuth } from '../stores/auth'

const products=ref<any[]>([])
const loading=ref(true)
const submitting=ref(false)
const auth=useAuth()
const selected=ref<any>(null)
const amount=ref<number | null>(null)
const message=ref('')
const error=ref('')
const risk=ref<any>(null)
const profile=ref<any>(null)
const riskOrder:Record<string,number>={C1:1,C2:2,C3:3,C4:4,C5:5,R1:1,R2:2,R3:3,R4:4,R5:5}
const minimumAmount=computed(()=>Number(selected.value?.minimum_amount||0))

onMounted(async()=>{
  try{
    products.value=(await profileApi.products()).data.data
    if(auth.loggedIn.value){
      const [assessment,summary]=await Promise.all([profileApi.riskAssessment(),profileApi.summary()])
      risk.value=assessment.data.data
      profile.value=summary.data.data
    }
  }catch(e:any){
    error.value=e.response?.data?.detail?.message||e.response?.data?.detail||e.message
  }finally{loading.value=false}
})
function productBlocked(product:any){
  const level=riskOrder[product.risk_level]||1
  const current=riskOrder[risk.value?.risk_level||'C1']||1
  return level>current
}
function productHint(product:any){
  if(profile.value?.risk_operation?.status==='restricted')return '当前账户已被风险限制，暂不能购买'
  if(product.target_customer_type==='enterprise'&&profile.value?.profile?.customer_type!=='enterprise')return '仅限企业客户'
  if(productBlocked(product))return risk.value?'风险承受能力不足，请完成更高等级测评':'请先完成风险测评'
  return ''
}
function openPurchase(product:any){
  error.value='';message.value='';selected.value=product;amount.value=Number(product.minimum_amount||0)
}
async function submit(){
  if(!selected.value||submitting.value)return
  const value=Number(amount.value)
  if(!Number.isFinite(value)||value<minimumAmount.value){error.value=`购买金额不能低于 ¥${minimumAmount.value}`;return}
  submitting.value=true;error.value='';message.value=''
  try{
    const response=await tradingApi.createOrder({product_id:selected.value.id,amount:value,idempotency_key:`customer-web-${selected.value.id}-${Date.now()}`})
    const order=response.data.data
    message.value=`订单 ${order.order_no||order.id} 已创建，请前往客户中心确认。`
    selected.value=null
  }catch(e:any){
    error.value=e.response?.data?.detail?.message||e.response?.data?.detail||e.message||'创建交易申请失败'
  }finally{submitting.value=false}
}
</script>
<template><section><div class="section-heading"><div><span class="eyebrow">PRODUCT MARKET</span><h2>产品展示</h2><p>精选财富管理产品，具体条款以产品说明书及正式文件为准。</p></div><span class="pill">产品中心</span></div><div v-if="loading" class="empty">正在读取产品目录…</div><div v-else class="dashboard-grid"><article v-for="product in products" :key="product.id" class="dashboard-card"><span class="card-kicker">{{product.risk_level}} · {{product.product_type}}</span><h3>{{product.name}}</h3><p>{{product.description}}</p><small>起购金额：¥{{product.minimum_amount}} · 期限：{{product.term_days}}天</small><button v-if="auth.loggedIn.value" class="card-link text-button" :disabled="!!productHint(product)" :title="productHint(product)" @click="openPurchase(product)">{{productHint(product)||'立即购买 →'}}</button><RouterLink v-else class="card-link" to="/login">登录后操作 →</RouterLink></article></div><p v-if="message" class="success">{{message}}</p><p v-if="error" class="error">{{error}}</p><div v-if="selected" class="admin-modal-backdrop" @click.self="selected=null"><div class="auth-card trade-modal"><span class="eyebrow">NEW ORDER</span><h3>购买 {{selected.name}}</h3><p>起购金额 ¥{{minimumAmount}}，创建后还需在客户中心确认。</p><label>购买金额<input v-model.number="amount" type="number" :min="minimumAmount" step="0.01" :disabled="submitting" /></label><div class="action-row"><button class="button" :disabled="submitting" @click="submit">{{submitting?'正在创建…':'创建交易申请'}}</button><button class="text-button" :disabled="submitting" @click="selected=null">取消</button></div></div></div></section></template>
