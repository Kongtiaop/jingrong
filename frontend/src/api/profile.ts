import { http } from './http'
export const profileApi = {
  summary: () => http.get('/profile/me'),
  updateProfile: (payload: any) => http.put('/profile/me', payload),
  update: (payload: any) => http.put('/profile/me', payload),
  assets: (payload: any) => http.post('/profile/me/assets', payload),
  assetHistory: (limit = 60) => http.get('/profile/me/assets/history', { params: { limit } }),
  products: () => http.get('/profile/products'),
  staffProducts: (q = '', status = '', limit = 20, offset = 0) => http.get('/profile/staff/products', { params: { q, status, limit, offset } }),
  createProduct: (payload: any) => http.post('/profile/staff/products', payload),
  updateProduct: (id: string, payload: any) => http.put(`/profile/staff/products/${id}`, payload),
  deleteProduct: (id: string) => http.delete(`/profile/staff/products/${id}`),
  restoreProduct: (id: string) => http.put(`/profile/staff/products/${id}/restore`),
  recommendations: () => http.get('/profile/me/recommendations'),
  customers: (q = '', tier = '', customer_type = '', limit = 20, offset = 0) => http.get('/profile/staff/customers', { params: { q, tier, customer_type, limit, offset } }),
  advisorCustomers: (q = '', tier = '', customer_type = '', limit = 20, offset = 0) => http.get('/profile/staff/advisor-customers', { params: { q, tier, customer_type, limit, offset } }),
  customer: (id: string) => http.get(`/profile/staff/customer/${id}`),
  managedCustomer: (id: string) => http.get(`/profile/staff/customers/${id}`),
  enterpriseVerification: () => http.get('/profile/me/enterprise-verification'),
  riskAssessment: () => http.get('/profile/me/risk-assessment'),
  submitRiskAssessment: (payload: any) => http.post('/profile/me/risk-assessment', payload),
  submitEnterpriseVerification: (payload: any) => http.post('/profile/me/enterprise-verification', payload),
  advisorAuthorizations: (limit=20, offset=0) => http.get('/profile/me/advisor-authorizations', { params: { limit, offset } }),
  createAdvisorAuthorization: (payload: any) => http.post('/profile/me/advisor-authorizations', payload),
  revokeAdvisorAuthorization: (id: string) => http.delete(`/profile/me/advisor-authorizations/${id}`),
  operationDrafts: (limit=20, offset=0) => http.get('/profile/me/operation-drafts', { params: { limit, offset } }),
  confirmOperationDraft: (id: string) => http.post(`/profile/me/operation-drafts/${id}/confirm`),
  rejectOperationDraft: (id: string) => http.post(`/profile/me/operation-drafts/${id}/reject`),
  submitOperationDraftOrder: (id: string) => http.post(`/profile/me/operation-drafts/${id}/submit-order`),
  staffAdvisors: (limit=20, offset=0) => http.get('/profile/staff/advisors', { params: { limit, offset } }),
  staffRecommendations: (customer_id: string) => http.post('/profile/staff/recommendations', { customer_id }),
  createStaffOperationDraft: (payload: any) => http.post('/profile/staff/operation-drafts', payload),
  advisorNaturalLanguageOperationDraft: (payload: { customer_id: string; query: string }) => http.post('/profile/staff/operation-drafts/natural-language', payload),
}







