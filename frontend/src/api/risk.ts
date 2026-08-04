import { http } from './http'
export const riskApi = {
  alerts: (params: any = {}) => http.get('/risk-alerts', { params }),
  alert: (id: string) => http.get(`/risk-alerts/${id}`),
  handle: (id: string, payload: any) => http.post(`/risk-alerts/${id}/handle`, payload),
  action: (id: string, payload: { action_code: string; note?: string }) => http.post(`/risk-alerts/${id}/action`, payload),
}
