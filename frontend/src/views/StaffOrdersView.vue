<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { tradingApi } from '../api/trading'

const orders = ref<any[]>([])
const error = ref('')
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    orders.value = (await tradingApi.pending()).data.data
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function approve(id: string) {
  await tradingApi.approve(id, '运营审核通过，Mock 执行')
  await load()
}

async function reject(id: string) {
  await tradingApi.reject(id, '工作人员审核拒绝')
  await load()
}

onMounted(load)
</script>

<template>
  <section>
    <div class="section-heading">
      <div>
        <span class="eyebrow">OPERATIONS WORKSPACE</span>
        <h2>订单审核与执行</h2>
        <p>处理客户待审核交易申请，批准后执行 Mock 交易并更新持仓。</p>
      </div>
      <span class="pill">运营后台</span>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="empty">正在读取待审核订单…</div>
    <div v-else-if="!orders.length" class="empty">暂无待审核订单。</div>

    <article v-for="order in orders" :key="order.id" class="result">
      <div class="result-meta">
        <span>{{ order.product_name || order.product_id }}</span>
        <span>{{ order.order_no }}</span>
      </div>
      <h3>¥{{ order.amount }}</h3>
      <p>客户：{{ order.user_id }}</p>
      <div class="action-row">
        <button class="button" @click="approve(order.id)">审核通过并执行</button>
        <button class="text-button" @click="reject(order.id)">拒绝</button>
      </div>
    </article>
  </section>
</template>
