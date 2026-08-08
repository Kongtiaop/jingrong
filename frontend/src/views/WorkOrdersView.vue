<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAuth } from '../stores/auth'
import { workordersApi, type WorkOrder, type WorkOrderAttachment, type WorkOrderSummary, type WorkOrderUpdate } from '../api/workorders'

const props = defineProps<{ customerMode?: boolean; focusWorkOrderId?: string }>()
const auth = useAuth()
const permissions = computed(() => auth.me.value?.permissions || [])
const isCustomer = computed(() => props.customerMode || auth.me.value?.roles?.includes('customer') === true)
const canCreate = computed(() => permissions.value.includes('workorder:create'))
const canAssign = computed(() => permissions.value.includes('workorder:assign'))
const canHandle = computed(() => permissions.value.includes('workorder:handle'))
const isSuperAdmin = computed(() => auth.me.value?.is_super_admin === true)
const canHandleRow = (row: WorkOrder) => canHandle.value && (isSuperAdmin.value || row.assigned_to === auth.me.value?.id)

const rows = ref<WorkOrder[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const listScope = ref<'all' | 'assigned'>('assigned')
const filters = ref({ status: '', priority: '', category: '' })
const summary = ref<WorkOrderSummary>({ total: 0, open: 0, assigned: 0, in_progress: 0, pending_customer: 0, resolved: 0, urgent: 0, high: 0 })
const assignees = ref<Array<{ id: string; username: string; display_name: string; roles: string[] }>>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')
const form = ref({ customer_id: '', category: 'customer_service', priority: 'normal', title: '', description: '' })
const note = ref<Record<string, string>>({})
const actionStatus = ref<Record<string, string>>({})
const assignee = ref<Record<string, string>>({})
const supplement = ref<Record<string, string>>({})
const updates = ref<Record<string, WorkOrderUpdate[]>>({})
const expandedUpdates = ref<Record<string, boolean>>({})
const updateLoading = ref<Record<string, boolean>>({})
const attachments = ref<Record<string, WorkOrderAttachment[]>>({})
const attachmentLoading = ref<Record<string, boolean>>({})
const selectedFile = ref<Record<string, File | null>>({})

const statusText: Record<string, string> = { open: '待处理', assigned: '已分派', in_progress: '处理中', pending_customer: '待客户补充', resolved: '已解决', closed: '已关闭', cancelled: '已取消' }
const statusIcon: Record<string, string> = { open: '○', assigned: '↳', in_progress: '↻', pending_customer: '…', resolved: '✓', closed: '▣', cancelled: '×' }
const categoryText: Record<string, string> = { customer_service: '客户服务', complaint: '投诉', risk_investigation: '风控调查', business_exception: '业务异常' }
const priorityText: Record<string, string> = { low: '低', normal: '普通', high: '高', urgent: '紧急' }
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const currentScope = computed(() => isCustomer.value ? 'all' : listScope.value)
const todoCount = computed(() => summary.value.open + summary.value.assigned + summary.value.in_progress + summary.value.pending_customer)

function statusClass(status: string) { return `status-${status.replace('_', '-')}` }

async function loadAttachments(row: WorkOrder) {
  if (attachmentLoading.value[row.id]) return
  attachmentLoading.value[row.id] = true
  try { attachments.value[row.id] = (await workordersApi.attachments(row.id)).data.data || [] }
  catch (e: any) { error.value = e.message || '附件加载失败' }
  finally { attachmentLoading.value[row.id] = false }
}

function onFileChange(row: WorkOrder, event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value[row.id] = input.files?.[0] || null
}

async function uploadAttachment(row: WorkOrder) {
  const file = selectedFile.value[row.id]
  if (!file) { error.value = '请先选择 JPG、PNG 或 PDF 文件。'; return }
  saving.value = true; error.value = ''; message.value = ''
  try {
    const result = (await workordersApi.uploadAttachment(row.id, file)).data.data
    attachments.value[row.id] = [...(attachments.value[row.id] || []), result]
    selectedFile.value[row.id] = null
    message.value = '附件上传成功。'
  } catch (e: any) { error.value = e.message || '附件上传失败' }
  finally { saving.value = false }
}

async function loadUpdates(row: WorkOrder, force = false) {
  if (updateLoading.value[row.id] || (!force && updates.value[row.id])) return
  updateLoading.value[row.id] = true
  try { updates.value[row.id] = (await workordersApi.updates(row.id)).data.data || [] }
  catch (e: any) { error.value = e.message || '补充记录加载失败' }
  finally { updateLoading.value[row.id] = false }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const allowedAllScope = canAssign.value || isSuperAdmin.value
    const scope = isCustomer.value ? 'all' : (listScope.value === 'all' && allowedAllScope ? 'all' : 'assigned')
    const result = (await workordersApi.list({
      scope,
      status: filters.value.status || undefined,
      priority: filters.value.priority || undefined,
      category: filters.value.category || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })).data.data
    rows.value = result.items || []
    total.value = result.total || 0
    summary.value = (await workordersApi.summary(scope)).data.data
    if (canAssign.value) assignees.value = (await workordersApi.assignees()).data.data || []
    await Promise.all(rows.value.map(async row => {
      await loadAttachments(row)
      if (isCustomer.value && row.status === 'pending_customer') {
        expandedUpdates.value[row.id] = true
        await loadUpdates(row, true)
      }
    }))
  } catch (e: any) {
    error.value = e.message || '工单加载失败'
  } finally {
    loading.value = false
  }
}

