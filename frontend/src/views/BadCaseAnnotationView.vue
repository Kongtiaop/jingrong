<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { annotationApi, type BadCaseRow } from '../api/annotation'
import PaginationBar from '../components/PaginationBar.vue'

const INTENT_LABEL = {order_status:'订单状态',workorder_status:'工单状态',asset_summary:'资产概览',product_status:'产品信息',product_recommendation:'产品推荐',business_operation:'交易操作',risk_public:'风险公开信息',risk_explanation:'风险解释',risk_restriction:'风险限制',policy:'政策咨询',faq:'常见问题',human_handoff:'转人工',unknown:'无法识别',company_info:'公司信息',company_business:'公司业务',workflow:'办理流程'} as Record<string,string>
const INTENTS = Object.keys(INTENT_LABEL)
const REASONS = ['intent_unknown','intent_low_confidence','intent_rules_fallback','rag_low_confidence','rag_embedding_degraded','rag_text_fallback','rag_handoff','clarify_stuck']
const reasonLabel = (r: string) => ({intent_unknown:'意图未知',intent_low_confidence:'意图低置信',intent_rules_fallback:'规则兜底',rag_low_confidence:'证据不足',rag_embedding_degraded:'向量降级',rag_text_fallback:'纯文本兜底',rag_handoff:'转人工',clarify_stuck:'澄清卡住'} as Record<string,string>)[r] || r
const evidenceLabel = (e: string) => ({sufficient:'证据充足',low_confidence:'证据不足',embedding_degraded:'向量降级',text_fallback:'纯文本兜底',handoff_required:'需转人工'} as Record<string,string>)[e] || e

const rows = ref<BadCaseRow[]>([])
const total = ref(0)
const page = ref(1), pageSize = ref(20)
const statusFilter = ref('pending'), reasonFilter = ref(''), q = ref(''), splitFilter = ref('')
const loading = ref(false), error = ref(''), saving = ref(false), message = ref('')
const summary = ref({ pending: 0, reviewed: 0, labeled: 0, discarded: 0 })

const selected = ref<BadCaseRow | null>(null)
const form = ref({ intent: '', requires_clarification: false, category: '', split: 'train' as 'train'|'val'|'test', note: '' })

