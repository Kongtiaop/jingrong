import { http } from './http'
export const riskApi = {
  alerts: (params: any = {}) => http.get('/risk-alerts', { params }),
  batches: (params: any = {}) => http.get('/risk-alerts/batches', { params }),
  schedulerStatus: () => http.get('/risk-alerts/scheduler-status'),
  batchAlerts: (id: string, params: any = {}) => http.get(`/risk-alerts/batches/${id}/alerts`, { params }),
  retryBatch: (id: string) => http.post(`/risk-alerts/batches/${id}/retry`),
  lateRescan: (id: string) => http.post(`/risk-alerts/batches/${id}/late-rescan`),
  alert: (id: string) => http.get(`/risk-alerts/${id}`),
  handle: (id: string, payload: any) => http.post(`/risk-alerts/${id}/handle`, payload),
  action: (id: string, payload: { action_code: string; note?: string }) => http.post(`/risk-alerts/${id}/action`, payload),
}
