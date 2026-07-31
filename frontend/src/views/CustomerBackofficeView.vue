<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { tradingApi } from '../api/trading'
import OrdersView from './OrdersView.vue'

const auth = useAuth()
const router = useRouter()
const active = ref('dashboard')
const account = ref<any>(null)
const orders = ref<any[]>([])
const error = ref('')
const loading = ref(true)
const items = [
  { key: 'dashboard', label: '我的看板', icon: '▦' },
  { key: 'profile', label: '个人信息', icon: '◉' },
  { key: 'orders', label: '我的订单', icon: '▤' },
  { key: 'assets', label: '资产与持仓', icon: '◇' },
]
const currentTitle = computed(() => items.find(item => item.key === active.value)?.label || '我的看板')
async function logout() { await auth.logout(); await router.push('/login') }
onMounted(async () => { try { account.value = (await tradingApi.account()).data.data; orders.value = (await tradingApi.orders()).data.data } catch (e: any) { error.value = e.response?.data?.detail || e.message } finally { loading.value = false } })
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
          <div v-else-if="active === 'profile'" class="portal-panel"><h2>个人信息</h2><dl class="portal-profile"><dt>用户名</dt><dd>{{auth.me.value?.username}}</dd><dt>姓名</dt><dd>{{auth.me.value?.display_name}}</dd><dt>身份</dt><dd>客户</dd></dl></div>
          <OrdersView v-else-if="active === 'orders'" />
          <div v-else class="portal-panel"><h2>资产与持仓</h2><p>资产快照和持仓明细将在此展示。</p></div>
        </template>
      </section>
    </main>
  </div>
</template>
