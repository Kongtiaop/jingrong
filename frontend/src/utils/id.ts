// crypto.randomUUID 仅在 HTTPS 或 localhost（安全上下文）可用；
// 公网 HTTP 下不可用，回退到随机串，避免组件/请求抛错。
export const genId = (): string =>
  (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    ? crypto.randomUUID()
    : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
