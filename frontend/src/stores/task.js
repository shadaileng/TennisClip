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

    async submit(file, level = 'intermediate') {
      this.error = null
      this.loading = true
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
        this.current = data
        if (TERMINAL_STATES.includes(data.status)) {
          this._stopPolling()
          this.loading = false
        }
      } catch (e) {
        // 轮询失败不断开，等待下次
        if (e.status === 404) {
          this.error = '任务不存在或已过期'
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
