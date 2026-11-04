<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(720px, 92vw)"
    top="8vh"
    append-to-body
    destroy-on-close
    class="path-picker-dialog"
    @update:model-value="close"
    @open="onOpen"
  >
    <div class="picker">
      <div class="picker-bar">
        <div class="crumbs">
          <button type="button" class="crumb" :class="{ current: !path }" @click="go('')">
            {{ mode === 'dir' ? '工作区根目录' : '工作区' }}
          </button>
          <template v-for="(segment, index) in segments" :key="`${segment}-${index}`">
            <span class="crumb-sep">/</span>
            <button
              type="button"
              class="crumb"
              :class="{ current: index === segments.length - 1 }"
              @click="go(segmentPath(index))"
            >{{ segment }}</button>
          </template>
        </div>
        <el-button text size="small" :disabled="!path" @click="go(parentPath)">↑ 上一级</el-button>
      </div>

      <div v-if="mode === 'file' && !workspace" class="picker-empty">请先选择工作区，再选择目标文件。</div>
      <div v-else v-loading="loading" class="picker-list">
        <div v-if="error" class="picker-error">{{ error }}</div>
        <template v-else>
          <!-- 文件模式把文件排在前面：这里的目标是选文件，子目录只用于进入下级。 -->
          <div v-if="mode === 'file' && files.length" class="list-label">文件 · {{ files.length }}</div>
          <div
            v-for="file in files"
            :key="`f-${file.path}`"
            :class="['row', 'is-file', { selected: selectedFile === file.path }]"
            role="button"
            tabindex="0"
            @click="selectFile(file)"
            @keydown.enter="selectFile(file)"
          >
            <el-icon class="row-icon"><Document /></el-icon>
            <span class="row-name">{{ file.name }}</span>
            <span class="row-size">{{ formatSize(file.size) }}</span>
          </div>

          <div v-if="mode === 'file' && dirs.length" class="list-label">子目录 · {{ dirs.length }}</div>
          <div
            v-for="dir in dirs"
            :key="`d-${dir.path}`"
            class="row is-dir"
            role="button"
            tabindex="0"
            @click="go(dir.path)"
            @keydown.enter="go(dir.path)"
          >
            <el-icon class="row-icon"><Folder /></el-icon>
            <span class="row-name">{{ dir.name }}</span>
            <span class="row-marks">
              <em v-for="mark in dir.marks || []" :key="mark" :class="['mark', `mark-${mark}`]">{{ markLabel(mark) }}</em>
            </span>
            <span v-if="mode === 'dir'" class="row-pick" @click.stop="choose(dir.path)">选择</span>
            <span v-else class="row-enter">进入 ›</span>
          </div>

          <div v-if="!dirs.length && !files.length" class="picker-empty">
            {{ mode === 'dir' ? '这个目录下没有子目录' : '这个目录下没有可选的测试文件' }}
          </div>
        </template>
      </div>

      <div v-if="mode === 'file' && workspace" class="picker-new">
        <el-input
          v-model="newName"
          :placeholder="`新建文件名，例如 ${defaultFile}`"
          maxlength="120"
          clearable
          @keyup.enter="confirm"
        >
          <template #prepend>新建</template>
        </el-input>
        <div class="picker-hint">{{ nameHint }}</div>
      </div>
    </div>

    <template #footer>
      <div class="picker-footer">
        <span class="picker-target" :title="pendingPath">{{ pendingLabel }}</span>
        <div class="picker-actions">
          <el-button @click="close(false)">取消</el-button>
          <el-button type="primary" :disabled="confirmDisabled" @click="confirm">确定</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Document, Folder } from '@element-plus/icons-vue'
import api from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // dir：选择工作区（路径相对 AUTOMATION_WORKSPACE_ROOT）；file：选择目标文件（路径相对工作区）
  mode: { type: String, default: 'dir' },
  workspace: { type: String, default: '' },
  framework: { type: String, default: 'robot-selenium' },
  startPath: { type: String, default: '' },
  defaultName: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue', 'select', 'loaded'])

