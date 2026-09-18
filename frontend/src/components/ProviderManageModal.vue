<script setup>
import { ref, computed, watch } from 'vue'
import { useTaskStore } from '../stores/task'
import { api } from '../lib/api'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])

const store = useTaskStore()

const editingId = ref(null) // null = 新增；非 null = 正在编辑的 id
const form = ref(blankForm())
const modelsList = ref([]) // 每模型一行
const checking = ref(false)
const checkResult = ref(null)
const checkError = ref(null)
const confirmDelete = ref(null)

function blankForm() {
  return { name: '', base_url: '', api_key: '', enabled: true }
}

const providers = computed(() => store.providers)
const loading = computed(() => store.loadingProviders)
const saving = computed(() => store.manageSaving)
const manageError = computed(() => store.manageError)

watch(
  () => props.open,
  (v) => {
    if (v) {
      store.manageError = null
      checkError.value = null
      checkResult.value = null
      store.loadProviders()
    }
  },
)

function startAdd() {
  editingId.value = null
  form.value = blankForm()
  modelsList.value = []
  confirmDelete.value = null
  checkResult.value = null
  checkError.value = null
}

function startEdit(p) {
  editingId.value = p.id
  form.value = {
    name: p.name,
    base_url: p.base_url,
    api_key: '',
    enabled: p.enabled,
  }
  modelsList.value = [...(p.models || [])]
  confirmDelete.value = null
  checkResult.value = null
  checkError.value = null
}

function cancelEdit() {
  startAdd()
}

function addModelRow() {
  modelsList.value.push('')
}

function removeModelRow(i) {
  if (modelsList.value.length <= 1) return
  modelsList.value.splice(i, 1)
}

const parsedModels = computed(() =>
  [...new Set(modelsList.value.map((s) => String(s).trim()).filter(Boolean))],
)

const canSave = computed(
  () =>
    form.value.name.trim().length > 0 &&
    /^https?:\/\//i.test(form.value.base_url.trim()) &&
    parsedModels.value.length > 0,
)

async function save() {
  const payload = {
    name: form.value.name.trim(),
    base_url: form.value.base_url.trim(),
    api_key: form.value.api_key || '',
    models: parsedModels.value,
    enabled: form.value.enabled,
    sort_order: 0,
  }
  try {
    if (editingId.value) {
      await store.updateProvider(editingId.value, payload)
    } else {
      await store.createProvider(payload)
    }
    startAdd()
  } catch {
    // store.manageError 已设置，保持弹窗不关闭
  }
}

function askDelete(p) {
  confirmDelete.value = p.id
}

async function remove(p) {
  try {
    await store.deleteProvider(p.id)
    if (editingId.value === p.id) startAdd()
  } catch {
    // store.manageError 已设置
  } finally {
    confirmDelete.value = null
  }
}

async function checkModels() {
  const base = form.value.base_url.trim()
  if (!/^https?:\/\//i.test(base) || parsedModels.value.length === 0) {
    checkError.value = '请先填写合法的 Base URL 与至少一个模型'
    checkResult.value = null
    return
  }
  checking.value = true
  checkError.value = null
  try {
    checkResult.value = await api.checkProviderModels({
      base_url: base,
      api_key: form.value.api_key || '',
      models: parsedModels.value,
    })
  } catch (e) {
    checkError.value = `校验失败：${String(e.message || e)}`
  } finally {
    checking.value = false
  }
}

function modelResult(m) {
  return checkResult.value?.results?.find((r) => r.model === m) || null
}

