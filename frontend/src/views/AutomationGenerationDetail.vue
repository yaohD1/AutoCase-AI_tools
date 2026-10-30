<template>
  <div class="run-page ui-buttons">
    <!-- 页头：状态、需求、进度、阶段合成一块，不再套卡片；动作都在右上，返回是一个文字链接 -->
    <header class="run-head">
      <div class="run-head-bar">
        <button type="button" class="back-link" @click="backToRecords"><span class="back-arrow">←</span>任务记录</button>
        <div class="run-actions">
          <el-button v-if="polling" text size="small" :icon="Refresh" @click="load">刷新</el-button>
          <el-button v-if="taskRunning" type="danger" plain size="small" :icon="CircleClose" :loading="cancelling" @click="cancelGeneration">停止任务</el-button>
          <el-button v-if="canRetry" type="primary" size="small" :icon="RefreshRight" :loading="retrying" @click="retryGeneration">重试</el-button>
          <el-button v-if="generation?.status === 'completed'" size="small" :icon="Download" @click="download">下载 ZIP</el-button>
        </div>
      </div>

      <div class="run-title-line">
        <h1 class="run-title">任务详情</h1>
        <span :class="['run-status', `is-${runTone}`]"><i></i>{{ statusLabel(generation?.status) }}</span>
        <span v-if="stageLabel(generation?.stage) !== statusLabel(generation?.status)" class="run-stage">{{ stageLabel(generation?.stage) }}</span>
      </div>

      <p :class="['run-requirement', { 'is-open': requirementExpanded }]">{{ generation?.requirement || '加载任务中…' }}</p>
      <button v-if="requirementLong" type="button" class="req-toggle" @click="requirementExpanded = !requirementExpanded">
        {{ requirementExpanded ? '收起需求' : '展开需求' }}<span :class="['req-caret', { up: requirementExpanded }]"></span>
      </button>

      <div class="run-meta">
        <span class="meta-item">批次 <b class="meta-mono">{{ runKeyText }}</b></span>
        <span class="meta-sep">·</span>
        <span class="meta-item meta-mono url" :title="generation?.config?.base_url || ''">{{ generation?.config?.base_url || '-' }}</span>
        <span class="meta-sep">·</span>
        <span class="meta-item">用时 {{ durationLabel }}</span>
        <span class="meta-sep">·</span>
        <span class="meta-item">修复 {{ generation?.heal_attempts || 0 }} 次</span>
        <span class="meta-sep">·</span>
        <span class="meta-item">{{ formatDate(generation?.created_at) }}</span>
      </div>

      <div class="run-progress-row">
        <el-progress class="run-progress" :percentage="stagePercent" :status="progressStatus" :stroke-width="3" :show-text="false" />
        <span :class="['run-percent', progressStatus]">{{ stagePercent }}<i>%</i></span>
      </div>

      <ol class="stage-track">
        <li v-for="(item, index) in stages" :key="item.key" :class="['stage-item', stageClass(item.key)]">
          <span class="stage-dot"><svg viewBox="0 0 12 12" class="dot-check"><path d="M2.6 6.3 4.8 8.4 9.4 3.9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
          <span class="stage-name">{{ item.label }}</span>
          <span v-if="index < stages.length - 1" class="stage-line"></span>
        </li>
      </ol>

      <el-alert v-if="generation?.error" class="run-error" type="error" :closable="false" show-icon>{{ generation.error }}</el-alert>
    </header>

    <div class="run-body">
      <aside class="run-side">
        <div class="side-head">文件与记录</div>

        <div class="side-groups">
        <div class="side-group">
          <div class="side-group-head"><span>测试计划</span><span class="side-count">{{ planArtifacts.length }}</span></div>
          <button v-for="item in planArtifacts" :key="item.path" type="button" class="side-item" @click="openArtifact(item)">
            <span class="side-path" :title="item.path">{{ baseName(item.path) }}</span>
          </button>
          <div v-if="!planArtifacts.length" class="side-empty">尚未生成</div>
        </div>

        <div class="side-group">
          <div class="side-group-head"><span>测试脚本</span><span class="side-count">{{ scriptArtifacts.length }}</span></div>
          <button v-for="item in scriptArtifacts" :key="item.path" type="button" class="side-item" @click="openArtifact(item)">
            <span class="side-path" :title="item.path">{{ baseName(item.path) }}</span>
          </button>
          <div v-if="!scriptArtifacts.length" class="side-empty">尚未生成</div>
        </div>

        <div class="side-group">
          <div class="side-group-head"><span>修复记录</span><span class="side-count">{{ healRecords.length }}</span></div>
          <button v-for="(record, index) in healRecords" :key="record.index || index" type="button" class="side-item" @click="openHealRecord(record, index)">
            <span class="side-path">修复 #{{ index + 1 }}</span><span class="side-kind">改动 {{ (record.changes || []).length }} 文件</span>
          </button>
          <div v-if="!healRecords.length" class="side-empty">暂无修复记录</div>
        </div>

        <div class="side-group">
          <div class="side-group-head"><span>Git 变更</span><span class="side-count">{{ generation?.files?.length || 0 }}</span></div>
          <div class="side-scroll">
            <button
              v-for="file in generation?.files || []"
              :key="file.path"
              type="button"
              class="side-item"
              :disabled="!isReadableArtifact(file.path)"
              @click="openArtifactByFile(file)"
            >
              <span class="side-path" :title="file.path">{{ baseName(file.path) }}</span><span class="side-kind">{{ file.status }}</span>
            </button>
          </div>
          <div v-if="!generation?.files?.length" class="side-empty">暂无变更</div>
        </div>
        </div>

        <div class="commit-box">
          <div class="commit-hint">只提交本次生成的文件，不会 push。</div>
          <el-input v-model="commitMessage" class="form-input" :disabled="!canCommit" placeholder="test: generate Playwright tests" />
          <el-button type="primary" size="small" :icon="Select" :loading="committing" :disabled="!canCommit" @click="commit">创建本地 Commit</el-button>
          <el-button type="danger" plain size="small" :icon="RefreshLeft" :loading="reverting" :disabled="!canRevert" @click="revertGeneration">撤回修改</el-button>
          <div v-if="generation?.commit_status === 'committed'" class="commit-state is-ok">已提交 {{ generation.commit_hash }}</div>
          <div v-else-if="generation?.commit_status === 'reverted'" class="commit-state is-warn">已撤回本批次改动（可从 ZIP 找回）</div>
        </div>
      </aside>

      <section class="log-panel">
        <div class="log-toolbar">
          <div class="log-status">
            <span :class="['log-dot', { active: polling, failed: generation?.status === 'failed' }]" />
            <strong>{{ logStatusLabel }}</strong>
            <span class="chip">{{ events.length }} 条</span>
            <span v-if="logFilter === 'agent' && mutedCount" class="log-muted">隐藏 {{ mutedCount }} 条工具日志</span>
            <span class="log-meta">{{ latestLogLabel }}</span>
          </div>
          <div class="log-actions">
            <div class="log-tabs">
              <button type="button" :class="['log-tab', { 'is-on': logFilter === 'agent' }]" @click="setFilter('agent')">Agent 输出</button>
              <button type="button" :class="['log-tab', { 'is-on': logFilter === 'all' }]" @click="setFilter('all')">全部</button>
            </div>
            <button type="button" :class="['follow-btn', { on: autoFollow }]" :title="autoFollow ? '已开启自动跟随，点击暂停' : '已暂停跟随，点击回到底部并恢复'" @click="toggleFollow">
              <span class="follow-dot"></span>自动跟随
            </button>
            <el-button v-if="!autoFollow" text type="primary" size="small" @click="scrollToBottom(true)">回到底部</el-button>
          </div>
        </div>
        <div ref="logContainer" class="log-stream" @scroll="handleLogScroll">
          <div v-for="group in groupedEvents" :key="group.key" class="agent-block">
            <div class="agent-block-head">
              <span :class="['agent-stage', `is-${group.stage}`]">{{ stageLabel(group.stage) }}</span>
              <span class="agent-block-count">{{ group.events.length }} 条</span>
            </div>
            <div class="agent-block-body">
              <div v-for="event in group.events" :key="event.sequence" :class="['log-line', `log-line-${eventType(event)}`, { 'log-line-typing': event._typing, 'log-line-speech': isAgentSpeech(event) }]">
                <span class="log-line-type">{{ eventTypeLabel(event) }}</span>
                <details v-if="isReasoning(event) && !event._typing" class="log-reasoning">
                  <summary>{{ reasoningSummary(event) }}</summary>
                  <pre>{{ event.message }}</pre>
                </details>
                <pre v-else>{{ event.message }}<span v-if="event._typing" class="type-cursor" /></pre>
                <time>{{ formatTime(event.created_at) }}</time>
              </div>
            </div>
          </div>
          <el-empty v-if="!events.length && !waitingForOutput" description="等待 OpenCode 输出日志" :image-size="60" />
          <el-empty v-else-if="!groupedEvents.length && !waitingForOutput" description="当前无 Agent 输出，可切换到「全部」查看工具与系统日志" :image-size="60" />
          <div v-if="waitingForOutput" class="log-waiting">
            <span class="cc-spinner">{{ spinnerFrame }}</span>
            <span class="cc-text">{{ waitingText }}</span>
          </div>
        </div>
      </section>
    </div>

    <el-dialog v-model="artifactDialog.visible" :title="artifactDialog.title" width="min(1100px, 92vw)" top="5vh" destroy-on-close>
      <div class="dialog-meta">
        <span class="chip">{{ artifactDialog.kindLabel }}</span>
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
            <span class="chip">{{ changeStatusLabel(change.status) }}</span>
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
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, RefreshRight, RefreshLeft, CircleClose, Download, Back, Select } from '@element-plus/icons-vue'
import api from '../api'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.query.project_id || '')
const generation = ref(null)
const events = ref([])
const artifacts = ref([])
const autoFollow = ref(true)
const logContainer = ref(null)
const artifactDialog = ref({ visible: false, loading: false, title: '', path: '', kindLabel: '', content: '', mode: 'text', heal: null })
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
// 状态 → 颜色支：完成绿、执行中蓝、失败红、等待灰。仍是同一个圆点 + 小方片。
const runTone = computed(() => ({ completed: 'ok', running: 'run', failed: 'danger', interrupted: 'danger', pending: 'mute' })[generation.value?.status] || 'mute')
const runKeyText = computed(() => generation.value?.run_key || generation.value?.id?.slice(0, 8) || '-')
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
// 模型推理内容多为英文，默认折叠，避免盖住中文叙述；展开后仍可查看原文。
function isReasoning(event) {
  const message = event.message || ''
  return event.event_type === 'diagnostic' && (message.startsWith('[推理]') || message.startsWith('[思考]'))
}
function reasoningSummary(event) {
  const body = (event.message || '').replace(/^\[(推理|思考)\]\s*/, '')
  return `思考过程：${body.slice(0, 40)}${body.length > 40 ? '…' : ''}`
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
    heal: null
  }
  try {
    const response = await api.getAutomationArtifact(route.params.id, projectId.value, item.path)
    if (requestId === artifactRequestId) {
      artifactDialog.value.content = response.data.content || ''
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
    heal: record
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

// 同批次的目录前缀完全一样，列目录没有信息量；只留文件名，完整路径交给 title。
function baseName(path) {
  const text = String(path || '')
  const cut = text.lastIndexOf('/')
  return cut >= 0 ? text.slice(cut + 1) : text
}
function backToRecords() {
  router.push({ path: '/scripts/records', query: projectId.value ? { project_id: projectId.value } : {} })
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
function stageLabel(stage) { return ({ queued: '等待启动', orchestrator: '总控协调', planner: 'Planner 规划探索', generator: 'Generator 生成脚本', healer: 'Healer 执行与修复', completed: '完成', failed: '失败', interrupted: '已中断' })[stage] || (stage === 'test_run' ? 'Healer 执行测试' : stage) || '-' }
function statusLabel(status) { return ({ completed: '完成', running: '执行中', failed: '失败', pending: '等待中', interrupted: '已中断' })[status] || status || '加载中' }
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
/* ==================== 任务详情 ====================
   一屏工作区：页头（状态 / 需求 / 进度 / 阶段）+ 左右两栏（文件与记录 / 日志）。
   页头不再套卡片 —— 状态和进度本身就是页面的标题层。 */

.run-page {
  max-width: 1440px;
  margin: 0 auto;
  height: calc(100dvh - 116px);
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
}

/* ---------- 页头 ---------- */
.run-head { flex: none; min-width: 0; }
.run-head-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.back-link:hover { color: var(--accent); }
.back-arrow { font-size: 12px; }

.run-actions { display: flex; align-items: center; gap: 8px; flex: none; }

.run-title-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.run-title {
  margin: 0;
  font-size: 21px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -.02em;
  color: var(--ink);
}
.run-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  border-radius: var(--radius-chip);
  font-size: 11.5px;
  font-weight: 600;
  line-height: 1;
}
.run-status i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.run-status.is-ok { background: var(--ok-soft); color: var(--ok); }
.run-status.is-run { background: var(--accent-soft); color: #0b62c4; }
.run-status.is-run i { animation: status-pulse 1.8s ease-in-out infinite; }
.run-status.is-danger { background: #fdeeed; color: #c9342b; }
.run-status.is-mute { background: #f4f6f9; color: var(--muted); }
@keyframes status-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(11, 98, 196, .32); }
  50% { box-shadow: 0 0 0 4px rgba(11, 98, 196, 0); }
}
.run-stage { font-size: 11.5px; color: var(--faint); }

/* 需求默认一行，长需求才出现「展开」 */
.run-requirement {
  margin: 8px 0 0;
  max-width: 1040px;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--ink-2);
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.run-requirement.is-open { display: block; max-height: 22vh; overflow: auto; }
.req-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: 11.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.req-toggle:hover { color: var(--accent); }
.req-caret {
  width: 6px;
  height: 6px;
  border-right: 1.4px solid currentColor;
  border-bottom: 1.4px solid currentColor;
  transform: rotate(45deg) translateY(-2px);
  transition: transform .2s ease;
}
.req-caret.up { transform: rotate(-135deg) translateY(-1px); }

/* 一行次要信息：批次顺手看，Base URL 用等宽字 */
.run-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 7px;
  font-size: 11.5px;
  color: var(--muted);
}
.meta-item { white-space: nowrap; }
.meta-item b { color: var(--ink-2); font-weight: 500; }
.meta-mono { font-family: var(--mono); }
.meta-item.url { max-width: 330px; overflow: hidden; text-overflow: ellipsis; }
.meta-sep { color: #dfe3e8; }

/* 进度：3px 细线，不再是一条带扫光的胶囊 */
.run-progress-row { display: flex; align-items: center; gap: 12px; margin-top: 13px; }
.run-progress { flex: 1; }
.run-progress :deep(.el-progress-bar__outer) { background: #eceff3; border-radius: 2px; }
.run-progress :deep(.el-progress-bar__inner) {
  border-radius: 2px;
  background: var(--accent);
  transition: width .5s cubic-bezier(.4, 0, .2, 1);
}
.run-progress :deep(.el-progress-bar__inner.is-success) { background: var(--ok); }
.run-progress :deep(.el-progress-bar__inner.is-exception) { background: var(--danger); }
.run-percent {
  flex: none;
  min-width: 42px;
  text-align: right;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.run-percent.success { color: var(--ok); }
.run-percent.exception { color: var(--danger); }
.run-percent i { margin-left: 1px; font-style: normal; font-size: 10px; opacity: .55; }

/* 阶段条：小圆点 + 细线，完成是实心，当前是转圈 */
.stage-track {
  display: flex;
  align-items: center;
  margin: 12px 0 0;
  padding: 10px 0 0;
  border-top: 1px solid var(--line-soft);
  list-style: none;
}
.stage-item {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--faint);
}
.stage-dot {
  position: relative;
  flex: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1.5px solid #d7dce4;
  background: #fff;
  display: grid;
  place-items: center;
  transition: background .3s ease, border-color .3s ease;
}
.dot-check { width: 8px; height: 8px; opacity: 0; transform: scale(.4); transition: .3s ease; color: #fff; }
.stage-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stage-line { flex: 1; height: 1px; margin: 0 10px; background: var(--line-soft); }
.stage-item.done { color: var(--ink-2); }
.stage-item.done .stage-dot { border-color: var(--accent); background: var(--accent); }
.stage-item.done .dot-check { opacity: 1; transform: scale(1); }
.stage-item.done .stage-line { background: #cfe3fb; }
.stage-item.current { color: var(--ink); font-weight: 500; }
.stage-item.current .stage-dot { border-color: var(--accent); }
.stage-item.current .stage-dot::after {
  content: '';
  position: absolute;
  inset: 0;
  margin: auto;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  border: 1.5px solid rgba(0, 113, 227, .22);
  border-top-color: var(--accent);
  animation: stage-spin .8s linear infinite;
}
@keyframes stage-spin { to { transform: rotate(360deg); } }
.stage-item.failed { color: #c9342b; }
.stage-item.failed .stage-dot { border-color: var(--danger); background: var(--danger); }
.stage-item.failed .dot-check { opacity: 0; }

.run-error { margin-top: 10px; }

/* ---------- 两栏主体 ---------- */
.run-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 258px minmax(0, 1fr);
  background: #fff;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  overflow: hidden;
}

/* 左：文件与记录 */
.run-side {
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 16px 16px 18px;
  border-right: 1px solid var(--line-soft);
  overflow: hidden;
}
/* 只有分组区滚，提交盒永远贴底 —— 否则文件一多，提交/撤回就被顶出视野 */
.side-groups {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
}
.side-groups::-webkit-scrollbar { width: 8px; }
.side-groups::-webkit-scrollbar-thumb { background: #e6e9ef; border-radius: 4px; }
.side-groups::-webkit-scrollbar-thumb:hover { background: #d8dde5; }

.side-head {
  flex: none;
  margin-bottom: 10px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--muted);
}
.side-group { flex: none; padding-bottom: 10px; margin-bottom: 8px; border-bottom: 1px solid var(--line-faint); }
.side-group:last-of-type { border-bottom: 0; }
.side-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 3px;
  font-size: 11.5px;
  color: var(--ink-2);
}
.side-count { font-size: 11px; color: var(--faint); font-variant-numeric: tabular-nums; }
.side-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: calc(100% + 12px);
  min-width: 0;
  margin-left: -6px;
  padding: 5px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background .14s ease;
}
.side-item:hover:not(:disabled) { background: #f6f8fb; }
.side-item:disabled { cursor: default; }
.side-path {
  flex: 1;
  min-width: 0;
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-item:hover:not(:disabled) .side-path { color: var(--accent); }
.side-kind { flex: none; font-size: 10.5px; color: var(--faint); }
.side-item:disabled .side-path,
.side-item:disabled .side-kind { color: #c9cfd8; }
.side-empty { padding: 4px 0 4px 0; font-size: 11.5px; color: #c6ccd5; }
.side-scroll { overflow: visible; }

/* 提交盒：一组动作需要边界，所以这里保留一块浅底 */
.commit-box {
  flex: none;
  margin-top: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fafbfc;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
}
.commit-hint { font-size: 10.5px; line-height: 1.5; color: var(--faint); }
.commit-box :deep(.el-input__wrapper) { padding: 0; }
.commit-box :deep(.el-input__inner) { font-size: 12px; }
.commit-box .el-button { width: 100%; }
.commit-box .el-button + .el-button { margin-left: 0; }
.commit-state { font-size: 11.5px; line-height: 1.5; }
.commit-state.is-ok { color: var(--ok); }
.commit-state.is-warn { color: var(--warn); }

/* 右：日志 */
.log-panel { display: flex; flex-direction: column; min-width: 0; min-height: 0; padding: 14px 16px 16px; }
.log-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 10px;
}
.log-status { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 11.5px; color: var(--muted); }
.log-status strong { font-size: 12px; font-weight: 600; color: var(--ink-2); white-space: nowrap; }
.log-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: #cfd5de; }
.log-dot.active { background: #31c46a; animation: dot-pulse 1.9s ease-in-out infinite; }
.log-dot.failed { background: var(--danger); }
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(49, 196, 106, .4); }
  50% { box-shadow: 0 0 0 4px rgba(49, 196, 106, 0); }
}
.log-meta { color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.log-muted { color: var(--faint); white-space: nowrap; }
.log-actions { display: flex; align-items: center; gap: 12px; flex: none; }
.log-tabs { display: flex; align-items: center; gap: 14px; }
.log-tab {
  padding: 0 0 3px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease, border-color .14s ease;
}
.log-tab:hover { color: var(--ink-2); }
.log-tab.is-on { color: var(--ink); border-bottom-color: var(--accent); }
.follow-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: #fff;
  font: inherit;
  font-size: 11.5px;
  color: var(--muted);
  cursor: pointer;
  transition: border-color .14s ease, color .14s ease, background .14s ease;
}
.follow-btn:hover { border-color: #d8dee7; color: var(--ink-2); }
.follow-btn.on { color: #0b62c4; border-color: #cfe2f8; background: var(--accent-soft); }
.follow-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; opacity: .45; }
.follow-btn.on .follow-dot { opacity: 1; }

/* 终端：保留深色（这里是唯一需要长时间盯的地方），去掉渐变和重投影 */
.log-stream {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px 16px 14px;
  background: #12161d;
  border: 1px solid rgba(255, 255, 255, .045);
  border-radius: 10px;
  color: #c7d0de;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, .16) transparent;
}
.log-stream::-webkit-scrollbar { width: 10px; height: 10px; }
.log-stream::-webkit-scrollbar-track { background: transparent; }
.log-stream::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, .14);
  border-radius: 8px;
  border: 2px solid transparent;
  background-clip: padding-box;
}
.log-stream::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, .26); background-clip: padding-box; }
.log-stream :deep(.el-empty) { padding: 40px 0; }
.log-stream :deep(.el-empty__description p) { color: #8592a8; font-size: 12.5px; }

.agent-block { border-bottom: 1px solid rgba(255, 255, 255, .05); }
.agent-block:last-child { border-bottom: 0; }
.agent-block-head {
  position: sticky;
  top: -10px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 0 -16px;
  padding: 9px 16px;
  background: rgba(18, 22, 29, .94);
  backdrop-filter: blur(8px) saturate(1.2);
  -webkit-backdrop-filter: blur(8px) saturate(1.2);
  border-bottom: 1px solid rgba(255, 255, 255, .05);
}
.agent-stage {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #93a1b8;
}
.agent-stage.is-planner { color: #8fb8f5; }
.agent-stage.is-generator { color: #7fd2a8; }
.agent-stage.is-healer { color: #f0b090; }
.agent-stage.is-completed { color: #7fd2a8; }
.agent-block-count {
  padding: 1px 7px;
  border-radius: 5px;
  background: rgba(255, 255, 255, .06);
  color: #8592a8;
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}
.agent-block-body { padding: 6px 0 12px; }

.log-line {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: baseline;
  margin: 1px -8px;
  padding: 3px 8px;
  border-radius: 6px;
  transition: background .15s ease;
  animation: line-in .24s ease both;
}
.log-line:hover { background: rgba(255, 255, 255, .035); }
.log-line-typing { box-shadow: inset 2px 0 0 rgba(96, 165, 250, .8); }
.log-line-typing pre { color: #eaf2ff; }
.log-line-error { box-shadow: inset 2px 0 0 rgba(248, 113, 113, .65); }
.log-line-success { box-shadow: inset 2px 0 0 rgba(74, 222, 128, .45); }
.log-line-speech { background: rgba(96, 165, 250, .05); }
.log-line-speech pre { color: #eef4ff; }
.log-line-speech .log-line-type { color: #7fb2ff; font-weight: 600; }
.log-line-type { font-size: 11.5px; letter-spacing: .01em; color: #7c8aa0; white-space: nowrap; }
.log-line pre {
  margin: 0;
  color: #d6dfeb;
  white-space: pre-wrap;
  word-break: break-word;
  font: 13px/1.7 'JetBrains Mono', 'Cascadia Code', 'Fira Code', ui-monospace, SFMono-Regular, 'SF Mono', Consolas, monospace;
  letter-spacing: .012em;
}
.log-line time { font-size: 11px; color: #5f6d82; white-space: nowrap; font-family: ui-monospace, Consolas, monospace; }
.log-line-error pre { color: #ffb6b6; }
.log-line-error .log-line-type { color: #f87171; }
.log-line-success pre { color: #92f0b4; }
.log-line-success .log-line-type { color: #4ade80; }
.type-cursor {
  display: inline-block;
  width: 7px;
  height: 14px;
  margin-left: 3px;
  vertical-align: -2px;
  background: #60a5fa;
  border-radius: 2px;
  animation: cursor-blink 1.1s ease-in-out infinite;
}
@keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: .15; } }
@keyframes line-in { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }

.log-reasoning { min-width: 0; }
.log-reasoning > summary { cursor: pointer; color: #7c8aa0; font-size: 12px; line-height: 1.7; list-style-position: inside; }
.log-reasoning[open] > summary { color: #9aa8bd; }

.log-waiting {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 6px 4px;
  color: #9aa4b2;
  font: 13px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
  animation: line-in .25s ease;
}
.cc-spinner { color: #c99a2e; font-size: 14px; line-height: 1; }
.cc-text { color: #aeb8c6; letter-spacing: .02em; }

@media (prefers-reduced-motion: reduce) {
  .log-line { animation: none; }
  .type-cursor, .log-dot.active, .stage-item.current .stage-dot::after, .run-status.is-run i { animation: none; }
}

/* ---------- 弹窗：源码 / 修复 diff ---------- */
.dialog-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; color: var(--muted); font: 11.5px var(--mono); }
.dialog-loading { min-height: 300px; display: grid; place-items: center; color: var(--faint); font-size: 13px; }
.source-view {
  margin: 0;
  max-height: 70vh;
  overflow: auto;
  padding: 14px 16px;
  background: #12161d;
  border-radius: 10px;
  color: #d6dfeb;
  white-space: pre-wrap;
  word-break: break-word;
  font: 12.5px/1.65 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.heal-view { max-height: 70vh; overflow: auto; }
.heal-section-title { margin: 16px 0 8px; font-size: 12px; font-weight: 600; color: var(--ink-2); }
.heal-section-title:first-child { margin-top: 0; }
.heal-reason {
  margin: 0;
  padding: 12px 14px;
  background: #f8fafc;
  border-radius: 8px;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  font: 12.5px/1.7 var(--sans);
}
.heal-empty { padding: 6px 0; font-size: 12.5px; color: var(--faint); }
.diff-file { margin-top: 12px; border: 1px solid var(--line-soft); border-radius: 8px; overflow: hidden; }
.diff-file-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  background: #fafbfc;
  border-bottom: 1px solid var(--line-soft);
}
.diff-path { font: 12px var(--mono); color: var(--ink-2); word-break: break-all; }
.diff-view {
  margin: 0;
  max-height: 44vh;
  overflow: auto;
  padding: 8px 0;
  background: #fff;
  font: 12px/1.6 var(--mono);
}
.diff-line { display: block; padding: 0 12px; white-space: pre-wrap; word-break: break-word; color: var(--ink-2); }
.diff-line.add { background: #eefaf1; color: #1a7f37; }
.diff-line.del { background: #fdeef0; color: #cf222e; }
.diff-line.hunk { background: #f2f7fd; color: #0550ae; }
.diff-line.meta { color: var(--faint); }

/* ---------- 矮屏 / 窄屏 ---------- */
@media (max-height: 640px) {
  .run-page { height: auto; }
  .run-body { min-height: 520px; }
}
@media (max-width: 980px) {
  .run-page { height: auto; }
  .run-body { grid-template-columns: 1fr; }
  .run-side { border-right: 0; border-bottom: 1px solid var(--line-soft); overflow: visible; }
  .log-panel { min-height: 460px; }
  .log-stream { min-height: 360px; max-height: 68vh; }
  .stage-track { flex-wrap: wrap; row-gap: 8px; }
  .stage-item { flex: 0 0 auto; }
  .stage-line { display: none; }
  .run-head-bar { flex-wrap: wrap; }
  .meta-item.url { max-width: 220px; }
  .log-toolbar { flex-wrap: wrap; }
  .log-status { flex-wrap: wrap; }
  .log-meta { white-space: normal; }
}
</style>
