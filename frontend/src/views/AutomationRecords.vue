<template>
  <div class="list-page records-page">
    <!-- 页头：任务记录从「配置卡下面的列表」升成独立页，主操作只剩「发起新任务」 -->
    <div class="list-head">
      <div class="list-head-main">
        <div class="eyebrow">AGENT · RUNS</div>
        <h1 class="list-title">任务记录</h1>
        <p class="list-sub">
          共 <b>{{ rows.length }}</b> 次任务<template v-if="runningCount">，<b>{{ runningCount }}</b> 次执行中</template>
          <template v-if="rows.length"> · 最近一次 {{ formatTime(rows[0].created_at) }}</template>
        </p>
      </div>
      <div class="list-head-actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="goScripts">发起新任务</el-button>
      </div>
    </div>

    <div class="list-filters">
      <el-select v-model="projectId" class="sel-project" placeholder="选择项目" @change="onProjectChange">
        <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
      </el-select>
      <el-select v-model="statusFilter" class="sel-status" placeholder="全部状态">
        <el-option v-for="opt in STATUS_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
      </el-select>
      <span class="list-filter-note">筛选后 <b>{{ filteredRows.length }}</b> 条<template v-if="rows.length > PAGE_SIZE"> · 当前页 <b>{{ pageRows.length }}</b> 条</template></span>
    </div>

    <div class="list-shell">
      <el-table :data="pageRows" v-loading="loading" style="width:100%" empty-text="这个项目还没有任务记录" @row-click="openDetail">
        <el-table-column label="状态" width="98">
          <template #default="{ row }">
            <span :class="['run-chip', `is-${statusTone(row.status)}`]"><i></i>{{ statusLabel(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="需求" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="list-cell-strong">{{ row.requirement || '（按用例生成）' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="阶段" width="134">
          <template #default="{ row }">
            <span class="cell-quiet">{{ stageLabel(row.stage) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="批次" width="140">
          <template #default="{ row }">
            <span class="cell-mono">{{ shortKey(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="文件" width="64" align="right">
          <template #default="{ row }">
            <span class="cell-num">{{ (row.files || []).length }}</span>
          </template>
        </el-table-column>
        <el-table-column label="修复" width="64" align="right">
          <template #default="{ row }">
            <span class="cell-num">{{ row.heal_attempts || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="130">
          <template #default="{ row }">
            <span class="cell-quiet">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="132" fixed="right">
          <template #default="{ row }">
            <button type="button" class="list-act" @click.stop="openDetail(row)">查看详情</button>
            <button
              v-if="row.status === 'completed'"
              type="button"
              class="list-act"
              :disabled="downloadingId === row.id"
              @click.stop="download(row)"
            >{{ downloadingId === row.id ? '打包中' : '下载 ZIP' }}</button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="filteredRows.length > PAGE_SIZE" class="list-pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="PAGE_SIZE"
          :total="filteredRows.length"
          layout="total, prev, pager, next"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const PAGE_SIZE = 10
const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'running', label: '执行中' },
  { value: 'completed', label: '完成' },
  { value: 'failed', label: '失败' },
  { value: 'interrupted', label: '已中断' }
]
const STAGE_LABELS = {
  queued: '等待启动', starting: '启动中', orchestrator: '总控协调', planner: '规划探索',
  generator: '生成脚本', healer: '执行与修复', test_run: '执行测试', completed: '完成',
  failed: '失败', interrupted: '已中断'
}

const route = useRoute()
const router = useRouter()
const projects = ref([])
const projectId = ref(typeof route.query.project_id === 'string' ? route.query.project_id : '')
const rows = ref([])
const loading = ref(false)
const statusFilter = ref('all')
const page = ref(1)
const downloadingId = ref('')
// 有任务在跑时静默刷新，让状态和阶段自己往前走，不用手动点刷新。
let poll = null

onMounted(loadProjects)
onActivated(startPoll)
onDeactivated(stopPoll)
onUnmounted(stopPoll)

watch(statusFilter, () => { page.value = 1 })

const filteredRows = computed(() => {
  if (statusFilter.value === 'all') return rows.value
  return rows.value.filter(item => item.status === statusFilter.value)
})
const pageRows = computed(() => filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const runningCount = computed(() => rows.value.filter(item => ['running', 'pending'].includes(item.status)).length)

async function loadProjects() {
  try {
    const { data } = await api.getProjects()
    projects.value = data.projects || []
    if (!projectId.value) projectId.value = projects.value[0]?.id || ''
    await load()
  } catch {
    ElMessage.error('加载项目失败')
  }
}

async function onProjectChange() {
  rows.value = []
  page.value = 1
  await load()
}

async function load() {
  if (!projectId.value) return
  loading.value = true
  try {
    const { data } = await api.getAutomationGenerations(projectId.value, 100)
    rows.value = data.generations || []
    // 选中的状态已经没有记录时，退回全部，避免停在空白表格上
    if (statusFilter.value !== 'all' && !rows.value.some(item => item.status === statusFilter.value)) statusFilter.value = 'all'
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '加载任务记录失败')
  } finally {
    loading.value = false
  }
}

function startPoll() {
  if (poll) return
  poll = window.setInterval(() => { if (runningCount.value) load() }, 5000)
}
function stopPoll() {
  if (poll) { window.clearInterval(poll); poll = null }
}

function goScripts() {
  router.push({ path: '/scripts', query: projectId.value ? { project_id: projectId.value } : {} })
}

function openDetail(row) {
  router.push({ path: `/scripts/generations/${row.id}`, query: { project_id: row.project_id || projectId.value } })
}

async function download(row) {
  if (!row?.id || downloadingId.value) return
  downloadingId.value = row.id
  try {
    const response = await api.downloadAutomationGeneration(row.id, row.project_id || projectId.value)
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.download = `automation_${row.run_key || row.id}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  } finally {
    downloadingId.value = ''
  }
}

function statusLabel(status) {
  return ({ completed: '完成', running: '执行中', failed: '失败', pending: '等待中', interrupted: '已中断' })[status] || status || '-'
}
function statusTone(status) {
  return ({ completed: 'ok', running: 'run', failed: 'danger', pending: 'mute', interrupted: 'danger' })[status] || 'mute'
}
function stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || '-'
}
function shortKey(row) {
  const key = row.run_key || row.id || ''
  return key.length > 18 ? `${key.slice(0, 8)}…${key.slice(-6)}` : key
}
// 后端存的是 UTC（无时区），补 Z 再转本地，否则整列会差 8 小时。
function parseUtc(value) {
  if (!value) return null
  let text = String(value).trim()
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(text)) text += 'Z'
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? null : date
}
function formatTime(value) {
  const date = parseUtc(value)
  if (!date) return '-'
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}
</script>

<style scoped>
.records-page { max-width: 1320px; }

/* 状态小方片：比 el-tag 安静，颜色只用于区分状态本身 */
.run-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 20px;
  padding: 0 7px;
  border-radius: var(--radius-chip);
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}
.run-chip i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.run-chip.is-ok { background: var(--ok-soft); color: var(--ok); }
.run-chip.is-run { background: var(--accent-soft); color: #0b62c4; }
.run-chip.is-run i { animation: chip-pulse 1.8s ease-in-out infinite; }
.run-chip.is-danger { background: #fdeeed; color: #c9342b; }
.run-chip.is-mute { background: #f4f6f9; color: var(--muted); }
@keyframes chip-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(11, 98, 196, .35); }
  50% { box-shadow: 0 0 0 3px rgba(11, 98, 196, 0); }
}

.cell-quiet { color: var(--muted); font-size: 12.5px; }
.cell-mono { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
.cell-num { color: var(--ink-2); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.sel-status { width: 128px; }

/* 行可以点开详情，给一点可点的暗示 */
:deep(.el-table__row) { cursor: pointer; }
.list-act:disabled { color: #c6ccd5; cursor: default; }
</style>
