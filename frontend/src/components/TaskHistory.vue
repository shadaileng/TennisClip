<script setup>
import { computed, watch } from 'vue'
import { useTaskStore, isRunningStatus } from '../stores/task'
import StatusBadge from './StatusBadge.vue'

const store = useTaskStore()

const LEVEL_LABELS = {
  beginner: '入门',
  intermediate: '进阶',
  professional: '专业',
  all: '所有高光',
}

const filterTabs = [
  { key: 'all', label: '全部' },
  { key: 'succeeded', label: '完成' },
  { key: 'failed', label: '失败' },
  { key: 'processing', label: '处理中' },
]
const activeFilter = computed({
  get: () => store._taskFilter || 'all',
  set: (v) => { store._taskFilter = v },
})

const filteredTasks = computed(() => {
  const f = activeFilter.value
  if (f === 'all') return store.tasks
  if (f === 'processing') return store.tasks.filter((t) => t.status === 'pending' || t.status === 'processing')
  return store.tasks.filter((t) => t.status === f)
})

function fmtTime(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

function fmtDuration(sec) {
  if (!sec) return '–'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m${s}s`
}

function canViewDetail(task) {
  // 完成/失败查看结果弹窗；进行中跟踪实时进度（主界面）
  return ['succeeded', 'failed'].includes(task.status)
}

function isRunning(task) {
  return isRunningStatus(task.status)
}

function onTaskClick(task) {
  if (isRunning(task)) {
    // 进行中：接入主界面 TaskCard 实时进度（复用既有轮询）
    store.trackTask(task.task_id)
  } else if (canViewDetail(task)) {
    store.viewTask(task.task_id)
  }
}

watch(() => store.showHistory, (v) => {
  if (v) store.loadTasks()
})
</script>

<template>
  <!-- 遮罩 -->
  <div
    v-if="store.showHistory"
    class="fixed inset-0 z-40 bg-black/40"
    @click="store.showHistory = false"
  />

  <!-- 滑出面板 -->
  <div
    v-if="store.showHistory"
    class="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-slate-700 bg-slate-900 shadow-2xl"
  >
    <!-- 顶栏 -->
    <div class="flex items-center justify-between border-b border-slate-700 px-5 py-4">
      <h2 class="text-base font-semibold text-slate-100">历史任务</h2>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
          @click="store.loadTasks()"
        >
          刷新
        </button>
        <button
          type="button"
          class="text-slate-400 transition hover:text-slate-200"
          @click="store.showHistory = false"
        >
          ✕
        </button>
      </div>
    </div>

    <!-- 状态筛选 -->
    <div class="flex gap-1 border-b border-slate-800 px-5 py-2">
      <button
        v-for="tab in filterTabs"
        :key="tab.key"
        type="button"
        :class="[
          'rounded-full px-3 py-1 text-xs font-medium transition',
          activeFilter === tab.key
            ? 'bg-emerald-500/15 text-emerald-300'
            : 'text-slate-400 hover:text-slate-200',
        ]"
        @click="activeFilter = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 列表 -->
    <div class="flex-1 overflow-y-auto px-5 py-3">
      <p v-if="store.loadingTasks" class="py-10 text-center text-sm text-slate-500">加载中…</p>

      <p v-else-if="!filteredTasks.length" class="py-10 text-center text-sm text-slate-500">
        暂无任务记录
      </p>

      <ul v-else class="space-y-2">
        <li
          v-for="task in filteredTasks"
          :key="task.task_id"
          :class="[
            'rounded-xl border p-3 transition',
            isRunning(task)
              ? 'cursor-pointer border-blue-500/30 bg-blue-500/5 hover:border-blue-400/60 hover:bg-blue-500/10'
              : canViewDetail(task)
                ? 'cursor-pointer border-slate-700 bg-slate-800/50 hover:border-emerald-500/50 hover:bg-slate-800'
                : 'border-slate-800 bg-slate-800/30',
          ]"
          @click="onTaskClick(task)"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <StatusBadge :status="task.status" />
              <span class="max-w-[200px] truncate text-xs text-slate-300">{{ task.source_video || '未知文件' }}</span>
            </div>
            <span class="text-[11px] text-slate-500">{{ fmtTime(task.created_at) }}</span>
          </div>
          <div class="mt-1.5 flex items-center gap-3 text-[11px] text-slate-500">
            <span>{{ LEVEL_LABELS[task.level] || task.level }}</span>
            <span>耗时 {{ fmtDuration(task.elapsed_seconds) }}</span>
            <span v-if="isRunning(task)" class="text-blue-400">进行中 · 点击查看实时进度</span>
              <button
                v-if="isRunning(task)"
                type="button"
                class="rounded border border-red-500/40 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-300 transition hover:border-red-400 hover:bg-red-500/20"
                @click.stop="store.stopTask(task.task_id)"
              >
                停止
              </button>
              <span v-if="!isRunning(task) && task.error" class="truncate text-red-400">{{ task.error }}</span>
          </div>
        </li>
      </ul>
    </div>

    <!-- 底栏统计 -->
    <div class="border-t border-slate-800 px-5 py-2.5 text-[11px] text-slate-500">
      共 {{ store.tasks.length }} 条记录
    </div>
  </div>
</template>
