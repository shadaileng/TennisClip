<script setup>
import { ref } from 'vue'

const emit = defineEmits(['submit'])
const props = defineProps({
  loading: Boolean,
  hasTask: Boolean,
})

const level = ref('intermediate')
const file = ref(null)
const inputRef = ref(null)
const dragging = ref(false)

const LEVELS = [
  { value: 'beginner', label: '入门' },
  { value: 'intermediate', label: '进阶' },
  { value: 'professional', label: '专业' },
]

function onFiles(e) {
  const f = e.target.files?.[0] || e.dataTransfer?.files?.[0]
  if (f) file.value = f
}

function submit() {
  if (file.value && !props.loading) {
    emit('submit', file.value, level.value)
  }
}
</script>

<template>
  <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
    <h2 class="mb-4 text-base font-semibold text-slate-100">上传网球视频</h2>

    <label class="mb-3 flex cursor-pointer items-center justify-between rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm">
      <span class="text-slate-300">
        分析层级
      </span>
      <div class="flex gap-1">
        <button
          v-for="lv in LEVELS"
          :key="lv.value"
          type="button"
          :class="[
            'rounded-lg px-3 py-1.5 text-xs transition',
            level === lv.value
              ? 'bg-emerald-500 text-slate-950 font-semibold'
              : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
          ]"
          @click="level = lv.value"
        >
          {{ lv.label }}
        </button>
      </div>
    </label>

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
      <template v-if="props.loading">处理中…</template>
      <template v-else-if="hasTask">重新提交</template>
      <template v-else>开始处理</template>
    </button>
  </div>
</template>
