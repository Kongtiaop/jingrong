<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useAuth } from './stores/auth'
import AgentBubble from './components/AgentBubble.vue'

const router = useRouter()
const route = useRoute()
const auth = useAuth()
const menuOpen = ref(false)
const isCustomer = computed(() => auth.me.value?.roles?.includes('customer') ?? false)
const isEmployee = computed(() => auth.loggedIn.value && !isCustomer.value)
const isPortal = computed(() => route.path.startsWith('/backoffice') || route.path.startsWith('/customer-center'))

async function sync() {
  if (auth.loggedIn.value) {
    const user = await auth.loadMe()
    if (!user && route.meta.requiresAuth) await router.replace('/login')
  } else {
    auth.initialized.value = true
    if (route.meta.requiresAuth) await router.replace('/login')
  }
}

function closeMenu() { menuOpen.value = false }
async function logout() {
  closeMenu()
  await auth.logout()
  await router.push('/login')
}

onMounted(() => { sync(); window.addEventListener('auth:changed', sync) })
onUnmounted(() => window.removeEventListener('auth:changed', sync))
</script>

<template>
  <div class="shell" @click="closeMenu">
    <header v-if="auth.initialized.value" class="topbar">
      <div><span class="eyebrow">WEALTH MANAGER</span><h1>智能财富管家</h1></div>
      <nav>
        <RouterLink to="/">首页</RouterLink>
        <RouterLink class="public-nav-product" to="/products">产品展示</RouterLink>
      </nav>
      <div class="account-area">
        <template v-if="auth.loggedIn.value">
          <div class="account-menu-wrap" @click.stop>
            <button class="account-trigger" @click="menuOpen = !menuOpen">
              <span class="account-name">{{ auth.loading.value ? '读取中…' : (auth.me.value?.display_name || auth.me.value?.username || '已登录') }}</span>
              <span class="role-badge">{{ isCustomer ? '客户' : '员工' }}</span>
              <span class="menu-chevron">⌄</span>
            </button>
            <div v-if="menuOpen" class="account-dropdown">
              <div class="account-dropdown-header"><strong>{{ auth.me.value?.display_name }}</strong><small>{{ auth.me.value?.username }}</small></div>
              <RouterLink v-if="isCustomer" to="/customer-center" active-class="account-dropdown-active" @click="closeMenu">个人中心</RouterLink>
              <RouterLink v-if="isEmployee" to="/backoffice" active-class="account-dropdown-active" @click="closeMenu">工作后台</RouterLink>
              
              
              <button class="dropdown-logout" @click="logout">退出登录</button>
            </div>
          </div>
        </template>
        <template v-else>
          <RouterLink class="login-link" to="/login">登录</RouterLink>
          <RouterLink class="register-link" to="/register">注册</RouterLink>
        </template>
      </div>
    </header>
    <main><RouterView /></main>
    <AgentBubble v-if="auth.loggedIn.value && isCustomer" />
  </div>
</template>
