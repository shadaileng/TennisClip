// 后端 API 客户端封装。
// 开发模式：Vite dev server 把 /api 与 /health 代理到 FastAPI（见 vite.config.js），
//   此时同域访问，无需设置 VITE_API_BASE_URL。
// 生产模式（前后端分开部署，跨域）：构建时通过 VITE_API_BASE_URL 注入后端基地址，
//   例如：VITE_API_BASE_URL=https://api.example.com pnpm build
//   未设置则为空串（同源部署，如 nginx 反代前后端同域）。
//
// import.meta.env.VITE_API_BASE_URL 由 Vite 在构建时静态替换为字符串常量。

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// 拼接完整 URL：带 VITE_API_BASE_URL 时为绝对地址，否则相对同域路径
function url(path) {
  return `${API_BASE_URL}${path}`
}

async function parse(res, allowEmpty = false) {
  const text = await res.text()
  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const msg =
      data && typeof data === 'object' && (data.detail || data.error)
        ? String(data.detail || data.error)
        : `HTTP ${res.status}`
    throw new ApiError(msg, res.status)
  }
  if (data === null && !allowEmpty) {
    throw new ApiError('empty response', res.status)
  }
  return data
}

export const api = {
  async health() {
    const res = await fetch(url('/health'))
    return parse(res)
  },

  async upload(file, level = 'intermediate') {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(url(`/api/v1/process?level=${encodeURIComponent(level)}`), {
      method: 'POST',
      body: form,
    })
    return parse(res)
  },

  async getTask(taskId) {
    const res = await fetch(url(`/api/v1/tasks/${encodeURIComponent(taskId)}`))
    return parse(res)
  },

  reportUrl(taskId) {
    return url(`/api/v1/tasks/${encodeURIComponent(taskId)}/report`)
  },

  async getReport(taskId) {
    const res = await fetch(this.reportUrl(taskId))
    return parse(res)
  },

  videoUrl(taskId) {
    return url(`/api/v1/tasks/${encodeURIComponent(taskId)}/video`)
  },
}
