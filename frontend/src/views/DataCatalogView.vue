<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { dataCatalogApi } from '../api/data-catalog'

const tables = ref<any[]>([])
const total = ref(0)
const selected = ref<any | null>(null)
const revisions = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const searching = ref(false)
const embedding = ref(false)
const message = ref('')
const error = ref('')
const searchQuery = ref('')
const searchResult = ref<any | null>(null)
const form = ref<any>({})
const rolesText = ref('')
const aliasesText = ref('')
const note = ref('')
const domainFilter = ref('')
const domainOptions = computed(() => [...new Map(tables.value.map((item) => [item.domain_code, item.domain_name])).entries()])

function csv(value: string) { return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean) }
function selectTable(item: any) {
  selected.value = JSON.parse(JSON.stringify(item))
  form.value = JSON.parse(JSON.stringify(item))
  rolesText.value = (item.allowed_roles || []).join(', ')
  aliasesText.value = (item.aliases || []).join(', ')
  note.value = ''
  revisions.value = []
  loadRevisions('table', item.id)
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const response = await dataCatalogApi.tables(domainFilter.value)
    tables.value = response.data.data.items
    total.value = response.data.data.total
    if (selected.value) {
      const updated = tables.value.find((item) => item.id === selected.value.id)
      if (updated) selectTable(updated)
    }
  } catch (e: any) { error.value = e.message || '无法读取数据目录' }
  finally { loading.value = false }
}
async function loadRevisions(type: 'table' | 'column', id: string) {
  try { revisions.value = (await dataCatalogApi.revisions(type, id)).data.data } catch { revisions.value = [] }
}
async function saveTable() {
  if (!selected.value) return
  saving.value = true; error.value = ''; message.value = ''
  try {
    const payload = {
      display_name: form.value.display_name,
      description: form.value.description,
      business_purpose: form.value.business_purpose,
      grain_description: form.value.grain_description,
      usage_guidance: form.value.usage_guidance,
      non_usage_guidance: form.value.non_usage_guidance,
      table_kind: form.value.table_kind,
      permission_level: form.value.permission_level,
      allowed_roles: csv(rolesText.value),
      aliases: csv(aliasesText.value),
      is_queryable: form.value.is_queryable,
      is_active: form.value.is_active,
      change_note: note.value,
    }
    const response = await dataCatalogApi.updateTable(selected.value.id, payload)
    selected.value = response.data.data
    form.value = JSON.parse(JSON.stringify(response.data.data))
    message.value = `已保存 ${response.data.data.display_name}，并写入版本记录。`
    await loadRevisions('table', selected.value.id)
    await load()
  } catch (e: any) { error.value = e.message || '保存失败' }
  finally { saving.value = false }
}
async function toggleColumn(column: any) {
  if (!selected.value) return
  saving.value = true; error.value = ''
  try {
    await dataCatalogApi.updateColumn(column.id, {
      is_queryable: !column.is_queryable,
      change_note: `后台调整字段“${column.display_name}”的未来 NL2SQL 可查询范围`,
    })
    column.is_queryable = !column.is_queryable
    message.value = `字段“${column.display_name}”已更新并进入版本记录。`
  } catch (e: any) { error.value = e.message || '字段更新失败' }
  finally { saving.value = false }
}
async function enqueueEmbeddings() {
  embedding.value = true; error.value = ''; message.value = ''
  try {
    const response = await dataCatalogApi.enqueueEmbeddings(200)
    message.value = `目录向量化任务已入队：${response.data.data.event_id}。Worker 完成后检索会自动升级为混合模式。`
  } catch (e: any) { error.value = e.message || '无法提交目录向量化任务' }
  finally { embedding.value = false }
}
async function search() {
  if (!searchQuery.value.trim()) return
  searching.value = true; error.value = ''
  try { searchResult.value = (await dataCatalogApi.search(searchQuery.value.trim())).data.data }
  catch (e: any) { error.value = e.message || '目录检索失败' }
  finally { searching.value = false }
}
onMounted(load)
</script>

