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

  // 模型服务商列表（id/name/base_url/api_key 掩码/models/default_model/enabled/sort_order/is_selected）
  async listProviders() {
    const res = await fetch(url('/api/v1/db/providers'))
    return parse(res)
  },

  // 新增模型服务商
  async createProvider(payload) {
    const res = await fetch(url('/api/v1/db/providers'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return parse(res)
  },

  // 编辑模型服务商（按 id 定位；api_key 留空表示保留原值）
  async updateProvider(id, payload) {
    const res = await fetch(url(`/api/v1/db/providers/${encodeURIComponent(id)}`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return parse(res)
  },

  // 删除模型服务商（按 id；被 ai.provider 引用时后端返回 409）
  async deleteProvider(id) {
    const res = await fetch(url(`/api/v1/db/providers/${encodeURIComponent(id)}`), {
      method: 'DELETE',
    })
    return parse(res)
  },

  // 校验模型可用性：list（GET /models）或逐模型 chat/completions 探测
  async checkProviderModels(payload) {
    const res = await fetch(url('/api/v1/db/providers/check-models'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return parse(res)
  },

  // 配置 KV 列表（ai.provider/ai.model/ai.api_key/ai.base_url）
  async loadConfig() {
    const res = await fetch(url('/api/v1/config'))
    return parse(res)
  },

  // 设置配置覆盖（切换服务商 ai.provider / 覆盖模型 ai.model）
  async configSet(key, value) {
    const res = await fetch(url(`/api/v1/config/${encodeURIComponent(key)}`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    })
    return parse(res)
  },

  // 删除配置覆盖（恢复默认值）
  async configDelete(key) {
    const res = await fetch(url(`/api/v1/config/${encodeURIComponent(key)}`), {
      method: 'DELETE',
    })
    return parse(res)
  },
}
