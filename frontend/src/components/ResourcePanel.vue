<script setup>
/**
 * 实时资源面板
 * 轮询 GET /api/v1/system/stats，展示 CPU / 内存 / 任务队列 / GPU 状态。
 * 30 秒内无任务时降频到 10s 轮询以减轻负载。
 */
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { api } from '../lib/api'

const loading = ref(false)
const error = ref(null)
const lastUpdated = ref(null)   // ISO 时间字符串

// CPU / 内存
const cpuPercent = ref(null)
const cpuCores = ref(0)
const memUsed = ref(0)
const memTotal = ref(0)
const memPercent = ref(null)

// 任务队列
const runningTasks = ref(0)
const queuedTasks = ref(0)

// GPU（可为 null：无 nvidia-smi 时）
const gpus = ref(null)

let timer = null

const MB = 1024 * 1024
const GB = 1024 * 1024 * 1024

function fmtBytes(b) {
  if (b == null || b <= 0) return '—'
  if (b >= GB) return (b / GB).toFixed(1) + ' GB'
  if (b >= MB) return (b / MB).toFixed(0) + ' MB'
  return b.toFixed(0) + ' B'
}

function fmtPct(v) {
  if (v == null) return '—'
  return v.toFixed(1) + '%'
}

function cpuColor(v) {
  if (v == null) return 'text-slate-400'
  if (v < 50) return 'text-emerald-400'
  if (v < 80) return 'text-yellow-400'
  return 'text-red-400'
}

function memColor(v) {
  if (v == null) return 'text-slate-400'
  if (v < 60) return 'text-emerald-400'
  if (v < 85) return 'text-yellow-400'
  return 'text-red-400'
}

async function fetchStats() {
  try {
    loading.value = true
    error.value = null
    const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
    const res = await fetch(`${API_BASE_URL}/api/v1/system/stats`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    cpuPercent.value = d.cpu?.percent ?? null
    cpuCores.value = d.cpu?.cores ?? 0
    memUsed.value = d.memory?.used ?? 0
    memTotal.value = d.memory?.total ?? 0
    memPercent.value = d.memory?.percent ?? null
    runningTasks.value = d.tasks?.running ?? 0
    queuedTasks.value = d.tasks?.queued ?? 0
    gpus.value = d.gpu ?? null
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  // 有运行中任务时用 2s 轮询，否则 10s 降频
  const interval = () => (runningTasks.value > 0 || queuedTasks.value > 0) ? 2000 : 10000
  fetchStats()
  timer = setInterval(fetchStats, interval())
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(startPolling)
onBeforeUnmount(stopPolling)

// 当任务状态变化时（由 task store 监听），强制立即刷新一次
import { useTaskStore } from '../stores/task'
const taskStore = useTaskStore()
watch(
  () => taskStore.current?.status,
  () => { if (!timer) startPolling() }
)
</script>

<template>
  <div
    class="rounded-xl border border-slate-700/60 bg-slate-900/60 p-4 text-xs text-slate-300 backdrop-blur"
  >
    <div class="mb-3 flex items-center justify-between">
      <span class="font-semibold text-slate-200">资源监控</span>
      <span class="flex items-center gap-1.5 text-[10px] text-slate-500">
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          :class="error ? 'bg-red-400' : 'bg-emerald-400 animate-pulse'"
        />
        {{ error ? '连接失败' : (lastUpdated ?? '刷新中…') }}
      </span>
    </div>

    <!-- CPU -->
    <div class="mb-3">
      <div class="mb-1 flex items-center justify-between">
        <span class="text-slate-400">CPU</span>
        <span :class="cpuColor(cpuPercent)" class="font-mono">
          {{ fmtPct(cpuPercent) }} · {{ cpuCores }} 核
        </span>
      </div>
      <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          class="h-full rounded-full transition-all duration-500"
          :class="cpuPercent != null && cpuPercent >= 80 ? 'bg-red-500' : cpuPercent != null && cpuPercent >= 50 ? 'bg-yellow-500' : 'bg-emerald-500'"
          :style="{ width: cpuPercent != null ? cpuPercent + '%' : '0%' }"
        />
      </div>
    </div>

    <!-- 内存 -->
    <div class="mb-3">
      <div class="mb-1 flex items-center justify-between">
        <span class="text-slate-400">内存</span>
        <span :class="memColor(memPercent)" class="font-mono">
          {{ fmtPct(memPercent) }} · {{ fmtBytes(memUsed) }} / {{ fmtBytes(memTotal) }}
        </span>
      </div>
      <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          class="h-full rounded-full transition-all duration-500"
          :class="memPercent != null && memPercent >= 85 ? 'bg-red-500' : memPercent != null && memPercent >= 60 ? 'bg-yellow-500' : 'bg-emerald-500'"
          :style="{ width: memPercent != null ? memPercent + '%' : '0%' }"
        />
      </div>
    </div>

    <!-- 任务队列 -->
    <div class="mb-3 flex items-center justify-between">
      <span class="text-slate-400">任务队列</span>
      <span class="font-mono">
        <span v-if="runningTasks > 0" class="text-emerald-400">{{ runningTasks }} 运行中</span>
        <span v-if="runningTasks > 0 && queuedTasks > 0" class="mx-1 text-slate-600">·</span>
        <span v-if="queuedTasks > 0" class="text-yellow-400">{{ queuedTasks }} 等待</span>
        <span v-if="runningTasks === 0 && queuedTasks === 0" class="text-slate-500">空闲</span>
      </span>
    </div>

    <!-- GPU（有则展示，无则显示 "未检测到"） -->
    <div v-if="gpus !== null" class="mb-2 border-t border-slate-800 pt-3">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-slate-400">GPU</span>
        <span class="font-mono text-purple-400">{{ gpus.length }} 卡</span>
      </div>
      <ul class="space-y-2">
        <li v-for="g in gpus" :key="g.index">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-slate-300">{{ g.name }} #{{ g.index }}</span>
            <span class="font-mono text-purple-300">{{ g.utilization_percent ?? '—' }}%</span>
          </div>
          <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              class="h-full rounded-full bg-purple-500 transition-all duration-500"
              :style="{ width: (g.utilization_percent ?? 0) + '%' }"
            />
          </div>
          <div class="mt-0.5 text-[10px] text-slate-500">
            显存 {{ fmtBytes(g.memory_used_mb) }} / {{ fmtBytes(g.memory_total_mb) }}
          </div>
        </li>
      </ul>
    </div>
    <div v-else class="mb-2 border-t border-slate-800 pt-3 text-[11px] text-slate-600 italic">
      GPU 未检测到（容器内无 nvidia-smi，或无 NVIDIA 设备）
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="mt-2 text-[11px] text-red-400">{{ error }}</div>
  </div>
</template>
