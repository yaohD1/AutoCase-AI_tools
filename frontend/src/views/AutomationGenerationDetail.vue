<template>
  <div class="generation-detail-page">
    <el-card class="status-card">
      <div class="status-summary">
        <el-tag :type="statusType(generation?.status)" size="large" effect="light" class="status-tag">{{ statusLabel(generation?.status) }}</el-tag>
        <span class="run-title">Agent 执行详情</span>
        <span class="stage-label">当前阶段 · {{ stageLabel(generation?.stage) }}</span>
        <span class="meta-divider"></span>
        <span class="status-meta run-key" :title="generation?.run_key">批次 {{ generation?.run_key || generation?.id?.slice(0, 8) || '-' }}</span>
        <span class="meta-divider"></span>
        <span class="status-meta url" :title="generation?.config?.base_url">{{ generation?.config?.base_url || '-' }}</span>
        <span class="meta-divider"></span>
        <span class="status-meta">修复 <b>{{ generation?.heal_attempts || 0 }}</b> 次</span>
        <div class="header-actions">
          <el-button v-if="polling" text type="primary" size="small" :icon="Refresh" @click="load">刷新</el-button>
          <el-button v-if="taskRunning" type="danger" size="small" :icon="CircleClose" :loading="cancelling" @click="cancelGeneration">停止任务</el-button>
          <el-button v-if="canRetry" type="primary" size="small" :icon="RefreshRight" :loading="retrying" @click="retryGeneration">重试</el-button>
          <el-button size="small" :icon="Back" @click="$router.push({ path: '/scripts', query: { project_id: projectId } })">返回任务记录</el-button>
          <el-button v-if="generation?.status === 'completed'" type="primary" size="small" :icon="Download" @click="download">下载 ZIP</el-button>
        </div>
      </div>
      <div class="progress-row">
        <el-progress class="run-progress" :class="{ 'is-running': taskRunning }" :percentage="stagePercent" :status="progressStatus" :stroke-width="8" :show-text="false" />
        <span :class="['progress-num', progressStatus]">{{ stagePercent }}<i>%</i></span>
        <button v-if="requirementLong" type="button" class="req-toggle" @click="requirementExpanded = !requirementExpanded">
          {{ requirementExpanded ? '收起需求' : '展开需求' }}<span :class="['req-caret', { up: requirementExpanded }]"></span>
        </button>
      </div>
      <p :class="['requirement', { 'is-collapsed': !requirementExpanded }]">{{ generation?.requirement || '加载任务中...' }}</p>
      <el-alert v-if="generation?.error" type="error" :closable="false" show-icon>{{ generation.error }}</el-alert>
    </el-card>

    <el-card class="workspace-card">
      <div class="execution-layout">
        <aside class="artifact-sidebar">
          <div class="sidebar-heading">文件与记录</div>
          <div class="artifact-group">
            <div class="artifact-group-title"><span>测试计划</span><span>{{ planArtifacts.length }}</span></div>
            <button v-for="item in planArtifacts" :key="item.path" class="artifact-item" @click="openArtifact(item)">
              <span>{{ item.path }}</span><small>{{ artifactKindLabel(item.kind) }}</small>
            </button>
            <div v-if="!planArtifacts.length" class="sidebar-empty">尚未生成</div>
          </div>
          <div class="artifact-group">
            <div class="artifact-group-title"><span>测试脚本</span><span>{{ scriptArtifacts.length }}</span></div>
            <button v-for="item in scriptArtifacts" :key="item.path" class="artifact-item" @click="openArtifact(item)">
              <span>{{ item.path }}</span><small>{{ artifactKindLabel(item.kind) }}</small>
            </button>
            <div v-if="!scriptArtifacts.length" class="sidebar-empty">尚未生成</div>
          </div>
          <div class="artifact-group">
            <div class="artifact-group-title"><span>修复记录</span><span>{{ healRecords.length }}</span></div>
            <button v-for="(record, index) in healRecords" :key="record.index || index" class="artifact-item" @click="openHealRecord(record, index)">
              <span>修复 #{{ index + 1 }}</span><small>改动 {{ (record.changes || []).length }} 文件</small>
            </button>
            <div v-if="!healRecords.length" class="sidebar-empty">暂无修复记录（旧任务无结构化数据）</div>
          </div>
          <div class="artifact-group">
            <div class="artifact-group-title"><span>Git 变更</span><span>{{ generation?.files?.length || 0 }}</span></div>
            <div class="artifact-scroll">
              <button v-for="file in generation?.files || []" :key="file.path" class="artifact-item" :disabled="!isReadableArtifact(file.path)" @click="openArtifactByFile(file)">
                <span>{{ file.path }}</span><small>{{ file.status }}</small>
              </button>
            </div>
            <div v-if="!generation?.files?.length" class="sidebar-empty">暂无变更</div>
          </div>
          <div class="commit-box">
            <div class="field-hint">提交/撤回只作用于本次 Agent 产生的计划、脚本和允许的 Playwright 配置，不涉及其他工作区修改，也不会 push。</div>
            <el-input v-model="commitMessage" :disabled="!canCommit" placeholder="test: generate Playwright tests" />
            <el-button type="primary" :icon="Select" :loading="committing" :disabled="!canCommit" @click="commit">创建本地 Commit</el-button>
            <el-button type="danger" :icon="RefreshLeft" :loading="reverting" :disabled="!canRevert" @click="revertGeneration">撤回修改</el-button>
            <div v-if="generation?.commit_status === 'committed'" class="commit-success">已提交 {{ generation.commit_hash }}</div>
            <div v-else-if="generation?.commit_status === 'reverted'" class="commit-reverted">已撤回本批次改动（可从 ZIP 下载找回内容）</div>
          </div>
        </aside>

        <section class="log-panel">
          <div class="stage-track">
            <div v-for="item in stages" :key="item.key" :class="['stage-item', stageClass(item.key)]">
              <span class="stage-dot"><svg viewBox="0 0 12 12" class="dot-check"><path d="M2.6 6.3 4.8 8.4 9.4 3.9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
              <span class="stage-name">{{ item.label }}</span>
            </div>
          </div>
          <div class="log-toolbar">
            <div class="log-toolbar-status">
              <span :class="['log-dot', { active: polling, failed: generation?.status === 'failed' }]" />
              <strong>{{ logStatusLabel }}</strong>
              <span class="log-chip">{{ events.length }} 条</span>
              <span v-if="logFilter === 'agent' && mutedCount" class="log-muted">已隐藏 {{ mutedCount }} 条工具/系统</span>
              <span class="log-meta">{{ durationLabel }} · {{ latestLogLabel }}</span>
            </div>
            <div class="log-toolbar-actions">
              <div class="log-filter">
                <button type="button" :class="['filter-btn', { on: logFilter === 'agent' }]" @click="setFilter('agent')">Agent 输出</button>
                <button type="button" :class="['filter-btn', { on: logFilter === 'all' }]" @click="setFilter('all')">全部</button>
              </div>
              <button type="button" :class="['follow-toggle', { on: autoFollow }]" :title="autoFollow ? '已开启自动跟随，点击暂停' : '已暂停跟随，点击回到底部并恢复'" @click="toggleFollow">
                <span class="follow-ind"></span>自动跟随
              </button>
              <el-button v-if="!autoFollow" text type="primary" size="small" @click="scrollToBottom(true)">回到底部</el-button>
            </div>
          </div>
          <div ref="logContainer" class="log-stream" @scroll="handleLogScroll">
            <div v-for="group in groupedEvents" :key="group.key" class="agent-block">
              <div class="agent-block-head">
                <el-tag size="small" effect="dark" :type="stageTagType(group.stage)">{{ stageLabel(group.stage) }}</el-tag>
                <span class="agent-block-count">{{ group.events.length }} 条</span>
              </div>
              <div class="agent-block-body">
                <div v-for="event in group.events" :key="event.sequence" :class="['log-line', `log-line-${eventType(event)}`, { 'log-line-typing': event._typing, 'log-line-speech': isAgentSpeech(event) }]">
                  <span class="log-line-type">{{ eventTypeLabel(event) }}</span>
                  <pre>{{ event.message }}<span v-if="event._typing" class="type-cursor" /></pre>
                  <time>{{ formatTime(event.created_at) }}</time>
                </div>
              </div>
            </div>
            <el-empty v-if="!events.length && !waitingForOutput" description="等待 OpenCode 输出日志" />
            <el-empty v-else-if="!groupedEvents.length && !waitingForOutput" description="当前无 Agent 输出，可切换到「全部」查看工具与系统日志" />
            <div v-if="waitingForOutput" class="log-waiting">
              <span class="cc-spinner">{{ spinnerFrame }}</span>
              <span class="cc-text">{{ waitingText }}</span>
            </div>
          </div>
        </section>
      </div>
    </el-card>

    <el-dialog v-model="artifactDialog.visible" :title="artifactDialog.title" width="min(1100px, 92vw)" top="5vh" destroy-on-close>
      <div class="dialog-meta">
        <el-tag size="small">{{ artifactDialog.kindLabel }}</el-tag>
        <el-tag v-if="artifactDialog.redacted" size="small" type="warning">已脱敏</el-tag>
        <span>{{ artifactDialog.path }}</span>
      </div>
      <div v-if="artifactDialog.loading" class="dialog-loading">正在读取内容...</div>
      <div v-else-if="artifactDialog.mode === 'heal'" class="heal-view">
        <div class="heal-section-title">失败原因</div>
        <pre class="heal-reason">{{ artifactDialog.heal?.reason || '（无文字说明）' }}</pre>
        <div class="heal-section-title">代码修改（{{ (artifactDialog.heal?.changes || []).length }} 个文件）</div>
        <div v-if="!(artifactDialog.heal?.changes || []).length" class="heal-empty">本次修复未改动代码。</div>
        <div v-for="change in artifactDialog.heal?.changes || []" :key="change.path" class="diff-file">
          <div class="diff-file-head">
            <span class="diff-path">{{ change.path }}</span>
            <el-tag size="small" :type="change.status === 'deleted' ? 'danger' : change.status === 'added' ? 'success' : 'warning'">{{ changeStatusLabel(change.status) }}</el-tag>
          </div>
          <pre class="diff-view"><span v-for="(line, i) in diffLines(change.diff)" :key="i" :class="['diff-line', line.type]">{{ line.text || ' ' }}</span></pre>
        </div>
      </div>
      <pre v-else class="source-view">{{ artifactDialog.content || '暂无内容' }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, RefreshRight, RefreshLeft, CircleClose, Download, Back, Select } from '@element-plus/icons-vue'