const DEFAULT_EXT = { 'robot-selenium': '.robot', 'playwright-ts': '.spec.ts' }
const MARK_LABELS = { git: 'Git', node: 'Node', playwright: 'Playwright', python: 'Python', robot: 'Robot' }

const path = ref('')
// dirs/files 属于哪个目录。点「选择」会先改 path 再确认，所以要用独立变量记住列表来源。
const listingPath = ref('')
const dirs = ref([])
const files = ref([])
const loading = ref(false)
const error = ref('')
const selectedFile = ref('')
const newName = ref('')

const dialogTitle = computed(() => (props.mode === 'dir' ? '选择工作区目录' : '选择目标文件'))
const segments = computed(() => (path.value ? path.value.split('/').filter(Boolean) : []))
const parentPath = computed(() => segments.value.slice(0, -1).join('/'))
const defaultFile = computed(() => {
  const base = props.defaultName || 'recorded_flow'
  const ext = DEFAULT_EXT[props.framework] || '.robot'
  return base.includes('.') ? base : base + ext
})

// 只允许填文件名，子目录请在列表里进入，避免拼出工作区外的路径。
const invalidName = computed(() => /[\\/]/.test(newName.value.trim()) || newName.value.includes('..'))
const normalisedName = computed(() => {
  let name = newName.value.trim().replace(/^\/+/, '')
  if (!name || invalidName.value) return ''
  if (!name.includes('.')) name += DEFAULT_EXT[props.framework] || '.robot'
  return name
})
const pendingPath = computed(() => {
  if (props.mode !== 'file') return path.value
  if (!props.workspace) return ''
  if (normalisedName.value) return join(path.value, normalisedName.value)
  return selectedFile.value
})
const pendingLabel = computed(() => {
  if (props.mode === 'dir') return path.value ? `相对路径：${path.value}` : '将选择根目录本身'
  if (!pendingPath.value) return '请选择文件或填写新文件名'
  return `目标文件：${pendingPath.value}`
})
// 目录模式永远可以确认（根目录本身也是合法工作区）；文件模式必须先有目标路径。
const confirmDisabled = computed(() => (props.mode === 'dir' ? loading.value || !!error.value : !pendingPath.value || invalidName.value))
const nameHint = computed(() => {
  if (invalidName.value) return '只能填写文件名，请在列表里进入子目录。'
  if (!normalisedName.value) return `留空则在 ${path.value || '工作区根目录'} 下选择已有文件。`
  const exists = files.value.some(file => file.path === pendingPath.value)
  return exists ? '该文件已存在，生成时会提示是否覆盖。' : `将写入 ${pendingPath.value}`
})

