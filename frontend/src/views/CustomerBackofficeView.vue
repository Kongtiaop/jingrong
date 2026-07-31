<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { tradingApi } from '../api/trading'
import { profileApi } from '../api/profile'
import OrdersView from './OrdersView.vue'

const auth = useAuth()
const router = useRouter()
const active = ref('dashboard')
const account = ref<any>(null)
const orders = ref<any[]>([])
const error = ref('')
const loading = ref(true)
const profile = ref<any>({})
const profileMessage = ref('')
const profileError = ref('')
const savingProfile = ref(false)
const items = [
  { key: 'dashboard', label: '我的看板', icon: '▦' },
  { key: 'profile', label: '个人信息', icon: '◉' },
  { key: 'orders', label: '我的订单', icon: '▤' },
  { key: 'assets', label: '资产与持仓', icon: '◇' },
  { key: 'enterprise', label: '企业认证', icon: '▣' },
  { key: 'risk', label: '风险测评', icon: '△' },
]
const currentTitle = computed(() => items.find(item => item.key === active.value)?.label || '我的看板')
async function saveProfile(){savingProfile.value=true;profileMessage.value='';profileError.value='';try{profile.value=(await profileApi.updateProfile({age:profile.value.age,occupation:profile.value.occupation||'',region:profile.value.region||'',investment_goal:profile.value.investment_goal||'',investment_horizon:profile.value.investment_horizon||'',liquidity_preference:profile.value.liquidity_preference||''})).data.data;profileMessage.value='个人信息已更新'}catch(e:any){profileError.value=e.response?.data?.detail||e.message}finally{savingProfile.value=false}}
async function logout() { await auth.logout(); await router.push('/login') }
onMounted(async () => { try { account.value = (await tradingApi.account()).data.data; orders.value = (await tradingApi.orders()).data.data; profile.value = (await profileApi.summary()).data.data.profile || {} } catch (e: any) { error.value = e.response?.data?.detail || e.message } finally { loading.value = false } })
</script>

<template>
  <div class="portal-shell customer-portal">
    <aside class="portal-sidebar">
      <div class="portal-brand"><div class="portal-logo">W</div><div><strong>财富管理平台</strong><small>客户服务端</small></div></div>
      <div class="portal-user"><div class="portal-avatar">{{(auth.me.value?.display_name || '客').slice(0,1)}}</div><div><strong>{{auth.me.value?.display_name}}</strong><small>客户</small></div></div>
      <nav class="portal-nav">
        <button class="portal-return" @click="router.push('/')"><span>⌂</span> 返回前台</button>
        <div class="portal-divider"></div>
        <button v-for="item in items" :key="item.key" :class="{ active: active === item.key }" @click="active = item.key"><span class="portal-icon">{{item.icon}}</span>{{item.label}}</button>
      </nav>
      <button class="portal-logout" @click="logout"><span>↪</span> 退出登录</button>
    </aside>
    <main class="portal-main">
      <header class="portal-content-header"><div><p>当前位置 / {{currentTitle}}</p><h1>{{currentTitle}}</h1></div><div class="portal-header-badge customer">客户中心</div></header>
      <section class="portal-content-body">
        <p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在读取客户数据…</div>
        <template v-else>
          <div v-if="active === 'dashboard'" class="portal-dashboard"><div class="portal-stat-grid"><article><span>可用余额</span><strong>¥{{account?.available_balance ?? 0}}</strong></article><article><span>冻结金额</span><strong>¥{{account?.frozen_balance ?? 0}}</strong></article><article><span>订单数量</span><strong>{{orders.length}}</strong></article></div></div>
          <div v-else-if="active === 'risk'" class="portal-panel"><h2>风险测评</h2><p>完成风险测评，系统将根据结果判断产品适配范围。</p><RouterLink class="button" to="/customer-center/risk-assessment">进入风险测评</RouterLink></div>
          <div v-else-if="active === 'enterprise'" class="portal-panel"><h2>企业客户认证</h2><p>提交企业资料，审核通过后可转为企业客户。</p><RouterLink class="button" to="/customer-center/enterprise-verification">进入认证申请</RouterLink></div>
          <div v-else-if="active === 'profile'" class="portal-panel"><h2>个人信息</h2><p v-if="profileMessage" class="success">{{profileMessage}}</p><p v-if="profileError" class="error">{{profileError}}</p><form class="product-editor-grid" @submit.prevent="saveProfile"><label>用户名<input :value="auth.me.value?.username" disabled /></label><label>显示名称<input :value="auth.me.value?.display_name" disabled /></label><label>年龄<input v-model.number="profile.age" type="number" min="0" max="150" /></label><label>职业<input v-model="profile.occupation" /></label><label>地区<input v-model="profile.region" /></label><label class="full">投资目标<select v-model="profile.investment_goal"><option value="capital_preservation">资产保值</option><option value="income">稳定收益</option><option value="balanced">均衡配置</option><option value="growth">资产增长</option><option value="aggressive">高风险增值</option></select></label><label>投资期限（年）<input v-model.number="profile.investment_horizon_years" type="number" min="0" max="100" /></label><label>流动性偏好<select v-model="profile.liquidity_preference"><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label><label class="full"><button class="button" :disabled="savingProfile">{{savingProfile?'保存中':'保存个人信息'}}</button></label></form></div>
          <OrdersView v-else-if="active === 'orders'" />
          <div v-else class="portal-panel"><h2>资产与持仓</h2><p>资产快照和持仓明细将在此展示。</p></div>
        </template>
      </section>
    </main>
  </div>
</template>