import api from '../api'

const route = useRoute()
const projectId = computed(() => route.query.project_id || '')
const generation = ref(null)
const events = ref([])
const artifacts = ref([])
const autoFollow = ref(true)
const logContainer = ref(null)
const artifactDialog = ref({ visible: false, loading: false, title: '', path: '', kindLabel: '', content: '', mode: 'text', heal: null, redacted: false })
let artifactRequestId = 0
const commitMessage = ref('test: generate Playwright tests')
const committing = ref(false)
const cancelling = ref(false)
const retrying = ref(false)
const reverting = ref(false)
const loading = ref(false)
const eventCursor = ref(0)
const heartbeat = ref(Date.now())
const eventsSyncFailed = ref(false)
const eventSyncFailures = ref(0)
const nextEventSyncAt = ref(0)
let timer = null
const revealedCount = ref(0)
const typingChars = ref(0)
let typeRAF = null
let lastTypeTs = 0
let charAccum = 0
let interLineUntil = 0
let lastTypeScrollTs = 0
const requirementExpanded = ref(false)
const logFilter = ref('agent')

const stages = [
  { key: 'orchestrator', label: '启动协调' },
  { key: 'planner', label: '规划探索' },
  { key: 'generator', label: '生成脚本' },
  { key: 'healer', label: '失败修复' },
  { key: 'completed', label: '完成' }
]
const taskRunning = computed(() => ['running', 'pending'].includes(generation.value?.status) || (['interrupted', 'failed'].includes(generation.value?.status) && generation.value?.process_id))
const polling = computed(() => taskRunning.value || eventsSyncFailed.value)
// 终态（失败/中止）且无残留进程时，可在当前任务上就地重跑（复用同一记录，不新建任务）。
const canRetry = computed(() => !taskRunning.value && ['failed', 'interrupted'].includes(generation.value?.status))
const stagePercent = computed(() => {
  const status = generation.value?.status
  if (status === 'completed') return 100
  const index = stages.findIndex(item => item.key === generation.value?.stage)
  return Math.max(8, Math.min(status === 'failed' ? 95 : 92, (index + 1) * 20))
})
const progressStatus = computed(() => {
  const status = generation.value?.status
  return status === 'failed' || status === 'interrupted' ? 'exception' : status === 'completed' ? 'success' : ''
})
const requirementLong = computed(() => (generation.value?.requirement || '').length > 90)
const durationLabel = computed(() => formatDuration(generation.value?.started_at, generation.value?.completed_at))
const latestEvent = computed(() => events.value.length ? events.value[events.value.length - 1] : null)
const logStatusLabel = computed(() => {
  if (!taskRunning.value && eventsSyncFailed.value) return '等待日志同步'
  if (!taskRunning.value) return generation.value?.status === 'failed' ? '任务已失败' : '日志已结束'
  if (generation.value?.status === 'interrupted') return '正在停止任务'
  const lastTime = latestEvent.value ? (parseUtc(latestEvent.value.created_at)?.getTime() || 0) : 0
  const idleSeconds = lastTime ? Math.floor((heartbeat.value - lastTime) / 1000) : null
  if (idleSeconds !== null && idleSeconds >= 20) return `Agent 执行中…（${stageLabel(generation.value?.stage)}，${idleSeconds}s 无新日志）`
  return '正在接收日志'
})
const latestLogLabel = computed(() => latestEvent.value ? `最近 ${formatDate(latestEvent.value.created_at)}` : '暂无日志')
const planArtifacts = computed(() => artifacts.value.filter(item => item.kind === 'plan'))
const scriptArtifacts = computed(() => artifacts.value.filter(item => item.kind === 'script'))
// 修复记录改为后端结构化数据（每次 healer 修复一条：失败原因 + 逐文件 before/after diff），
// 不再用流式 text 事件（那会把同一条消息的增量碎片当成多条记录）。
const healRecords = computed(() => generation.value?.heal_records || [])
const visibleEvents = computed(() => {
  const out = events.value.slice(0, revealedCount.value)
  const cur = events.value[revealedCount.value]
  if (cur && typingChars.value > 0) out.push({ ...cur, message: (cur.message || '').slice(0, typingChars.value), _typing: true })
  return out
})
function isAgentSpeech(event) {
  const message = event.message || ''
  return event.event_type === 'text' || (event.event_type === 'diagnostic' && (message.startsWith('[推理]') || message.startsWith('[思考]')))
}
function isAgentEvent(event) {
  return isAgentSpeech(event) || ['error', 'failed', 'completed', 'interrupted', 'model_error'].includes(event.event_type)
}
const filteredEvents = computed(() => logFilter.value === 'all' ? visibleEvents.value : visibleEvents.value.filter(isAgentEvent))
const mutedCount = computed(() => logFilter.value === 'all' ? 0 : visibleEvents.value.filter(event => !isAgentEvent(event)).length)
const groupedEvents = computed(() => {
  const groups = []
  for (const event of filteredEvents.value) {
    const stage = event.stage || 'orchestrator'
    const last = groups[groups.length - 1]
    if (last && last.stage === stage) {
      last.events.push(event)
    } else {
      groups.push({ key: `${stage}-${event.sequence}`, stage, events: [event] })
    }
  }
  return groups
})
const canCommit = computed(() => generation.value?.status === 'completed' && !['committed', 'reverted'].includes(generation.value?.commit_status) && generation.value?.files?.length > 0)
// 撤回：任务已结束（非运行中）、未撤回过，且确有本批改动（有 files 或已提交）时可用；对 completed/failed/interrupted 均开放。
const canRevert = computed(() => !taskRunning.value && generation.value?.commit_status !== 'reverted' && ((generation.value?.files?.length > 0) || generation.value?.commit_status === 'committed'))
// 日志底部“等待输出”动画（仿 Claude Code spinner）：任务运行中且打字机已追上末尾（暂无新输出）时显示。
const waitingForOutput = computed(() => taskRunning.value && revealedCount.value >= events.value.length)
const waitingText = computed(() => {
  if (generation.value?.status === 'pending' || generation.value?.stage === 'queued') return '正在启动 Agent…'
  return `${stageLabel(generation.value?.stage)} 进行中，等待输出…`
})
const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const spinnerIndex = ref(0)
const spinnerFrame = computed(() => SPINNER_FRAMES[spinnerIndex.value % SPINNER_FRAMES.length])
let spinnerTimer = null
function startSpinner() { if (spinnerTimer) return; spinnerTimer = window.setInterval(() => { spinnerIndex.value = (spinnerIndex.value + 1) % SPINNER_FRAMES.length }, 90) }
function stopSpinner() { if (spinnerTimer) { window.clearInterval(spinnerTimer); spinnerTimer = null } }
watch(waitingForOutput, async (on) => { if (on) { startSpinner(); await nextTick(); if (autoFollow.value) scrollToBottom() } else stopSpinner() }, { immediate: true })