async function applyFilters() { page.value = 1; await load() }
async function changePage(direction: number) {
  const next = Math.min(pageCount.value, Math.max(1, page.value + direction))
  if (next !== page.value) { page.value = next; await load() }
}

async function create() {
  if (!form.value.title.trim()) return
  saving.value = true; error.value = ''; message.value = ''
  try {
    await workordersApi.create({ ...form.value, ...(isCustomer.value ? {} : { customer_id: form.value.customer_id || undefined }) })
    form.value = { customer_id: '', category: 'customer_service', priority: 'normal', title: '', description: '' }
    message.value = '工单已创建'
    await load()
  } catch (e: any) { error.value = e.message || '工单创建失败' }
  finally { saving.value = false }
}

async function assign(row: WorkOrder) {
  if (!assignee.value[row.id]) return
  error.value = ''
  try { await workordersApi.assign(row.id, assignee.value[row.id]); message.value = '工单已分派'; await load() }
  catch (e: any) { error.value = e.message || '分派失败' }
}

async function handle(row: WorkOrder) {
  const status = (actionStatus.value[row.id] || 'in_progress') as 'in_progress' | 'pending_customer' | 'resolved'
  const content = (note.value[row.id] || '').trim()
  error.value = ''; message.value = ''
  if ((status === 'pending_customer' || status === 'resolved') && !content) {
    error.value = status === 'resolved' ? '标记已解决前必须填写解决说明。' : '标记待客户补充前必须填写具体补充要求。'
    return
  }
  saving.value = true
  try {
    await workordersApi.handle(row.id, status, content)
    note.value[row.id] = ''
    message.value = `工单已更新为“${statusText[status]}”。`
    await load()
  } catch (e: any) { error.value = e.message || '工单处理失败' }
  finally { saving.value = false }
}

async function close(row: WorkOrder) {
  saving.value = true; error.value = ''
  try { await workordersApi.close(row.id); message.value = '工单已关闭'; await load() }
  catch (e: any) { error.value = e.message || '工单关闭失败' }
  finally { saving.value = false }
}

async function downloadAttachment(row: WorkOrder, file: WorkOrderAttachment) {
  error.value = ''
  try {
    const response = await workordersApi.downloadAttachment(row.id, file.id)
    const blobUrl = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = file.original_name
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(blobUrl)
  } catch (e: any) {
    error.value = e.message || '附件下载失败'
  }
}

async function toggleUpdates(row: WorkOrder) {
  expandedUpdates.value[row.id] = !expandedUpdates.value[row.id]
  if (expandedUpdates.value[row.id]) { await loadUpdates(row); await loadAttachments(row) }
}

