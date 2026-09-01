export type AgentStreamEvent = { type: string; conversation_id: string; request_id: string; sequence: number; payload: Record<string, any> }
type AgentStreamHandlers = { onEvent: (event: AgentStreamEvent) => void; onError?: (error: Event) => void; onClose?: () => void }
export type CustomerAgentStream = { socket: WebSocket; opened: Promise<void>; sendRequest: (requestId: string, content: string) => Promise<void>; sendAmendment: (requestId: string, content: string) => Promise<void>; close: () => void }

function websocketUrl(conversationId: string) {
  const token=sessionStorage.getItem('access_token')||''
  const apiBase=import.meta.env.VITE_API_BASE_URL||'http://127.0.0.1:8010/api/v1'
  const base=new URL(apiBase,window.location.origin)
  const protocol=base.protocol==='https:'?'wss:':'ws:'
  const path=base.pathname.replace(/\/$/,'')+`/customer-service/sessions/ws/${encodeURIComponent(conversationId)}`
  return `${protocol}//${base.host}${path}?access_token=${encodeURIComponent(token)}`
}

export function openCustomerAgentStream(conversationId: string, handlers: AgentStreamHandlers): CustomerAgentStream {
  const socket=new WebSocket(websocketUrl(conversationId))
  let resolveOpened!:()=>void
  let rejectOpened!: (error:Error)=>void
  const opened=new Promise<void>((resolve,reject)=>{resolveOpened=resolve;rejectOpened=reject})
  const send=async(event:Record<string,string>)=>{await opened;socket.send(JSON.stringify(event))}
  socket.onopen=()=>resolveOpened()
  socket.onmessage=message=>{try{handlers.onEvent(JSON.parse(message.data))}catch{} }
  socket.onerror=event=>{rejectOpened(new Error('WebSocket connection failed'));handlers.onError?.(event)}
  socket.onclose=()=>{rejectOpened(new Error('WebSocket closed before open'));handlers.onClose?.()}
  return {socket,opened,sendRequest:(requestId,content)=>send({type:'agent.request',request_id:requestId,content}),sendAmendment:(requestId,content)=>send({type:'agent.amend',request_id:requestId,content}),close:()=>socket.close()}
}

export type HumanRealtimeHandlers = { onEvent: (event: AgentStreamEvent) => void; onState?: (state: 'connecting'|'connected'|'recovering'|'closed') => void }
export type HumanRealtimeSubscription = { connect: () => void; close: () => void; state: () => 'connecting'|'connected'|'recovering'|'closed' }

export function openHumanRealtimeSubscription(conversationId: string, handlers: HumanRealtimeHandlers): HumanRealtimeSubscription {
  let socket: WebSocket | null=null
  let closed=false
  let state:'connecting'|'connected'|'recovering'|'closed'='closed'
  let retryTimer: number | undefined
  let retryAttempt=0

  const setState=(next:typeof state)=>{state=next;handlers.onState?.(next)}
  const schedule=()=>{
    if(closed||retryTimer!==undefined)return
    setState('recovering')
    const delay=Math.min(1000*Math.pow(2,retryAttempt++),10000)
    retryTimer=window.setTimeout(()=>{retryTimer=undefined;connect()},delay)
  }
  const connect=()=>{
    if(closed)return
    setState('connecting')
    socket=new WebSocket(websocketUrl(conversationId))
    socket.onopen=()=>{retryAttempt=0;setState('connected')}
    socket.onmessage=message=>{try{handlers.onEvent(JSON.parse(message.data))}catch{} }
    socket.onerror=()=>{if(socket?.readyState!==WebSocket.CLOSED)socket?.close()}
    socket.onclose=()=>{socket=null;if(!closed)schedule();else setState('closed')}
  }
  connect()
  return { connect, close:()=>{closed=true;if(retryTimer!==undefined)window.clearTimeout(retryTimer);retryTimer=undefined;socket?.close();socket=null;setState('closed')}, state:()=>state }
}
