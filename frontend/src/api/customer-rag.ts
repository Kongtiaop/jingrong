import { http } from './http'

export type CustomerRagIntent = 'company_info'|'company_business'|'faq'|'workflow'|'policy'|'risk_public'|'product_recommendation'
export type CustomerQueryRouteResponse = {
  source_type: 'business_fact'|'rag_explanation'|'handoff'
  route: string
  answer: string
  data: Record<string, any>
  citations: any[]
  evidence_status: string
  next_action: string
}

export const customerRagApi = {
  route: (query: string) => http.post<{data: CustomerQueryRouteResponse}>('/customer-rag/route', { query }),
  answer: (payload:{query:string;intent:CustomerRagIntent;top_k?:number}) => http.post('/customer-rag/answer', payload),
}


export const customerServiceApi = {
  executePlan: (query: string, conversationId: string) => http.post('/customer-service/sessions/execute-plan', { note: query }, { headers: { 'X-Conversation-Id': conversationId } }),
}


export const customerServiceMessageApi = {
  messages: (sessionId: string) => http.get(`/customer-service/sessions/${sessionId}/messages`),
  send: (sessionId: string, content: string) => http.post(`/customer-service/sessions/${sessionId}/messages`, { content }),
  claim: (sessionId: string) => http.post(`/customer-service/sessions/${sessionId}/claim`),
  pause: (sessionId: string, note = '') => http.post(`/customer-service/sessions/${sessionId}/pause`, { note }),
  release: (sessionId: string, note = '') => http.post(`/customer-service/sessions/${sessionId}/release`, { note }),
  resolve: (sessionId: string, note: string) => http.post(`/customer-service/sessions/${sessionId}/resolve`, { note }),
  heartbeat: (sessionId: string) => http.post(`/customer-service/sessions/${sessionId}/heartbeat`),
  customerClose: (sessionId: string) => http.post(`/customer-service/sessions/${sessionId}/customer-close`, { note: 'customer_requested_close' }),
  sessions: (status = 'waiting_human') => http.get('/customer-service/sessions', { params: { status } }),
}
