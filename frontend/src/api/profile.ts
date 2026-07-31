import { http } from './http'
export const profileApi = {
  summary: () => http.get('/profile/me'),
  update: (payload: any) => http.put('/profile/me', payload),
  assets: (payload: any) => http.post('/profile/me/assets', payload),
  products: () => http.get('/profile/products'),
  staffProducts: (q = '', status = '', limit = 20, offset = 0) => http.get('/profile/staff/products', { params: { q, status, limit, offset } }),
  createProduct: (payload: any) => http.post('/profile/staff/products', payload),
  updateProduct: (id: string, payload: any) => http.put(`/profile/staff/products/${id}`, payload),
  deleteProduct: (id: string) => http.delete(`/profile/staff/products/${id}`),
  restoreProduct: (id: string) => http.put(`/profile/staff/products/${id}/restore`),
  recommendations: () => http.get('/profile/me/recommendations'),
  customers: (q = '', limit = 20, offset = 0) => http.get('/profile/staff/customers', { params: { q, limit, offset } }),
  customer: (id: string) => http.get(`/profile/staff/customer/${id}`),
}
