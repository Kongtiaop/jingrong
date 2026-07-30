import { http } from './http'
export interface KnowledgeHit { id:string; content:string; title_path:string; document_id:string; version_id:string; chunk_index:number; score:number; metadata:Record<string, unknown> }
export interface SearchResponse { query:string; hits:KnowledgeHit[]; retrieval_mode:string; embedding_dimension:number }
export const knowledgeApi = { search: (query:string, top_k=5) => http.post<{data:SearchResponse}>('/knowledge/search',{query,top_k}), defaultBase: () => http.get('/knowledge/default') }
