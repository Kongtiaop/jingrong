import axios from 'axios'

export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1', timeout: 120000, headers: { 'Content-Type': 'application/json' } })
let refreshing: Promise<string | null> | null = null
http.interceptors.request.use((config) => { config.headers['X-Trace-Id'] = crypto.randomUUID(); const token=sessionStorage.getItem('access_token'); if(token) config.headers.Authorization=`Bearer ${token}`; return config })
http.interceptors.response.use((response) => response, async (error) => {
  const original=error.config
  const isRefreshRequest = typeof original?.url === 'string' && original.url.endsWith('/auth/refresh')
  if(error.response?.status===401 && original && !isRefreshRequest && !original._retry && sessionStorage.getItem('refresh_token')) {
    original._retry=true
    if(!refreshing) refreshing=import('./auth').then(({authApi})=>authApi.refresh(sessionStorage.getItem('refresh_token')!).then(r=>{sessionStorage.setItem('access_token',r.data.data.access_token); sessionStorage.setItem('refresh_token',r.data.data.refresh_token); window.dispatchEvent(new CustomEvent('auth:changed')); return r.data.data.access_token}).catch(()=>null)).finally(()=>{refreshing=null})
    const token=await refreshing
    if(token){ original.headers.Authorization=`Bearer ${token}`; return http(original) }
    // A development reset or explicit logout can invalidate the stored
    // refresh session. Drop stale browser credentials and let App.vue route
    // protected pages back to login instead of retrying 401 requests forever.
    sessionStorage.removeItem('access_token')
    sessionStorage.removeItem('refresh_token')
    window.dispatchEvent(new CustomEvent('auth:changed'))
  }
  if(error.response?.status===401 && isRefreshRequest) {
    sessionStorage.removeItem('access_token')
    sessionStorage.removeItem('refresh_token')
    window.dispatchEvent(new CustomEvent('auth:changed'))
  }
  const detail = error.response?.data?.detail
  const message = typeof detail === 'string'
    ? detail
    : typeof detail?.message === 'string'
      ? detail.message
      : typeof error.response?.data?.message === 'string'
        ? error.response.data.message
        : error.message || '请求失败'
  const normalized = new Error(message) as Error & { code?: string; detail?: unknown; response?: unknown }
  normalized.code = typeof detail?.code === 'string' ? detail.code : undefined
  normalized.detail = detail
  normalized.response = error.response
  return Promise.reject(normalized)
})


