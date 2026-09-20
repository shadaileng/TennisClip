<script setup>
import { computed } from 'vue'
import { useTaskStore } from '../stores/task'
import StatusBadge from './StatusBadge.vue'
import VideoPlayer from './VideoPlayer.vue'
import ReportView from './ReportView.vue'

const store = useTaskStore()
const props = defineProps({
  task: { type: Object, default: null },
  error: String,
})
const emit = defineEmits(['clear'])

const task = computed(() => props.task)
const isTerminal = computed(() =>
  !task.value || ['succeeded', 'failed', 'timeout'].includes(task.value.status)
)
const showResults = computed(
  () => task.value?.status === 'succeeded' && (task.value.highlight || task.value.report || task.value.highlight_video_path)
)

// 处理阶段步骤条（与后端 run_pipeline 的 stage 对齐）
// all 档位（所有高光回合）仅剪辑、不生成报告，步骤条隐藏「报告生成」
const stages = computed(() =>
  store.allHighlights
    ? [
        { key: 'preprocessing', label: '预处理' },
        { key: 'highlighting', label: '高光识别' },
        { key: 'editing', label: '自动剪辑' },
      ]
    : [
        { key: 'preprocessing', label: '预处理' },
        { key: 'highlighting', label: '高光识别' },
        { key: 'editing', label: '自动剪辑' },
        { key: 'reporting', label: '报告生成' },
      ]
)
const currentStageIndex = computed(() =>
  stages.findIndex((s) => s.key === task.value?.stage)
)
const allDone = computed(() => task.value?.status === 'succeeded')
function isStageDone(i) {
  if (allDone.value) return true
  return currentStageIndex.value >= 0 && i < currentStageIndex.value
}
function isStageActive(i) {
  if (allDone.value) return false
  return i === currentStageIndex.value
}
function stageClass(i) {
  if (isStageDone(i)) return 'bg-emerald-500 text-slate-950'
  if (isStageActive(i)) return 'bg-emerald-500/20 text-emerald-300 ring-2 ring-emerald-500'
  return 'bg-slate-800 text-slate-400'
}
function labelClass(i) {
  if (isStageDone(i)) return 'text-emerald-300'
  if (isStageActive(i)) return 'text-emerald-200'
  return 'text-slate-500'
}
</script>

<template>
  <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
    <div class="mb-4 flex items-center justify-between">
      <h2 class="text-base font-semibold text-slate-100">处理任务</h2>
      <button
        v-if="task"
        class="text-xs text-slate-400 hover:text-slate-200"
        @click="emit('clear')"
      >
        清除
      </button>
    </div>

    <p v-if="!task" class="py-10 text-center text-sm text-slate-500">
      暂无任务。上传视频后在这里查看处理进度与结果。
    </p>

    <template v-else>
      <!-- 状态徽章 -->
      <div class="mb-3 flex items-center gap-2">
        <StatusBadge :status="task.status" />
        <span class="text-xs text-slate-500">{{ task.source_video }}</span>
      </div>

      <!-- 阶段步骤条（非终态，实时高亮当前阶段） -->
      <div v-if="!isTerminal" class="mb-4">
        <ol class="flex items-center">
          <li
            v-for="(s, i) in stages"
            :key="s.key"
            class="flex items-center"
            :class="i === stages.length - 1 ? 'flex-none' : 'flex-1'"
          >
            <div class="flex flex-col items-center">
              <span
                class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold"
                :class="stageClass(i)"
              >
                <template v-if="isStageDone(i)">✓</template>
                <template v-else-if="isStageActive(i)"><span class="animate-pulse">●</span></template>
                <template v-else>{{ i + 1 }}</template>
              </span>
              <span class="mt-1 whitespace-nowrap text-[11px]" :class="labelClass(i)">{{ s.label }}</span>
            </div>
            <span
              v-if="i < stages.length - 1"
              class="mx-1 h-0.5 flex-1 rounded"
              :class="isStageDone(i) ? 'bg-emerald-500' : 'bg-slate-700'"
            />
          </li>
        </ol>
        <p class="mt-2 text-xs text-slate-400">
          正在处理：{{ stages[currentStageIndex]?.label || '排队中' }}…
        </p>
      </div>

      <!-- 错误 -->
      <div v-if="error" class="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
        {{ error }}
      </div>

      <!-- 失败 -->
      <div v-if="task.status === 'failed' || task.status === 'timeout'" class="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
        处理失败：{{ task.error || task.status }}
      </div>

      <!-- 成功结果 -->
      <div v-if="showResults" class="space-y-4">
        <VideoPlayer :src="store.highlightVideoUrl" :segments="store.highlightSegments" />
        <ReportView :report="store.report" :all-mode="store.allHighlights" />
        <div class="flex gap-2">
          <a
            :href="store.highlightVideoUrl"
            download
            class="rounded-lg bg-emerald-500 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
          >
            ⬇ 下载集锦
          </a>
          <a
            v-if="!store.allHighlights"
            :href="`/api/v1/tasks/${task.task_id}/report`"
            download
            class="rounded-lg bg-slate-700 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-600"
          >
            ⬇ 下载报告
          </a>
        </div>
      </div>
    </template>
  </div>
</template>
