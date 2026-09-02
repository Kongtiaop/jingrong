<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { genId } from '../utils/id'
import { operationsApi } from '../api/operations'
import { profileApi } from '../api/profile'

type AuditItem = { id:string; operation_id:string; operation:string; operation_status:string; customer_id:string; operator_user_id?:string; actor_name:string; action:string; detail:string; created_at:string }
const items=ref<AuditItem[]>([]),pendingItems=ref<any[]>([]),customers=ref<any[]>([])
const total=ref(0),pendingTotal=ref(0),loading=ref(false),pendingLoading=ref(false),message=ref(''),operationMessage=ref(''),operationId=ref(''),action=ref(''),pendingType=ref(''),reviewing=ref(''),reviewNotes=ref<Record<string,string>>({}),page=ref(1)
const staffForm=ref({customer_id:'',kind:'redeem',product_id:'',quantity:'',redeem_amount:'',to_account:'',transfer_amount:'',customer_confirmed:false})
const staffSubmitting=ref(false),staffSubmitMessage=ref('')
const pageSize=20
const totalPages=computed(()=>Math.max(1,Math.ceil(total.value/pageSize)))
const operationLabel=(v:string)=>v==='redeem'?'赎回':v==='transfer'?'转账':v
const actionLabel=(v:string)=>({submit:'提交',settle:'审核通过',reject:'审核拒绝'} as Record<string,string>)[v]||v
const statusLabel=(v:string)=>({pending_review:'待审核',settled:'已结算',rejected:'已拒绝'} as Record<string,string>)[v]||v
function idempotencyKey(prefix:string){return `${prefix}:${genId()}`}
async function loadCustomers(){try{const r=await profileApi.customers('', '', '', 100, 0);customers.value=r.data.data.items||[]}catch(e:any){staffSubmitMessage.value=e.message||'客户列表加载失败'}}
async function submitStaffOperation(){
  staffSubmitMessage.value=''
  const customerId=staffForm.value.customer_id
  if(!customerId){staffSubmitMessage.value='请先选择客户。';return}
  if(!staffForm.value.customer_confirmed){staffSubmitMessage.value='请确认客户已完成本次代办授权/确认。';return}
  staffSubmitting.value=true
  try{
    if(staffForm.value.kind==='redeem'){
      const quantity=Number(staffForm.value.quantity),amount=Number(staffForm.value.redeem_amount)
      if(!staffForm.value.product_id||!Number.isFinite(quantity)||quantity<=0||!Number.isFinite(amount)||amount<=0)throw new Error('请填写产品 ID、赎回数量和金额。')
      await operationsApi.staffRedeem({customer_id:customerId,product_id:staffForm.value.product_id.trim(),quantity,amount,customer_confirmed:true,idempotency_key:idempotencyKey('staff-redeem')})
      staffSubmitMessage.value='已为客户提交赎回申请，等待后续审核。'
      staffForm.value.product_id='';staffForm.value.quantity='';staffForm.value.redeem_amount=''
    }else{
      const amount=Number(staffForm.value.transfer_amount)
      if(!staffForm.value.to_account.trim()||!Number.isFinite(amount)||amount<=0)throw new Error('请填写收款账户和转账金额。')
      await operationsApi.staffTransfer({customer_id:customerId,to_account:staffForm.value.to_account.trim(),amount,customer_confirmed:true,idempotency_key:idempotencyKey('staff-transfer')})
      staffSubmitMessage.value='已为客户提交转账申请，等待后续审核。'
      staffForm.value.to_account='';staffForm.value.transfer_amount=''
    }
    await Promise.all([loadPending(),load()])
  }catch(e:any){staffSubmitMessage.value=e.message||'代办申请提交失败'}finally{staffSubmitting.value=false}
}
async function load(){loading.value=true;message.value='';try{const r=await operationsApi.auditLogs({operation_id:operationId.value||undefined,action:action.value||undefined,limit:pageSize,offset:(page.value-1)*pageSize});items.value=r.data.data.items;total.value=r.data.data.total}catch(e:any){message.value=e.message||'加载失败'}finally{loading.value=false}}
async function loadPending(){pendingLoading.value=true;try{const r=await operationsApi.pending({operation_type:(pendingType.value||undefined) as any,limit:50});pendingItems.value=r.data.data.items;pendingTotal.value=r.data.data.total}catch(e:any){operationMessage.value=e.response?.data?.detail?.message||e.message||'待审核队列加载失败'}finally{pendingLoading.value=false}}
async function reviewPending(item:any,approve:boolean){reviewing.value=item.operation_id;operationMessage.value='';try{const note=reviewNotes.value[item.operation_id]||'';if(item.operation==='redeem')await operationsApi.reviewRedeem(item.operation_id,approve,note);else await operationsApi.reviewTransfer(item.operation_id,approve,note);operationMessage.value=`${item.operation==='redeem'?'赎回':'转账'}已${approve?'通过':'拒绝'}审核`;await Promise.all([loadPending(),load()])}catch(e:any){operationMessage.value=e.response?.data?.detail?.message||e.message||'审核失败'}finally{reviewing.value=''}}
function search(){page.value=1;load()}
function clear(){operationId.value='';action.value='';page.value=1;load()}
function formatTime(v:string){return v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'-'}
onMounted(()=>{load();loadPending();loadCustomers()})
</script>
<template>
<section class="operations-workspace">
  <header class="module-header operations-heading"><div><p class="module-kicker">BUSINESS OPERATIONS</p><h2>业务操作与审计</h2><p>集中查看赎回、转账的提交、审核与结算轨迹。</p></div><div class="operations-summary"><span>审计记录</span><strong>{{total}}</strong></div></header>
  <section class="operations-staff-create"><div class="operations-staff-create-head"><div><span>STAFF ASSISTED OPERATION</span><h3>为客户代办赎回或转账</h3><p>先选择客户，再按客户已确认的授权范围提交申请。后端仍会校验授权、额度、持仓、余额、风险限制和幂等。</p></div></div><div class="operations-staff-form"><label>客户<select v-model="staffForm.customer_id"><option value="">请选择客户</option><option v-for="customer in customers" :key="customer.id||customer.user_id" :value="customer.id||customer.user_id">{{customer.display_name||customer.username}} · {{customer.username}}</option></select></label><label>代办类型<select v-model="staffForm.kind"><option value="redeem">赎回</option><option value="transfer">转账</option></select></label><template v-if="staffForm.kind==='redeem'"><label>产品 ID<input v-model="staffForm.product_id" placeholder="输入客户持仓产品 ID"></label><label>赎回数量<input v-model="staffForm.quantity" type="number" min="0.01" step="0.01"></label><label>赎回金额<input v-model="staffForm.redeem_amount" type="number" min="0.01" step="0.01"></label></template><template v-else><label>收款账户<input v-model="staffForm.to_account" placeholder="输入目标账户号"></label><label>转账金额<input v-model="staffForm.transfer_amount" type="number" min="0.01" step="0.01"></label></template><label class="operations-confirm"><input v-model="staffForm.customer_confirmed" type="checkbox"> 已核验客户授权/确认，且本次操作在授权范围内</label><button class="primary" :disabled="staffSubmitting" @click="submitStaffOperation">{{staffSubmitting?'提交中…':`为客户提交${staffForm.kind==='redeem'?'赎回':'转账'}申请`}}</button></div><p v-if="staffSubmitMessage" :class="staffSubmitMessage.includes('已为客户')?'success':'error'">{{staffSubmitMessage}}</p></section>
  <section class="operations-pending-panel"><div class="operations-pending-head"><div><span>REVIEW QUEUE</span><h3>待审核业务操作</h3><p>直接审核客户本人或工作人员代办的赎回、转账申请；风险限制会在通过时由服务端再次校验。</p></div><div><select v-model="pendingType" @change="loadPending"><option value="">全部待审核</option><option value="redeem">赎回</option><option value="transfer">转账</option></select><strong>{{pendingTotal}}</strong><small>笔待办</small></div></div><p v-if="operationMessage" class="portal-message">{{operationMessage}}</p><div v-if="pendingLoading" class="operations-empty"><strong>正在读取待审核队列…</strong></div><div v-else-if="!pendingItems.length" class="operations-empty"><strong>暂无待审核操作</strong><span>当前筛选下没有赎回或转账申请。</span></div><div v-else class="operations-pending-grid"><article v-for="item in pendingItems" :key="item.operation_id" class="pending-operation-card" :class="item.operation"><div class="pending-card-head"><div><span class="operation-mark" :class="item.operation">{{item.operation==='transfer'?'转':'赎'}}</span><div><strong>{{item.operation==='transfer'?'转账申请':'赎回申请'}}</strong><small>{{item.customer_name||item.customer_username}} · {{item.customer_username}}</small></div></div><span class="pill" :class="'risk-'+(item.risk_status||'normal')">{{item.risk_status==='restricted'?'交易限制':item.risk_status==='monitoring'?'风险关注':'正常'}}</span></div><dl><dt>申请时间</dt><dd>{{formatTime(item.created_at)}}</dd><template v-if="item.operation==='redeem'"><dt>产品 / 数量</dt><dd>{{item.product_name||item.product_id}} · {{item.quantity}}</dd></template><template v-else><dt>收款账户</dt><dd>{{item.to_account||'—'}}</dd></template><dt>金额</dt><dd>¥{{item.amount}}</dd><dt>发起方式</dt><dd>{{item.operator_name?`工作人员代办：${item.operator_name}`:'客户本人发起'}} · {{item.confirmation_mode||'—'}}</dd><dt v-if="item.risk_rule_code">风险提示</dt><dd v-if="item.risk_rule_code">{{item.risk_rule_code}}（{{item.risk_alert_level||'—'}}）</dd></dl><label class="pending-review-note"><span>审核备注</span><textarea v-model="reviewNotes[item.operation_id]" placeholder="填写通过或拒绝原因（可选）" /></label><div class="pending-card-actions"><button class="operations-secondary" :disabled="reviewing===item.operation_id" @click="reviewPending(item,false)">拒绝</button><button class="operations-primary" :disabled="reviewing===item.operation_id" @click="reviewPending(item,true)">{{reviewing===item.operation_id?'处理中…':'通过审核'}}</button></div></article></div></section>
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
