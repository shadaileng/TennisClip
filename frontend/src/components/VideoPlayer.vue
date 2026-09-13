<script setup>
import { ref } from 'vue'

const props = defineProps({
  src: String,
  segments: { type: Array, default: () => [] },
})

const videoRef = ref(null)

function fmt(sec) {
  if (sec == null) return '–'
  const m = Math.floor(sec / 60)
  const s = (sec % 60).toFixed(1)
  return m ? `${m}:${s.padStart(4, '0')}` : `${s}s`
}
</script>
</script>

<template>
  <div>
    <div class="mb-2 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-100">15 秒高光集锦</h3>
      <span class="text-xs text-slate-500">共 {{ segments.length }} 个回合</span>
    </div>

    <video
      v-if="src"
      ref="videoRef"
      :src="src"
      controls
      preload="metadata"
      class="w-full rounded-xl bg-black"
    />
    <p v-else class="rounded-lg bg-slate-800 p-4 text-center text-xs text-slate-500">
      集锦视频尚未生成（需后端安装 FFMPEG）
    </p>

    <ul v-if="segments.length" class="mt-3 space-y-1.5">
      <li
        v-for="(s, i) in segments"
        :key="i"
        class="flex items-center justify-between rounded-lg bg-slate-800/60 px-3 py-2 text-xs"
      >
        <div class="flex items-center gap-2">
          <span class="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-300">
            {{ i + 1 }}
          </span>
          <span class="font-medium text-slate-200">{{ s.label }}</span>
          <span class="text-slate-500">{{ fmt(s.start) }} – {{ fmt(s.end) }}</span>
        </div>
        <span class="text-slate-400">置信度 {{ (s.confidence * 100).toFixed(0) }}%</span>
      </li>
    </ul>
  </div>
</template>
