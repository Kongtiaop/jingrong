<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { adminApi } from '../api/admin'
import { profileApi } from '../api/profile'
import PaginationBar from '../components/PaginationBar.vue'
const roleText:Record<string,string>={customer:'客户',customer_service:'客服',customer_manager:'客户经理',advisor:'理财顾问',product_manager:'产品经理',risk_manager:'风控经理',operations:'运营',auditor:'审计',super_admin:'超级管理员',employee_pending:'待分组员工'}
const statusText:Record<string,string>={active:'启用',disabled:'停用',deleted:'已删除'}
const productTypeText:Record<string,string>={fund:'基金',cash_management:'现金管理',fixed_income:'固收理财',balanced_fund:'平衡配置',equity_fund:'权益基金',private_strategy:'私行策略',corporate_cash:'企业现金管理'}
const module=ref('user');const items=ref<any[]>([]);const total=ref(0);const loading=ref(false);const error=ref('');const page=ref(1);const pageSize=ref(10)
async function load(){loading.value=true;error.value='';try{const r=(await adminApi.recycleBin(module.value,pageSize.value,(page.value-1)*pageSize.value)).data.data;items.value=r.items;total.value=r.total}catch(e:any){error.value=e.response?.data?.detail||e.message}finally{loading.value=false}}
async function restore(item:any){if(!window.confirm('确认恢复该数据？'))return;try{if(module.value==='user')await adminApi.restoreUser(item.id);else await profileApi.restoreProduct(item.id);await load()}catch(e:any){error.value=e.response?.data?.detail||e.message}}
function go(n:number){page.value=Math.max(1,n);load()} function changeSize(n:number){pageSize.value=n;page.value=1;load()}
onMounted(load)
</script>
<template><section class="table-module"><div class="module-header"><div><span class="module-kicker">仅超级管理员</span><h2>数据回收站</h2><p>仅超级管理员可以恢复已软删除的数据。</p></div><span class="table-summary">{{total}} 条</span></div><div class="module-tools"><label>恢复模块<select v-model="module" @change="load"><option value="user">用户数据</option><option value="product">产品数据</option></select></label><button class="table-action" @click="load">刷新</button></div><p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在读取回收站…</div><div v-else class="module-table-wrap"><table class="admin-table"><thead><tr><th>名称</th><th>类型/角色</th><th>状态</th><th>创建时间</th><th>修改时间</th><th>删除时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td>{{item.name}}</td><td>{{module==='user' ? (item.roles||[]).map((r:any)=>roleText[r]||r).join('、') : (productTypeText[item.key]||item.key)}}</td><td>{{statusText[item.status] || item.status}}</td><td>{{item.created_at}}</td><td>{{item.updated_at}}</td><td>{{item.deleted_at}}</td><td><button class="table-action" @click="restore(item)">恢复数据</button></td></tr><tr v-if="!items.length"><td colspan="7" class="table-empty">当前模块回收站为空</td></tr></tbody></table></div><PaginationBar :page="page" :page-size="pageSize" :total="total" @change="go" @size-change="changeSize"/></section></template>



