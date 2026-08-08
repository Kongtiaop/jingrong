import { http } from './http'

export type Notification = {
  id: string
  user_id: string
  notification_type: string
  title: string
  content: string
  resource_type: string
  resource_id: string
  is_read: boolean
  read_at: string | null
  created_at: string
}

export const notificationsApi = {
  list: (params: { unread_only?: boolean; limit?: number; offset?: number } = {}) => http.get<{ data: { items: Notification[]; total: number; unread_count: number } }>('/notifications', { params }),
  markRead: (id: string) => http.post<{ data: { updated: number; unread_count: number } }>(`/notifications/${id}/read`),
  markAllRead: () => http.post<{ data: { updated: number; unread_count: number } }>('/notifications/read-all'),
}
