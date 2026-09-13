// 后端 API 客户端封装。
// 开发模式下 Vite 把 /api 与 /health 代理到 FastAPI（见 vite.config.js）。

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
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
    const res = await fetch('/health')
    return parse(res)
  },

  async upload(file, level = 'intermediate') {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`/api/v1/process?level=${encodeURIComponent(level)}`, {
      method: 'POST',
      body: form,
    })
    return parse(res)
  },

  async getTask(taskId) {
    const res = await fetch(`/api/v1/tasks/${encodeURIComponent(taskId)}`)
    return parse(res)
  },

  reportUrl(taskId) {
    return `/api/v1/tasks/${encodeURIComponent(taskId)}/report`
  },

  async getReport(taskId) {
    const res = await fetch(this.reportUrl(taskId))
    return parse(res)
  },

  videoUrl(taskId) {
    return `/api/v1/tasks/${encodeURIComponent(taskId)}/video`
  },
}
