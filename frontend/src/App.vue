<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useAuth } from './stores/auth'
const router=useRouter();const route=useRoute();const auth=useAuth()
async function sync(){if(auth.loggedIn.value){const user=await auth.loadMe();if(!user&&route.meta.requiresAuth)await router.push('/login')}else{auth.initialized.value=true}}
onMounted(()=>{sync();window.addEventListener('auth:changed',sync)});onUnmounted(()=>window.removeEventListener('auth:changed',sync))
async function logout(){await auth.logout();await router.push('/login')}
</script>
<template><div class="shell"><header v-if="auth.initialized.value" class="topbar"><div><span class="eyebrow">WEALTH MANAGER</span><h1>智能财富管家</h1></div><nav><RouterLink to="/">首页</RouterLink><RouterLink to="/products">产品展示</RouterLink><RouterLink v-if="auth.loggedIn.value" to="/knowledge">知识库测试</RouterLink><RouterLink v-if="auth.loggedIn.value" to="/customer">我的服务</RouterLink><RouterLink v-if="auth.loggedIn.value" to="/orders">我的订单</RouterLink><RouterLink v-if="auth.loggedIn.value && auth.me.value?.roles?.some(r=>r!=='customer')" to="/staff">工作后台</RouterLink></nav><div class="account-area"><template v-if="auth.loggedIn.value"><span class="account-name">{{auth.loading.value?'读取中…':(auth.me.value?.display_name||auth.me.value?.username||'已登录')}}</span><span v-if="auth.me.value?.roles?.length" class="role-badge">{{auth.me.value.roles[0]}}</span><button class="logout-button" @click="logout">退出登录</button></template><template v-else><RouterLink class="login-link" to="/login">登录</RouterLink><RouterLink class="register-link" to="/register">注册</RouterLink></template></div></header><main><RouterView/></main></div></template>
