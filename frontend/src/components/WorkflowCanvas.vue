<script setup>
import { ref, reactive, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useWorkflowStore } from '../stores/workflow'

const store = useWorkflowStore()

// ──────── Canvas state ────────
const canvasRef = ref(null)
const pan = reactive({ x: 0, y: 0 })
const zoom = ref(1)
const isPanning = ref(false)
const panStart = reactive({ x: 0, y: 0 })

// ──────── Selection & Node dragging ────────
const selectedNodeId = ref(null)
const dragNode = ref(null)
const dragOffset = reactive({ x: 0, y: 0 })

// ──────── Connection drawing ────────
const connecting = reactive({
  active: false,
  fromNodeId: '',
  fromPort: '',
  fromPortType: '',
  mouseX: 0,
  mouseY: 0,
})

// ──────── Port position cache (measured from DOM) ────────
// Key: "nodeId:portName:in|out" → { x, y } in canvas coords
const portPosCache = reactive({})
let portElements = {}

function registerPort(key, el) {
  if (el) portElements[key] = el
  else delete portElements[key]
}

function measurePorts() {
  const canvasRect = canvasRef.value?.getBoundingClientRect()
  if (!canvasRect) return
  for (const [key, el] of Object.entries(portElements)) {
    const r = el.getBoundingClientRect()
    portPosCache[key] = {
      x: (r.left + r.width / 2 - canvasRect.left - pan.x) / zoom.value,
      y: (r.top + r.height / 2 - canvasRect.top - pan.y) / zoom.value,
    }
  }
}

function getPortPos(nodeId, portName, isOutput) {
  const key = `${nodeId}:${portName}:${isOutput ? 'out' : 'in'}`
  if (portPosCache[key]) return portPosCache[key]
  // Fallback: estimate from node position
  const node = store.draft?.graph.nodes.find((n) => n.id === nodeId)
  if (!node) return { x: 0, y: 0 }
  const NODE_W = 220
  return {
    x: isOutput ? node._x + NODE_W : node._x,
    y: node._y + 50,
  }
}

// ──────── Helpers ────────
function nodeSchema(type) {
  return store.schema.find((s) => s.type === type)
}

// ──────── Bezier curve ────────
function bezierPath(x1, y1, x2, y2) {
  const dx = Math.max(Math.abs(x2 - x1) * 0.5, 50)
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
}

// ──────── Mouse → canvas coords ────────
function toCanvas(clientX, clientY) {
  const rect = canvasRef.value?.getBoundingClientRect()
  if (!rect) return { x: 0, y: 0 }
  return {
    x: (clientX - rect.left - pan.x) / zoom.value,
    y: (clientY - rect.top - pan.y) / zoom.value,
  }
}

// ──────── Canvas mouse events ────────
function onCanvasMouseDown(e) {
  if (e.button === 1 || (e.button === 0 && e.target === canvasRef.value)) {
    isPanning.value = true
    panStart.x = e.clientX - pan.x
    panStart.y = e.clientY - pan.y
    selectedNodeId.value = null
    e.preventDefault()
  }
}

function onDocMouseMove(e) {
  if (isPanning.value) {
    pan.x = e.clientX - panStart.x
    pan.y = e.clientY - panStart.y
    nextTick(measurePorts)
    return
  }
  if (dragNode.value) {
    const pos = toCanvas(e.clientX, e.clientY)
    dragNode.value._x = pos.x - dragOffset.x
    dragNode.value._y = pos.y - dragOffset.y
    nextTick(measurePorts)
    return
  }
  if (connecting.active) {
    const pos = toCanvas(e.clientX, e.clientY)
    connecting.mouseX = pos.x
    connecting.mouseY = pos.y
  }
}

function onDocMouseUp() {
  isPanning.value = false
  dragNode.value = null
  if (connecting.active) connecting.active = false
}

function onWheel(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? 0.9 : 1.1
  const newZoom = Math.min(3, Math.max(0.2, zoom.value * delta))
  const rect = canvasRef.value.getBoundingClientRect()
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  pan.x = cx - (cx - pan.x) * (newZoom / zoom.value)
  pan.y = cy - (cy - pan.y) * (newZoom / zoom.value)
  zoom.value = newZoom
  nextTick(measurePorts)
}

