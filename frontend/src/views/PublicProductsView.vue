<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { profileApi } from '../api/profile'
import { tradingApi } from '../api/trading'
import { useAuth } from '../stores/auth'
const products=ref<any[]>([]);const loading=ref(true);const auth=useAuth();const selected=ref<any>(null);const amount=ref(0);const message=ref('')
onMounted(async()=>{try{products.value=(await profileApi.products()).data.data}catch{}finally{loading.value=false}})
async function submit(){if(!selected.value)return;try{await tradingApi.createOrder({product_id:selected.value.id,amount:Number(amount.value)});message.value='订单已创建，请前往我的订单确认';selected.value=null}catch(e:any){message.value=e.response?.data?.detail||e.message}}
</script>
<template><section><div class="section-heading"><div><span class="eyebrow">PRODUCT MARKET</span><h2>产品展示</h2><p>精选财富管理产品，具体条款以产品说明书及正式文件为准。</p></div><span class="pill">产品中心</span></div><div v-if="loading" class="empty">正在读取产品目录…</div><div v-else class="dashboard-grid"><article v-for="product in products" :key="product.id" class="dashboard-card"><span class="card-kicker">{{product.risk_level}} · {{product.product_type}}</span><h3>{{product.name}}</h3><p>{{product.description}}</p><small>起购金额：¥{{product.minimum_amount}} · 期限：{{product.term_days}}天</small><button v-if="auth.loggedIn.value" class="card-link text-button" @click="selected=product;amount=product.minimum_amount">立即购买 →</button><RouterLink v-else class="card-link" to="/login">登录后操作 →</RouterLink></article></div><p v-if="message" class="success">{{message}}</p><div v-if="selected" class="auth-card trade-modal"><span class="eyebrow">NEW ORDER</span><h3>购买 {{selected.name}}</h3><label>购买金额<input v-model.number="amount" type="number" :min="selected.minimum_amount" /></label><div class="action-row"><button class="button" @click="submit">创建交易申请</button><button class="text-button" @click="selected=null">取消</button></div></div></section></template>
