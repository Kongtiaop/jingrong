<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import StaffCustomersView from './StaffCustomersView.vue'
import StaffOrdersView from './StaffOrdersView.vue'
import AdminWorkspaceView from './AdminWorkspaceView.vue'
import StaffProductsView from './StaffProductsView.vue'

const auth = useAuth()
const router = useRouter()
const active = ref('dashboard')
const isAdmin = computed(() => auth.me.value?.is_super_admin === true)
const permissions = computed(() => auth.me.value?.permissions || [])

const items = computed(() => {
  const out = [{ key: 'dashboard', label: '数据看板', icon: '▦' }, { key: 'profile', label: '个人信息', icon: '◉' }]
  if (permissions.value.includes('customer:read')) out.push({ key: 'customers', label: '客户管理', icon: '♙' })
  if (permissions.value.includes('order:write')) out.push({ key: 'orders', label: '订单审核', icon: '✓' })
  if (permissions.value.includes('product:write')) out.push({ key: 'products', label: '产品管理', icon: '◇' })
  if (permissions.value.includes('risk:write')) out.push({ key: 'risk', label: '风险管理', icon: '△' })
  if (permissions.value.includes('audit:read')) out.push({ key: 'audit', label: '审计查询', icon: '▤' })
  if (isAdmin.value) out.push({ key: 'admin', label: '用户与权限', icon: '⚙' })
  return out
})
const currentTitle = computed(() => items.value.find(item => item.key === active.value)?.label || '数据看板')
async function logout() { await auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="portal-shell">
    <aside class="portal-sidebar">
      <div class="portal-brand"><div class="portal-logo">W</div><div><strong>财富管理平台</strong><small>业务管理端</small></div></div>
      <div class="portal-user"><div class="portal-avatar">{{(auth.me.value?.display_name || '员').slice(0,1)}}</div><div><strong>{{auth.me.value?.display_name}}</strong><small>{{auth.me.value?.roles?.join('、')}}</small></div></div>
      <nav class="portal-nav">
        <button class="portal-return" @click="router.push('/')"><span>⌂</span> 返回前台</button>
        <div class="portal-divider"></div>
        <button v-for="item in items" :key="item.key" :class="{ active: active === item.key }" @click="active = item.key"><span class="portal-icon">{{item.icon}}</span>{{item.label}}</button>
      </nav>
      <button class="portal-logout" @click="logout"><span>↪</span> 退出登录</button>
    </aside>
    <main class="portal-main">
      <header class="portal-content-header"><div><p>当前位置 / {{currentTitle}}</p><h1>{{currentTitle}}</h1></div><div class="portal-header-badge">{{isAdmin ? '超级管理员' : '工作人员'}}</div></header>
      <section class="portal-content-body">
        <div v-if="active === 'dashboard'" class="portal-dashboard"><div class="portal-stat-grid"><article><span>当前角色</span><strong>{{auth.me.value?.roles?.[0]}}</strong></article><article><span>授权模块</span><strong>{{items.length - 2}}</strong></article><article><span>账号状态</span><strong>正常</strong></article></div></div>
        <div v-else-if="active === 'profile'" class="portal-panel"><h2>个人信息</h2><dl class="portal-profile"><dt>用户名</dt><dd>{{auth.me.value?.username}}</dd><dt>姓名</dt><dd>{{auth.me.value?.display_name}}</dd><dt>角色</dt><dd>{{auth.me.value?.roles?.join('、')}}</dd></dl></div>
        <StaffCustomersView v-else-if="active === 'customers'" />
        <StaffOrdersView v-else-if="active === 'orders'" />
        <StaffProductsView v-else-if="active === 'products'" />
        <AdminWorkspaceView v-else-if="active === 'admin'" />
        <div v-else class="portal-panel"><h2>{{currentTitle}}</h2><p>该模块将在后续业务阶段接入。</p></div>
      </section>
    </main>
  </div>
</template>


