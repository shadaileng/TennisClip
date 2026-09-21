<script setup>
import { ref, computed } from 'vue'
import { useWorkflowStore } from '../stores/workflow'

const store = useWorkflowStore()

// 节点分类中文标签
const categoryLabels = {
  input: '输入',
  preprocess: '预处理',
  detect: '检测',
  analyze: '分析',
  post: '后处理',
  edit: '剪辑',
  report: '报告',
  output: '输出',
}

// 获取 Schema 中某个类型的节点信息
function nodeSchema(type) {
  return store.schema.find((s) => s.type === type)
}

// 获取节点的中文标签
function nodeLabel(nodeId) {
  if (!store.draft) return nodeId
  const node = store.draft.graph.nodes.find((n) => n.id === nodeId)
  if (!node) return nodeId
  const schema = nodeSchema(node.type)
  return `${nodeId} · ${schema?.label || node.type}`
}

// ──────── 节点操作 ────────

function addNode(type) {
  if (!store.draft) return
  const idx = store.draft.graph.nodes.length + 1
  const id = `n${idx}`
  store.draft.graph.nodes.push({ id, type, params: {} })
  const schema = nodeSchema(type)
  if (schema) {
    const node = store.draft.graph.nodes[store.draft.graph.nodes.length - 1]
    for (const p of schema.params) {
      if (p.default !== undefined && p.default !== null) {
        node.params[p.key] = p.default
      }
    }
  }
}

function removeNode(id) {
  if (!store.draft) return
  store.draft.graph.nodes = store.draft.graph.nodes.filter((n) => n.id !== id)
  store.draft.graph.edges = store.draft.graph.edges.filter(
    (e) => e.from[0] !== id && e.to[0] !== id
  )
  if (selectedNodeId.value === id) selectedNodeId.value = null
}

function moveNodeUp(id) {
  if (!store.draft) return
  const nodes = store.draft.graph.nodes
  const idx = nodes.findIndex((n) => n.id === id)
  if (idx > 0) [nodes[idx - 1], nodes[idx]] = [nodes[idx], nodes[idx - 1]]
}

function moveNodeDown(id) {
  if (!store.draft) return
  const nodes = store.draft.graph.nodes
  const idx = nodes.findIndex((n) => n.id === id)
  if (idx < nodes.length - 1) [nodes[idx], nodes[idx + 1]] = [nodes[idx + 1], nodes[idx]]
}

function toggleNode(id) {
  if (!store.draft) return
  const node = store.draft.graph.nodes.find((n) => n.id === id)
  if (node) node.enabled = node.enabled === false ? true : false
}

function updateParam(nodeId, key, value) {
  if (!store.draft) return
  const node = store.draft.graph.nodes.find((n) => n.id === nodeId)
  if (node) node.params[key] = value
}

// ──────── 边操作 ────────

const edgeFromNode = ref('')
const edgeFromPort = ref('')
const edgeToNode = ref('')
const edgeToPort = ref('')

// 获取指定节点的可用输出端口
function getOutputPorts(nodeId) {
  if (!store.draft) return []
  const node = store.draft.graph.nodes.find((n) => n.id === nodeId)
  if (!node) return []
  const schema = nodeSchema(node.type)
  return schema?.outputs || []
}

// 获取指定节点的可用输入端口
function getInputPorts(nodeId) {
  if (!store.draft) return []
  const node = store.draft.graph.nodes.find((n) => n.id === nodeId)
  if (!node) return []
  const schema = nodeSchema(node.type)
  return schema?.inputs || []
}

function addEdge() {
  if (!store.draft || !edgeFromNode.value || !edgeFromPort.value || !edgeToNode.value || !edgeToPort.value) return
  // 检查是否已存在
  const exists = store.draft.graph.edges.some(
    (e) => e.from[0] === edgeFromNode.value && e.from[1] === edgeFromPort.value &&
           e.to[0] === edgeToNode.value && e.to[1] === edgeToPort.value
  )
  if (exists) return
  const idx = store.draft.graph.edges.length + 1
  store.draft.graph.edges.push({
    id: `e${idx}`,
    from: [edgeFromNode.value, edgeFromPort.value],
    to: [edgeToNode.value, edgeToPort.value],
  })
  // 重置
  edgeFromNode.value = ''
  edgeFromPort.value = ''
  edgeToNode.value = ''
  edgeToPort.value = ''
}

