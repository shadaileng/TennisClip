import { defineStore } from 'pinia'
import { api } from '../lib/api'
import { readChunks, CHUNK_SIZE } from '../utils/md5'

// 后端任务状态（TaskStatus 枚举值）
export const TERMINAL_STATES = ['succeeded', 'failed', 'timeout']

export const useTaskStore = defineStore('task', {
  state: () => ({
    current: null,   // 当前任务 TaskResult
    health: null,    // /health 结果
    loading: false,
    error: null,
    // 两步上传状态机：idle | hashing | instant | uploading | processing
    uploadPhase: 'idle',
    uploadProgress: 0,      // 0~1（哈希 + 上传整体进度）
    uploadedChunks: 0,
    totalChunks: 0,
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
    // —— 策略调整（全局配置：阶段/分析模式/层级）——
    strategizing: false, // 策略调整弹窗开关
    strategyError: null,
    strategySaving: false,
    defaultLevel: 'intermediate', // 分析层级全局默认（highlight.level）
    analysisMode: 'frame',         // 分析模式全局默认（llm.analysis_mode）
    pipelineStages: null,         // 启用阶段有序列表（pipeline.stages，JSON 数组）
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
    // all 档位（所有高光回合）：仅剪辑、不生成技术分析报告
    allHighlights(state) {
      return !!state.current?.highlight?.all_highlights
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
          else if (it.key === 'highlight.level') this.defaultLevel = it.value || 'intermediate'
          else if (it.key === 'llm.analysis_mode') this.analysisMode = it.value || 'frame'
          else if (it.key === 'pipeline.stages') {
            try {
              this.pipelineStages = JSON.parse(it.value)
            } catch {
              this.pipelineStages = null
            }
          }
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

    // ---------- 策略调整（全局管线配置） ----------

    openStrategy() {
      this.strategizing = true
      this.strategyError = null
    },

    closeStrategy() {
      this.strategizing = false
    },

    async saveStrategy(payload) {
      this.strategySaving = true
      this.strategyError = null
      try {
        // 顺序无关：三项独立 KV，写后重载配置刷新本地状态
        await api.configSet('pipeline.stages', JSON.stringify(payload.stages))
        await api.configSet('llm.analysis_mode', payload.analysisMode)
        await api.configSet('highlight.level', payload.level)
        await this.loadConfig()
      } catch (e) {
        this.strategyError = `策略保存失败：${String(e.message || e)}`
      } finally {
        this.strategySaving = false
      }
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
      this.uploadPhase = 'hashing'
      this.uploadProgress = 0
      this.uploadedChunks = 0
      this.totalChunks = 0
      const originalName = file.name
      const MAX_COMPLETE_RETRY = 3
      try {
        // 1) 增量计算 MD5 + 读取分片（进度占整体 0~30%）
        const { md5, size, total, parts } = await readChunks(file, CHUNK_SIZE, (p) => {
          this.uploadProgress = p * 0.3
        })
        this.totalChunks = total
        this.uploadedChunks = 0

        // 2) MD5 预检：命中即跳过上传（秒传），直接复用已落盘视频重新分析
        const check = await api.checkUpload(md5, size)
        if (check.hit) {
          this.uploadPhase = 'instant'
          this.uploadProgress = 1
          const res = await api.processByMd5(md5, level)
          this._beginTask(res, originalName, level, md5, true)
          return
        }

        // 3) 未命中：先查已上传分片（断点续传），补齐缺失分片（单片失败自动重试）
        this.uploadPhase = 'uploading'
        let progress = await this._safeListChunks(md5, parts)
        let missing = new Set(
          progress.missing && progress.missing.length
            ? progress.missing
            : parts.map((p) => p.index),
        )
        await this._uploadMissing(parts, md5, size, total, missing, originalName)

        // 4) 合并校验 + 登记；整文件 MD5 不符(409) 时重传非法段后重试
        let completeOk = false
        for (let attempt = 0; attempt < MAX_COMPLETE_RETRY && !completeOk; attempt++) {
          try {
            await api.completeUpload(md5, size)
            completeOk = true
          } catch (e) {
            if (e.status !== 409) throw e
            // 服务端已清掉非法段：重新拉取缺失并重传（无缺失则整文件重传）
            const prog = await this._safeListChunks(md5, parts)
            const miss = prog.missing && prog.missing.length ? prog.missing : parts.map((p) => p.index)
            await this._uploadMissing(parts, md5, size, total, new Set(miss), originalName)
          }
        }
        if (!completeOk) throw new Error('视频合并校验失败，请重试上传')

        this.uploadProgress = 1

        // 5) 进入分析
        const res = await api.processByMd5(md5, level)
        this._beginTask(res, originalName, level, md5, false)
      } catch (e) {
        this.error = (e && e.message) || String(e)
        this.loading = false
        this.uploadPhase = 'idle'
      }
    },

    async _safeListChunks(md5, parts) {
      try {
        return await api.listChunks(md5)
      } catch {
        return { ok: [], missing: parts.map((p) => p.index) }
      }
    },

    async _uploadMissing(parts, md5, size, total, missing, originalName) {
      const byIndex = new Map(parts.map((p) => [p.index, p]))
      let uploaded = parts.length - missing.size
      this.uploadedChunks = uploaded
      for (const idx of missing) {
        const part = byIndex.get(idx)
        if (!part) continue
        await this._uploadChunkWithRetry(part, md5, size, total, originalName)
        uploaded += 1
        this.uploadedChunks = uploaded
        this.uploadProgress = 0.3 + 0.7 * (uploaded / parts.length)
      }
    },

    async _uploadChunkWithRetry(part, md5, size, total, originalName, maxRetry = 3) {
      let lastErr
      for (let attempt = 0; attempt < maxRetry; attempt++) {
        try {
          await api.uploadChunk({
            md5,
            index: part.index,
            total,
            sizeBytes: size,
            originalName,
            crc32: part.crc32,
            blob: part.blob,
          })
          return
        } catch (e) {
          // 单片校验(400 crc 不符)/网络抖动：重试该片（达上限后抛出）
          lastErr = e
        }
      }
      throw lastErr || new Error(`分片 ${part.index} 上传失败`)
    },

    _beginTask(res, fileName, level, md5, instant) {
      this.uploadPhase = 'processing'
      this.current = {
        task_id: res.task_id,
        status: res.status,
        stage: 'pending',
        source_video: fileName,
        level,
        md5,
        instant,
        highlight: null,
        report: null,
        highlight_video_path: null,
        report_path: null,
        error: null,
        elapsed_seconds: 0,
      }
      this._startPolling()
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
      this.uploadPhase = 'idle'
      this.uploadProgress = 0
      this.uploadedChunks = 0
      this.totalChunks = 0
    },
  },
})
