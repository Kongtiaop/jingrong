<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { genId } from '../utils/id'
import { openCustomerAgentStream, openHumanRealtimeSubscription, type AgentStreamEvent, type HumanRealtimeSubscription } from '../services/customer-agent-stream'
import { customerServiceApi, customerServiceMessageApi, type CustomerRagIntent } from '../api/customer-rag'
import AgentTracePanel from '../components/AgentTracePanel.vue'

const progressStage=ref(''),progressDisplay=ref(''),reconnectState=ref<'idle'|'recovering'|'connected'>('idle'),lastMessageId=ref(''),cancelRequested=ref(false),amendment=ref(''),query=ref(''),intent=ref<CustomerRagIntent>('faq'),loading=ref(false),error=ref(''),result=ref<any>(null),messages=ref<any[]>([]),streamState=ref<'connecting'|'connected'|'fallback'|'closed'>('closed'),stream=ref<any>(null),humanSessionId=ref(sessionStorage.getItem('customer_service_session_id')||''),humanStatus=ref(''),humanLifecycle=ref(''),connectionState=ref<'connected'|'recovering'|'paused'>('connected'),conversationId=ref(sessionStorage.getItem('customer_service_conversation_id')||genId()),poller=ref<number|undefined>(undefined),humanRealtime=ref<HumanRealtimeSubscription|null>(null)
const historyTrace=ref<any>(null)
// 会话历史（AI 问答内容）：此前问答内容无处留存，刷新页面即丢，现从后端加载
const historyMessages=ref<any[]>([])
// 上一轮提问原文：用于把已完成的问答沉淀进历史列表
const lastQuery=ref('')
// 「建议转人工」卡片是否已被用户处理（选"继续对话"后不再打扰）
const handoffDismissed=ref(false)
sessionStorage.setItem('customer_service_conversation_id', conversationId.value)
let requestId=''

async function refreshHuman(){if(!humanSessionId.value)return;try{const incoming=(await customerServiceMessageApi.messages(humanSessionId.value,lastMessageId.value||undefined)).data.data||[];for(const message of incoming){if(!messages.value.some((item:any)=>item.id===message.id))messages.value.push({role:message.sender_type==='customer'?'user':'assistant',text:message.content,...message});lastMessageId.value=message.id}connectionState.value='connected'}catch{connectionState.value='recovering'}}
function startPolling(){if(poller.value||!humanSessionId.value)return;poller.value=window.setInterval(refreshHuman,2500)}
function stopPolling(){if(poller.value)window.clearInterval(poller.value);poller.value=undefined}
function startHumanRealtime(){if(!humanSessionId.value)return;if(humanRealtime.value)humanRealtime.value.close();humanRealtime.value=openHumanRealtimeSubscription(conversationId.value,{onEvent:event=>{handleHumanEvent(event)},onState:state=>{if(state==='connected'){reconnectState.value='connected';connectionState.value='connected'}else if(state==='recovering'||state==='connecting'){reconnectState.value='recovering';if(connectionState.value!=='paused')connectionState.value='recovering'}}})}
function stopHumanRealtime(){humanRealtime.value?.close();humanRealtime.value=null}
async function reconnectHuman(){if(!humanSessionId.value)return;reconnectState.value='recovering';connectionState.value='recovering';startHumanRealtime();await refreshHuman()}
function clearHumanSession(status:string){humanStatus.value=status;humanLifecycle.value=status;stopPolling();stopHumanRealtime();humanSessionId.value='';sessionStorage.removeItem('customer_service_session_id')}
async function validateHumanSession(){if(!humanSessionId.value)return;try{const session=(await customerServiceMessageApi.session(humanSessionId.value)).data.data;if(!['waiting_human','human_active','paused'].includes(session.status)){clearHumanSession(session.status);return}humanStatus.value=session.status;humanLifecycle.value=session.status;await refreshHuman();startPolling();startHumanRealtime()}catch{clearHumanSession('expired')}}
function goToAction(target:string){if(!target.startsWith('/customer-center?tab=operations'))return;window.location.assign(target)}
function cancelAgent(){if(!loading.value||!requestId)return;stream.value?.socket?.send(JSON.stringify({type:'agent.cancel',request_id:requestId}));cancelRequested.value=true}
function closeHuman(){if(!humanSessionId.value)return;customerServiceMessageApi.customerClose(humanSessionId.value).then(()=>clearHumanSession('resolved'))}
const options:Array<{value:CustomerRagIntent;label:string}>= [{value:'company_info',label:'公司信息'},{value:'company_business',label:'公司业务'},{value:'faq',label:'常见问题'},{value:'workflow',label:'服务流程'},{value:'policy',label:'金融政策'},{value:'risk_public',label:'风险与资料规则'}]