async function submitSupplement(row: WorkOrder) {
  const content = (supplement.value[row.id] || '').trim()
  error.value = ''; message.value = ''
  if (!content) { error.value = '请填写补充内容后再提交。'; return }
  saving.value = true
  try {
    const result = (await workordersApi.supplement(row.id, content)).data.data
    updates.value[row.id] = [...(updates.value[row.id] || []), result]
    expandedUpdates.value[row.id] = true
    supplement.value[row.id] = ''
    message.value = '补充内容已提交，工单已回到处理中，处理人员会收到提醒。'
    await load()
    const refreshed = rows.value.find(item => item.id === row.id)
    if (refreshed) await loadUpdates(refreshed, true)
  } catch (e: any) { error.value = e.message || '补充内容提交失败' }
  finally { saving.value = false }
}

watch(() => props.focusWorkOrderId, async (id) => {
  if (!id) return
  await load()
  requestAnimationFrame(() => document.getElementById(`workorder-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}, { immediate: true })
onMounted(load)
</script>

<template>
  <section class="workorder-page">
    <div class="workorder-hero">
      <div><span class="eyebrow">WORK ORDERS</span><h2>{{ isCustomer ? '我的工单' : '工单工作台' }}</h2><p>{{ isCustomer ? '提交服务问题并跟踪处理进度。' : '优先处理我的高优先级和待客户补充工单。' }}</p></div>
      <span class="workorder-count">{{ total }} 条记录</span>
    </div>
    <p v-if="message" class="success" aria-live="polite">{{ message }}</p>
    <p v-if="error" class="error" aria-live="polite">{{ error }}</p>

    <div v-if="!isCustomer" class="workorder-todos">
      <button type="button" :class="['workorder-todo', { active: currentScope === 'assigned' }]" @click="listScope='assigned'; applyFilters()"><span>我的待办</span><strong>{{ todoCount }}</strong></button>
      <button v-if="canAssign || isSuperAdmin" type="button" :class="['workorder-todo', { active: currentScope === 'all' }]" @click="listScope='all'; applyFilters()"><span>全部可见</span><strong>{{ summary.total }}</strong></button>
      <div class="workorder-todo"><span>待客户补充</span><strong>{{ summary.pending_customer }}</strong></div>
      <div class="workorder-todo urgent"><span>高 / 紧急</span><strong>{{ summary.high + summary.urgent }}</strong></div>
    </div>

    <div class="workorder-filter">
      <label v-if="!isCustomer">范围<select v-model="listScope" @change="applyFilters"><option value="assigned">我的待办</option><option v-if="canAssign || isSuperAdmin" value="all">全部可见</option></select></label>
      <label>状态<select v-model="filters.status" @change="applyFilters"><option value="">全部状态</option><option value="open">待处理</option><option value="assigned">已分派</option><option value="in_progress">处理中</option><option value="pending_customer">待客户补充</option><option value="resolved">已解决</option><option value="closed">已关闭</option></select></label>
      <label>优先级<select v-model="filters.priority" @change="applyFilters"><option value="">全部优先级</option><option value="urgent">紧急</option><option value="high">高</option><option value="normal">普通</option><option value="low">低</option></select></label>
      <label>类型<select v-model="filters.category" @change="applyFilters"><option value="">全部类型</option><option value="customer_service">客户服务</option><option value="complaint">投诉</option><option value="risk_investigation">风控调查</option><option value="business_exception">业务异常</option></select></label>
      <button type="button" class="ghost-button" @click="filters={ status: '', priority: '', category: '' }; applyFilters()">重置</button>
    </div>

    <form v-if="canCreate" class="workorder-create" @submit.prevent="create">
      <div class="workorder-form-head"><div><span class="eyebrow">NEW TICKET</span><h3>创建工单</h3></div><span>{{ isCustomer ? '仅创建本人服务请求' : '按客户和业务范围创建' }}</span></div>
      <div class="workorder-form-grid">
        <label v-if="!isCustomer">客户 ID<input v-model="form.customer_id" placeholder="客户用户 ID" required /></label>
        <label>类型<select v-model="form.category"><option value="customer_service">客户服务</option><option value="complaint">投诉</option><option value="risk_investigation">风控调查</option><option value="business_exception">业务异常</option></select></label>
        <label>优先级<select v-model="form.priority"><option value="low">低</option><option value="normal">普通</option><option value="high">高</option><option value="urgent">紧急</option></select></label>
        <label class="wide">标题<input v-model="form.title" maxlength="200" required placeholder="请概括需要处理的问题" /></label>
        <label class="wide">描述<textarea v-model="form.description" rows="3" maxlength="10000" placeholder="补充背景、时间和期望处理方式"></textarea></label>
      </div><button class="button" :disabled="saving">{{ saving ? '提交中…' : '创建工单' }}</button>
    </form>

    <div v-if="loading" class="workorder-empty">正在加载工单…</div>
    <div v-else-if="!rows.length" class="workorder-empty">没有符合当前筛选条件的工单。</div>
    <div v-else class="workorder-list">
      <article v-for="row in rows" :id="`workorder-${row.id}`" :key="row.id" class="workorder-card">
        <div class="workorder-card-top"><div><span class="workorder-ticket">{{ row.ticket_no }}</span><h3>{{ row.title }}</h3></div><span :class="['workorder-status', statusClass(row.status)]"><b>{{ statusIcon[row.status] || '•' }}</b><span>{{ statusText[row.status] || row.status }}</span></span></div>
        <div class="workorder-meta"><span>{{ categoryText[row.category] || row.category }}</span><span :class="['priority-mark', row.priority]">优先级：{{ priorityText[row.priority] || row.priority }}</span><span>创建：{{ new Date(row.created_at).toLocaleString('zh-CN') }}</span></div>
        <p class="workorder-description">{{ row.description || '暂无描述' }}</p>
        <p v-if="canHandle && !isSuperAdmin && row.assigned_to !== auth.me?.id" class="workorder-assignment-hint">当前工单由其他处理人负责，你只能查看。</p>
        <p v-if="row.resolution_note" class="workorder-resolution">处理说明：{{ row.resolution_note }}</p>
        <div class="workorder-updates"><button type="button" class="workorder-update-toggle" @click.prevent.stop="toggleUpdates(row)">{{ expandedUpdates[row.id] ? '收起补充记录' : '查看补充记录' }}</button><div v-if="expandedUpdates[row.id]" class="workorder-update-list"><p v-if="updateLoading[row.id]" class="workorder-update-empty">正在加载补充记录…</p><p v-else-if="!updates[row.id]?.length" class="workorder-update-empty">暂无补充记录。</p><article v-for="item in updates[row.id] || []" :key="item.id" :class="['workorder-update-item', item.author_role]"><strong>{{ item.author_role === 'customer' ? '客户补充' : '工作人员补充要求' }}</strong><span>{{ item.content }}</span><time>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</time></article></div></div><div v-if="attachments[row.id]?.length" class="workorder-attachments"><strong>附件</strong><button v-for="file in attachments[row.id]" :key="file.id" type="button" class="attachment-download" @click="downloadAttachment(row, file)">{{ file.original_name }} · {{ Math.ceil(file.size_bytes / 1024) }} KB · 下载</button></div>
        <div v-if="isCustomer && row.status==='pending_customer'" class="workorder-supplement"><strong>工作人员正在等待你的补充</strong><small>请先查看上方“补充记录”中的具体要求；提交后工单会自动回到处理中。</small><textarea v-model="supplement[row.id]" rows="3" maxlength="10000" placeholder="请补充工作人员要求的材料、时间、凭证或问题细节"></textarea><label class="attachment-picker">上传凭证（JPG / PNG / PDF）<input type="file" accept="image/jpeg,image/png,application/pdf" @change="onFileChange(row, $event)" /></label><button type="button" class="button" :disabled="saving" @click.prevent.stop="submitSupplement(row)">{{ saving ? '提交中…' : '提交补充内容' }}</button><button type="button" class="ghost-button" :disabled="saving || !selectedFile[row.id]" @click.prevent.stop="uploadAttachment(row)">上传附件</button></div><p v-else-if="isCustomer && row.status==='in_progress'" class="workorder-progress-hint">客户补充已经提交，工单正在由工作人员继续处理。</p>
        <div v-if="!isCustomer && (canAssign || canHandleRow(row)) && !['closed','cancelled'].includes(row.status)" class="workorder-actions">
          <div v-if="canAssign" class="workorder-action-group"><select v-model="assignee[row.id]"><option value="">选择处理人</option><option v-for="person in assignees" :key="person.id" :value="person.id">{{ person.display_name || person.username }} · {{ person.roles.join('/') }}</option></select><button type="button" class="ghost-button" @click.prevent.stop="assign(row)">分派</button></div>
          <div v-if="canHandleRow(row)" class="workorder-action-group"><select v-model="actionStatus[row.id]" :disabled="row.status==='resolved'"><option value="in_progress">处理中</option><option value="pending_customer">待客户补充</option><option value="resolved">已解决</option></select><input v-if="row.status!=='resolved'" v-model="note[row.id]" :placeholder="(actionStatus[row.id]||'in_progress')==='resolved' ? '解决说明（必填）' : (actionStatus[row.id]||'in_progress')==='pending_customer' ? '需客户补充内容（必填）' : '处理说明（可选）'" /><button v-if="row.status!=='resolved'" type="button" class="button" :disabled="saving" @click.prevent.stop="handle(row)">{{saving?'提交中…':'提交处理'}}</button><button v-if="row.status==='resolved'" type="button" class="ghost-button" :disabled="saving" @click.prevent.stop="close(row)">关闭</button></div>
        </div>
      </article>
    </div>
    <div v-if="total > pageSize" class="workorder-pagination"><button type="button" class="ghost-button" :disabled="page<=1" @click="changePage(-1)">上一页</button><span>第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }} 条</span><button type="button" class="ghost-button" :disabled="page>=pageCount" @click="changePage(1)">下一页</button></div>
  </section>
</template>

<style scoped>
.workorder-page{display:grid;gap:18px}.workorder-hero{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;padding:22px 24px;border:1px solid #dce8f1;border-radius:18px;background:linear-gradient(135deg,#f6fbff,#fff)}.workorder-hero h2{margin:6px 0;color:#284963}.workorder-hero p{margin:0;color:#718499;font-size:13px}.workorder-count{color:#3a6cf4;font-weight:800}.workorder-todos{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.workorder-todo{display:grid;gap:5px;padding:13px 15px;border:1px solid #dce6ef;border-radius:12px;background:#fff;color:#718499;text-align:left}.workorder-todo.active{border-color:#7198ef;background:#f2f6ff}.workorder-todo span{font-size:12px}.workorder-todo strong{color:#315fbd;font-size:22px}.workorder-todo.urgent strong{color:#c04c35}.workorder-filter{display:flex;gap:10px;align-items:end;flex-wrap:wrap;padding:14px 16px;border:1px solid #dce6ef;border-radius:12px;background:#fff}.workorder-filter label{display:grid;gap:5px;color:#718499;font-size:12px}.workorder-filter select{min-width:120px;padding:8px 9px;border:1px solid #d6e1eb;border-radius:8px;background:#fff;color:#334d65;font:inherit}.workorder-create{padding:20px;border:1px solid #dce6ef;border-radius:16px;background:#fff;box-shadow:0 12px 32px #34516a0b}.workorder-form-head{display:flex;justify-content:space-between;gap:16px;margin-bottom:14px}.workorder-form-head h3{margin:4px 0;color:#2d455d}.workorder-form-head>span{color:#8a9aaa;font-size:12px}.workorder-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:14px}.workorder-form-grid label{display:grid;gap:6px;color:#6e8193;font-size:12px}.workorder-form-grid .wide{grid-column:1/-1}.workorder-form-grid input,.workorder-form-grid select,.workorder-form-grid textarea,.workorder-action-group input,.workorder-action-group select{padding:9px 10px;border:1px solid #d6e1eb;border-radius:8px;background:#fff;color:#334d65;font:inherit}.workorder-list{display:grid;gap:12px}.workorder-card{padding:18px 20px;border:1px solid #dce6ef;border-radius:15px;background:#fff}.workorder-card-top{display:flex;justify-content:space-between;gap:16px}.workorder-ticket{color:#3a6cf4;font:700 11px ui-monospace,monospace;letter-spacing:.08em}.workorder-card h3{margin:6px 0;color:#2c4660;font-size:16px}.workorder-status{display:inline-flex;align-items:center;gap:7px;min-width:94px;padding:7px 10px;border:1px solid #d9e4ee;border-radius:8px;background:#f7fafc;color:#60778d;font-size:12px;font-weight:700;white-space:nowrap}.workorder-status b{display:grid;place-items:center;width:18px;height:18px;border-radius:5px;background:#dce6ef;color:#45627b;font-size:13px;line-height:1}.workorder-status.status-in-progress{border-color:#b8d0fb;background:#f0f6ff;color:#3569c8}.workorder-status.status-in-progress b{background:#d5e6ff;color:#3569c8}.workorder-status.status-pending-customer{border-color:#f0d8aa;background:#fff9ed;color:#a86f19}.workorder-status.status-pending-customer b{background:#ffeac0;color:#a86f19}.workorder-status.status-resolved{border-color:#bfe4d0;background:#f1fbf6;color:#19865a}.workorder-status.status-resolved b{background:#d2f1df;color:#19865a}.workorder-status.status-assigned{border-color:#d5cdf6;background:#f6f3ff;color:#6755b2}.workorder-status.status-assigned b{background:#e5defd;color:#6755b2}.workorder-status.status-closed{border-color:#d6dce2;background:#f4f6f8;color:#687582}.workorder-status.status-closed b{background:#e5eaef;color:#687582}.workorder-meta{display:flex;flex-wrap:wrap;gap:12px;color:#8a9aaa;font-size:11px}.priority-mark.high,.priority-mark.urgent{font-weight:800;color:#bb553f}.workorder-description{margin:14px 0 0;color:#526a80;line-height:1.65;white-space:pre-wrap}.workorder-resolution{margin:8px 0 0;color:#19865a;font-size:12px}.workorder-assignment-hint{margin:8px 0 0;color:#9a6a2f;font-size:12px}.workorder-updates{margin-top:12px}.workorder-update-toggle{border:0;background:transparent;color:#3569c8;font-size:12px;padding:0;cursor:pointer}.workorder-update-list{display:grid;gap:7px;margin-top:9px;padding:10px;border-radius:10px;background:#f8fbfe}.workorder-update-item{display:grid;gap:3px;padding:8px 10px;border-left:3px solid #c7d9ea;background:#fff;color:#526a80;font-size:12px}.workorder-update-item.customer{border-left-color:#3a6cf4}.workorder-update-item strong{color:#2f4d66;font-size:12px}.workorder-update-item time{color:#9aabba;font-size:11px}.workorder-update-empty{margin:0;color:#8b9aaa;font-size:12px}.workorder-attachments{display:grid;gap:5px;margin-top:9px;padding:9px 10px;border-radius:9px;background:#f8fbfe}.workorder-attachments strong{color:#526a80;font-size:12px}.workorder-attachments a,.attachment-download{border:0;background:transparent;padding:0;color:#3569c8;font-size:12px;text-align:left;cursor:pointer}.attachment-picker{display:grid;gap:5px;color:#617c97;font-size:12px}.workorder-supplement{display:grid;gap:9px;margin-top:14px;padding:14px;border:1px solid #bed4f0;border-radius:12px;background:#f4f8ff}.workorder-supplement strong{color:#2e5e9d;font-size:13px}.workorder-supplement small,.workorder-progress-hint{color:#617c97;font-size:12px;line-height:1.5}.workorder-progress-hint{margin:12px 0 0;padding:10px 12px;border-radius:9px;background:#f4f8ff}.workorder-supplement textarea{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #c7d9ea;border-radius:8px;background:#fff;font:inherit}.workorder-actions{display:grid;gap:9px;margin-top:16px;padding-top:14px;border-top:1px solid #edf1f5}.workorder-action-group{display:flex;gap:8px;flex-wrap:wrap}.workorder-action-group input{min-width:190px;flex:1}.workorder-action-group select{min-width:130px}.ghost-button{padding:9px 13px;border:1px solid #cbd9e5;border-radius:8px;background:#fff;color:#45627b;cursor:pointer}.workorder-pagination{display:flex;justify-content:center;align-items:center;gap:12px;color:#718499;font-size:12px}.workorder-pagination button:disabled{opacity:.45;cursor:not-allowed}.workorder-empty{padding:36px;text-align:center;color:#8a9aaa;border:1px dashed #d4e0ea;border-radius:14px}@media(max-width:760px){.workorder-todos{grid-template-columns:repeat(2,minmax(0,1fr))}.workorder-hero,.workorder-form-head{align-items:flex-start;flex-direction:column}.workorder-form-grid{grid-template-columns:1fr}.workorder-form-grid .wide{grid-column:auto}.workorder-action-group{align-items:stretch;flex-direction:column}.workorder-action-group>*{width:100%}}
</style>