<template>
  <section class="table-module catalog-admin">
    <div class="module-header">
      <div><span class="module-kicker">DATA CATALOG / NL2SQL FOUNDATION</span><h2>数据目录治理</h2><p>为每张业务表和字段维护用途、粒度、可查询范围、别名及审计版本。当前仅用于 Schema Context 检索，尚未开放 SQL 生成或执行。</p></div>
      <div class="catalog-header-actions">
        <button class="table-action" :disabled="embedding" @click="enqueueEmbeddings">{{ embedding ? '向量化入队中…' : '生成/补齐目录向量' }}</button>
        <button class="table-action" :disabled="loading" @click="load">刷新目录</button>
      </div>
    </div>
    <div class="catalog-guard"><b>安全状态：</b><span>Catalog 检索已启用</span><span class="blocked">NL2SQL 生成与执行仍关闭</span><em>未来模型只能拿到 Top-K 候选表、字段与 JOIN 说明，不能获取整库结构。</em></div>
    <div class="catalog-toolbar"><label>业务域<select v-model="domainFilter" @change="load"><option value="">全部业务域</option><option v-for="item in domainOptions" :key="item[0]" :value="item[0]">{{item[1]}}</option></select></label><div class="catalog-search"><input v-model="searchQuery" placeholder="验证召回，例如：客户资产趋势" @keyup.enter="search"/><button class="table-action" :disabled="searching||!searchQuery.trim()" @click="search">{{searching?'检索中…':'验证检索'}}</button></div></div>
    <div v-if="searchResult" class="catalog-search-result"><b>检索结果：</b><span v-for="item in searchResult.tables" :key="item.id">{{item.display_name}}（{{item.table_name}}）</span><small>{{searchResult.retrieval_mode}} · {{searchResult.next_guard}}</small></div>
    <p v-if="error" class="error">{{error}}</p><p v-if="message" class="success">{{message}}</p>
    <div class="catalog-grid"><section class="catalog-table-list"><div class="module-tools"><strong>目录表（{{total}}）</strong><span>先确认业务语义，再允许后续 Agent 选择 Schema。</span></div><div v-if="loading" class="empty">正在加载目录…</div><button v-for="item in tables" :key="item.id" :class="['catalog-row',{selected:selected?.id===item.id}]" @click="selectTable(item)"><span><b>{{item.display_name}}</b><small>{{item.table_name}}</small></span><i>{{item.domain_name}}</i><em :class="item.is_queryable?'enabled':'disabled'">{{item.is_queryable?'可查询':'禁用'}}</em></button></section>
      <section v-if="selected" class="catalog-editor"><div class="catalog-editor-head"><div><span>{{selected.domain_name}}</span><h3>{{selected.table_name}}</h3></div><small>{{selected.columns.length}} 个字段</small></div><div class="catalog-form"><label>显示名称<input v-model="form.display_name"/></label><label>表类别<select v-model="form.table_kind"><option value="entity">实体</option><option value="dimension">维表</option><option value="snapshot">快照</option><option value="event">事件</option></select></label><label>权限级别<select v-model="form.permission_level"><option value="internal">内部</option><option value="restricted">受限</option></select></label><label>允许角色<input v-model="rolesText" placeholder="advisor, operations"/></label><label class="wide">中文别名<input v-model="aliasesText" placeholder="资产趋势, 净资产"/></label><label class="wide">表说明<textarea v-model="form.description" rows="3"/></label><label class="wide">业务用途<textarea v-model="form.business_purpose" rows="3"/></label><label class="wide">数据粒度 / 一行代表什么<textarea v-model="form.grain_description" rows="3"/></label><label class="wide">推荐使用场景<textarea v-model="form.usage_guidance" rows="2"/></label><label class="wide">禁止/不推荐使用场景<textarea v-model="form.non_usage_guidance" rows="2"/></label><label>未来可查询<input type="checkbox" v-model="form.is_queryable"/></label><label>目录启用<input type="checkbox" v-model="form.is_active"/></label><label class="wide">变更说明<textarea v-model="note" rows="2" placeholder="说明为什么调整元数据，便于审计回溯"/></label></div><button class="button" :disabled="saving" @click="saveTable">{{saving?'保存中…':'保存表元数据并记录版本'}}</button>
        <div class="catalog-columns"><div class="module-tools"><strong>字段治理</strong><span>敏感字段及不可查询字段不会进入未来 NL2SQL Context。</span></div><article v-for="column in selected.columns" :key="column.id"><div><b>{{column.display_name}}</b><small>{{column.column_name}} · {{column.semantic_type}}{{column.unit?` · ${column.unit}`:''}}</small><p>{{column.business_meaning}}</p></div><span :class="column.is_sensitive?'sensitive':'normal'">{{column.is_sensitive?'敏感':'普通'}}</span><button :disabled="saving" @click="toggleColumn(column)">{{column.is_queryable?'禁止候选':'允许候选'}}</button></article></div>
        <div class="catalog-revisions"><div class="module-tools"><strong>版本记录</strong><span>编辑表或字段后自动保留快照。</span></div><p v-if="!revisions.length" class="empty">尚无人工编辑版本。</p><article v-for="item in revisions" :key="item.id"><b>v{{item.revision_no}}</b><span>{{item.change_note||'未填写说明'}}</span><small>{{item.created_at}}</small></article></div>
      </section><section v-else class="catalog-empty"><h3>选择一张表</h3><p>左侧选择目录表后，可维护业务语义、字段可查询范围与版本记录。</p></section></div>
  </section>
</template>