function activateDetail() {
  if (timer) return
  load()
  startTypewriter()
  timer = window.setInterval(() => {
    heartbeat.value = Date.now()
    if (!generation.value || taskRunning.value || (eventsSyncFailed.value && Date.now() >= nextEventSyncAt.value)) load()
  }, 1500)
}
function deactivateDetail() {
  if (timer) { window.clearInterval(timer); timer = null }
  stopTypewriter()
  stopSpinner()
}
onMounted(activateDetail)
onActivated(activateDetail)
onDeactivated(deactivateDetail)
onUnmounted(deactivateDetail)

async function load() {
  if (!projectId.value || loading.value) return
  loading.value = true
  try {
    const response = await api.getAutomationGeneration(route.params.id, projectId.value)
    const latest = response.data.generation
    const firstLoad = !generation.value
    generation.value = { ...latest, events: events.value }
    artifacts.value = latest.artifacts || []
    const shouldSyncEvents = !eventsSyncFailed.value || Date.now() >= nextEventSyncAt.value
    if (shouldSyncEvents) {
      try {
        const eventResponse = await api.getAutomationGenerationEvents(route.params.id, projectId.value, firstLoad ? 0 : eventCursor.value)
        events.value = firstLoad ? (eventResponse.data.events || []) : [...events.value, ...(eventResponse.data.events || [])]
        eventCursor.value = eventResponse.data.cursor || eventCursor.value
        if (firstLoad || !taskRunning.value) { revealedCount.value = events.value.length; typingChars.value = 0; charAccum = 0; interLineUntil = 0 }
        startTypewriter()
        eventsSyncFailed.value = false
        eventSyncFailures.value = 0
        nextEventSyncAt.value = 0
        generation.value = { ...generation.value, events: events.value }
      } catch {
        eventsSyncFailed.value = true
        eventSyncFailures.value += 1
        nextEventSyncAt.value = Date.now() + Math.min(30000, 1500 * (2 ** Math.min(eventSyncFailures.value, 5)))
      }
    }
    await nextTick()
    if (autoFollow.value) scrollToBottom()
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '加载任务详情失败')
  } finally {
    loading.value = false
  }
}

