<script setup>
import { ref, computed } from 'vue'
import { useTaskStore } from '../stores/task'

const emit = defineEmits(['submit'])
const props = defineProps({
  loading: Boolean,
  hasTask: Boolean,
})

const store = useTaskStore()

const file = ref(null)
const inputRef = ref(null)
const dragging = ref(false)

// 服务商下拉：自定义（独立配置） + 各启用服务商
const providerOptions = computed(() => store.providerOptions)
const selectedProvider = computed({
  get: () => store.aiProvider || 'custom',
  set: (v) => store.selectProvider(v),
})
const activeProvider = computed(() => store.activeProvider)
const currentModels = computed(() => activeProvider.value?.models || [])
const selectedModel = computed({
  get: () => store.aiModel || '',
  set: (v) => store.selectModel(v),
})
const switchingProvider = computed(() => store.switchingProvider)
const loadingProviders = computed(() => store.loadingProviders)
const providerError = computed(() => store.providerError)

// 当前策略展示（读全局配置）
const LEVEL_LABELS = {
  beginner: '入门',
  intermediate: '进阶',
  professional: '专业',
  all: '所有高光回合',
}
const STAGE_DEFS = [
  { key: 'preprocess', label: '预处理' },
  { key: 'highlight', label: '高光识别' },
  { key: 'edit', label: '剪辑' },
  { key: 'report', label: '报告' },
]
const activeStages = computed(
  () => store.pipelineStages || ['preprocess', 'highlight', 'edit', 'report'],
)
const stageDefs = computed(() =>
  STAGE_DEFS.map((s) => ({ ...s, enabled: activeStages.value.includes(s.key) })),
)
const modeLabel = computed(() => (store.analysisMode === 'video' ? '视频理解' : '抽帧'))
const levelLabel = computed(() => LEVEL_LABELS[store.defaultLevel] || store.defaultLevel)

// 两步上传阶段反馈
const uploadPhase = computed(() => store.uploadPhase)
const uploadPercent = computed(() => Math.round((store.uploadProgress || 0) * 100))

function onFiles(e) {
  const f = e.target.files?.[0] || e.dataTransfer?.files?.[0]
  if (f) file.value = f
}

function submit() {
  if (file.value && !props.loading) {
    emit('submit', file.value, store.defaultLevel)
  }
}
</script>

