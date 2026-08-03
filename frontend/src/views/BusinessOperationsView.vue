<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { operationsApi } from '../api/operations'

type AuditItem = { id:string; operation_id:string; operation:string; operation_status:string; customer_id:string; operator_user_id?:string; actor_name:string; action:string; detail:string; created_at:string }
const items=ref<AuditItem[]>([])
const total=ref(0), loading=ref(false), message=ref(''), operationId=ref(''), action=ref(''), page=ref(1)
const pageSize=20
const totalPages=computed(()=>Math.max(1,Math.ceil(total.value/pageSize)))
const operationLabel=(v:string)=>v==='redeem'?'赎回':v==='transfer'?'转账':v
const actionLabel=(v:string)=>({submit:'提交',settle:'审核通过',reject:'审核拒绝'} as Record<string,string>)[v]||v
const statusLabel=(v:string)=>({pending_review:'待审核',settled:'已结算',rejected:'已拒绝'} as Record<string,string>)[v]||v
async function load(){loading.value=true;message.value='';try{const r=await operationsApi.auditLogs({operation_id:operationId.value||undefined,action:action.value||undefined,limit:pageSize,offset:(page.value-1)*pageSize});items.value=r.data.data.items;total.value=r.data.data.total}catch(e:any){message.value=e.message||'加载失败'}finally{loading.value=false}}
function search(){page.value=1;load()}
function clear(){operationId.value='';action.value='';page.value=1;load()}
function formatTime(v:string){return v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'-'}
onMounted(load)
</script>
<template>
<section class="operations-workspace">
  <header class="module-header operations-heading"><div><p class="module-kicker">BUSINESS OPERATIONS</p><h2>业务操作与审计</h2><p>集中查看赎回、转账的提交、审核与结算轨迹。</p></div><div class="operations-summary"><span>审计记录</span><strong>{{total}}</strong></div></header>
  <div class="operations-filter-panel">
    <label><span>操作编号</span><input v-model.trim="operationId" placeholder="输入 operation_id" @keyup.enter="search" /></label>
    <label><span>审计动作</span><select v-model="action" @change="search"><option value="">全部动作</option><option value="submit">提交</option><option value="settle">审核通过</option><option value="reject">审核拒绝</option></select></label>
    <button class="operations-primary" @click="search">查询</button><button class="operations-secondary" @click="clear">重置</button>
  </div>
  <p v-if="message" class="portal-message error">{{message}}</p>
  <div class="operations-board">
    <div class="operations-board-head"><span>操作轨迹</span><small>{{loading?'正在同步…':`第 ${page} / ${totalPages} 页`}}</small></div>
    <div v-if="!loading && !items.length" class="operations-empty"><strong>暂无匹配记录</strong><span>调整筛选条件后重试。</span></div>
    <article v-for="item in items" :key="item.id" class="operation-row">
      <div class="operation-mark" :class="item.action">{{item.operation==='transfer'?'转':'赎'}}</div>
      <div class="operation-main"><div class="operation-title"><strong>{{operationLabel(item.operation)}}</strong><span class="status-chip" :class="item.operation_status">{{statusLabel(item.operation_status)}}</span><span class="action-chip">{{actionLabel(item.action)}}</span></div><code>{{item.operation_id}}</code><p>{{item.detail||'未填写说明'}}</p></div>
      <div class="operation-meta"><strong>{{item.actor_name}}</strong><span>{{formatTime(item.created_at)}}</span><small>客户 {{item.customer_id?.slice(0,8)}}…</small></div>
    </article>
    <footer class="operations-pagination"><button :disabled="page<=1" @click="page--;load()">上一页</button><span>{{page}} / {{totalPages}}</span><button :disabled="page>=totalPages" @click="page++;load()">下一页</button></footer>
  </div>
</section>
</template>