async function openArtifact(item) {
  const requestId = ++artifactRequestId
  artifactDialog.value = {
    visible: true,
    loading: true,
    title: item.path,
    path: item.path,
    kindLabel: artifactKindLabel(item.kind),
    content: '',
    mode: 'text',
    heal: null,
    redacted: false
  }
  try {
    const response = await api.getAutomationArtifact(route.params.id, projectId.value, item.path)
    if (requestId === artifactRequestId) {
      artifactDialog.value.content = response.data.content || ''
      artifactDialog.value.redacted = !!response.data.redacted
    }
  } catch (error) {
    if (requestId === artifactRequestId) artifactDialog.value.content = error.response?.data?.error || '文件暂不可用'
  } finally {
    if (requestId === artifactRequestId) artifactDialog.value.loading = false
  }
}

function openArtifactByFile(file) {
  const item = artifacts.value.find(artifact => artifact.path === file.path)
  if (item) openArtifact(item)
}

function openHealRecord(record, index) {
  artifactRequestId += 1
  artifactDialog.value = {
    visible: true,
    loading: false,
    title: `修复记录 #${index + 1}`,
    path: `Healer · ${formatDate(record.ended_at || record.started_at)}`,
    kindLabel: '修复记录',
    content: '',
    mode: 'heal',
    heal: record,
    redacted: false
  }
}

// 把 unified diff 文本按行标注类型，供模板着色（+ 新增 / - 删除 / @@ 区块头）。
function diffLines(diffText) {
  return String(diffText || '').split('\n').map(line => {
    let type = 'plain'
    if (line.startsWith('+++') || line.startsWith('---')) type = 'meta'
    else if (line.startsWith('@@')) type = 'hunk'
    else if (line.startsWith('+')) type = 'add'
    else if (line.startsWith('-')) type = 'del'
    return { type, text: line }
  })
}

function changeStatusLabel(status) {
  return ({ added: '新增', modified: '修改', deleted: '删除' })[status] || status || '变更'
}

function isReadableArtifact(path) {
  return artifacts.value.some(artifact => artifact.path === path)
}

function handleLogScroll(event) {
  const element = event.target
  autoFollow.value = element.scrollHeight - element.scrollTop - element.clientHeight < 40
}

async function scrollToBottom(force = false) {
  if (force) autoFollow.value = true
  await nextTick()
  if (logContainer.value && (force || autoFollow.value)) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}
function toggleFollow() {
  if (autoFollow.value) autoFollow.value = false
  else scrollToBottom(true)
}
function setFilter(mode) {
  if (logFilter.value === mode) return
  logFilter.value = mode
  scrollToBottom(true)
}

// 打字机引擎：后端仍按行推送，前端逐字“打出”，行间加停顿；积压时自适应加速，避免越拖越卡。
function startTypewriter() {
  if (typeRAF) return
  lastTypeTs = 0
  typeRAF = requestAnimationFrame(typeTick)
}
function stopTypewriter() {
  if (typeRAF) { cancelAnimationFrame(typeRAF); typeRAF = null }
}
function typeTick(ts) {
  const total = events.value.length
  const caughtUp = revealedCount.value >= total
  if (caughtUp && !taskRunning.value) { typingChars.value = 0; typeRAF = null; return }
  typeRAF = requestAnimationFrame(typeTick)
  if (!lastTypeTs) { lastTypeTs = ts; return }
  const dt = Math.min(120, ts - lastTypeTs)
  lastTypeTs = ts
  if (caughtUp) { typingChars.value = 0; charAccum = 0; return }
  if (ts < interLineUntil) return
  const backlog = total - revealedCount.value
  if (backlog > 40) { revealedCount.value = total - 6; typingChars.value = 0; charAccum = 0; interLineUntil = 0; return }
  const cur = events.value[revealedCount.value]
  const fullLen = (cur.message || '').length
  if (!fullLen) { revealedCount.value += 1; typingChars.value = 0; interLineUntil = ts + 40; return }
  const speed = Math.min(1400, Math.max(60 + backlog * 50, fullLen * 1.8))
  charAccum += speed * dt / 1000
  const step = Math.floor(charAccum)
  if (step > 0) {
    charAccum -= step
    typingChars.value = Math.min(fullLen, typingChars.value + step)
    if (autoFollow.value && ts - lastTypeScrollTs > 60) { lastTypeScrollTs = ts; scrollToBottom() }
  }
  if (typingChars.value >= fullLen) {
    revealedCount.value += 1
    typingChars.value = 0
    charAccum = 0
    interLineUntil = ts + (backlog > 3 ? 0 : 120)
  }
}

async function commit() {
  try {
    await ElMessageBox.confirm('只创建本地 commit，不会 push；其他工作区修改不会被提交。继续吗？', '确认提交', { type: 'warning' })
    committing.value = true
    const response = await api.commitAutomationGeneration(route.params.id, projectId.value, commitMessage.value)
    generation.value = response.data.generation
    ElMessage.success('本地 commit 创建成功')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.error || '创建 commit 失败')
  } finally { committing.value = false }
}