// ──────── Node drag ────────
function onNodeMouseDown(e, node) {
  e.stopPropagation()
  selectedNodeId.value = node.id
  dragNode.value = node
  const pos = toCanvas(e.clientX, e.clientY)
  dragOffset.x = pos.x - node._x
  dragOffset.y = pos.y - node._y
}

// ──────── Port events ────────
function onOutputPortMouseDown(e, nodeId, portName, portType) {
  e.stopPropagation()
  e.preventDefault()
  const fromPos = getPortPos(nodeId, portName, true)
  connecting.active = true
  connecting.fromNodeId = nodeId
  connecting.fromPort = portName
  connecting.fromPortType = portType
  connecting.mouseX = fromPos.x
  connecting.mouseY = fromPos.y
}

function onInputPortMouseUp(e, nodeId, portName, portType) {
  e.stopPropagation()
  e.preventDefault()
  if (!connecting.active) return
  if (connecting.fromPortType !== portType) return
  if (connecting.fromNodeId === nodeId) return
  const exists = store.draft.graph.edges.some(
    (ed) => ed.from[0] === connecting.fromNodeId && ed.from[1] === connecting.fromPort &&
            ed.to[0] === nodeId && ed.to[1] === portName
  )
  if (exists) { connecting.active = false; return }
  const idx = store.draft.graph.edges.length + 1
  store.draft.graph.edges.push({
    id: `e${idx}`,
    from: [connecting.fromNodeId, connecting.fromPort],
    to: [nodeId, portName],
  })
  connecting.active = false
}

function removeEdge(idx) { store.draft.graph.edges.splice(idx, 1) }

async function activateCurrent() {
  if (!store.draft?.id) return
  await store.activate(store.draft.id)
}

function removeNode(id) {
  store.draft.graph.nodes = store.draft.graph.nodes.filter((n) => n.id !== id)
  store.draft.graph.edges = store.draft.graph.edges.filter((e) => e.from[0] !== id && e.to[0] !== id)
  if (selectedNodeId.value === id) selectedNodeId.value = null
}

// ──────── Add node at canvas center ────────
function addNodeAtCenter(type) {
  if (!store.draft) return
  const rect = canvasRef.value?.getBoundingClientRect()
  const cx = rect ? (rect.width / 2 - pan.x) / zoom.value : 200
  const cy = rect ? (rect.height / 2 - pan.y) / zoom.value : 200
  const idx = store.draft.graph.nodes.length + 1
  const node = {
    id: `n${idx}`, type, params: {},
    _x: cx - 110 + (Math.random() * 60 - 30),
    _y: cy - 40 + (Math.random() * 60 - 30),
  }
  const schema = nodeSchema(type)
  if (schema) {
    for (const p of schema.params) {
      if (p.default !== undefined && p.default !== null) node.params[p.key] = p.default
    }
  }
  store.draft.graph.nodes.push(node)
  nextTick(measurePorts)
}

// ──────── Auto-layout ────────
function autoLayout() {
  if (!store.draft) return
  const nodes = store.draft.graph.nodes
  const edges = store.draft.graph.edges
  const inDeg = {}
  const adj = {}
  nodes.forEach((n) => { inDeg[n.id] = 0; adj[n.id] = [] })
  edges.forEach((e) => {
    if (inDeg[e.to[0]] !== undefined && adj[e.from[0]]) {
      inDeg[e.to[0]]++
      adj[e.from[0]].push(e.to[0])
    }
  })
  const layers = []
  let queue = nodes.filter((n) => inDeg[n.id] === 0).map((n) => n.id)
  const visited = new Set()
  while (queue.length) {
    layers.push([...queue])
    queue.forEach((id) => visited.add(id))
    const next = []
    queue.forEach((id) => {
      adj[id].forEach((child) => {
        inDeg[child]--
        if (inDeg[child] === 0 && !visited.has(child)) next.push(child)
      })
    })
    queue = next
  }
  nodes.forEach((n) => { if (!visited.has(n.id)) layers.push([n.id]) })
  const GAP_X = 260, GAP_Y = 160, START_X = 80, START_Y = 80
  layers.forEach((layer, row) => {
    layer.forEach((nid, col) => {
      const node = nodes.find((n) => n.id === nid)
      if (node) { node._x = START_X + col * GAP_X; node._y = START_Y + row * GAP_Y }
    })
  })
  nextTick(measurePorts)
}