function handleHumanEvent(event:AgentStreamEvent){
  const sessionId=String(event.payload.session_id||humanSessionId.value)
  if(event.type==='human.session.claimed'){humanSessionId.value=sessionId;humanStatus.value=String(event.payload.status||'human_active');humanLifecycle.value='human_active';connectionState.value='connected';sessionStorage.setItem('customer_service_session_id',sessionId);refreshHuman();startPolling();startHumanRealtime();return true}
  if(event.type==='human.message.received'){const message=event.payload.message;if(message&&!messages.value.some((item:any)=>item.id===message.id))messages.value.push({role:message.sender_type==='customer'?'user':'assistant',text:message.content,...message});if(message?.id)lastMessageId.value=message.id;connectionState.value='connected';return true}
  if(event.type==='human.session.paused'){humanStatus.value='paused';humanLifecycle.value='paused';connectionState.value='paused';return true}
  if(event.type==='human.session.released'){humanStatus.value='waiting_human';humanLifecycle.value='waiting_human';connectionState.value='recovering';if(sessionId){humanSessionId.value=sessionId;sessionStorage.setItem('customer_service_session_id',sessionId);startPolling();startHumanRealtime()}return true}
  if(event.type==='human.session.resolved'||event.type==='human.session.closed'){clearHumanSession(String(event.payload.status||event.type.split('.').pop()||'resolved'));return true}
  return false
}
function handleStreamEvent(event: AgentStreamEvent, assistant: any){
  if(handleHumanEvent(event))return
  if(event.type==='session.ready'){streamState.value='connected';return}
  if(event.type==='agent.accepted'){progressStage.value='accepted';progressDisplay.value=String(event.payload.display||'已收到您的问题');return}
  if(event.type==='agent.progress'){progressStage.value=String(event.payload.stage||'processing');progressDisplay.value=String(event.payload.display||'正在处理');return}
  if(event.type==='agent.output.chunk'){assistant.answer=(assistant.answer||'')+String(event.payload.content||'');return}
  if(event.type==='agent.task.completed'){progressStage.value='task_completed';progressDisplay.value='已完成一项任务';assistant.results=assistant.results||[];const task=event.payload.task;const index=assistant.results.findIndex((item:any)=>item.task_id===task.task_id);if(index>=0)assistant.results[index]=task;else assistant.results.push(task);return}
  if(event.type==='agent.cancelled'){progressStage.value='cancelled';progressDisplay.value=String(event.payload.display||'本次请求已取消');assistant.overall_status='cancelled';assistant.answer=assistant.answer||String(event.payload.display||'本次请求已取消');result.value=assistant;return}
  if(event.type==='agent.final'){progressStage.value='completed';progressDisplay.value='回复已生成';const final=event.payload.result||{};Object.assign(assistant,final);result.value=assistant;if(final.customer_service_session_id){humanSessionId.value=final.customer_service_session_id;humanStatus.value=final.customer_service_session_status||'waiting_human';humanLifecycle.value=humanStatus.value;sessionStorage.setItem('customer_service_session_id',humanSessionId.value);startPolling();startHumanRealtime()}return}
  if(event.type==='agent.error')throw new Error(String(event.payload.message||'客服流式服务暂时不可用'))
}
async function runStream(content:string, amend=false){
  loading.value=true;progressStage.value='connecting';progressDisplay.value='正在连接实时客服';cancelRequested.value=false;error.value='';result.value=null;streamState.value='connecting'
  const assistant:any={conversation_id:conversationId.value,overall_status:'streaming',answer:'',results:[]};result.value=assistant;let fallback=false
  try{await new Promise<void>((resolve,reject)=>{requestId=genId();let finished=false;let opened=false;let connectTimer:number|undefined;const clearConnectTimer=()=>{if(connectTimer!==undefined){window.clearTimeout(connectTimer);connectTimer=undefined}};const fail=(reason?:any)=>{if(finished)return;finished=true;clearConnectTimer();reject(reason||new Error('stream unavailable'))};const onEvent=(event:AgentStreamEvent)=>{try{if(event.type==='session.ready')return;if(event.type==='agent.output.chunk'){streamState.value='connected';handleStreamEvent(event,assistant);return}handleStreamEvent(event,assistant);if(event.type==='agent.final'||event.type==='agent.cancelled'){finished=true;clearConnectTimer();resolve()}if(event.type==='agent.error')fail(new Error(String(event.payload.message||'客服流式服务暂时不可用')))}catch(e){fail(e)}};const activeStream=openCustomerAgentStream(conversationId.value,{onEvent,onError:()=>{reconnectHuman();fail()},onClose:()=>{reconnectHuman();if(!finished)fail(new Error(opened?'WebSocket closed before agent.final':'WebSocket closed before connection'))}});stream.value=activeStream;connectTimer=window.setTimeout(()=>fail(new Error('WebSocket connection timeout')),5000);(amend?activeStream.sendAmendment(requestId,content):activeStream.sendRequest(requestId,content)).then(()=>{opened=true;clearConnectTimer();streamState.value='connected'}).catch(fail)})}
  catch{fallback=true;streamState.value='fallback';stream.value?.close?.();stream.value=null;try{result.value=(await customerServiceApi.executePlan(content,conversationId.value)).data.data}catch(e:any){error.value=e.response?.data?.detail?.message||e.message||'客服服务暂时不可用';result.value=null}}
  finally{loading.value=false;requestId='';if(!fallback){stream.value?.close?.();stream.value=null}}
}
async function ask(){
  const text=query.value.trim();if(!text||loading.value)return
  // 把上一轮已完成的问答沉淀进历史列表（本地），使连续提问能累积显示；
  // 刷新后由 /messages 从后端加载，两边内容一致（后端是唯一事实来源）。
  if(result.value?.answer&&lastQuery.value){
    historyMessages.value.push({id:'local-u-'+Date.now(),role:'user',content:lastQuery.value})
    historyMessages.value.push({id:'local-a-'+Date.now(),role:'assistant',content:result.value.answer})
  }
  lastQuery.value=text
  handoffDismissed.value=false
  await runStream(text)
}
async function submitAmendment(){const text=amendment.value.trim();if(!text||loading.value)return;amendment.value='';await runStream(text,true)}
// 刷新页面后 result 已随内存消失，实时内容也一起没了；
// 这时从后端还原两样东西——问答内容（messages）与工具链（trace），
// 两者共用同一个 conversation_id，可并列展示以还原"这条回答是怎么来的"。
async function loadHistoryTrace(){
  try{
    const res=await customerServiceApi.trace(conversationId.value)
    const data=res.data?.data
    historyTrace.value=data?.rounds?.length?data:null
  }catch{historyTrace.value=null}
}
async function loadHistoryMessages(){
  try{
    const res=await customerServiceApi.messages(conversationId.value)
    historyMessages.value=res.data?.data||[]
  }catch{historyMessages.value=[]}
}
// 确认转人工：转人工改为「建议 → 用户确认 → 执行」，这里才真正建单
async function confirmHandoff(){
  try{
    const res=await customerServiceApi.confirmHandoff(conversationId.value)
    const data=res.data?.data||{}
    handoffDismissed.value=true
    if(data.customer_service_session_id){
      humanSessionId.value=data.customer_service_session_id
      humanStatus.value=data.customer_service_session_status||'waiting_human'
      humanLifecycle.value=humanStatus.value
      sessionStorage.setItem('customer_service_session_id',humanSessionId.value)
      startPolling();startHumanRealtime()
    }
  }catch{error.value='转人工失败，请稍后重试'}
}
function dismissHandoff(){handoffDismissed.value=true}
onMounted(async()=>{await validateHumanSession();await loadHistoryTrace();await loadHistoryMessages()})
</script>
<template><section class="workspace-page"><div class="row-actions"><button class="button secondary" @click="$router.push('/customer-center')">返回客户中心</button></div><div class="section-heading"><div><span class="eyebrow">CUSTOMER SERVICE</span><h2 data-testid="customer-rag-title">智能客服</h2><p>实时账户问题先查询业务数据，公开知识问题再检索客服资料。</p></div></div><div class="panel-card"><select v-model="intent" class="advisor-select"><option v-for="item in options" :key="item.value" :value="item.value">{{item.label}}</option></select><textarea data-testid="customer-rag-input" v-model="query" placeholder="请输入您的问题"/><div class="row-actions"><button v-if="loading" data-testid="customer-rag-cancel" class="button secondary" @click="cancelAgent">取消查询</button><button data-testid="customer-rag-send" class="button" :disabled="loading||!query.trim()" @click="ask">{{loading?'查询中…':'发送问题'}}</button></div></div><p v-if="reconnectState==='recovering'" data-testid="customer-agent-reconnecting" class="muted">实时连接恢复中，正在补齐消息…</p><p v-if="loading||progressDisplay" data-testid="customer-agent-progress" class="muted">{{progressDisplay}}</p><p v-if="cancelRequested" data-testid="customer-agent-cancel-requested" class="muted">取消请求已发送。</p><p v-if="humanLifecycle" data-testid="customer-human-lifecycle" class="muted">人工会话状态：{{humanLifecycle}}</p><p v-if="connectionState!=='connected'" class="muted">{{connectionState==='paused'?'人工会话已暂停，客服重新接入后会继续。':'人工会话连接恢复中…'}}</p><p v-if="error" class="error">{{error}}</p><section v-if="messages.length" class="panel-card human-message-panel" data-testid="customer-human-messages"><h3>人工会话消息</h3><article v-for="message in messages.filter((item:any)=>item.id)" :key="message.id" :class="'service-message '+(message.role|| (message.sender_type==='customer'?'user':'assistant'))"><p>{{message.text||message.content}}</p></article></section><div v-if="humanSessionId" data-testid="customer-human-state" class="service-human-actions"><button class="button secondary" @click="closeHuman">结束人工服务</button><span>当前人工会话消息每 2.5 秒刷新</span></div><p v-if="streamState==='fallback'" class="muted">实时通道不可用，已切换兼容请求。</p><section v-if="result" class="panel-card"><div v-if="result.handoff_suggested&&!handoffDismissed" class="panel-card" data-testid="handoff-suggest"><strong>要转人工客服吗？</strong><p class="muted">这个问题我可能答不好。您可以转人工，也可以继续补充信息。</p><div class="row-actions"><button data-testid="handoff-confirm" class="button" @click="confirmHandoff">转人工</button><button data-testid="handoff-decline" class="button secondary" @click="dismissHandoff">继续对话</button></div></div><span class="status-badge active">{{result.overall_status||result.evidence_status}}</span><span v-if="result.route" class="route-label">{{result.route}}</span><h3>客服回复</h3><p>{{result.answer||result.message}}</p><p v-if="result.clarification" class="muted">下一步：{{result.clarification}}</p><div v-if="result.overall_status==='needs_clarification'" class="panel-card"><label for="customer-rag-amend">补充信息</label><textarea id="customer-rag-amend" data-testid="customer-rag-amend-input" v-model="amendment" placeholder="请补充订单号、产品名称或其他必要信息"/><button data-testid="customer-rag-amend-send" class="button secondary" :disabled="loading||!amendment.trim()" @click="submitAmendment">提交补充</button></div><p v-if="result.next_action" class="muted">{{result.next_action}}</p><div v-if="result.results?.length" data-testid="customer-task-results" class="task-result-list"><h4>任务明细</h4><article v-for="task in result.results" :key="task.task_id" class="task-result-item"><div class="task-result-heading"><b>{{task.intent}}</b><span class="status-badge" :class="task.status">{{task.status}}</span></div><p v-if="task.answer">{{task.answer}}</p><p v-if="task.clarification" class="muted">{{task.clarification}}</p><button v-if="task.status==='needs_confirmation' && task.data?.action_target" data-testid="customer-operation-action" class="button secondary" @click="goToAction(task.data.action_target)">前往办理</button><small v-if="task.error" class="error">{{task.error}}</small><AgentTracePanel :trace="task.data?.model_tool_trace"/></article></div></section><section v-if="!result && historyMessages.length" class="panel-card" data-testid="customer-history-messages"><h4>历史对话</h4><p class="muted">本会话此前的问答，刷新后仍可回看。</p><article v-for="item in historyMessages" :key="item.id" :class="'service-message '+(item.role==='user'?'user':'assistant')"><p>{{item.content}}</p></article></section><section v-if="!result && historyTrace" class="panel-card" data-testid="customer-history-trace"><h4>历史推理记录</h4><p class="muted">本会话此前调用过的工具与耗时，刷新后仍可回看。</p><AgentTracePanel :trace="historyTrace"/></section></section></template>
