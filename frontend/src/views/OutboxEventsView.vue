<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { adminApi } from '../api/admin'

const status = ref('dead')
const rows = ref<any[]>([])
const counts = ref<Record<string, number>>({})
const error = ref('')
const loading = ref(false)
const retrying = ref('')

async function load(){
  loading.value=true;error.value=''
  try{const data=(await adminApi.outboxEvents(status.value,100)).data.data;rows.value=data.items||[];counts.value=data.counts||{}}
  catch(e:any){error.value=e.message||'Outbox 事件加载失败'}
  finally{loading.value=false}
}
async function retry(id:string){
  retrying.value=id;error.value=''
  try{await adminApi.retryOutboxEvent(id);await load()}catch(e:any){error.value=e.message||'重试失败'}finally{retrying.value=''}
}
onMounted(load)
</script>
<template><section class="workspace-page"><div class="section-heading"><div><span class="eyebrow">OUTBOX OPERATIONS</span><h2 data-testid="outbox-events-title">通知投递监控</h2><p>查看通知 Outbox 状态、失败原因并对 dead 事件发起受控重试。</p></div><button class="button secondary" @click="load">刷新</button></div><div class="panel-card"><div class="row-actions"><select v-model="status" class="advisor-select" @change="load"><option value="">全部状态</option><option value="pending">pending</option><option value="published">published</option><option value="dead">dead</option></select><span class="muted">pending {{counts.pending||0}} · published {{counts.published||0}} · dead {{counts.dead||0}}</span></div><p v-if="error" class="error">{{error}}</p><p v-if="loading" class="muted">正在加载 Outbox 事件…</p><div class="module-table-wrap"><table class="admin-table"><thead><tr><th>事件</th><th>状态</th><th>尝试</th><th>资源</th><th>失败原因</th><th>时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td><strong>{{row.event_type}}</strong><small>{{row.id}}</small></td><td><span class="status-chip" :class="row.status">{{row.status}}</span></td><td>{{row.attempt}}</td><td>{{row.aggregate_type}}<small>{{row.aggregate_id||'—'}}</small></td><td>{{row.error_message||'—'}}</td><td>{{new Date(row.created_at).toLocaleString('zh-CN')}}</td><td><button v-if="row.status==='dead'" class="button secondary" :disabled="retrying===row.id" @click="retry(row.id)">{{retrying===row.id?'重试中…':'重试'}}</button><span v-else class="muted">—</span></td></tr><tr v-if="!rows.length&&!loading"><td colspan="7" class="table-empty">暂无匹配的通知 Outbox 事件。</td></tr></tbody></table></div></div></section></template>
