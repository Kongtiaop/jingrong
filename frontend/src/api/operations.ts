import { http } from './http'

export type OperationAuditQuery = { operation_id?: string; action?: string; limit?: number; offset?: number }

export const operationsApi = {
  auditLogs: (params: OperationAuditQuery = {}) => http.get('/operations/audit-logs', { params }),
  staffOperation: (id: string) => http.get(`/operations/staff/${id}`),
  reviewRedeem: (id: string, approve: boolean, note = '') => http.post(`/operations/redeem/${id}/review`, { approve, note }),
  reviewTransfer: (id: string, approve: boolean, note = '') => http.post(`/operations/transfer/${id}/review`, { approve, note }),
}
