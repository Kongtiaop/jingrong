import { http } from './http'

export type BadCaseRow = {
  id: string
  query: string
  intent: string
  confidence: number | null
  evidence_status: string
  handoff: boolean
  clarifications: number
  reasons: string[]
  status: 'pending' | 'reviewed'
  created_at: string
  labeled_intent: string
  requires_clarification: boolean
  category: string
  note: string
  split: string
  labeled_by: string | null
  labeled_at: string | null
}

export type LabelPayload = {
  intent: string
  requires_clarification: boolean
  category: string
  note: string
  split: 'train' | 'val' | 'test'
  discard?: boolean
}

export const annotationApi = {
  summary: () => http.get('/admin/annotation/summary'),
  badCases: (params: Record<string, string | number>) => http.get('/admin/annotation/bad-cases', { params }),
  label: (id: string, body: LabelPayload) => http.post(`/admin/annotation/bad-cases/${id}/label`, body),
  skip: (id: string) => http.post(`/admin/annotation/bad-cases/${id}/skip`),
  exportLabeled: () => http.get('/admin/annotation/export-labeled'),
  exportPending: () => http.get('/admin/annotation/export-pending'),
}
