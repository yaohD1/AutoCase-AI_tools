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
            :key="item.key"
            type="button"
            :class="['rec-item', { 'is-active': selected && selected.key === item.key }]"
            :title="item.kind === 'draft' ? item.base_url : item.target_path"
            @click="openRecord(item)"
          >
            <span class="rec-name">{{ item.kind === 'draft' ? (item.title || '未命名录制') : baseName(item.target_path) }}</span>
            <span :class="['chip', item.kind === 'draft' ? 'is-warn' : recordStatusTone(item.status)]">
              {{ item.kind === 'draft' ? '录制存档' : recordStatusLabel(item.status) }}
            </span>
            <span class="rec-path">{{ item.kind === 'draft' ? `${item.step_count} 步 · ${shortUrl(item.base_url)}` : dirName(item.target_path) }}</span>
            <span class="rec-time">{{ formatDate(item.updated_at || item.created_at) }}</span>
          </button>
          <div v-if="!records.length && !loading" class="rec-empty">还没有记录</div>
        </div>
      </div>

      <!-- 右：详情。头部固定，中间预览自己滚，提交盒钉在底部 -->
      <div v-if="selected" class="rec-detail">
        <div class="rec-detail-head">
          <div class="rec-detail-main">
            <div class="rec-title">{{ selected.kind === 'draft' ? (selected.title || '未命名录制') : baseName(selected.target_path) }}</div>
            <div class="rec-path is-full" :title="selected.kind === 'draft' ? selected.base_url : selected.target_path">
              {{ selected.kind === 'draft' ? selected.base_url : dirName(selected.target_path) }}
            </div>
          </div>
          <div class="rec-detail-actions">
            <template v-if="selected.kind === 'draft'">
              <span class="chip is-warn">录制存档</span>
              <button type="button" class="list-act is-danger" :disabled="deletingDraft" @click="deleteDraft">删除</button>
              <el-button type="primary" size="small" :loading="resuming" :disabled="!selected.step_count" @click="resumeDraft">
                继续录制
              </el-button>
            </template>
            <template v-else>
              <span :class="['chip', recordStatusTone(selected.status)]">{{ recordStatusLabel(selected.status) }}</span>
              <button
                v-if="selected.status === 'generated' || selected.status === 'committed'"
                type="button"
                class="list-act is-danger"
                :disabled="reverting"
                @click="revertSelected"
              >{{ selected.status === 'generated' ? '撤销写入' : '回滚提交' }}</button>
            </template>
          </div>
        </div>

        <div class="rec-meta">
          <span>{{ frameworkName(selected.framework) }}</span>
          <span class="rec-sep">·</span>
          <template v-if="selected.kind === 'draft'">
            <span>{{ selected.step_count }} 个步骤</span>
            <span class="rec-sep">·</span>
            <span>更新于 {{ formatDate(selected.updated_at) }}</span>
          </template>
          <template v-else>
            <span>模型 {{ selected.model || '—' }}</span>
            <span class="rec-sep">·</span>
            <span>{{ formatDate(selected.created_at) }}</span>
            <span v-if="(selected.steps || []).length" class="rec-sep">·</span>
            <span v-if="(selected.steps || []).length">{{ selected.steps.length }} 个步骤</span>
          </template>
        </div>

        <div class="rec-scroll">
          <!-- 草稿：只渲染步骤清单。没有文件可预览、没有提交可做，把生成记录那套搬过来只会误导。 -->
          <template v-if="selected.kind === 'draft'">
            <div class="rec-note">
              这是一次未完成的录制。点「继续录制」会重新打开浏览器并恢复这些步骤，需要重新登录一次（存档不保存登录态）。
            </div>
            <div v-if="(selected.steps || []).length" class="rec-block">
              <div class="rec-label">已保存的步骤</div>
              <div class="rec-steps">
                <div v-for="(step, index) in selected.steps" :key="step.no" class="rec-step">
                  <span class="rec-step-no">{{ index + 1 }}</span>
                  <span class="rec-step-kind">{{ stepKindLabel(step.type) }}</span>
                  <span class="rec-step-text">{{ step.text || step.value || (step.target && step.target.alias) || '' }}</span>
                </div>
              </div>
            </div>
            <div v-else class="rec-empty">这条存档里没有步骤</div>
          </template>

          <template v-else>
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

          <!-- 平台自己写/复用的共享库：任务书里没有，但目录里有，必须列出来 -->
          <div v-if="selected.platform_files && selected.platform_files.length" class="rec-block">
            <div class="rec-label">平台另外写入的辅助文件（所有用例共用）</div>
            <div class="rec-reply">
              <div v-for="item in selected.platform_files" :key="item.path" class="rec-platform-row">
                <code>{{ item.path }}</code>
                <span class="rec-platform-state">{{ platformStateName(item.state) }}</span>
              </div>
            </div>
            <div class="rec-hint">内容没变就直接复用，不会重复写；模板更新过时才会覆盖</div>
          </div>
          </template>
        </div>

        <!-- 提交盒：只有未提交时出现，永远是页面里唯一的蓝色主行动 -->
        <div v-if="selected.kind !== 'draft' && selected.status === 'generated'" class="rec-commit">
          <div class="rec-commit-label">提交说明</div>
          <div class="rec-commit-row">
            <el-input v-model="commitMessage" class="form-input" maxlength="500" placeholder="提交说明（可选）" />
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
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api from '../api'
import { useTheme } from '../composables/useTheme'