async function loadSummary() {
  try { summary.value = (await annotationApi.summary()).data.data } catch { /* non-fatal */ }
}
async function load() {
  loading.value = true; error.value = ''; message.value = ''
  try {
    const params: Record<string, string | number> = { status: statusFilter.value, limit: pageSize.value, offset: (page.value-1)*pageSize.value }
    if (reasonFilter.value) params.reason = reasonFilter.value
    if (q.value.trim()) params.q = q.value.trim()
    if (splitFilter.value) params.split = splitFilter.value
    const r = (await annotationApi.badCases(params)).data.data
    rows.value = r.items; total.value = r.total
  } catch (e:any) { error.value = e.response?.data?.detail?.message || e.message || '加载失败' }
  finally { loading.value = false }
}
function go(n:number){ page.value = Math.max(1,n); load() }
function changeSize(n:number){ pageSize.value = n; page.value = 1; load() }
function openLabel(row: BadCaseRow){ selected.value = row; form.value = { intent: row.labeled_intent || '', requires_clarification: row.requires_clarification, category: row.category || '', split: (row.split as any) || 'train', note: row.note || '' } }
async function saveLabel(discard: boolean){
  if(!selected.value) return
  saving.value = true; error.value = ''; message.value = ''
  try {
    await annotationApi.label(selected.value.id, { ...form.value, discard })
    message.value = discard ? '已标记为无效并跳过' : '标注已保存'
    selected.value = null
    await load(); await loadSummary()
  } catch(e:any){ error.value = e.response?.data?.detail?.message || e.message || '保存失败' }
  finally { saving.value = false }
}
async function skip(row: BadCaseRow){
  saving.value = true; error.value = ''; message.value = ''
  try { await annotationApi.skip(row.id); message.value = '已跳过'; await load(); await loadSummary() }
  catch(e:any){ error.value = e.response?.data?.detail?.message || e.message || '跳过失败' }
  finally { saving.value = false }
}
function download(blob: Blob, name: string){
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
}
async function exportLabeled(){
  try {
    const cases = (await annotationApi.exportLabeled()).data.data
    const payload = { description: '线上 bad case 人工标注 → 黄金评测集', cases }
    download(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }), 'annotated_golden_cases.json')
  } catch(e:any){ error.value = e.response?.data?.detail?.message || e.message || '导出失败' }
}
async function exportPending(){
  try {
    const items = (await annotationApi.exportPending()).data.data
    download(new Blob([JSON.stringify({ description: '线上 bad case 积累池（未标注）', cases: items }, null, 2)], { type: 'application/json' }), 'bad_cases_export.json')
  } catch(e:any){ error.value = e.response?.data?.detail?.message || e.message || '导出失败' }
}
onMounted(() => { load(); loadSummary() })
</script>
<template>
<section class="table-module">
  <div class="module-header">
    <div><span class="module-kicker">评测标注</span><h2>评测数据标注</h2><p>把线上坏样本标注成正确意图，导出后并入黄金评测集（半自动飞轮）。</p></div>
    <span class="table-summary">待标注 {{summary.pending}} · 已标注 {{summary.labeled}}</span>
  </div>
  <div class="module-tools">
    <select v-model="statusFilter" @change="page=1;load()"><option value="pending">待标注</option><option value="reviewed">已处理</option><option value="all">全部</option></select>
    <select v-model="reasonFilter" @change="page=1;load()"><option value="">全部原因</option><option v-for="r in REASONS" :key="r" :value="r">{{reasonLabel(r)}}</option></select>
    <select v-model="splitFilter" @change="page=1;load()"><option value="">全部数据划分</option><option value="train">训练集(train)</option><option value="val">验证集(val)</option><option value="test">测试集(test)</option></select>
    <input v-model="q" placeholder="搜索问题关键词" @keyup.enter="page=1;load()"/>
    <button class="table-action" @click="page=1;load()">查询</button>
    <span class="toolbar-sep"></span>
    <button class="button ghost" @click="exportLabeled">导出黄金集</button>
    <button class="button ghost" @click="exportPending">导出待标注</button>
  </div>
  <p v-if="message" class="success">{{message}}</p>
  <p v-if="error" class="error">{{error}}</p>
  <div v-if="loading" class="empty">正在读取坏样本…</div>
  <div v-else class="module-table-wrap wide-table">
    <table class="admin-table compact-table">
      <thead><tr><th>用户话术</th><th>触发原因</th><th>置信/证据</th><th>标注意图</th><th>数据划分</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td class="query-cell"><strong>{{row.query}}</strong></td>
          <td><span v-for="r in row.reasons" :key="r" class="table-tag">{{reasonLabel(r)}}</span></td>
          <td><small>{{INTENT_LABEL[row.intent]||row.intent||'—'}} · {{row.confidence!=null?row.confidence.toFixed(2):'—'}}</small><small v-if="row.evidence_status" class="audit-sub">{{evidenceLabel(row.evidence_status)}}</small></td>
          <td><strong>{{INTENT_LABEL[row.labeled_intent]||row.labeled_intent||'—'}}</strong></td>
          <td><span class="table-tag">{{row.split||'—'}}</span></td>
          <td><span class="table-tag">{{row.status==='reviewed'?'已处理':'待标注'}}</span></td>
          <td class="time-cell">{{(row.created_at||'').slice(0,10)}}</td>
          <td>
            <button v-if="row.status==='pending'" class="table-action" @click="openLabel(row)">标注</button>
            <button v-if="row.status==='pending'" class="table-action danger" :disabled="saving" @click="skip(row)">跳过</button>
            <button v-else class="table-action" @click="openLabel(row)">编辑</button>
          </td>
        </tr>
        <tr v-if="!rows.length"><td colspan="8" class="table-empty">当前没有待标注的坏样本</td></tr>
      </tbody>
    </table>
  </div>
  <PaginationBar :page="page" :page-size="pageSize" :total="total" @change="go" @size-change="changeSize"/>
  <Teleport to="body">
    <div v-if="selected" class="admin-modal-backdrop" @click.self="selected=null">
      <div class="admin-modal annotation-modal">
        <div class="modal-header"><h3>标注坏样本</h3><button class="modal-close" @click="selected=null">×</button></div>
        <p class="annotation-query"><strong>用户话术：</strong>{{selected.query}}</p>
        <p class="annotation-reasons"><span v-for="r in selected.reasons" :key="r" class="table-tag">{{reasonLabel(r)}}</span></p>
        <div class="annotation-grid">
          <label>正确意图<select v-model="form.intent"><option value="">— 请选择意图 —</option><option v-for="i in INTENTS" :key="i" :value="i">{{INTENT_LABEL[i]}}（{{i}}）</option></select></label>
          <label>数据划分<select v-model="form.split"><option value="train">训练集(train)</option><option value="val">验证集(val)</option><option value="test">测试集(test)</option></select></label>
          <label class="full">语义类别（可选）<input v-model="form.category" placeholder="如：口语化 / 否定 / 多意图"/></label>
          <label class="full check"><input v-model="form.requires_clarification" type="checkbox"/> 该话术需要澄清</label>
          <label class="full">备注<textarea v-model="form.note" rows="2" placeholder="标注理由 / 期望行为"></textarea></label>
        </div>
        <div class="action-row">
          <button class="button" :disabled="saving||!form.intent" @click="saveLabel(false)">{{saving?'保存中…':'保存标注'}}</button>
          <button class="button ghost" :disabled="saving" @click="saveLabel(true)">标记为无效并跳过</button>
          <button class="text-button" :disabled="saving" @click="selected=null">取消</button>
        </div>
      </div>
    </div>
  </Teleport>
</section>
</template>