async function cancelGeneration() {
  try {
    await ElMessageBox.confirm('将终止 OpenCode 进程并释放工作区锁。继续吗？', '停止任务', { type: 'warning', confirmButtonText: '停止任务' })
    cancelling.value = true
    await api.cancelAutomationGeneration(route.params.id, projectId.value)
    ElMessage.info('已请求停止任务')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.error || '停止任务失败')
  } finally { cancelling.value = false }
}

async function retryGeneration() {
  try {
    await ElMessageBox.confirm('将在当前任务上重新运行：清空本次日志与产物，使用当前自动化配置和相同需求重跑。继续吗？', '重试任务', { type: 'warning', confirmButtonText: '重新运行' })
  } catch { return }
  retrying.value = true
  try {
    await api.retryAutomationGeneration(route.params.id, projectId.value)
    generation.value = null
    events.value = []
    artifacts.value = []
    eventCursor.value = 0
    revealedCount.value = 0
    typingChars.value = 0
    ElMessage.success('已重新发起运行')
    await load()
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '重试失败')
  } finally { retrying.value = false }
}

async function revertGeneration() {
  const gen = generation.value
  const committed = gen?.commit_status === 'committed'
  const files = gen?.files || []
  const newCount = files.filter(f => f.status === '??' || f.status === 'A').length
  const modCount = files.length - newCount
  const intro = committed
    ? '将对本批次提交执行 git revert，生成一个反向提交来撤销改动（保留历史、不 push）。'
    : `将从工作区撤回本批次改动：删除 ${newCount} 个新增文件、还原 ${modCount} 个修改文件。新增文件不在 Git 中，删除后仅能从 ZIP 找回。`
  try {
    await ElMessageBox.confirm(`${intro} 继续吗？`, '撤回修改', { type: 'warning', confirmButtonText: '撤回', cancelButtonText: '取消' })
  } catch { return }
  reverting.value = true
  try {
    await api.revertAutomationGeneration(route.params.id, projectId.value, false)
    ElMessage.success('已撤回本批次改动')
    await load()
  } catch (error) {
    const data = error.response?.data
    if (data?.require_force && Array.isArray(data.drifted) && data.drifted.length) {
      const list = data.drifted.slice(0, 8).join('、') + (data.drifted.length > 8 ? ` 等 ${data.drifted.length} 个文件` : '')
      try {
        await ElMessageBox.confirm(`${data.error}受影响文件：${list}。仍要强制撤回吗？这些文件的手工修改将被覆盖或删除。`, '需要二次确认', { type: 'warning', confirmButtonText: '强制撤回', cancelButtonText: '取消' })
      } catch { return }
      try {
        await api.revertAutomationGeneration(route.params.id, projectId.value, true)
        ElMessage.success('已强制撤回本批次改动')
        await load()
      } catch (err2) {
        ElMessage.error(err2.response?.data?.error || '撤回失败')
      }
      return
    }
    ElMessage.error(data?.error || '撤回失败')
  } finally { reverting.value = false }
}

async function download() {
  try {
    const response = await api.downloadAutomationGeneration(route.params.id, projectId.value)
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.download = `automation_${route.params.id}.zip`
    document.body.appendChild(link); link.click(); link.remove(); window.URL.revokeObjectURL(url)
  } catch { ElMessage.error('下载失败') }
}