function close() {
  emit('close')
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div
      class="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80 shadow-2xl backdrop-blur"
    >
      <!-- 头部 -->
      <div class="flex items-center justify-between border-b border-slate-800 px-5 py-4">
        <h3 class="flex items-center gap-2 text-base font-semibold text-slate-100">
          <span class="text-lg">🧩</span> 模型服务商管理
        </h3>
        <button
          type="button"
          class="rounded-lg px-2 py-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-100"
          @click="close"
        >
          ✕
        </button>
      </div>

      <!-- 内容（左右两栏：左=列表，右=表单） -->
      <div class="flex-1 overflow-y-auto px-5 py-4">
        <p class="mb-4 text-xs text-slate-500">
          手动维护 OpenAI 兼容服务商；被当前直选引用的不可删除。一个服务商可配置多个模型，密钥直接入库（接口以掩码返回）。
        </p>

        <div class="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <!-- 列表（左） -->
          <div class="space-y-2">
            <h4 class="text-sm font-medium text-slate-200">服务商列表</h4>
          <div
            v-for="p in providers"
            :key="p.id"
            class="rounded-xl border border-slate-800 bg-slate-800/40 p-3 transition hover:border-slate-700"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="truncate text-sm font-medium text-slate-100">{{ p.name }}</span>
                  <span
                    v-if="p.is_selected"
                    class="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300"
                    >当前</span
                  >
                  <span
                    v-if="!p.enabled"
                    class="rounded-full bg-slate-700 px-2 py-0.5 text-[10px] text-slate-300"
                    >已停用</span
                  >
                </div>
                <p class="mt-0.5 truncate text-xs text-slate-400">{{ p.base_url }}</p>
                <div class="mt-1.5 flex flex-wrap gap-1">
                  <span
                    v-for="m in p.models"
                    :key="m"
                    class="rounded-md bg-slate-900/70 px-1.5 py-0.5 text-[10px] text-slate-300"
                    >{{ m }}</span
                  >
                </div>
                <p class="mt-1 text-[10px] text-slate-500">
                  密钥：{{ p.api_key ? '已设置' : '未设置' }} · 默认模型：{{ p.default_model || '—' }}
                </p>
              </div>
              <div class="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 transition hover:border-emerald-400 hover:text-emerald-300"
                  @click="startEdit(p)"
                >
                  编辑
                </button>
                <button
                  v-if="confirmDelete !== p.id"
                  type="button"
                  :disabled="p.is_selected"
                  :title="p.is_selected ? '请先在上传面板切换服务商' : '删除该服务商'"
                  class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-red-300 transition hover:border-red-400 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-slate-700 disabled:hover:bg-transparent"
                  @click="askDelete(p)"
                >
                  删除
                </button>
                <span v-else class="flex items-center gap-1">
                  <button
                    type="button"
                    class="rounded-lg bg-red-500 px-2 py-1 text-xs font-semibold text-slate-950 transition hover:bg-red-400"
                    @click="remove(p)"
                  >
                    确认
                  </button>
                  <button
                    type="button"
                    class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 transition hover:bg-slate-800"
                    @click="confirmDelete = null"
                  >
                    取消
                  </button>
                </span>
              </div>
            </div>
          </div>
          <p v-if="!loading && !providers.length" class="text-xs text-slate-500">暂无服务商，请在下方新增。</p>
          <p v-if="loading" class="text-xs text-slate-500">加载中…</p>
        </div>

        <!-- 表单（右） -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <h4 class="text-sm font-medium text-slate-200">
              {{ editingId ? `编辑服务商「${form.name}」` : '新增服务商' }}
            </h4>
            <button
              v-if="editingId"
              type="button"
              class="rounded-lg border border-emerald-500/50 px-2.5 py-1 text-xs font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10"
              @click="startAdd"
            >
              + 新增服务商
            </button>
          </div>
          <div class="rounded-xl border border-slate-800 bg-slate-800/40 p-4">
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label class="flex flex-col gap-1 text-xs text-slate-400">
              名称
              <input
                v-model="form.name"
                :disabled="!!editingId"
                type="text"
                placeholder="如 openai"
                class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-emerald-400 disabled:opacity-50"
              />
            </label>
            <label class="flex flex-col gap-1 text-xs text-slate-400">
              Base URL（须 http(s)://）
              <input
                v-model="form.base_url"
                type="text"
                placeholder="https://api.openai.com/v1"
                class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-emerald-400"
              />
            </label>
            <label class="flex flex-col gap-1 text-xs text-slate-400 sm:col-span-2">
              API Key（编辑时留空表示保留原值）
              <input
                v-model="form.api_key"
                type="password"
                autocomplete="off"
                placeholder="sk-..."
                class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-emerald-400"
              />
            </label>
          </div>

          <!-- 每模型一行 -->
          <div class="mt-3 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs text-slate-400">模型列表</span>
              <button
                type="button"
                class="rounded-lg border border-dashed border-slate-600 px-2 py-1 text-xs text-slate-300 transition hover:border-emerald-400 hover:text-emerald-300"
                @click="addModelRow"
              >
                + 添加模型
              </button>
            </div>
            <div
              v-for="(m, i) in modelsList"
              :key="i"
              class="flex items-center gap-2"
            >
              <input
                v-model="modelsList[i]"
                type="text"
                placeholder="模型名，如 gpt-4o"
                class="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 outline-none transition focus:border-emerald-400"
              />
              <span
                v-if="i === 0"
                class="rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300"
                >默认</span
              >
              <span
                v-if="modelResult(m)"
                :class="[
                  'rounded-md px-1.5 py-0.5 text-[10px] font-semibold',
                  modelResult(m).ok
                    ? 'bg-emerald-500/15 text-emerald-300'
                    : 'bg-red-500/15 text-red-300',
                ]"
                >{{ modelResult(m).ok ? '✓' : '✗' }}</span
              >
              <button
                type="button"
                :disabled="modelsList.length <= 1"
                class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-400 transition hover:border-red-400 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-40"
                @click="removeModelRow(i)"
              >
                删
              </button>
            </div>
          </div>

          <!-- 校验模型结果区 -->
          <div v-if="checkResult" class="mt-3 rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-xs">
            <p class="text-slate-300">
              探测策略：{{ checkResult.strategy === 'list' ? 'GET /models 清单' : '逐模型 chat/completions 探测' }}
              <span v-if="checkResult.available" class="text-slate-500">
                · 可用模型 {{ checkResult.available.length }} 个</span
              >
            </p>
            <ul class="mt-2 space-y-1">
              <li v-for="r in checkResult.results" :key="r.model" class="flex items-center gap-2">
                <span :class="r.ok ? 'text-emerald-300' : 'text-red-300'">{{ r.ok ? '✓' : '✗' }}</span>
                <span class="text-slate-200">{{ r.model }}</span>
                <span class="text-slate-500">{{ r.message }}</span>
              </li>
            </ul>
          </div>
          <p v-if="checkError" class="mt-3 text-xs text-red-300">{{ checkError }}</p>

          <div class="mt-3 flex flex-wrap items-center gap-3">
            <label class="flex items-center gap-2 text-xs text-slate-300">
              <input v-model="form.enabled" type="checkbox" class="h-4 w-4 accent-emerald-500" />
              启用（可选为生效服务商）
            </label>
            <button
              type="button"
              :disabled="checking"
              class="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-60"
              @click="checkModels"
            >
              {{ checking ? '校验中…' : '校验模型' }}
            </button>
          </div>

          <p v-if="manageError" class="mt-3 text-xs text-red-300">{{ manageError }}</p>

          <div class="mt-4 flex justify-end gap-2">
            <button
              v-if="editingId"
              type="button"
              class="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-slate-800"
              @click="cancelEdit"
            >
              取消编辑
            </button>
            <button
              type="button"
              :disabled="!canSave || saving"
              :class="[
                'rounded-lg px-4 py-1.5 text-xs font-semibold transition',
                canSave && !saving
                  ? 'bg-emerald-500 text-slate-950 hover:bg-emerald-400'
                  : 'cursor-not-allowed bg-slate-800 text-slate-500',
              ]"
              @click="save"
            >
              {{ saving ? '保存中…' : editingId ? '保存修改' : '新增服务商' }}
            </button>
          </div>
        </div>
        </div>
        </div>
      </div>
    </div>
  </div>
</template>
