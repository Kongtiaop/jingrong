<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { knowledgeApi } from '../api/knowledge'

const documents=ref<any[]>([]),total=ref(0),file=ref<File|null>(null),category=ref('faq'),permissionLevel=ref('public'),loading=ref(false),uploading=ref(false),message=ref(''),error=ref(''),lastUpdated=ref<Date|null>(null),polling=ref(false)
let timer:number|undefined
const categories=[['company_info','公司信息'],['company_business','公司业务'],['faq','常见问题'],['workflow','服务流程'],['policy','金融政策'],['risk_public','风险资料']]
const processingCount=computed(()=>documents.value.filter((doc)=>doc.status==='processing').length)
function selectFile(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]||null;message.value='';error.value=''}
function statusLabel(status:string){return ({processing:'处理中',active:'已入库',failed:'处理失败'} as Record<string,string>)[status]||status}
function pipelineLabel(doc:any){if(doc.status==='active')return '解析、切分和向量化已完成';if(doc.status==='failed')return doc.error_message||'Worker 处理失败';return '已上传，等待 Outbox/Knowledge Worker 完成'}
function size(bytes:number){return bytes<1024?`${bytes} B`:`${(bytes/1024).toFixed(1)} KB`}
function timeLabel(value:Date|null){return value?value.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—'}
async function load(silent=false){if(!silent)loading.value=true;error.value='';try{const r=await knowledgeApi.documents();documents.value=r.data.data.items;total.value=r.data.data.total;lastUpdated.value=new Date()}catch(e:any){error.value=e.message||'无法加载知识库文档'}finally{if(!silent)loading.value=false}}
function startPolling(){if(timer||!processingCount.value)return;polling.value=true;timer=window.setInterval(async()=>{await load(true);if(!processingCount.value){stopPolling();message.value='文档处理已完成，状态已自动刷新。'}},3000)}
function stopPolling(){if(timer){window.clearInterval(timer);timer=undefined}polling.value=false}
async function upload(){if(!file.value)return;uploading.value=true;error.value='';message.value='';try{const r=await knowledgeApi.upload(file.value,category.value,permissionLevel.value);const data=r.data.data;message.value=`“${data.document.file_name}”已上传，正在自动解析和向量化。`;file.value=null;const input=document.querySelector<HTMLInputElement>('#knowledge-upload-input');if(input)input.value='';await load();startPolling()}catch(e:any){error.value=e.message||'上传失败'}finally{uploading.value=false}}
onMounted(async()=>{await load();startPolling()})
onUnmounted(stopPolling)
</script>

<template>
  <section class="table-module knowledge-admin">
    <div class="module-header"><div><span class="module-kicker">KNOWLEDGE PIPELINE</span><h2>知识库管理</h2><p>上传后自动写入文件存储、Outbox、Redis 队列，并由知识库 Worker 切分、向量化后写入 pgvector。</p></div><div class="knowledge-header-actions"><span v-if="polling" class="knowledge-live-status"><i></i>自动跟踪处理中任务</span><button class="table-action" :disabled="loading" @click="load">刷新状态</button></div></div>
    <section class="knowledge-upload-card">
      <div><span>UPLOAD DOCUMENT</span><h3>上传客服知识文档</h3><p>支持 UTF-8 编码的 TXT / MD 及 DOCX，单文件最大 20MB。仅公开客户可见资料会被客服 RAG 检索。</p></div>
      <div class="knowledge-upload-form"><label class="knowledge-file"><input id="knowledge-upload-input" type="file" accept=".txt,.md,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" @change="selectFile"/><b>{{file?file.name:'选择知识库文档'}}</b><small v-if="file">{{size(file.size)}}</small></label><label>资料分类<select v-model="category"><option v-for="item in categories" :key="item[0]" :value="item[0]">{{item[1]}}</option></select></label><label>可见范围<select v-model="permissionLevel"><option value="public">客户可见</option><option value="staff">仅员工</option></select></label><button class="button" :disabled="!file||uploading" @click="upload">{{uploading?'正在上传…':'上传并入库'}}</button></div>
      <p v-if="message" class="success">{{message}}</p><p v-if="error" class="error">{{error}}</p>
    </section>
    <div class="knowledge-pipeline-note"><b>处理链路：</b>上传 → <code>processing</code> → Outbox → Redis <code>ingestion</code> Stream → Knowledge Worker → 文档切分 / Embedding → <code>active</code>。<em>页面每 3 秒自动刷新处理中任务；最近刷新：{{timeLabel(lastUpdated)}}</em></div>
    <div class="module-tools"><strong>已上传文档（{{total}}）</strong><span v-if="processingCount">当前 {{processingCount}} 个文档处理中，页面会自动跟踪。</span><span v-else>没有待处理文档，当前状态已是最新。</span></div>
    <p v-if="loading" class="empty">正在读取知识库文档…</p>
    <div v-else class="module-table-wrap"><table class="admin-table"><thead><tr><th>文件名</th><th>分类</th><th>可见范围</th><th>大小</th><th>状态</th><th>处理说明</th><th>上传时间</th></tr></thead><tbody><tr v-for="doc in documents" :key="doc.id"><td><b>{{doc.file_name}}</b><small class="knowledge-source-key">{{doc.source_path?.split(/[\\/]/).pop()||doc.document_key}}</small></td><td><span class="table-tag">{{doc.category}}</span></td><td>{{doc.permission_level==='public'?'客户可见':'仅员工'}}</td><td>{{size(Number(doc.file_size||0))}}</td><td><span :class="'knowledge-status '+doc.status">{{statusLabel(doc.status)}}</span></td><td class="knowledge-error">{{pipelineLabel(doc)}}</td><td>{{doc.created_at}}</td></tr><tr v-if="!documents.length"><td colspan="7" class="table-empty">暂无上传文档</td></tr></tbody></table></div>
  </section>
</template>