function stageClass(key) {
  if (generation.value?.status === 'completed') return 'done'
  if (generation.value?.status === 'interrupted') return key === 'orchestrator' ? 'failed' : ''
  if (generation.value?.status === 'failed') return key === generation.value.stage ? 'failed' : ''
  if (generation.value?.stage === 'queued' || generation.value?.stage === 'starting') return key === 'orchestrator' ? 'current' : ''
  const current = stages.findIndex(item => item.key === generation.value?.stage)
  const index = stages.findIndex(item => item.key === key)
  return index < current ? 'done' : index === current ? 'current' : ''
}
function eventType(event) {
  if (['error', 'model_error', 'interrupted', 'failed'].includes(event.event_type) || /error|fail|exception|fatal|critical|abort/i.test(`${event.event_type || ''} ${event.message || ''}`)) return 'error'
  if (event.event_type === 'completed') return 'success'
  return 'default'
}
function eventTypeLabel(event) {
  const message = event.message || ''
  if (message.startsWith('[思考]') || message.startsWith('[推理]')) return '思考'
  return ({ started: '启动', server: '服务日志', server_ready: '服务就绪', server_stopped: '服务停止', process_started: '进程启动', session_created: '会话创建', text: 'Agent 输出', tool: '工具调用', stdout: 'OpenCode 输出', stderr: 'OpenCode 日志', diagnostic: '诊断', model_error: '模型请求错误', process_exit: '进程退出', error: '错误', interrupted: '已中断', completed: '完成', failed: '失败' })[event.event_type] || '输出'
}
function stageTagType(stage) {
  return ({ orchestrator: '', planner: 'warning', generator: 'success', healer: 'danger', completed: 'success', failed: 'danger', interrupted: 'danger', queued: 'info' })[stage] ?? 'info'
}
function stageLabel(stage) { return ({ queued: '等待启动', orchestrator: '总控协调', planner: 'Planner 规划探索', generator: 'Generator 生成脚本', healer: 'Healer 执行与修复', completed: '完成', failed: '失败', interrupted: '已中断' })[stage] || (stage === 'test_run' ? 'Healer 执行测试' : stage) || '-' }
function statusLabel(status) { return ({ completed: '完成', running: '执行中', failed: '失败', pending: '等待中', interrupted: '已中断' })[status] || status || '加载中' }
function statusType(status) { return ({ completed: 'success', running: 'warning', failed: 'danger', pending: 'info', interrupted: 'danger' })[status] || 'info' }
function parseUtc(value) {
  if (!value) return null
  let text = String(value).trim()
  // 后端存的是 datetime.utcnow() 的 naive ISO 字符串（无时区），显式标为 UTC 再转本地（北京）时间显示
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(text)) text += 'Z'
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? null : date
}
function formatDate(value) { const date = parseUtc(value); return date ? date.toLocaleString() : '-' }
function formatTime(value) {
  const date = parseUtc(value)
  if (!date) return ''
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}
function formatDuration(start, end) {
  const startDate = parseUtc(start)
  if (!startDate) return '-'
  const endDate = parseUtc(end) || new Date()
  const seconds = Math.max(0, Math.floor((endDate.getTime() - startDate.getTime()) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}分${String(seconds % 60).padStart(2, '0')}秒`
}
function artifactKindLabel(kind) { return ({ plan: 'Planner', script: 'Playwright', config: '配置' })[kind] || kind || '文件' }
</script>

<style scoped>
.generation-detail-page { max-width:1440px; margin:0 auto; height:calc(100dvh - 144px); display:flex; flex-direction:column; gap:12px; min-height:0; }
.header-actions { display:flex; gap:8px; flex:none; margin-left:auto; }
.run-title { font-size:16px; font-weight:650; color:#1d1d1f; letter-spacing:-.2px; white-space:nowrap; }
.requirement { margin:6px 0 0; color:#6e6e73; font-size:13px; line-height:1.5; white-space:pre-wrap; max-width:860px; }
.requirement.is-collapsed { display:-webkit-box; -webkit-line-clamp:1; line-clamp:1; -webkit-box-orient:vertical; overflow:hidden; white-space:normal; }
.requirement:not(.is-collapsed) { max-height:28vh; overflow:auto; }
.req-toggle { display:inline-flex; align-items:center; gap:6px; margin-top:0; padding:0; border:0; background:transparent; color:#0071e3; font-size:12.5px; font-weight:500; cursor:pointer; transition:color .15s ease; }
.req-toggle:hover { color:#0077ed; }
.req-caret { width:7px; height:7px; border-right:1.6px solid currentColor; border-bottom:1.6px solid currentColor; transform:rotate(45deg) translateY(-2px); transition:transform .22s ease; }
.req-caret.up { transform:rotate(-135deg) translateY(-1px); }
.status-card { flex:none; }
.status-card :deep(.el-card__body) { padding:12px 20px; }
.status-summary { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.status-tag { font-weight:600; letter-spacing:.2px; }
.stage-label { display:inline-flex; align-items:center; font-weight:600; color:#1d1d1f; font-size:14px; }
.meta-divider { width:1px; height:14px; background:#e4e7ed; flex:none; }
.status-meta { color:#86909c; font-size:12.5px; }
.status-meta.url { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.status-meta b { color:#0071e3; font-weight:700; }
.progress-row { display:flex; align-items:center; gap:14px; margin:10px 0 2px; }
.run-progress { flex:1; position:relative; overflow:hidden; border-radius:8px; }
.run-progress :deep(.el-progress-bar__outer) { border-radius:8px; background:#eef1f6; }
.run-progress :deep(.el-progress-bar__inner) { border-radius:8px; background:linear-gradient(90deg,#0a84ff,#0071e3); transition:width .5s cubic-bezier(.4,0,.2,1); }
.run-progress.is-running :deep(.el-progress-bar__inner) { background:linear-gradient(90deg,#1687f4,#056bd6); }
.run-progress.is-running::after { content:''; position:absolute; z-index:2; top:0; bottom:0; left:-28%; width:28%; border-radius:8px; background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent); animation:progress-sweep 2.4s ease-in-out infinite; pointer-events:none; }
@keyframes progress-sweep { 0% { transform:translateX(0); opacity:0; } 12% { opacity:.7; } 72% { opacity:.7; } 100% { transform:translateX(457%); opacity:0; } }
.progress-num { font-size:17px; font-weight:700; line-height:1; color:#0071e3; font-variant-numeric:tabular-nums; min-width:58px; text-align:right; font-family:-apple-system,'SF Pro Display',system-ui,sans-serif; }
.progress-num i { font-style:normal; font-size:12px; font-weight:600; margin-left:1px; opacity:.55; }
.progress-num.success { color:#34c759; }
.progress-num.exception { color:#ff3b30; }
.stage-track { display:flex; align-items:center; gap:0; flex:none; margin:0 0 8px; padding-bottom:8px; border-bottom:1px solid #f0f2f5; }
.stage-item { flex:1; display:flex; align-items:center; gap:8px; color:#a8abb2; font-size:11.5px; font-weight:500; position:relative; min-width:0; }
.stage-item:not(:last-child)::after { content:''; height:2px; flex:1; margin:0 10px; border-radius:2px; background:#e4e7ed; transition:background .4s ease; }
.stage-item.done:not(:last-child)::after { background:linear-gradient(90deg,#0071e3,#5aa9ff); }
.stage-dot { position:relative; width:16px; height:16px; border-radius:50%; background:#fff; border:2px solid #dcdfe6; flex:none; display:grid; place-items:center; transition:all .3s ease; }
.dot-check { width:10px; height:10px; opacity:0; transform:scale(.4); transition:all .3s ease; color:#fff; }
.stage-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.stage-item.done { color:#0071e3; }
.stage-item.done .stage-dot { background:#0071e3; border-color:#0071e3; }
.stage-item.done .dot-check { opacity:1; transform:scale(1); }
.stage-item.current { color:#0071e3; font-weight:600; }
.stage-item.current .stage-dot { border-color:#0071e3; }
.stage-item.current .stage-dot::after { content:''; position:absolute; top:50%; left:50%; box-sizing:border-box; width:11px; height:11px; border-radius:50%; border:2px solid rgba(0,113,227,.25); border-top-color:#0071e3; animation:stage-spin .7s linear infinite; }
.stage-item.failed { color:#ff3b30; }
.stage-item.failed .stage-dot { background:#ff3b30; border-color:#ff3b30; box-shadow:0 0 0 4px rgba(255,59,48,.14); }
@keyframes stage-spin { from { transform:translate(-50%,-50%) rotate(0deg); } to { transform:translate(-50%,-50%) rotate(360deg); } }
.workspace-card { flex:1; min-height:0; display:flex; flex-direction:column; }
.workspace-card :deep(.el-card__body) { flex:1; min-height:0; display:flex; flex-direction:column; padding:14px 20px; }
.execution-layout { display:grid; grid-template-columns:264px minmax(0,1fr); gap:20px; flex:1; min-height:0; }
.artifact-sidebar { min-width:0; min-height:0; overflow-y:auto; overscroll-behavior:contain; border-right:1px solid #ebeef5; padding-right:16px; }
.artifact-sidebar::-webkit-scrollbar { width:8px; }
.artifact-sidebar::-webkit-scrollbar-thumb { background:#dcdfe6; border-radius:4px; }
.artifact-sidebar::-webkit-scrollbar-thumb:hover { background:#c0c4cc; }
.sidebar-heading { color:#1d1d1f; font-size:11.5px; font-weight:700; letter-spacing:.7px; text-transform:uppercase; margin-bottom:12px; }
.artifact-group { border-bottom:1px solid #f0f2f5; padding:0 0 12px; margin-bottom:12px; }
.artifact-group-title { display:flex; justify-content:space-between; align-items:center; color:#606266; font-size:12px; font-weight:700; margin-bottom:8px; }
.artifact-group-title span:last-child { color:#86909c; font-weight:600; font-size:11px; background:#f0f2f5; padding:1px 8px; border-radius:20px; min-width:22px; text-align:center; }
.artifact-item { display:flex; flex-direction:column; align-items:flex-start; gap:3px; width:100%; min-width:0; border:0; background:transparent; border-radius:8px; padding:8px 10px; color:#606266; cursor:pointer; text-align:left; transition:background .15s ease,color .15s ease; }
.artifact-item:hover:not(:disabled) { background:rgba(0,113,227,.07); color:#0071e3; }
.artifact-item:disabled { color:#c0c4cc; cursor:not-allowed; }
.artifact-item span, .artifact-item small { display:block; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.artifact-item span { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; }
.artifact-item small { color:#a8abb2; font-size:11px; }
.sidebar-empty { color:#c0c4cc; font-size:12px; padding:6px 10px; }
.artifact-scroll { max-height:220px; overflow-y:auto; overscroll-behavior:contain; padding-right:2px; }
.artifact-scroll::-webkit-scrollbar { width:8px; }
.artifact-scroll::-webkit-scrollbar-thumb { background:#dcdfe6; border-radius:4px; }
.artifact-scroll::-webkit-scrollbar-thumb:hover { background:#c0c4cc; }
.commit-box { display:flex; flex-direction:column; align-items:stretch; gap:9px; padding-top:6px; }
.commit-box .el-input { width:100%; }
.commit-box .el-button { width:100%; }
.commit-box .el-button + .el-button { margin-left:0; }
.field-hint { color:#909399; font-size:11.5px; line-height:1.55; }
.commit-success { color:#34c759; font-size:12px; font-weight:500; }
.commit-reverted { color:#e6a23c; font-size:12px; font-weight:500; }
.log-panel { display:flex; min-width:0; min-height:0; flex-direction:column; }
.log-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:0 0 8px; flex:none; }
.log-toolbar-status { display:flex; align-items:center; gap:9px; min-width:0; color:#86909c; font-size:12.5px; }
.log-toolbar-status strong { color:#1f2733; font-weight:600; }
.log-chip { font-family:ui-monospace,monospace; font-size:11px; color:#0071e3; background:rgba(0,113,227,.08); padding:1px 8px; border-radius:20px; font-weight:600; flex:none; }
.log-meta { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.log-toolbar-actions { display:flex; align-items:center; gap:8px; flex:none; }
.follow-toggle { display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:20px; border:1px solid #e4e7ed; background:#fff; color:#86909c; font-size:12px; font-weight:500; cursor:pointer; transition:all .18s ease; }
.follow-toggle:hover { border-color:#c6d9f5; color:#0071e3; }
.follow-toggle .follow-ind { width:7px; height:7px; border-radius:50%; background:#c0c4cc; transition:all .18s ease; }
.follow-toggle.on { color:#0071e3; border-color:#b3d4ff; background:rgba(0,113,227,.06); }
.follow-toggle.on .follow-ind { background:#0071e3; box-shadow:0 0 0 3px rgba(0,113,227,.14); }
.log-muted { color:#a8abb2; font-size:11.5px; white-space:nowrap; }
.log-filter { display:inline-flex; gap:2px; padding:2px; background:#f0f2f5; border-radius:20px; }
.filter-btn { padding:3px 12px; border:0; border-radius:20px; background:transparent; color:#86909c; font-size:12px; font-weight:500; cursor:pointer; transition:all .18s ease; }
.filter-btn:hover { color:#0071e3; }
.filter-btn.on { background:#fff; color:#0071e3; box-shadow:0 1px 3px rgba(0,0,0,.1); }
.log-line-speech { background:rgba(96,165,250,.055); }
.log-line-speech pre { color:#eef4ff; }
.log-line-speech .log-line-type { color:#7fb2ff; font-weight:600; }
.log-dot { width:8px; height:8px; border-radius:50%; background:#c0c4cc; flex:none; transition:box-shadow .3s ease; }.log-dot.active { background:#4ade80; box-shadow:0 0 0 3px rgba(74,222,128,.16), 0 0 10px rgba(74,222,128,.55); animation:dot-pulse 1.9s ease-in-out infinite; }.log-dot.waiting { background:#c99a2e; box-shadow:0 0 0 3px rgba(201,154,46,.14); }.log-dot.failed { background:#f87171; box-shadow:0 0 0 3px rgba(248,113,113,.16); }
@keyframes dot-pulse { 0%,100% { box-shadow:0 0 0 3px rgba(74,222,128,.16), 0 0 10px rgba(74,222,128,.55); } 50% { box-shadow:0 0 0 4px rgba(74,222,128,.08), 0 0 16px rgba(74,222,128,.75); } }
.log-stream { flex:1; min-height:0; overflow:auto; padding:12px 18px; background:linear-gradient(180deg,#151e2e 0%,#0f1626 100%); border:1px solid rgba(255,255,255,.06); border-radius:14px; color:#c7d0de; box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 10px 34px rgba(0,0,0,.28); scrollbar-width:thin; scrollbar-color:rgba(255,255,255,.16) transparent; }
.log-stream :deep(.el-empty) { padding:48px 0; }
.log-waiting { display:flex; align-items:center; gap:10px; padding:12px 14px 4px; color:#9aa4b2; font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace; animation:line-in .25s ease; }
.cc-spinner { color:#c99a2e; font-size:14px; font-weight:500; line-height:1; }
.cc-text { letter-spacing:.02em; color:#aeb8c6; }
.log-stream :deep(.el-empty__description p) { color:#8592a8; }
.log-stream::-webkit-scrollbar { width:10px; height:10px; }
.log-stream::-webkit-scrollbar-track { background:transparent; }
.log-stream::-webkit-scrollbar-thumb { background:rgba(255,255,255,.14); border-radius:8px; border:2px solid transparent; background-clip:padding-box; }
.log-stream::-webkit-scrollbar-thumb:hover { background:rgba(255,255,255,.26); background-clip:padding-box; }
.agent-block { border-bottom:1px solid rgba(255,255,255,.06); }.agent-block:last-child { border-bottom:0; }
.agent-block-head { position:sticky; top:-12px; z-index:2; display:flex; align-items:center; gap:9px; margin:0 -18px; padding:10px 18px; background:rgba(15,22,38,.86); backdrop-filter:blur(10px) saturate(1.3); -webkit-backdrop-filter:blur(10px) saturate(1.3); border-bottom:1px solid rgba(255,255,255,.07); }
.agent-block-count { color:#8592a8; font-size:11.5px; font-family:'JetBrains Mono',ui-monospace,Consolas,monospace; padding:1px 8px; border-radius:20px; background:rgba(255,255,255,.06); }
.agent-block-body { padding:8px 0 14px; }
.log-line { display:grid; grid-template-columns:96px minmax(0,1fr) auto; gap:12px; align-items:baseline; padding:4px 9px; margin:1px -9px; border-radius:8px; transition:background .15s ease; animation:line-in .26s cubic-bezier(.22,.61,.36,1) both; }
.log-line:hover { background:rgba(255,255,255,.035); }
.log-line-typing { box-shadow:inset 3px 0 0 rgba(96,165,250,.85); }
.log-line-typing pre { color:#eaf2ff; text-shadow:0 0 14px rgba(96,165,250,.3); }
.log-line-error { box-shadow:inset 3px 0 0 rgba(248,113,113,.7); }
.log-line-success { box-shadow:inset 3px 0 0 rgba(74,222,128,.5); }
.type-cursor { display:inline-block; width:8px; height:15px; margin-left:3px; vertical-align:-2px; background:#60a5fa; border-radius:2px; box-shadow:0 0 9px rgba(96,165,250,.75); animation:cursor-blink 1.1s ease-in-out infinite; }
@keyframes cursor-blink { 0%,100% { opacity:1; } 50% { opacity:.15; } }
@keyframes line-in { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce) { .log-line { animation:none; } .type-cursor, .log-dot.active, .stage-item.current .stage-dot::after, .run-progress.is-running::after { animation:none; } }
.log-line-type { color:#7c8aa0; font-size:12px; white-space:nowrap; letter-spacing:.01em; }
.log-line pre { margin:0; color:#d6dfeb; white-space:pre-wrap; word-break:break-word; font:13.5px/1.7 'JetBrains Mono','Cascadia Code','Fira Code',ui-monospace,SFMono-Regular,'SF Mono',Consolas,monospace; letter-spacing:.012em; }
.log-line time { color:#5f6d82; font-size:11.5px; white-space:nowrap; font-family:'JetBrains Mono',ui-monospace,Consolas,monospace; letter-spacing:.02em; }
.log-line-error pre { color:#ffb6b6; }.log-line-error .log-line-type { color:#f87171; }.log-line-success pre { color:#92f0b4; }.log-line-success .log-line-type { color:#4ade80; }
.dialog-meta { display:flex; align-items:center; gap:8px; margin-bottom:10px; color:#909399; font:12px ui-monospace,monospace; }.source-view { margin:0; max-height:70vh; overflow:auto; background:#1f2937; color:#d1d5db; border-radius:6px; padding:16px; white-space:pre-wrap; word-break:break-word; font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; }.dialog-loading { min-height:300px; display:grid; place-items:center; color:#909399; }
.heal-view { max-height:70vh; overflow:auto; }
.heal-section-title { font-size:13px; font-weight:650; color:#1d1d1f; margin:14px 0 8px; }
.heal-section-title:first-child { margin-top:0; }
.heal-reason { margin:0; background:#f6f8fa; border:1px solid #eaecef; border-radius:6px; padding:12px 14px; white-space:pre-wrap; word-break:break-word; color:#24292f; font:13px/1.7 -apple-system,'Segoe UI',system-ui,sans-serif; }
.heal-empty { color:#909399; font-size:13px; padding:8px 0; }
.diff-file { margin-top:12px; border:1px solid #eaecef; border-radius:6px; overflow:hidden; }
.diff-file-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 12px; background:#f6f8fa; border-bottom:1px solid #eaecef; }
.diff-path { font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace; color:#1d1d1f; word-break:break-all; }
.diff-view { margin:0; max-height:44vh; overflow:auto; background:#fff; padding:8px 0; font:12.5px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; }
.diff-line { display:block; padding:0 12px; white-space:pre-wrap; word-break:break-word; color:#24292f; }
.diff-line.add { background:#e6ffed; color:#1a7f37; }
.diff-line.del { background:#ffeef0; color:#cf222e; }
.diff-line.hunk { background:#f1f8ff; color:#0550ae; }
.diff-line.meta { color:#6a737d; }
@media (max-height:600px) { .generation-detail-page { height:auto; min-height:calc(100dvh - 144px); }.workspace-card { min-height:520px; }.log-stream { min-height:360px; } }
@media (max-width:900px) { .generation-detail-page { height:auto; gap:14px; }.header-actions { width:100%; }.execution-layout { grid-template-columns:1fr; }.artifact-sidebar { border-right:0; border-bottom:1px solid #ebeef5; padding:0 0 14px; max-height:none; overflow:visible; }.log-stream { min-height:420px; max-height:70vh; }.stage-track { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; min-width:0; }.stage-item:not(:last-child)::after { display:none; }.log-toolbar-status { flex-wrap:wrap; }.log-meta { white-space:normal; }.status-card { overflow:auto; }.status-meta.url { max-width:200px; } }
</style>