// 续录会另起一个浏览器窗口，主题得跟着当前这个窗口走，否则面板会变回浅色。
const { theme: appTheme } = useTheme()

const FRAMEWORKS = [
  { value: 'robot-selenium', label: 'Robot Framework（SeleniumLibrary）' },
  { value: 'playwright-ts', label: 'Playwright TypeScript' }
]
const STEP_LABELS = {
  goto: '打开页面', click: '点击', fill: '输入', select: '选择', new_page: '新标签页',
  assert: '断言', wait: '等待', sleep: '固定等待', press: '按键', reload: '刷新页面',
  slider: '滑块验证', captcha: '验证码', pick: '拾取元素'
}

const records = ref([])
const loading = ref(false)
const selected = ref(null)
const commitMessage = ref('')
const committing = ref(false)
const reverting = ref(false)
const resuming = ref(false)
const deletingDraft = ref(false)
// 从录制页带 ?id= 跳过来时优先选中那一条
const route = useRoute()
const router = useRouter()
const wantedId = ref(typeof route.query.id === 'string' ? `generation:${route.query.id}` : '')

// 未提交只算生成记录：录制存档还没有文件，谈不上提交。
const pendingCount = computed(() => records.value.filter(item => item.kind !== 'draft' && item.status === 'generated').length)

onMounted(load)
// 这一页在 keep-alive 里：从录制页存档后返回，手里还是离开时那份列表和详情。
// 首次激活紧跟 onMounted（那时已经拉过），跳过；之后的每次激活都重新同步。
let activatedOnce = false
onActivated(() => {
  if (!activatedOnce) {
    activatedOnce = true
    return
  }
  load(true)
})

