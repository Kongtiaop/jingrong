import { http } from './http'

export type KnowledgeUploadResult = {
  document: { id:string; file_name:string; category:string; permission_level:string; status:string; file_size:number; created_at:string; updated_at:string; error_message?:string|null }
  event_id: string
  pipeline_status: string
}

export const knowledgeApi = {
  upload: (file: File, category: string, permissionLevel = 'public') => {
    const form = new FormData()
    form.append('file', file)
    form.append('category', category)
    form.append('permission_level', permissionLevel)
    return http.post('/knowledge/documents/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  documents: (limit = 100, offset = 0) => http.get('/knowledge/documents', { params: { limit, offset } }),
}
