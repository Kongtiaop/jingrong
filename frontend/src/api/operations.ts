import { http } from './http'

export type OperationAuditQuery = { operation_id?: string; action?: string; limit?: number; offset?: number }

export const operationsApi = {
  redeem: (payload: { product_id: string; quantity: number; amount: number; idempotency_key?: string }) => http.post('/operations/redeem', payload),
  transfer: (payload: { to_account: string; amount: number; confirmed: boolean; idempotency_key?: string }) => http.post('/operations/transfer', payload),
  auditLogs: (params: OperationAuditQuery = {}) => http.get('/operations/audit-logs', { params }),
  mine: (params: { operation_type?: 'redeem'|'transfer'; status?: string; limit?: number; offset?: number } = {}) => http.get('/operations/me', { params }),
  pending: (params: { operation_type?: 'redeem'|'transfer'; limit?: number; offset?: number } = {}) => http.get('/operations/staff/pending', { params }),
  staffOperation: (id: string) => http.get(`/operations/staff/${id}`),
  staffRedeem: (payload: { customer_id: string; product_id: string; quantity: number; amount: number; customer_confirmed: boolean; idempotency_key?: string }) => http.post('/operations/staff/redeem', payload),
  staffTransfer: (payload: { customer_id: string; to_account: string; amount: number; customer_confirmed: boolean; idempotency_key?: string }) => http.post('/operations/staff/transfer', payload),
  reviewRedeem: (id: string, approve: boolean, note = '') => http.post(`/operations/redeem/${id}/review`, { approve, note }),
  reviewTransfer: (id: string, approve: boolean, note = '') => http.post(`/operations/transfer/${id}/review`, { approve, note }),
}
