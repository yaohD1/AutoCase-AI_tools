<template>
  <div class="list-page ui-buttons rec-page">
    <!-- 页头：一句陈述句，右上角只有刷新；「返回录制」是导航，做成文字链接不占按钮位 -->
    <div class="list-head">
      <div class="list-head-main">
        <div class="eyebrow">录制 · RECORDS</div>
        <h1 class="list-title">生成记录</h1>
        <p class="list-sub">
          共 <b>{{ records.length }}</b> 条记录<template v-if="pendingCount">，其中 <b>{{ pendingCount }}</b> 条未提交</template>；
          可以预览文件内容、提交到 Git，或撤销这次写入。
        </p>
      </div>
      <div class="list-head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
        <button type="button" class="head-link" @click="$router.push('/recording')">返回录制</button>
      </div>
    </div>

    <div class="rec-shell">
      <!-- 左：记录列表。一条记录一行，文件名 + 目录/时间，状态用方片 -->
      <div class="rec-list">
        <div class="rec-list-head">
          <span>记录</span>
          <span class="rec-list-count">{{ records.length }}</span>
        </div>
        <div class="rec-list-scroll">
          <button
            v-for="item in records"
            :key="item.id"
            type="button"
            :class="['rec-item', { 'is-active': selected && selected.id === item.id }]"
            :title="item.target_path"
            @click="openRecord(item.id)"
          >
            <span class="rec-name">{{ baseName(item.target_path) }}</span>
            <span :class="['chip', recordStatusTone(item.status)]">{{ recordStatusLabel(item.status) }}</span>
            <span class="rec-path">{{ dirName(item.target_path) }}</span>
            <span class="rec-time">{{ formatDate(item.created_at) }}</span>
          </button>
          <div v-if="!records.length && !loading" class="rec-empty">还没有生成记录</div>
        </div>
      </div>

      <!-- 右：详情。头部固定，中间预览自己滚，提交盒钉在底部 -->
      <div v-if="selected" class="rec-detail">
        <div class="rec-detail-head">
          <div class="rec-detail-main">
            <div class="rec-title">{{ baseName(selected.target_path) }}</div>
            <div class="rec-path is-full" :title="selected.target_path">{{ dirName(selected.target_path) }}</div>
          </div>
          <div class="rec-detail-actions">
            <span :class="['chip', recordStatusTone(selected.status)]">{{ recordStatusLabel(selected.status) }}</span>
            <button
              v-if="selected.status === 'generated' || selected.status === 'committed'"
              type="button"
              class="list-act is-danger"
              :disabled="reverting"
              @click="revertSelected"
            >{{ selected.status === 'generated' ? '撤销写入' : '回滚提交' }}</button>
          </div>
        </div>

        <div class="rec-meta">
          <span>{{ frameworkName(selected.framework) }}</span>
          <span class="rec-sep">·</span>
          <span>模型 {{ selected.model || '—' }}</span>
          <span class="rec-sep">·</span>
          <span>{{ formatDate(selected.created_at) }}</span>
          <span v-if="(selected.steps || []).length" class="rec-sep">·</span>
          <span v-if="(selected.steps || []).length">{{ selected.steps.length }} 个步骤</span>
        </div>

        <div class="rec-scroll">
          <div v-if="selected.changed_since_generation && selected.status === 'generated'" class="rec-note is-warn">
            文件在生成后已被改动或删除，提交和撤销都会被拒绝。请先核对文件内容。
          </div>
          <div v-if="selected.status === 'committed'" class="rec-note is-ok">
            已提交 <span class="mono">{{ (selected.commit_hash || '').slice(0, 8) }}</span>
            <template v-if="selected.commit_message">· {{ selected.commit_message }}</template>
          </div>

          <div v-if="(selected.steps || []).length" class="rec-block">
            <div class="rec-label">录制的步骤</div>
            <div class="rec-steps">
              <div v-for="(step, index) in selected.steps" :key="step.no" class="rec-step">
                <span class="rec-step-no">{{ index + 1 }}</span>
                <span class="rec-step-kind">{{ stepKindLabel(step.type) }}</span>
                <span class="rec-step-text">{{ step.text || step.value || '' }}</span>
              </div>
            </div>
          </div>

          <div class="rec-block">
            <div class="rec-label">
              <span>文件内容{{ selected.truncated ? '（已截断）' : '' }}</span>
              <button v-if="selected.file_exists" type="button" class="form-label-act" @click="copyContent">复制</button>
            </div>
            <pre :class="['rec-code', { 'is-empty': !selected.file_exists }]">{{ selected.file_exists ? selected.content : '文件当前不存在（生成后可能已被删除或撤回）' }}</pre>
          </div>

          <div v-if="selected.reply" class="rec-block">
            <div class="rec-label">生成 Agent 的说明</div>
            <div class="rec-reply">{{ selected.reply }}</div>
          </div>
        </div>

        <!-- 提交盒：只有未提交时出现，永远是页面里唯一的蓝色主行动 -->
        <div v-if="selected.status === 'generated'" class="rec-commit">
          <div class="rec-commit-label">提交说明</div>
          <div class="rec-commit-row">
            <el-input v-model="commitMessage" class="form-input" maxlength="500" placeholder="这次生成的简短说明" />
            <el-button type="primary" :loading="committing" :disabled="selected.changed_since_generation" @click="commitSelected">
              提交到 Git
            </el-button>
          </div>
        </div>
      </div>

      <div v-else class="rec-detail-empty">从左侧选择一条记录</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api from '../api'

