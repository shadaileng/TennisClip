import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, ApiError } from '../lib/api'

export const useWorkflowStore = defineStore('workflow', () => {
  // 节点 Schema（前端渲染依据）
  const schema = ref([])
  // 工作流列表
  const workflows = ref([])
  // 当前编辑的草稿（图 JSON）
  const draft = ref(null)
  // 校验结果
  const validation = ref({ ok: true, errors: [] })
  // 面板开关
  const panelOpen = ref(false)
  // 加载状态
  const loading = ref(false)
  // 错误信息
  const error = ref('')

  /** 加载节点 Schema */
  async function loadSchema() {
    try {
      schema.value = await api.workflowSchema()
    } catch (e) {
      error.value = e.message
    }
  }

  /** 加载工作流列表 */
  async function loadWorkflows() {
    try {
      loading.value = true
      workflows.value = await api.listWorkflows()
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  /** 打开面板（自动加载数据） */
  async function open() {
    panelOpen.value = true
    await Promise.all([loadSchema(), loadWorkflows()])
  }

  /** 关闭面板 */
  function close() {
    panelOpen.value = false
    draft.value = null
    validation.value = { ok: true, errors: [] }
    error.value = ''
  }

  /** 新建草稿（从零开始或复制已有工作流） */
  function newDraft(copyFrom = null) {
    if (copyFrom) {
      const g = JSON.parse(JSON.stringify(copyFrom.graph))
      // Ensure nodes have positions
      g.nodes.forEach((n, i) => {
        if (n._x === undefined) { n._x = 80 + (i % 4) * 260; n._y = 80 + Math.floor(i / 4) * 160 }
      })
      draft.value = { id: copyFrom.id, name: copyFrom.name + '（副本）', graph: g }
    } else {
      draft.value = {
        name: '新工作流',
        graph: {
          version: 1,
          name: '新工作流',
          nodes: [
            { id: 'n1', type: 'input.video', params: {}, _x: 80, _y: 100 },
            { id: 'n2', type: 'output.artifact', params: {}, _x: 340, _y: 100 },
          ],
          edges: [
            { id: 'e1', from: ['n1', 'video'], to: ['n2', 'video'] },
          ],
        },
      }
    }
    validation.value = { ok: true, errors: [] }
  }

  /** 校验当前草稿（不落库） */
  async function validateDraft() {
    if (!draft.value) return
    try {
      validation.value = await api.validateWorkflow(draft.value.graph)
    } catch (e) {
      error.value = e.message
    }
  }

  /** 保存草稿（新建或更新） */
  async function saveDraft() {
    if (!draft.value) return
    try {
      loading.value = true
      error.value = ''
      // Strip canvas-only _x/_y before validation & save
      const cleanGraph = JSON.parse(JSON.stringify(draft.value.graph))
      cleanGraph.nodes.forEach((n) => { delete n._x; delete n._y })
      // 先校验
      const v = await api.validateWorkflow(cleanGraph)
      if (!v.ok) {
        validation.value = v
        return false
      }
      if (draft.value.id) {
        await api.updateWorkflow(draft.value.id, {
          name: draft.value.name,
          graph: cleanGraph,
        })
      } else {
        const result = await api.createWorkflow({
          name: draft.value.name,
          graph: cleanGraph,
        })
        draft.value.id = result.id
      }
      await loadWorkflows()
      return true
    } catch (e) {
      error.value = e.message
      return false
    } finally {
      loading.value = false
    }
  }

  /** 激活工作流 */
  async function activate(id) {
    try {
      await api.activateWorkflow(id)
      await loadWorkflows()
    } catch (e) {
      error.value = e.message
    }
  }

  /** 删除工作流 */
  async function remove(id) {
    try {
      await api.deleteWorkflow(id)
      await loadWorkflows()
    } catch (e) {
      error.value = e.message
    }
  }

  /** 导出 JSON */
  function exportJson() {
    if (!draft.value) return
    const blob = new Blob([JSON.stringify(draft.value.graph, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${draft.value.name || 'workflow'}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  /** 导入 JSON */
  function importJson(jsonStr) {
    try {
      const graph = JSON.parse(jsonStr)
      if (!graph.nodes || !graph.edges) {
        throw new Error('非法工作流 JSON：缺少 nodes 或 edges')
      }
      // Add positions if missing
      graph.nodes.forEach((n, i) => {
        if (n._x === undefined) { n._x = 80 + (i % 4) * 260; n._y = 80 + Math.floor(i / 4) * 160 }
      })
      draft.value = {
        name: graph.name || '导入的工作流',
        graph,
      }
      validation.value = { ok: true, errors: [] }
      return true
    } catch (e) {
      error.value = '导入失败：' + e.message
      return false
    }
  }

  return {
    schema,
    workflows,
    draft,
    validation,
    panelOpen,
    loading,
    error,
    loadSchema,
    loadWorkflows,
    open,
    close,
    newDraft,
    validateDraft,
    saveDraft,
    activate,
    remove,
    exportJson,
    importJson,
  }
})
