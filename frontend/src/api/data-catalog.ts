import { http } from './http'

export const dataCatalogApi = {
  tables: (domainCode = '', limit = 100, offset = 0) => http.get('/data-catalog/tables', { params: { domain_code: domainCode || undefined, limit, offset } }),
  updateTable: (id: string, payload: any) => http.put(`/data-catalog/tables/${id}`, payload),
  updateColumn: (id: string, payload: any) => http.put(`/data-catalog/columns/${id}`, payload),
  revisions: (resourceType: 'table' | 'column', id: string) => http.get(`/data-catalog/revisions/${resourceType}/${id}`),
  search: (query: string, topK = 5, domainCode = '') => http.post('/data-catalog/search', { query, top_k: topK, domain_code: domainCode || undefined }),
  enqueueEmbeddings: (limit = 100) => http.post('/data-catalog/embeddings', null, { params: { limit } }),
}