// ──────── Lifecycle ────────
onMounted(() => {
  document.addEventListener('mousemove', onDocMouseMove)
  document.addEventListener('mouseup', onDocMouseUp)
  // Ensure nodes have positions
  if (store.draft) {
    store.draft.graph.nodes.forEach((n, i) => {
      if (n._x === undefined) { n._x = 80 + (i % 4) * 260; n._y = 80 + Math.floor(i / 4) * 160 }
    })
  }
  setTimeout(() => {
    const rect = canvasRef.value?.getBoundingClientRect()
    if (rect) { pan.x = rect.width * 0.1; pan.y = rect.height * 0.05 }
    nextTick(measurePorts)
  }, 100)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onDocMouseMove)
  document.removeEventListener('mouseup', onDocMouseUp)
})

// ──────── Node colors ────────
const categoryColors = {
  input:     { bg: 'bg-blue-900/80',   border: 'border-blue-500',   header: 'bg-blue-700' },
  preprocess:{ bg: 'bg-purple-900/80', border: 'border-purple-500', header: 'bg-purple-700' },
  detect:    { bg: 'bg-amber-900/80',  border: 'border-amber-500',  header: 'bg-amber-700' },
  analyze:   { bg: 'bg-red-900/80',    border: 'border-red-500',    header: 'bg-red-700' },
  post:      { bg: 'bg-cyan-900/80',   border: 'border-cyan-500',   header: 'bg-cyan-700' },
  edit:      { bg: 'bg-green-900/80',  border: 'border-green-500',  header: 'bg-green-700' },
  report:    { bg: 'bg-orange-900/80', border: 'border-orange-500', header: 'bg-orange-700' },
  output:    { bg: 'bg-pink-900/80',   border: 'border-pink-500',   header: 'bg-pink-700' },
}
function getNodeColors(node) {
  const schema = nodeSchema(node.type)
  return categoryColors[schema?.category] || categoryColors.input
}

const categoryLabels = { input:'输入', preprocess:'预处理', detect:'检测', analyze:'分析', post:'后处理', edit:'剪辑', report:'报告', output:'输出' }
const paletteOpen = ref(false)
</script>

