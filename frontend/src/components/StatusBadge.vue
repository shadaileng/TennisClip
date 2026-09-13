<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: String,
})

const style = computed(() => {
  switch (props.status) {
    case 'pending':
    case 'processing':
      return { label: '处理中', cls: 'bg-amber-500/15 text-amber-300' }
    case 'succeeded':
      return { label: '完成', cls: 'bg-emerald-500/15 text-emerald-300' }
    case 'failed':
    case 'timeout':
      return { label: '失败', cls: 'bg-red-500/15 text-red-300' }
    default:
      return { label: props.status || '未知', cls: 'bg-slate-700 text-slate-300' }
  }
})
</script>

<template>
  <span :class="['inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium', style.cls]">
    <span class="h-1.5 w-1.5 rounded-full bg-current" />
    {{ style.label }}
  </span>
</template>
