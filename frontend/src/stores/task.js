import { defineStore } from 'pinia'
import { api } from '../lib/api'

// 后端任务状态（TaskStatus 枚举值）
export const TERMINAL_STATES = ['succeeded', 'failed', 'timeout']

export const useTaskStore = defineStore('task', {
  state: () => ({
    current: null,   // 当前任务 TaskResult
    health: null,    // /health 结果
    loading: false,
    error: null,
    _timer: null,
    _failCount: 0,   // 连续轮询失败（5xx）计数
    providers: [],   // 模型服务商列表（含 is_selected）
    aiProvider: null, // 当前 ai.provider 配置值（'custom' 或某服务商名）
    aiModel: '',     // 当前 ai.model 覆盖（空=跟随服务商默认）
    loadingProviders: false,
    loadingConfig: false,
    switchingProvider: false, // 切换中
    providerError: null,
    managing: false, // 模型管理弹窗开关
    manageError: null,
    manageSaving: false,
  }),

  getters: {
    isTerminal(state) {
      return !!(state.current && TERMINAL_STATES.includes(state.current.status))
    },
    highlightVideoUrl(state) {
      return state.current ? api.videoUrl(state.current.task_id) : ''
    },
    highlightSegments(state) {
      return state.current?.highlight?.segments || []
    },
    report(state) {
      return state.current?.report || null
    },
    // 当前生效服务商（优先 is_selected，其次 aiProvider 配置值）
    activeProvider(state) {
      const sel = state.providers.find((p) => p.is_selected)
      if (sel) return sel
      const byName = state.providers.find((p) => p.name === state.aiProvider)
      return byName || null
    },
    // 当前服务商默认模型
    currentDefaultModel(state) {
      const ap = state.providers.find((p) => p.is_selected) || state.providers.find((p) => p.name === state.aiProvider)
      return ap?.default_model || ''
    },
    // 当前生效模型（ai.model 覆盖优先，否则服务商默认）
    selectedModel(state) {
      const ap = state.providers.find((p) => p.is_selected) || state.providers.find((p) => p.name === state.aiProvider)
      return state.aiModel || ap?.default_model || ''
    },
    // 上传面板服务商下拉：自定义 + 各启用服务商
    providerOptions(state) {
      const enabled = state.providers.filter((p) => p.enabled).map((p) => ({ value: p.name, label: p.name }))
      return [{ value: 'custom', label: '自定义（独立配置）' }, ...enabled]
    },
  },

  actions: {
    async checkHealth() {
      try {
        this.health = await api.health()
      } catch (e) {
        this.health = { status: 'unreachable', error: String(e.message || e) }
      }
      return this.health
    },

    // 读取配置 KV（ai.provider / ai.model 等）
    async loadConfig() {
      this.loadingConfig = true
      try {
        const items = await api.loadConfig()
        for (const it of items) {
          if (it.key === 'ai.provider') this.aiProvider = it.value || 'custom'
          else if (it.key === 'ai.model') this.aiModel = it.value || ''
        }
      } catch (e) {
        this.providerError = `配置加载失败：${String(e.message || e)}`
      } finally {
        this.loadingConfig = false
      }
      return { aiProvider: this.aiProvider, aiModel: this.aiModel }
    },

    async loadProviders() {
      this.loadingProviders = true
      this.providerError = null
      try {
        this.providers = await api.listProviders()
        if (this.aiProvider == null) await this.loadConfig()
      } catch (e) {
        this.providerError = `模型列表加载失败：${String(e.message || e)}`
        this.providers = []
        this.aiProvider = null
        this.aiModel = ''
      } finally {
        this.loadingProviders = false
      }
      return this.providers
    },

    // 切换当前生效服务商（写入 ai.provider 配置）
    async selectProvider(name) {
      if (this.switchingProvider || !name) return
      this.switchingProvider = true
      this.providerError = null
      try {
        await api.configSet('ai.provider', name)
        this.aiProvider = name
        this.aiModel = '' // 切换服务商后由新服务商默认模型接管
        await this.loadProviders()
      } catch (e) {
        this.providerError = `服务商切换失败：${String(e.message || e)}`
      } finally {
        this.switchingProvider = false
      }
    },

    // 覆盖/恢复当前所选模型（空值 = 跟随服务商默认）
    async selectModel(model) {
      if (this.switchingProvider) return
      this.switchingProvider = true
      this.providerError = null
      try {
        if (model) await api.configSet('ai.model', model)
        else await api.configDelete('ai.model')
        this.aiModel = model || ''
      } catch (e) {
        this.providerError = `模型选择失败：${String(e.message || e)}`
      } finally {
        this.switchingProvider = false
      }
    },

    // ---------- 模型管理（CRUD，按 id） ----------

    openManage() {
      this.managing = true
      this.manageError = null
    },

    closeManage() {
      this.managing = false
    },

    async createProvider(payload) {
      this.manageSaving = true
      this.manageError = null
      try {
        await api.createProvider(payload)
        await this.loadProviders()
      } catch (e) {
        this.manageError = `新增失败：${String(e.message || e)}`
        throw e
      } finally {
        this.manageSaving = false
      }
    },

    async updateProvider(id, payload) {
      this.manageSaving = true
      this.manageError = null
      try {
        await api.updateProvider(id, payload)
        await this.loadProviders()
      } catch (e) {
        this.manageError = `保存失败：${String(e.message || e)}`
        throw e
      } finally {
        this.manageSaving = false
      }
    },

    async deleteProvider(id) {
      this.manageSaving = true
      this.manageError = null
      try {
        await api.deleteProvider(id)
        await this.loadProviders()
      } catch (e) {
        this.manageError = `删除失败：${String(e.message || e)}`
        throw e
      } finally {
        this.manageSaving = false
      }
    },

    async submit(file, level = 'intermediate') {
      this.error = null
      this.loading = true
      this._failCount = 0
      try {
        const res = await api.upload(file, level)
        this.current = {
          task_id: res.task_id,
          status: res.status,
          source_video: file.name,
          level,
          highlight: null,
          report: null,
          highlight_video_path: null,
          report_path: null,
          error: null,
          elapsed_seconds: 0,
        }
        this._startPolling()
      } catch (e) {
        this.error = e.message
        this.loading = false
      }
    },

    _startPolling() {
      this._stopPolling()
      this._timer = setInterval(() => {
        this.poll()
      }, 1500)
    },

    _stopPolling() {
      if (this._timer) {
        clearInterval(this._timer)
        this._timer = null
      }
    },

    async poll() {
      if (!this.current) return
      try {
        const data = await api.getTask(this.current.task_id)
        this._failCount = 0
        this.current = data
        if (TERMINAL_STATES.includes(data.status)) {
          this._stopPolling()
          this.loading = false
          if (data.status !== 'succeeded' && data.error) {
            this.error = data.error
          }
        }
      } catch (e) {
        if (e.status === 404) {
          this.error = '任务不存在或已过期'
          this._stopPolling()
          this.loading = false
          return
        }
        this._failCount += 1
        if (this._failCount >= 5) {
          this.error = '任务状态查询连续失败，请稍后重试或检查后端日志'
          this._stopPolling()
          this.loading = false
        }
      }
    },

    async fetchReport() {
      if (!this.current) return null
      try {
        const data = await api.getReport(this.current.task_id)
        this.current = { ...this.current, report: data }
        return data
      } catch {
        return this.current.report
      }
    },

    clear() {
      this._stopPolling()
      this.current = null
      this.error = null
      this.loading = false
    },
  },
})