function markLabel(mark) { return MARK_LABELS[mark] || mark }
function join(base, name) { return base ? `${base}/${name}` : name }
function segmentPath(index) { return segments.value.slice(0, index + 1).join('/') }
function formatSize(size) {
  const value = Number(size || 0)
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${Math.round(value / 1024)} KB`
  return `${value} B`
}

function close(visible = false) { emit('update:modelValue', visible) }

function onOpen() {
  error.value = ''
  selectedFile.value = ''
  newName.value = ''
  go(props.startPath || '')
}

async function go(target, allowFallback = true) {
  path.value = target || ''
  if (props.mode === 'file' && !props.workspace) {
    dirs.value = []
    files.value = []
    return
  }
  loading.value = true
  error.value = ''
  selectedFile.value = ''
  try {
    const { data } = props.mode === 'dir'
      ? await api.browseAutomationDirs(path.value)
      : await api.browseAutomationFiles(props.workspace, path.value, props.framework)
    path.value = data.path || ''
    listingPath.value = path.value
    dirs.value = data.dirs || []
    files.value = props.mode === 'dir' ? [] : (data.files || [])
    if (props.mode === 'dir' && data.root) emit('loaded', { root: data.root })
  } catch (err) {
    dirs.value = []
    files.value = []
    loading.value = false
    // 记住的路径可能已经被改名或删除：退回起点重来一次，别把用户卡在错误页。
    if (allowFallback && path.value) {
      await go('', false)
      return
    }
    error.value = err.response?.data?.error || '读取目录失败'
    return
  }
  loading.value = false
}

function selectFile(file) {
  selectedFile.value = file.path
  newName.value = ''
}

function choose(target) {
  path.value = target
  confirm()
}

function confirm() {
  if (props.mode === 'file' && !pendingPath.value) return
  emit('select', {
    path: pendingPath.value,
    // dirs 是「当前列表所属目录」的子目录；dirsOf 让调用方判断它是否就是刚选中的目录。
    dirs: dirs.value.map(item => item.name),
    dirsOf: listingPath.value,
    files: files.value.map(item => item.path)
  })
  close(false)
}
</script>

<style scoped>
.picker { display:flex; flex-direction:column; gap:12px; }
.picker-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:8px 10px; border:1px solid var(--line-soft); border-radius:10px; background:var(--bg-sunken); }
.crumbs { display:flex; align-items:center; flex-wrap:wrap; gap:2px; min-width:0; }
.crumb { padding:2px 6px; border:none; border-radius:6px; background:transparent; color:var(--accent); font-size:12.5px; font-weight:600; cursor:pointer; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.crumb:hover { background:var(--accent-soft); }
.crumb.current { color:var(--ink); cursor:default; }
.crumb.current:hover { background:transparent; }
.crumb-sep { color:var(--faint); font-size:12px; }

.picker-list { height:320px; overflow-y:auto; border:1px solid var(--line-soft); border-radius:10px; padding:6px; }
.picker-list::-webkit-scrollbar { width:8px; }
.picker-list::-webkit-scrollbar-thumb { background:var(--scroll-thumb); border-radius:4px; }
.list-label { padding:8px 10px 4px; color:var(--faint); font-size:11.5px; font-weight:700; letter-spacing:.3px; }
.row { display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:8px; cursor:pointer; transition:background .15s ease; }
.row:hover { background:var(--bg-hover); }
.row.is-file.selected { background:var(--accent-soft); box-shadow:inset 0 0 0 1px var(--accent-line); }
.row-icon { color:var(--muted); font-size:16px; flex-shrink:0; }
.row.is-dir .row-icon { color:var(--warn); }
.row-name { flex:1; min-width:0; color:var(--ink); font-size:13.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.row-marks { display:flex; gap:4px; flex-shrink:0; }
.mark { padding:1px 6px; border-radius:5px; font-style:normal; font-size:11px; font-weight:600; background:var(--chip-bg); color:var(--chip-ink); }
.mark-git { background:var(--warn-soft); color:var(--warn); }
.mark-playwright { background:var(--ok-soft); color:var(--ok); }
.mark-robot { background:var(--accent-soft); color:var(--accent-ink); }
.mark-python { background:var(--bg-sunken); color:var(--ink-2); }
.mark-node { background:var(--ok-soft); color:var(--ok); }
.row-size { flex-shrink:0; color:var(--faint); font-size:12px; font-variant-numeric:tabular-nums; }
.row-enter { flex-shrink:0; color:var(--faint); font-size:12px; }
.row-pick { flex-shrink:0; padding:3px 10px; border:1px solid var(--accent-line); border-radius:6px; background:var(--bg-card); color:var(--accent); font-size:12px; font-weight:600; }
.row-pick:hover { background:var(--accent); border-color:var(--accent); color:#fff; }
.picker-empty { display:flex; align-items:center; justify-content:center; height:100%; min-height:120px; padding:24px; color:var(--muted); font-size:13px; }
.picker-error { padding:12px; color:var(--danger); font-size:13px; line-height:1.6; }

.picker-new { display:flex; flex-direction:column; gap:6px; }
.picker-hint { color:var(--muted); font-size:12px; line-height:1.5; }

.picker-footer { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.picker-target { min-width:0; color:var(--ink-2); font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.picker-actions { display:flex; gap:8px; flex-shrink:0; }
</style>