async function load(force = false) {
  loading.value = true
  try {
    // 两个来源合成一个列表：录制存档（没录完的）和生成记录（已写文件的）。
    // 排序按最后一次动过的时间：刚存过的存档应该排在最上面。
    const [gens, draftRows] = await Promise.all([
      api.listRecordingGenerations(),
      api.listRecordingDrafts()
    ])
    const merged = [
      ...(gens.data.generations || []).map(row => ({ ...row, kind: 'generation', key: `generation:${row.id}` })),
      ...(draftRows.data.drafts || []).map(row => ({ ...row, kind: 'draft', key: `draft:${row.id}` }))
    ].sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')))
    records.value = merged
    // 优先留住你正在看的那条：列表会重排，key 不会。只有它被删掉了才退回第一条，
    // 空列表才给空态——避免右半屏永远是「选择一条记录」。
    const kept = selected.value && merged.find(item => item.key === selected.value.key)
    const target = kept || merged.find(item => item.key === wantedId.value) || merged[0]
    if (!target) {
      selected.value = null
      return
    }
    // force：同一条也要重新取详情。只比 key 的话，覆盖保存过的存档永远显示旧步骤
    // （曾经就是「左边 5 步、右边 7 步」）。
    if (force || !selected.value || selected.value.key !== target.key) await openRecord(target)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function reload() {
  // 列表和详情一起重取，否则刷新只更新左半边。
  await load(true)
}

async function openRecord(item) {
  try {
    if (item.kind === 'draft') {
      const { data } = await api.getRecordingDraft(item.id)
      selected.value = { ...data, kind: 'draft', key: `draft:${data.id}` }
      return
    }
    const { data } = await api.getRecordingGeneration(item.id)
    // 后台刷新同一条记录时别覆盖用户已经改过的提交说明。
    const same = selected.value && selected.value.key === `generation:${data.id}`
    selected.value = { ...data, kind: 'generation', key: `generation:${data.id}` }
    if (!same) commitMessage.value = `录制生成：${data.title || data.target_path}`
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

// 继续录制：按存档重开一次录制，浏览器从起始地址打开、步骤原样恢复。
// 存档不带登录态，这里要提前说清楚，否则用户会以为打开就是登录后的页面。
async function resumeDraft() {
  const draft = selected.value
  try {
    await ElMessageBox.confirm(
      `将从 ${draft.base_url} 重新打开浏览器，并恢复 ${draft.step_count} 个步骤。\n存档不保存登录态，需要在浏览器里重新登录一次。`,
      '继续录制',
      { confirmButtonText: '继续录制', cancelButtonText: '取消', type: 'info' }
    )
  } catch {
    return
  }
  resuming.value = true
  try {
    await api.resumeRecordingDraft(draft.id, { theme: appTheme.value })
    ElMessage.success('已打开录制浏览器，步骤已恢复')
    router.push('/recording')
  } catch (error) {
    if (error && error.response && error.response.status === 409) {
      ElMessage.warning('已有正在进行的录制，请先停止再继续')
    } else {
      ElMessage.error(errorText(error))
    }
  } finally {
    resuming.value = false
  }
}

async function deleteDraft() {
  try {
    await ElMessageBox.confirm(
      '删除后这份存档就没了，已保存的步骤不能恢复。确定吗？',
      '删除录制存档',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  deletingDraft.value = true
  try {
    await api.deleteRecordingDraft(selected.value.id)
    ElMessage.success('已删除')
    selected.value = null
    wantedId.value = ''
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    deletingDraft.value = false
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
    await openRecord({ kind: 'generation', id: selected.value.id })
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
    await openRecord({ kind: 'generation', id: selected.value.id })
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

// 平台辅助文件的三种状态：新建、覆盖旧版本、内容一致直接复用。
function platformStateName(state) {
  return ({ created: '新建', updated: '已更新', reused: '已存在·复用' })[state] || state || ''
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

// 存档没有目标文件，第二行写「几步 · 哪个站点」，比一串完整 URL 好认
function shortUrl(value) {
  try {
    return new URL(value).host || String(value || '')
  } catch {
    return String(value || '')
  }
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
  background: var(--bg-card);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
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
.rec-list-scroll::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

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
.rec-item:hover { background: var(--bg-hover); }
.rec-item.is-active { background: var(--accent-soft); border-left-color: var(--accent); }

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
.rec-detail-actions .list-act:disabled { color: var(--faint); cursor: default; }

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
.rec-sep { color: var(--line-ctl); }

.rec-scroll {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
}
.rec-scroll::-webkit-scrollbar { width: 8px; }
.rec-scroll::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

/* 提示条：一块淡底 + 一条细边，不用 el-alert 那个带图标的灰盒子 */
.rec-note {
  margin-bottom: 12px;
  padding: 8px 11px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.55;
}
.rec-note.is-warn { background: var(--warn-soft); border: 1px solid var(--warn-line); color: var(--warn); }
.rec-note.is-ok { background: var(--ok-soft); border: 1px solid var(--ok-line); color: var(--ok); }

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
  background: var(--bg-code);
  color: var(--bg-code-ink);
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.65;
  white-space: pre;
}
.rec-code.is-empty {
  background: var(--bg-hover);
  border: 1px solid var(--line-faint);
  color: var(--faint);
  white-space: pre-wrap;
}

.rec-reply {
  padding: 9px 11px;
  border-radius: 8px;
  background: var(--bg-sunken);
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--ink-2);
  white-space: pre-wrap;
}

/* 平台辅助文件：路径一行一条，状态贴在右边，不让长路径把布局撑开 */
.rec-platform-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.8;
}
.rec-platform-row code {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rec-platform-state { flex: none; color: var(--muted); font-family: var(--mono); }
.rec-hint { margin-top: 6px; color: var(--faint); font-size: 11.5px; line-height: 1.5; }

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
.rec-commit-row :deep(.el-input__wrapper) { border-bottom-color: var(--line-field); }

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
