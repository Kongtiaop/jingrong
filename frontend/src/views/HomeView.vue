<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { healthApi } from '../api/health'
const status=ref('检查中');const checks=ref<Record<string,any>>({})
onMounted(async()=>{try{const r=await healthApi.check();status.value=r.data.data.status;checks.value=r.data.data.checks}catch{status.value='不可用'}})
</script>
<template><section class="hero"><div><span class="eyebrow">WEALTH MANAGER</span><h2>公开浏览产品，登录后管理你的财富。</h2><p>所有人都可以浏览产品和知识内容；登录后才能查看个人资产、持仓、订单和提交交易。</p><div class="hero-actions"><RouterLink class="button" to="/products">浏览产品</RouterLink><RouterLink class="button secondary-button" to="/knowledge">查看知识库</RouterLink></div></div><div class="status-card"><span>系统状态</span><strong :class="status==='ok'?'ok':'warn'">{{status}}</strong><small v-for="(value,key) in checks" :key="key">{{key}}：{{value.status}}</small></div></section></template>
