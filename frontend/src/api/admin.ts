import { http } from './http'
export const adminApi = {
  users: (q = '', role = '', status = 'active', limit = 20, offset = 0) => http.get('/admin/users', { params: { q, role, status, limit, offset } }),
  roles: () => http.get('/admin/roles'),
  updateRoles: (id: string, roles: string[]) => http.put(`/admin/users/${id}/roles`, { roles }),
  updateStatus: (id: string, status: 'active' | 'disabled') => http.put(`/admin/users/${id}/status`, null, { params: { status } }),
  deleteUser: (id: string) => http.delete(`/admin/users/${id}`),
  restoreUser: (id: string) => http.put(`/admin/users/${id}/restore`),
  updateRolePermissions: (id: string, permissions: string[]) => http.put(`/admin/roles/${id}/permissions`, { permissions }),
  permissions: () => http.get('/admin/permissions'),
}
