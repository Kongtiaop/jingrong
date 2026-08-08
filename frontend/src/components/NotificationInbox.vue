<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { notificationsApi, type Notification } from '../api/notifications'

const emit = defineEmits<{ openWorkOrder: [id: string] }>()
const rows = ref<Notification[]>([])
const unread = ref(0)
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = (await notificationsApi.list({ limit: 30 })).data.data
    rows.value = result.items || []
    unread.value = result.unread_count || 0
  } catch (e: any) {
    error.value = e.message || '通知加载失败'
  } finally {
    loading.value = false
  }
}

async function open(row: Notification) {
  try {
    if (!row.is_read) {
      const result = (await notificationsApi.markRead(row.id)).data.data
      row.is_read = true
      unread.value = result.unread_count
    }
    if (row.resource_type === 'work_order') emit('openWorkOrder', row.resource_id)
  } catch (e: any) {
    error.value = e.message || '通知更新失败'
  }
}

async function markAllRead() {
  try {
    const result = (await notificationsApi.markAllRead()).data.data
    rows.value.forEach(row => { row.is_read = true })
    unread.value = result.unread_count
  } catch (e: any) {
    error.value = e.message || '标记已读失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="notification-center">
    <div class="notification-center-head"><div><span class="eyebrow">INBOX</span><h3>工单通知</h3></div><button v-if="unread" class="notification-read-all" @click="markAllRead">全部已读</button></div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="notification-empty">正在加载通知…</div>
    <div v-else-if="!rows.length" class="notification-empty">暂无通知。</div>
    <div v-else class="notification-list">
      <button v-for="row in rows" :key="row.id" :class="['notification-row', { unread: !row.is_read }]" @click="open(row)">
        <span class="notification-dot" aria-hidden="true"></span><span class="notification-copy"><strong>{{ row.title }}</strong><small>{{ row.content }}</small><time>{{ new Date(row.created_at).toLocaleString('zh-CN') }}</time></span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.notification-center{min-width:330px;max-width:430px;max-height:520px;overflow:auto;padding:14px;border:1px solid #dbe7f1;border-radius:14px;background:#fff;box-shadow:0 18px 42px #27445b22}.notification-center-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}.notification-center-head h3{margin:3px 0;color:#29465f;font-size:15px}.notification-read-all{border:0;background:transparent;color:#3469d6;font-size:12px;cursor:pointer}.notification-list{display:grid;gap:7px}.notification-row{display:flex;width:100%;gap:10px;padding:11px;border:1px solid transparent;border-radius:10px;background:#f8fbfe;text-align:left;cursor:pointer}.notification-row:hover{border-color:#d9e8f4;background:#f2f7fd}.notification-row.unread{background:#edf5ff}.notification-dot{width:8px;height:8px;margin-top:6px;border-radius:50%;background:transparent;flex:0 0 auto}.notification-row.unread .notification-dot{background:#3a6cf4;box-shadow:0 0 0 4px #3a6cf41a}.notification-copy{display:grid;gap:3px;min-width:0}.notification-copy strong{color:#304a61;font-size:13px}.notification-copy small{color:#708499;font-size:12px;line-height:1.45}.notification-copy time{color:#9aabba;font-size:11px}.notification-empty{padding:22px 8px;color:#8798a8;text-align:center;font-size:13px}@media(max-width:560px){.notification-center{position:fixed;right:12px;left:12px;top:62px;max-width:none;min-width:0;z-index:20}}
</style>
