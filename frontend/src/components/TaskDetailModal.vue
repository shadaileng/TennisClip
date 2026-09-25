<script setup>
import { computed } from 'vue'
import { useTaskStore } from '../stores/task'
import StatusBadge from './StatusBadge.vue'
import VideoPlayer from './VideoPlayer.vue'
import ReportView from './ReportView.vue'
import { api } from '../lib/api'

const store = useTaskStore()

const task = computed(() => store.selectedTask)
const show = computed(() => store.showDetail)

const hasHighlight = computed(() => !!task.value?.highlight_video_path)
const hasReport = computed(() => !!task.value?.report)

const highlightVideoUrl = computed(() =>
  task.value?.task_id ? api.videoUrl(task.value.task_id) : '',
)
const highlightSegments = computed(() =>
  task.value?.highlight?.segments || [],
)
const reportData = computed(() => task.value?.report || null)
const isAllHighlights = computed(() => !!task.value?.highlight?.all_highlights)

const reportUrl = computed(() =>
  task.value?.task_id ? api.reportUrl(task.value.task_id) : '',
)

const LEVEL_LABELS = {
  beginner: '入门',
  intermediate: '进阶',
  professional: '专业',
  all: '所有高光',
}

function fmtDuration(sec) {
  if (!sec) return '–'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}分${s}秒`
}

function fmtTime(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <!-- 遮罩 -->
  <div
    v-if="show"
    class="fixed inset-0 z-40 bg-black/50"
    @click="store.showDetail = false"
  />

  <!-- 弹窗 -->
  <div
    v-if="show && task"
    class="fixed inset-4 z-50 mx-auto my-auto flex max-w-3xl flex-col rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl md:inset-x-auto md:inset-y-8 md:max-h-[calc(100vh-4rem)]"
  >
    <!-- 顶栏 -->
    <div class="flex items-center justify-between border-b border-slate-700 px-5 py-4">
      <div class="flex items-center gap-3">
        <StatusBadge :status="task.status" />
        <span class="max-w-[280px] truncate text-sm font-medium text-slate-100">
          {{ task.source_video || '未知文件' }}
        </span>
      </div>
      <button
        type="button"
        class="text-slate-400 transition hover:text-slate-200"
        @click="store.showDetail = false"
      >
        ✕
      </button>
    </div>

    <!-- 任务信息条 -->
    <div class="flex flex-wrap items-center gap-3 border-b border-slate-800 px-5 py-2.5 text-xs text-slate-400">
      <span>层级：{{ LEVEL_LABELS[task.level] || task.level }}</span>
      <span>耗时：{{ fmtDuration(task.elapsed_seconds) }}</span>
      <span v-if="task.created_at">创建：{{ fmtTime(task.created_at) }}</span>
      <!-- 错误占满整行并换行，避免挤压同行的层级/耗时信息 -->
      <span v-if="task.error" class="w-full break-words whitespace-pre-wrap leading-relaxed text-red-400">
        错误：{{ task.error }}
      </span>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
      <!-- 失败/处理中 -->
      <p v-if="task.status === 'failed' || task.status === 'timeout'" class="break-words whitespace-pre-wrap py-8 text-center text-sm leading-relaxed text-red-400">
        任务处理失败：{{ task.error || task.status }}
      </p>
      <p v-else-if="task.status !== 'succeeded'" class="py-8 text-center text-sm text-slate-500">
        任务正在处理中…
      </p>

      <!-- 有视频就显示视频 -->
      <VideoPlayer v-if="hasHighlight" :src="highlightVideoUrl" :segments="highlightSegments" />

      <!-- 有报告就显示报告 -->
      <ReportView v-if="hasReport || isAllHighlights" :report="reportData" :all-mode="isAllHighlights" />

      <!-- 成功但什么都没有 -->
      <p v-if="task.status === 'succeeded' && !hasHighlight && !hasReport" class="py-8 text-center text-sm text-slate-500">
        暂无结果数据
      </p>
    </div>

    <!-- 底栏操作 -->
    <div class="flex items-center justify-end gap-2 border-t border-slate-700 px-5 py-3">
      <a
        v-if="hasHighlight"
        :href="highlightVideoUrl"
        download
        class="rounded-lg bg-emerald-500 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
      >
        ⬇ 下载集锦
      </a>
      <a
        v-if="hasReport && !isAllHighlights"
        :href="reportUrl"
        download
        class="rounded-lg bg-slate-700 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-600"
      >
        ⬇ 下载报告
      </a>
      <button
        type="button"
        class="rounded-lg border border-slate-600 px-3 py-2 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
        @click="store.showDetail = false"
      >
        关闭
      </button>
    </div>
  </div>
</template>
