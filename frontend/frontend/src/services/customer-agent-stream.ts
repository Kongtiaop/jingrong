export type AgentStreamEvent = {
  type: string
  conversation_id: string
  request_id: string
  sequence: number
  payload: Record<string, any>
}

export function openCustomerAgentStream(conversationId: string, handlers: {
  onEvent: (event: AgentStreamEvent) => void
  onError?: (error: Event) => void
  onClose?: () => void
}) {
  const token = sessionStorage.getItem('access_token')
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
  const httpBase = new URL(base, window.location.origin)
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const path = `${protocol}//${httpBase.host}${httpBase.pathname.replace(/\/$/, '')}/customer-service/sessions/ws/${encodeURIComponent(conversationId)}?access_token=${encodeURIComponent(token || '')}`
  const socket = new WebSocket(path)
  socket.onmessage = message => {
    try { handlers.onEvent(JSON.parse(message.data) as AgentStreamEvent) } catch { /* ignore malformed server frames */ }
  }
  socket.onerror = error => handlers.onError?.(error)
  socket.onclose = () => handlers.onClose?.()
  return {
    socket,
    sendRequest(requestId: string, content: string) {
      socket.send(JSON.stringify({ type: 'agent.request', request_id: requestId, content }))
    },
    close() { socket.close() },
  }
}