<template>
  <div v-if="store.panelOpen" class="fixed inset-0 z-50 flex flex-col bg-slate-950">
    <!-- Top bar -->
    <div class="flex items-center justify-between border-b border-slate-700 bg-slate-900 px-4 py-2 z-10">
      <div class="flex items-center gap-3">
        <h2 class="text-sm font-bold text-slate-100">🎾 工作流画布</h2>
        <input v-if="store.draft" v-model="store.draft.name" class="w-48 rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 outline-none focus:ring-1 focus:ring-emerald-500" />
      </div>
      <div class="flex items-center gap-2">
        <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="autoLayout">📐 排版</button>
        <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.validateDraft()">✓ 校验</button>
        <button class="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500" @click="store.saveDraft()" :disabled="store.loading">💾 保存</button>
        <button v-if="store.draft?.id" class="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-500" @click="activateCurrent()" :disabled="store.loading">⚡ 激活</button>
        <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.exportJson()">📤 导出</button>
        <button class="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-600" @click="store.close()">✕ 关闭</button>
      </div>
    </div>
    <!-- Errors -->
    <div v-if="store.validation.errors.length" class="border-b border-red-800 bg-red-900/30 px-4 py-1 text-xs text-red-300 z-10">
      <span v-for="(err, i) in store.validation.errors" :key="i" class="mr-3">[{{ err.code }}] {{ err.message }}</span>
    </div>
    <div v-else-if="store.error" class="border-b border-red-800 bg-red-900/30 px-4 py-1 text-xs text-red-300 z-10">{{ store.error }}</div>
    <div v-else-if="store.success" class="border-b border-emerald-800 bg-emerald-900/30 px-4 py-1 text-xs text-emerald-300 z-10">{{ store.success }}</div>

    <!-- Main area -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Left palette -->
      <div class="w-48 border-r border-slate-700 bg-slate-900 overflow-y-auto z-10 shrink-0">
        <div class="p-2">
          <button class="mb-2 w-full rounded bg-emerald-600/20 px-2 py-1.5 text-xs text-emerald-400 hover:bg-emerald-600/30" @click="paletteOpen = !paletteOpen">+ 添加节点</button>
          <div v-if="paletteOpen">
            <div v-for="(label, cat) in categoryLabels" :key="cat" class="mb-1">
              <div class="mb-0.5 text-[10px] uppercase text-slate-500 px-1">{{ label }}</div>
              <div v-for="s in store.schema.filter((n) => n.category === cat)" :key="s.type"
                class="cursor-pointer rounded px-2 py-1 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                @click="addNodeAtCenter(s.type); paletteOpen = false">{{ s.label }}</div>
            </div>
          </div>
        </div>
        <div class="border-t border-slate-700 p-2">
          <div class="mb-1 text-[10px] uppercase text-slate-500 px-1">预设</div>
          <button class="mb-1 w-full rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-400 hover:bg-slate-700" @click="store.newDraft()">+ 新建空白</button>
          <div v-for="wf in store.workflows" :key="wf.id" class="mb-0.5 flex items-center justify-between rounded px-2 py-1 text-[11px] hover:bg-slate-800 cursor-pointer group" :class="wf.is_active ? 'text-emerald-400' : 'text-slate-400'" @click="store.newDraft(wf)">
            <span class="truncate">{{ wf.name }}<span v-if="wf.is_active" class="ml-1">●</span></span>
            <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button v-if="!wf.is_active" class="text-slate-600 hover:text-emerald-400" title="激活此工作流" @click.stop="store.activate(wf.id)">⚡</button>
              <button v-if="!wf.is_builtin" class="text-slate-600 hover:text-red-400" @click.stop="store.remove(wf.id)">✕</button>
            </div>
          </div>
        </div>
        <div class="border-t border-slate-700 p-2 text-[10px] text-slate-500">
          缩放 {{ Math.round(zoom * 100) }}% · 滚轮缩放 · 拖拽空白平移
        </div>
      </div>

      <!-- Canvas -->
      <div ref="canvasRef" class="flex-1 relative cursor-grab overflow-hidden"
        @mousedown="onCanvasMouseDown"
        @wheel.prevent="onWheel"
        :class="{ 'cursor-grabbing': isPanning }"
        style="background-image: radial-gradient(circle, #1e293b 1px, transparent 1px); background-size: 20px 20px;">

        <!-- Transform wrapper -->
        <div :style="{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: '0 0', position: 'absolute', top: 0, left: 0 }">

          <!-- SVG connections -->
          <svg style="position:absolute;top:0;left:0;width:9999px;height:9999px;overflow:visible;pointer-events:none;">
            <defs>
              <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#6b7280" />
              </marker>
            </defs>
            <!-- Existing edges -->
            <path
              v-for="(edge, idx) in store.draft?.graph.edges || []"
              :key="edge.id"
              :d="(() => {
                const from = getPortPos(edge.from[0], edge.from[1], true)
                const to = getPortPos(edge.to[0], edge.to[1], false)
                return bezierPath(from.x, from.y, to.x, to.y)
              })()"
              fill="none" stroke="#4b5563" stroke-width="2"
              marker-end="url(#arrow)"
              style="pointer-events: stroke; cursor: pointer;"
              class="hover:stroke-red-400 transition-colors"
              @click.stop="removeEdge(idx)"
            />
            <!-- Drawing connection -->
            <path v-if="connecting.active"
              :d="bezierPath(
                getPortPos(connecting.fromNodeId, connecting.fromPort, true).x,
                getPortPos(connecting.fromNodeId, connecting.fromPort, true).y,
                connecting.mouseX, connecting.mouseY
              )"
              fill="none" stroke="#10b981" stroke-width="2" stroke-dasharray="6 3"
            />
          </svg>

          <!-- Nodes -->
          <div v-for="node in store.draft?.graph.nodes || []" :key="node.id"
            class="absolute rounded-lg shadow-xl border-2 select-none"
            :class="[
              getNodeColors(node).bg, getNodeColors(node).border,
              selectedNodeId === node.id ? 'ring-2 ring-emerald-400' : '',
              node.enabled === false ? 'opacity-40' : '',
            ]"
            :style="{ left: node._x + 'px', top: node._y + 'px', width: '220px' }"
            @mousedown="onNodeMouseDown($event, node)"
          >
            <!-- Header -->
            <div :class="[getNodeColors(node).header, 'flex items-center justify-between rounded-t-md px-2 py-1.5 cursor-move']">
              <span class="text-[11px] font-bold text-white truncate">{{ nodeSchema(node.type)?.label || node.type }}</span>
              <div class="flex items-center gap-1">
                <button class="text-white/60 hover:text-white text-[10px]" @click.stop="node.enabled = node.enabled === false ? true : false">{{ node.enabled === false ? '◻' : '◼' }}</button>
                <button class="text-white/60 hover:text-red-300 text-[10px]" @click.stop="removeNode(node.id)">✕</button>
              </div>
            </div>

            <!-- Body: input ports | params | output ports -->
            <div class="flex">
              <!-- Inputs (left side) -->
              <div class="flex-1 px-2 py-1">
                <div v-for="port in (nodeSchema(node.type)?.inputs || [])" :key="'in-'+port.name"
                  class="flex items-center h-[22px]">
                  <div class="port-dot w-3 h-3 rounded-full border-2 border-slate-400 bg-slate-900 hover:border-emerald-400 hover:bg-emerald-900 cursor-crosshair transition shrink-0"
                    :ref="(el) => registerPort(`${node.id}:${port.name}:in`, el)"
                    @mouseup="onInputPortMouseUp($event, node.id, port.name, port.type)"
                  ></div>
                  <span class="ml-1 text-[10px] text-slate-300 truncate">{{ port.name }}</span>
                </div>
              </div>
              <!-- Outputs (right side) -->
              <div class="flex-1 px-2 py-1">
                <div v-for="port in (nodeSchema(node.type)?.outputs || [])" :key="'out-'+port.name"
                  class="flex items-center justify-end h-[22px]">
                  <span class="mr-1 text-[10px] text-slate-300 truncate">{{ port.name }}</span>
                  <div class="port-dot w-3 h-3 rounded-full border-2 border-emerald-400 bg-emerald-900 hover:border-emerald-300 hover:bg-emerald-700 cursor-crosshair transition shrink-0"
                    :ref="(el) => registerPort(`${node.id}:${port.name}:out`, el)"
                    @mousedown="onOutputPortMouseDown($event, node.id, port.name, port.type)"
                  ></div>
                </div>
              </div>
            </div>

            <!-- Params -->
            <div v-if="(nodeSchema(node.type)?.params || []).length" class="border-t border-slate-700/50 px-2 py-1 space-y-1">
              <div v-for="p in nodeSchema(node.type).params" :key="p.key" class="flex items-center gap-1">
                <label class="text-[9px] text-slate-500 w-14 shrink-0 truncate" :title="p.description">{{ p.label }}</label>
                <select v-if="p.type === 'select'"
                  :value="node.params[p.key] ?? p.default"
                  @change="node.params[p.key] = $event.target.value"
                  class="flex-1 min-w-0 rounded bg-slate-800 border border-slate-600 px-1 py-0.5 text-[10px] text-slate-200 outline-none focus:border-emerald-500">
                  <option value="">{{ p.key === 'model' ? '跟随全局配置' : '默认' }}</option>
                  <option v-for="opt in (p.options || [])" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <input v-else-if="p.type === 'int' || p.type === 'float'"
                  type="number"
                  :value="node.params[p.key] ?? p.default"
                  @input="node.params[p.key] = Number($event.target.value)"
                  :min="p.min" :max="p.max"
                  class="flex-1 min-w-0 rounded bg-slate-800 border border-slate-600 px-1 py-0.5 text-[10px] text-slate-200 outline-none focus:border-emerald-500" />
                <input v-else-if="p.type === 'bool'"
                  type="checkbox"
                  :checked="node.params[p.key] ?? p.default"
                  @change="node.params[p.key] = $event.target.checked"
                  class="accent-emerald-500" />
                <input v-else
                  :value="node.params[p.key] ?? p.default"
                  @input="node.params[p.key] = $event.target.value"
                  class="flex-1 min-w-0 rounded bg-slate-800 border border-slate-600 px-1 py-0.5 text-[10px] text-slate-200 outline-none focus:border-emerald-500" />
              </div>
            </div>

            <!-- Node ID badge -->
            <div class="absolute -top-2.5 -left-2.5 bg-slate-800 text-[8px] text-slate-400 rounded px-1 py-0.5 border border-slate-700">{{ node.id }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
