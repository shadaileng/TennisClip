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
  () => task.value?.status === 'succeeded' && (task.value.highlight || task.value.report)
)
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

      <!-- 进度条（非终态） -->
      <div v-if="!isTerminal" class="mb-4">
        <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
          <div class="h-full w-1/3 animate-pulse rounded-full bg-emerald-500" />
        </div>
        <p class="mt-2 text-xs text-slate-400">
          正在处理：预处理 → 高光识别 → 自动剪辑 → 报告生成…
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
        <ReportView :report="store.report" />
        <div class="flex gap-2">
          <a
            :href="store.highlightVideoUrl"
            download
            class="rounded-lg bg-emerald-500 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
          >
            ⬇ 下载集锦
          </a>
          <a
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
