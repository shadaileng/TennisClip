<script setup>
import { computed } from 'vue'

const props = defineProps({
  severity: String,
})

const style = computed(() => {
  switch (props.severity) {
    case 'high':
      return { label: '高', cls: 'bg-red-500/15 text-red-300' }
    case 'low':
      return { label: '低', cls: 'bg-emerald-500/15 text-emerald-300' }
    default:
      return { label: '中', cls: 'bg-amber-500/15 text-amber-300' }
  }
})
</script>

<template>
  <span :class="['rounded-full px-2 py-0.5 text-xs font-medium', style.cls]">
    {{ style.label }}
  </span>
</template>