function removeEdge(idx) {
  if (!store.draft) return
  store.draft.graph.edges.splice(idx, 1)
}

// ──────── 选中的节点 ────────

const selectedNodeId = ref(null)
const selectedNode = computed(() => {
  if (!selectedNodeId.value || !store.draft) return null
  return store.draft.graph.nodes.find((n) => n.id === selectedNodeId.value)
})
const selectedNodeSchema = computed(() => {
  if (!selectedNode.value) return null
  return nodeSchema(selectedNode.value.type)
})

// ──────── 导入导出 ────────

const fileInput = ref(null)
function triggerImport() { fileInput.value?.click() }
function handleImport(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => store.importJson(reader.result)
  reader.readAsText(file)
  event.target.value = ''
}

// 当前编辑区域 tab
const activeTab = ref('nodes')
</script>

<template>
  <div v-if="store.panelOpen" class="fixed inset-0 z-50 flex flex-col bg-slate-950">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-slate-700 bg-slate-900 px-5 py-3">
      <h2 class="text-sm font-bold text-slate-100">🎾 工作流编排</h2>
      <div class="flex items-center gap-2">
        <button class="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500" @click="store.saveDraft()" :disabled="!store.draft || store.loading">💾 保存</button>
        <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.close()">✕ 关闭</button>
      </div>
    </div>

    <div class="flex flex-1 overflow-hidden">
      <!-- 左侧：预设 + 节点目录 -->
      <div class="flex w-60 flex-col border-r border-slate-700 overflow-y-auto">
        <!-- 预设列表 -->
        <div class="border-b border-slate-700 p-3">
          <div class="mb-2 flex items-center justify-between">
            <span class="text-xs font-medium text-slate-400">预设</span>
            <button class="text-xs text-emerald-400 hover:text-emerald-300" @click="store.newDraft()">+ 新建</button>
          </div>
          <div v-if="store.loading" class="text-xs text-slate-500">加载中…</div>
          <div v-else-if="!store.workflows.length" class="text-xs text-slate-500">暂无</div>
          <div v-for="wf in store.workflows" :key="wf.id" class="flex items-center justify-between rounded px-2 py-1 text-xs transition hover:bg-slate-800 cursor-pointer" :class="{ 'bg-emerald-900/30 text-emerald-300': wf.is_active }" @click="store.newDraft(wf)">
            <span class="truncate">{{ wf.name }}<span v-if="wf.is_builtin" class="ml-1 text-slate-500">🔒</span><span v-if="wf.is_active" class="ml-1 text-emerald-400">●</span></span>
            <div class="flex gap-1">
              <button class="text-slate-500 hover:text-emerald-400" title="激活" @click.stop="store.activate(wf.id)">✓</button>
              <button v-if="!wf.is_builtin" class="text-slate-500 hover:text-red-400" title="删除" @click.stop="store.remove(wf.id)">✕</button>
            </div>
          </div>
        </div>
        <!-- 节点目录 -->
        <div class="p-3">
          <div class="mb-2 text-xs font-medium text-slate-400">添加节点</div>
          <div v-for="(label, cat) in categoryLabels" :key="cat" class="mb-2">
            <div class="mb-1 text-[10px] uppercase text-slate-500">{{ label }}</div>
            <div v-for="s in store.schema.filter((n) => n.category === cat)" :key="s.type" class="cursor-pointer rounded px-2 py-0.5 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200" @click="addNode(s.type)">+ {{ s.label }}</div>
          </div>
        </div>
      </div>

      <!-- 右侧：编辑区 -->
      <div class="flex flex-1 flex-col overflow-hidden">
        <div v-if="!store.draft" class="flex flex-1 items-center justify-center text-sm text-slate-500">选择预设或点击「新建」开始编排</div>
        <template v-else>
          <!-- 名称 -->
          <div class="border-b border-slate-700 p-3">
            <input v-model="store.draft.name" class="w-full rounded bg-slate-800 px-2 py-1 text-sm text-slate-100 outline-none focus:ring-1 focus:ring-emerald-500" placeholder="工作流名称" />
          </div>
          <!-- 校验状态 -->
          <div v-if="store.validation.errors.length" class="border-b border-red-800 bg-red-900/20 px-3 py-2 text-xs text-red-300">
            <div v-for="(err, i) in store.validation.errors" :key="i">[{{ err.code }}] {{ err.message }}</div>
          </div>
          <div v-else-if="store.error" class="border-b border-red-800 bg-red-900/20 px-3 py-2 text-xs text-red-300">{{ store.error }}</div>

          <!-- Tab 切换 -->
          <div class="flex border-b border-slate-700">
            <button class="px-4 py-2 text-xs font-medium transition" :class="activeTab === 'nodes' ? 'text-emerald-400 border-b-2 border-emerald-400' : 'text-slate-400 hover:text-slate-200'" @click="activeTab = 'nodes'">节点 ({{ store.draft.graph.nodes.length }})</button>
            <button class="px-4 py-2 text-xs font-medium transition" :class="activeTab === 'edges' ? 'text-emerald-400 border-b-2 border-emerald-400' : 'text-slate-400 hover:text-slate-200'" @click="activeTab = 'edges'">连线 ({{ store.draft.graph.edges.length }})</button>
          </div>

          <!-- 节点 Tab -->
          <div v-if="activeTab === 'nodes'" class="flex-1 overflow-y-auto p-3">
            <div v-for="(node, idx) in store.draft.graph.nodes" :key="node.id" class="mb-2 rounded border px-3 py-2 transition cursor-pointer" :class="{ 'border-slate-700 bg-slate-800/50': selectedNodeId !== node.id, 'border-emerald-500 bg-emerald-900/20': selectedNodeId === node.id, 'opacity-50': node.enabled === false }" @click="selectedNodeId = node.id">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="text-[10px] text-slate-500">{{ node.id }}</span>
                  <span class="text-xs font-medium text-slate-200">{{ nodeSchema(node.type)?.label || node.type }}</span>
                  <span class="text-[10px] text-slate-500">{{ node.type }}</span>
                </div>
                <div class="flex items-center gap-1">
                  <button class="text-[10px] text-slate-500 hover:text-slate-300" title="上移" @click.stop="moveNodeUp(node.id)">↑</button>
                  <button class="text-[10px] text-slate-500 hover:text-slate-300" title="下移" @click.stop="moveNodeDown(node.id)">↓</button>
                  <button class="text-[10px] text-slate-500 hover:text-emerald-400" :title="node.enabled === false ? '启用' : '停用'" @click.stop="toggleNode(node.id)">{{ node.enabled === false ? '◻' : '◼' }}</button>
                  <button class="text-[10px] text-slate-500 hover:text-red-400" title="删除" @click.stop="removeNode(node.id)">✕</button>
                </div>
              </div>
              <!-- 参数 -->
              <div v-if="selectedNodeId === node.id && nodeSchema(node.type)?.params.length" class="mt-2 space-y-1 border-t border-slate-700 pt-2">
                <div v-for="param in nodeSchema(node.type).params" :key="param.key" class="flex items-center gap-2">
                  <label class="w-20 text-[10px] text-slate-400">{{ param.label }}</label>
                  <select v-if="param.type === 'select' && param.options" :value="node.params[param.key] ?? param.default" @change="updateParam(node.id, param.key, $event.target.value)" class="flex-1 rounded bg-slate-700 px-1 py-0.5 text-[11px] text-slate-200">
                    <option v-for="opt in param.options" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
                  <input v-else-if="param.type === 'bool'" type="checkbox" :checked="node.params[param.key] ?? param.default" @change="updateParam(node.id, param.key, $event.target.checked)" class="h-3 w-3" />
                  <input v-else-if="param.type === 'int' || param.type === 'float'" type="number" :value="node.params[param.key] ?? param.default" :min="param.min" :max="param.max" @input="updateParam(node.id, param.key, Number($event.target.value))" class="w-20 rounded bg-slate-700 px-1 py-0.5 text-[11px] text-slate-200" />
                  <input v-else type="text" :value="node.params[param.key] ?? param.default" @input="updateParam(node.id, param.key, $event.target.value)" class="flex-1 rounded bg-slate-700 px-1 py-0.5 text-[11px] text-slate-200" />
                </div>
              </div>
            </div>
          </div>

          <!-- 连线 Tab -->
          <div v-if="activeTab === 'edges'" class="flex-1 overflow-y-auto p-3">
            <!-- 添加连线表单 -->
            <div class="mb-3 rounded border border-slate-700 bg-slate-800/50 p-3">
              <div class="mb-2 text-xs font-medium text-slate-400">添加连线</div>
              <div class="flex flex-wrap items-end gap-2">
                <div>
                  <label class="mb-0.5 block text-[10px] text-slate-500">源节点</label>
                  <select v-model="edgeFromNode" class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200">
                    <option value="">选择…</option>
                    <option v-for="n in store.draft.graph.nodes" :key="n.id" :value="n.id">{{ nodeLabel(n.id) }}</option>
                  </select>
                </div>
                <div>
                  <label class="mb-0.5 block text-[10px] text-slate-500">输出端口</label>
                  <select v-model="edgeFromPort" class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200">
                    <option value="">选择…</option>
                    <option v-for="p in getOutputPorts(edgeFromNode)" :key="p.name" :value="p.name">{{ p.name }} ({{ p.type }})</option>
                  </select>
                </div>
                <span class="text-slate-500 text-xs pb-1">→</span>
                <div>
                  <label class="mb-0.5 block text-[10px] text-slate-500">目标节点</label>
                  <select v-model="edgeToNode" class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200">
                    <option value="">选择…</option>
                    <option v-for="n in store.draft.graph.nodes" :key="n.id" :value="n.id">{{ nodeLabel(n.id) }}</option>
                  </select>
                </div>
                <div>
                  <label class="mb-0.5 block text-[10px] text-slate-500">输入端口</label>
                  <select v-model="edgeToPort" class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200">
                    <option value="">选择…</option>
                    <option v-for="p in getInputPorts(edgeToNode)" :key="p.name" :value="p.name">{{ p.name }} ({{ p.type }})</option>
                  </select>
                </div>
                <button class="rounded bg-emerald-600 px-3 py-1 text-[11px] text-white hover:bg-emerald-500" @click="addEdge()">添加</button>
              </div>
            </div>
            <!-- 现有连线列表 -->
            <div v-if="!store.draft.graph.edges.length" class="text-xs text-slate-500">暂无连线</div>
            <div v-for="(edge, idx) in store.draft.graph.edges" :key="edge.id" class="mb-1 flex items-center justify-between rounded bg-slate-800/50 px-2 py-1">
              <span class="text-[11px] text-slate-300">
                <span class="text-blue-400">{{ edge.from[0] }}</span>.<span class="text-blue-300">{{ edge.from[1] }}</span>
                <span class="text-slate-500 mx-1">→</span>
                <span class="text-green-400">{{ edge.to[0] }}</span>.<span class="text-green-300">{{ edge.to[1] }}</span>
              </span>
              <button class="text-[10px] text-slate-500 hover:text-red-400" @click="removeEdge(idx)">✕</button>
            </div>
          </div>

          <!-- 底部操作栏 -->
          <div class="flex items-center gap-2 border-t border-slate-700 px-3 py-2">
            <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.validateDraft()">校验</button>
            <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.exportJson()">导出 JSON</button>
            <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="triggerImport()">导入 JSON</button>
            <input ref="fileInput" type="file" accept=".json" class="hidden" @change="handleImport" />
          </div>
        </template>
      </div>
    </div>
  </div>
  <!-- 遮罩 -->
  <div v-if="store.panelOpen" class="fixed inset-0 z-40 bg-black/50" @click="store.close()" />
</template>
