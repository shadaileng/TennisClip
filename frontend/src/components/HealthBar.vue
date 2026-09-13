<script setup>
const props = defineProps({
  health: { type: Object, default: null },
})
</script>

<template>
  <div class="flex items-center gap-2 text-xs">
    <span
      v-if="!health"
      class="inline-flex items-center gap-1.5 rounded-full bg-slate-800 px-2.5 py-1 text-slate-300"
    >
      <span class="h-2 w-2 rounded-full bg-slate-400" /> 检测中…
    </span>
    <span
      v-else-if="health.status === 'ok'"
      class="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-emerald-300"
    >
      <span class="h-2 w-2 rounded-full bg-emerald-400" />
      在线 · {{ health.provider }} / {{ health.model }}
    </span>
    <span
      v-else
      class="inline-flex items-center gap-1.5 rounded-full bg-red-500/15 px-2.5 py-1 text-red-300"
    >
      <span class="h-2 w-2 rounded-full bg-red-400" /> 后端不可达
    </span>
  </div>
</template>
