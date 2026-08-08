import { http } from './http'

export type WorkOrder = {
  id: string
  ticket_no: string
  customer_id: string | null
  created_by: string
  assigned_to: string | null
  category: string
  priority: string
  status: string
  title: string
  description: string
  resolution_note: string
  created_at: string
  updated_at: string
  resolved_at: string | null
  closed_at: string | null
}

export type WorkOrderUpdate = { id: string; work_order_id: string; author_user_id: string; author_role: string; content: string; created_at: string }
export type WorkOrderSummary = { total: number; open: number; assigned: number; in_progress: number; pending_customer: number; resolved: number; urgent: number; high: number }
export type WorkOrderAttachment = { id: string; work_order_id: string; update_id: string | null; uploaded_by: string; original_name: string; content_type: string; size_bytes: number; created_at: string }

export const workordersApi = {
  assignees: () => http.get<{ data: Array<{ id: string; username: string; display_name: string; roles: string[] }> }>('/workorders/assignees'),
  summary: (scope: "all" | "assigned" = "assigned") => http.get<{ data: WorkOrderSummary }>("/workorders/summary", { params: { scope } }),
  list: (params: { status?: string; category?: string; priority?: string; scope?: "all" | "assigned"; limit?: number; offset?: number } = {}) => http.get<{ data: { items: WorkOrder[]; total: number } }>('/workorders', { params }),
  create: (payload: { customer_id?: string; category: string; priority: string; title: string; description: string }) => http.post<{ data: WorkOrder }>('/workorders', payload),
  attachments: (id: string) => http.get<{ data: WorkOrderAttachment[] }>(`/workorders/${id}/attachments`),
  uploadAttachment: (id: string, file: File) => { const form = new FormData(); form.append("file", file); return http.post<{ data: WorkOrderAttachment }>(`/workorders/${id}/attachments`, form, { headers: { "Content-Type": "multipart/form-data" } }) },
  downloadAttachment: (id: string, attachmentId: string) => http.get(`/workorders/${id}/attachments/${attachmentId}/download`, { responseType: "blob" }),
  updates: (id: string) => http.get<{ data: WorkOrderUpdate[] }>(`/workorders/${id}/updates`),
  supplement: (id: string, content: string) => http.post<{ data: WorkOrderUpdate }>(`/workorders/${id}/supplements`, { content }),
  assign: (id: string, assigned_to: string) => http.post<{ data: WorkOrder }>(`/workorders/${id}/assign`, { assigned_to }),
  handle: (id: string, status: 'in_progress' | 'pending_customer' | 'resolved', note: string) => http.post<{ data: WorkOrder }>(`/workorders/${id}/handle`, { status, note }),
  close: (id: string) => http.post<{ data: WorkOrder }>(`/workorders/${id}/close`),
}