const FRAMEWORKS = [
  { value: 'robot-selenium', label: 'Robot Framework（SeleniumLibrary）' },
  { value: 'playwright-ts', label: 'Playwright TypeScript' }
]
const STEP_LABELS = {
  goto: '打开页面', click: '点击', fill: '输入', select: '选择', new_page: '新标签页',
  assert: '断言', wait: '等待', sleep: '固定等待'
}

const records = ref([])
const loading = ref(false)
const selected = ref(null)
const commitMessage = ref('')
const committing = ref(false)
const reverting = ref(false)
// 从录制页带 ?id= 跳过来时优先选中那一条
const route = useRoute()
const wantedId = ref(typeof route.query.id === 'string' ? route.query.id : '')

const pendingCount = computed(() => records.value.filter(item => item.status === 'generated').length)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const { data } = await api.listRecordingGenerations()
    records.value = data.generations || []
    // 默认选中最新一条：避免右半屏永远是「选择一条记录」的空态。
    const target = records.value.find(item => item.id === wantedId.value) || records.value[0]
    if (target && (!selected.value || selected.value.id !== target.id)) await openRecord(target.id)
    if (!target) selected.value = null
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function reload() {
  if (selected.value) wantedId.value = selected.value.id
  await load()
}

async function openRecord(id) {
  try {
    const { data } = await api.getRecordingGeneration(id)
    selected.value = data
    commitMessage.value = `录制生成：${data.title || data.target_path}`
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function copyContent() {
  try {
    await navigator.clipboard.writeText(selected.value.content || '')
    ElMessage.success('已复制文件内容')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

async function commitSelected() {
  committing.value = true
  try {
    await api.commitRecordingGeneration(selected.value.id, commitMessage.value)
    ElMessage.success('已提交')
    await openRecord(selected.value.id)
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    committing.value = false
  }
}

async function revertSelected() {
  const committed = selected.value.status === 'committed'
  try {
    await ElMessageBox.confirm(
      committed ? '将生成一个回滚提交，撤销这次录制生成的文件。确定吗？' : '将删除新写入的文件，或恢复生成前的原内容。确定吗？',
      committed ? '回滚提交' : '撤销写入',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  reverting.value = true
  try {
    await api.revertRecordingGeneration(selected.value.id)
    ElMessage.success('已撤销')
    await openRecord(selected.value.id)
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    reverting.value = false
  }
}

function frameworkName(value) {
  return (FRAMEWORKS.find(f => f.value === value) || {}).label || (value || '—')
}

function stepKindLabel(type) { return STEP_LABELS[type] || type }

function recordStatusLabel(status) {
  return ({ generated: '未提交', committed: '已提交', reverted: '已撤销' })[status] || status
}

// 状态只借三档颜色：待处理黄、完成绿、撤销灰。没有新的语义色
function recordStatusTone(status) {
  return ({ generated: 'is-warn', committed: 'is-ok', reverted: 'is-mute' })[status] || ''
}

// 同目录下文件名才是区分点，所以列表只显示文件名，目录退成第二行的小字
function baseName(path) {
  const text = String(path || '')
  const cut = text.lastIndexOf('/')
  return cut >= 0 ? text.slice(cut + 1) : text
}

function dirName(path) {
  const text = String(path || '')
  const cut = text.lastIndexOf('/')
  return cut >= 0 ? `${text.slice(0, cut)}/` : '仓库根目录'
}

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  const pad = n => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function errorText(error) {
  if (error && error.response && error.response.data && error.response.data.error) return error.response.data.error
  return (error && error.message) || '操作失败'
}
</script>

<style scoped>
/* 一屏两栏：左记录（320）右详情。高度锁在视口内，列表和预览各自滚，
   提交盒固定在右下角 —— 文件很长时也不用滚到底才能提交。 */
.rec-page { height: calc(100dvh - 132px); }
.head-link {
  border: 0;
  background: none;
  padding: 0 2px;
  font: inherit;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.head-link:hover { color: var(--accent); }

.rec-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  background: #fff;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  overflow: hidden;
}

/* ---------- 左：记录列表 ---------- */
.rec-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--line-soft);
}

.rec-list-head {
  flex: none;
  display: flex;
  align-items: baseline;
  gap: 6px;
  height: 38px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line-soft);
  font-size: 11.5px;
  color: var(--muted);
}
.rec-list-count {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
}

.rec-list-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px 6px 8px;
}
.rec-list-scroll::-webkit-scrollbar { width: 8px; }
.rec-list-scroll::-webkit-scrollbar-thumb { background: #e6e9ef; border-radius: 4px; }

.rec-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  row-gap: 3px;
  width: 100%;
  padding: 8px 9px 7px 8px;
  border: 0;
  border-left: 2px solid transparent;
  border-radius: 0 8px 8px 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background .14s ease, border-color .14s ease;
}
.rec-item:hover { background: #fafbfc; }
.rec-item.is-active { background: #f5f8fd; border-left-color: var(--accent); }

.rec-name {
  min-width: 0;
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-item.is-active .rec-name { color: var(--ink); font-weight: 500; }
.rec-item .chip { justify-self: end; }
.rec-path {
  min-width: 0;
  font-size: 11px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-time {
  justify-self: end;
  font-size: 11px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.rec-empty {
  padding: 30px 12px;
  text-align: center;
  font-size: 12px;
  color: var(--faint);
}

/* ---------- 右：详情 ---------- */
.rec-detail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 14px 18px 12px 20px;
}

.rec-detail-head {
  flex: none;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.rec-detail-main { min-width: 0; }
.rec-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -.01em;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-path.is-full {
  margin-top: 2px;
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--faint);
}
.rec-detail-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
}
.rec-detail-actions .list-act:disabled { color: #cdd3db; cursor: default; }

.rec-meta {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--muted);
  overflow: hidden;
  white-space: nowrap;
}
.rec-sep { color: #d8dde5; }

.rec-scroll {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
}
.rec-scroll::-webkit-scrollbar { width: 8px; }
.rec-scroll::-webkit-scrollbar-thumb { background: #e6e9ef; border-radius: 4px; }

/* 提示条：一块淡底 + 一条细边，不用 el-alert 那个带图标的灰盒子 */
.rec-note {
  margin-bottom: 12px;
  padding: 8px 11px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.55;
}
.rec-note.is-warn { background: var(--warn-soft); border: 1px solid #f2e3c9; color: var(--warn); }
.rec-note.is-ok { background: var(--ok-soft); border: 1px solid #d9ecdf; color: var(--ok); }

.rec-block { margin-bottom: 14px; }
.rec-label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 11.5px;
  color: var(--muted);
}

.rec-steps { border-top: 1px solid var(--line-faint); }
.rec-step {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line-faint);
  font-size: 12.5px;
  color: var(--ink-2);
}
.rec-step-no {
  flex: none;
  width: 14px;
  text-align: right;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
}
.rec-step-kind {
  flex: none;
  width: 56px;
  font-size: 11.5px;
  color: var(--muted);
}
.rec-step-text { min-width: 0; word-break: break-word; }

.rec-code {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  padding: 12px 14px;
  border-radius: 10px;
  background: #0f172a;
  color: #dde5f0;
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.65;
  white-space: pre;
}
.rec-code.is-empty {
  background: #fafbfc;
  border: 1px solid var(--line-faint);
  color: var(--faint);
  white-space: pre-wrap;
}

.rec-reply {
  padding: 9px 11px;
  border-radius: 8px;
  background: #f7f8fa;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--ink-2);
  white-space: pre-wrap;
}

/* 提交盒：钉在底栏，和上方内容用一条细线分开 */
.rec-commit {
  flex: none;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}
.rec-commit-label {
  margin-bottom: 1px;
  font-size: 11.5px;
  color: var(--muted);
}
.rec-commit-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.rec-commit-row :deep(.form-input) { flex: 1; }
.rec-commit-row :deep(.el-input__wrapper) { border-bottom-color: rgba(0, 0, 0, .09); }

.rec-detail-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12.5px;
  color: var(--faint);
}

/* 窄屏：上下排列，列表限高，详情自己滚 */
@media (max-width: 1080px) {
  .rec-page { height: auto; }
  .rec-shell { grid-template-columns: minmax(0, 1fr); }
  .rec-list {
    border-right: 0;
    border-bottom: 1px solid var(--line-soft);
    max-height: 240px;
  }
  .rec-detail { padding: 14px 16px 12px; }
  .rec-scroll { overflow: visible; }
}
</style>