<template>
  <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
    <div class="mb-4 flex items-center justify-between">
      <h2 class="text-base font-semibold text-slate-100">上传网球视频</h2>
      <button
        type="button"
        class="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10"
        @click="store.openStrategy()"
      >
        策略设置
      </button>
    </div>

    <!-- 当前策略概览（位于服务商上方） -->
    <div class="mb-4 rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-3">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-xs font-medium text-slate-400">当前策略</span>
        <button
          type="button"
          class="rounded-lg border border-emerald-500/40 px-2.5 py-1 text-[11px] font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10"
          @click="store.openStrategy()"
        >
          调整
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="rounded-md bg-slate-700/50 px-2 py-1 text-xs text-slate-300">
          模式 · <span class="text-emerald-300">{{ modeLabel }}</span>
        </span>
        <span class="rounded-md bg-slate-700/50 px-2 py-1 text-xs text-slate-300">
          层级 · <span class="text-emerald-300">{{ levelLabel }}</span>
        </span>
      </div>
      <div class="mt-2 flex flex-wrap gap-1.5">
        <span
          v-for="s in stageDefs"
          :key="s.key"
          :class="[
            'rounded-full px-2.5 py-0.5 text-[11px] transition',
            s.enabled
              ? 'bg-emerald-500/15 text-emerald-300'
              : 'bg-slate-800 text-slate-600 line-through',
          ]"
        >
          {{ s.label }}
        </span>
      </div>
    </div>

    <label class="mb-3 flex cursor-pointer items-center justify-between rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm">
      <span class="text-slate-300">服务商</span>
      <div class="flex w-56 items-center justify-end gap-2">
        <select
          v-if="providerOptions.length"
          v-model="selectedProvider"
          :disabled="switchingProvider"
          class="max-w-full truncate rounded-lg border border-slate-600 bg-slate-900 px-3 py-1.5 text-xs text-slate-100 outline-none transition focus:border-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option v-for="o in providerOptions" :key="o.value" :value="o.value">
            {{ o.label }}{{ activeProvider && activeProvider.name === o.value ? ' ✓' : '' }}
          </option>
        </select>
        <span v-else-if="loadingProviders" class="text-xs text-slate-400">加载模型中…</span>
        <span v-else class="text-xs text-slate-500">无可用模型</span>
      </div>
    </label>

    <label class="mb-3 flex cursor-pointer items-center justify-between rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm">
      <span class="text-slate-300">模型</span>
      <div class="flex w-56 items-center justify-end gap-2">
        <select
          v-if="currentModels.length"
          v-model="selectedModel"
          :disabled="switchingProvider"
          class="max-w-full truncate rounded-lg border border-slate-600 bg-slate-900 px-3 py-1.5 text-xs text-slate-100 outline-none transition focus:border-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="">跟随服务商默认（{{ activeProvider?.default_model || '' }}）</option>
          <option v-for="m in currentModels" :key="m" :value="m">
            {{ m }}
          </option>
        </select>
        <span v-else-if="loadingProviders" class="text-xs text-slate-400">加载模型中…</span>
        <span v-else class="text-xs text-slate-500">无可用模型</span>
      </div>
    </label>

    <p v-if="providerError" class="mb-3 -mt-1 text-right text-xs text-red-300">{{ providerError }}</p>

    <div
      :class="[
        'flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition',
        dragging ? 'border-emerald-400 bg-emerald-500/10' : 'border-slate-700 bg-slate-800/40'
      ]"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onFiles($event); dragging = false"
      @click="inputRef?.click()"
    >
      <input ref="inputRef" type="file" accept="video/*" class="hidden" @change="onFiles" />
      <span class="text-3xl">📁</span>
      <p class="mt-2 text-sm text-slate-400">
        <template v-if="file">{{ file.name }}（{{ (file.size / 1024 / 1024).toFixed(1) }} MB）</template>
        <template v-else>拖拽视频到此处，或点击选择文件</template>
      </p>
      <p class="mt-1 text-xs text-slate-500">支持 mp4 / mov / mkv / avi，1–5 分钟</p>
    </div>

    <!-- 两步上传阶段反馈 -->
    <div
      v-if="uploadPhase === 'instant'"
      class="mt-4 flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300"
    >
      <span class="text-lg">⚡</span>
      <span>秒传命中，直接开始分析（已跳过上传）</span>
    </div>
    <div
      v-else-if="uploadPhase === 'hashing' || uploadPhase === 'uploading'"
      class="mt-4 rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-3 text-sm text-slate-300"
    >
      <div class="mb-2 flex items-center justify-between">
        <span>
          {{ uploadPhase === 'hashing' ? '计算文件指纹（MD5）…' : `分片上传中 ${store.uploadedChunks}/${store.totalChunks}` }}
        </span>
        <span :class="uploadPhase === 'uploading' ? 'text-emerald-400' : 'text-slate-500'">{{ uploadPercent }}%</span>
      </div>
      <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
        <div
          class="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-[width] duration-200"
          :style="{ width: uploadPercent + '%' }"
        ></div>
      </div>
    </div>

    <button
      type="button"
      :disabled="!file || props.loading"
      :class="[
        'mt-4 w-full rounded-xl py-3 text-sm font-semibold transition',
        file && !props.loading
          ? 'bg-emerald-500 text-slate-950 hover:bg-emerald-400'
          : 'cursor-not-allowed bg-slate-800 text-slate-500'
      ]"
      @click="submit"
    >
      <template v-if="props.loading">
        <span v-if="uploadPhase === 'instant'">秒传并分析中…</span>
        <span v-else-if="uploadPhase === 'hashing'">计算中…</span>
        <span v-else-if="uploadPhase === 'uploading'">上传中…</span>
        <span v-else>处理中…</span>
      </template>
      <template v-else-if="hasTask">重新提交</template>
      <template v-else>上传并分析</template>
    </button>
  </div>
</template>
