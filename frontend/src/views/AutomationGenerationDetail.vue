<template>
  <div class="generation-detail-page">
    <div class="page-header">
      <div class="header-main">
        <div class="eyebrow">AUTOMATION RUN</div>
        <h1>Agent 执行详情</h1>
        <p :class="['requirement', { 'is-collapsed': !requirementExpanded }]">{{ generation?.requirement || '加载任务中...' }}</p>
        <button v-if="requirementLong" type="button" class="req-toggle" @click="requirementExpanded = !requirementExpanded">
          {{ requirementExpanded ? '收起需求' : '展开完整需求' }}<span :class="['req-caret', { up: requirementExpanded }]"></span>
        </button>
      </div>
      <div class="header-actions">
        <el-button v-if="taskRunning" type="danger" plain :loading="cancelling" @click="cancelGeneration">停止任务</el-button>
        <el-button @click="$router.push({ path: '/scripts', query: { project_id: projectId } })">返回任务记录</el-button>
        <el-button v-if="generation?.status === 'completed'" @click="download">下载 ZIP</el-button>
      </div>
    </div>

    <el-card class="status-card">
      <div class="status-summary">
        <el-tag :type="statusType(generation?.status)" size="large" effect="light" class="status-tag">{{ statusLabel(generation?.status) }}</el-tag>
        <span class="stage-label">当前阶段 · {{ stageLabel(generation?.stage) }}</span>
        <span class="meta-divider"></span>
        <span class="status-meta url" :title="generation?.config?.base_url">{{ generation?.config?.base_url || '-' }}</span>
        <span class="meta-divider"></span>
        <span class="status-meta">修复 <b>{{ generation?.heal_attempts || 0 }}</b> 次</span>
        <el-button v-if="polling" text type="primary" class="refresh-btn" @click="load">刷新</el-button>
      </div>
      <div class="progress-row">
        <el-progress class="run-progress" :percentage="stagePercent" :status="progressStatus" :stroke-width="10" :show-text="false" />
        <span :class="['progress-num', progressStatus]">{{ stagePercent }}<i>%</i></span>
      </div>
      <div class="stage-track">
        <div v-for="item in stages" :key="item.key" :class="['stage-item', stageClass(item.key)]">
          <span class="stage-dot"><svg viewBox="0 0 12 12" class="dot-check"><path d="M2.6 6.3 4.8 8.4 9.4 3.9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
          <span class="stage-name">{{ item.label }}</span>
        </div>
      </div>
      <el-alert v-if="generation?.error" type="error" :closable="false" show-icon>{{ generation.error }}</el-alert>
    </el-card>

    <el-card class="workspace-card">
      <template #header>
        <div class="section-header">
          <span>执行详情</span>
          <div class="section-actions">
            <span class="live-label" v-if="polling"><i></i>实时更新</span>
            <el-button text @click="load">刷新</el-button>
          </div>
        </div>
      </template>
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
            <div class="artifact-group-title"><span>修复记录</span><span>{{ healerEvents.length }}</span></div>
            <button v-for="(event, index) in healerEvents" :key="event.sequence" class="artifact-item" @click="openHealerEvent(event, index)">
              <span>修复记录 #{{ index + 1 }}</span><small>{{ formatDate(event.created_at) }}</small>
            </button>
            <div v-if="!healerEvents.length" class="sidebar-empty">暂无记录</div>
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
            <div class="field-hint">只会提交本次 Agent 产生的计划、脚本和允许的 Playwright 配置，不会提交其他工作区修改，也不会 push。</div>
            <el-input v-model="commitMessage" :disabled="generation?.commit_status === 'committed'" placeholder="test: generate Playwright tests" />
            <el-button type="primary" :loading="committing" :disabled="!canCommit" @click="commit">创建本地 Commit</el-button>
            <div v-if="generation?.commit_status === 'committed'" class="commit-success">已提交 {{ generation.commit_hash }}</div>
          </div>
        </aside>

        <section class="log-panel">
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
            <el-empty v-if="!events.length" description="等待 OpenCode 输出日志" />
            <el-empty v-else-if="!groupedEvents.length" description="当前无 Agent 输出，可切换到「全部」查看工具与系统日志" />
          </div>
        </section>
      </div>
    </el-card>

    <el-dialog v-model="artifactDialog.visible" :title="artifactDialog.title" width="min(1100px, 92vw)" top="5vh" destroy-on-close>
      <div class="dialog-meta">
        <el-tag size="small">{{ artifactDialog.kindLabel }}</el-tag>
        <span>{{ artifactDialog.path }}</span>
      </div>
      <div v-if="artifactDialog.loading" class="dialog-loading">正在读取内容...</div>
      <pre v-else class="source-view">{{ artifactDialog.content || '暂无内容' }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const route = useRoute()
const projectId = computed(() => route.query.project_id || '')
const generation = ref(null)
const events = ref([])
const artifacts = ref([])
const autoFollow = ref(true)
const logContainer = ref(null)
const artifactDialog = ref({ visible: false, loading: false, title: '', path: '', kindLabel: '', content: '' })
let artifactRequestId = 0
const commitMessage = ref('test: generate Playwright tests')
const committing = ref(false)
const cancelling = ref(false)
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
  { key: 'test_run', label: '执行测试' },
  { key: 'healer', label: '失败修复' },
  { key: 'completed', label: '完成' }
]
const taskRunning = computed(() => ['running', 'pending'].includes(generation.value?.status) || (['interrupted', 'failed'].includes(generation.value?.status) && generation.value?.process_id))
const polling = computed(() => taskRunning.value || eventsSyncFailed.value)
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
const healerEvents = computed(() => events.value.filter(event => event.stage === 'healer' || /healer|修复/i.test(event.message || '')))
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
const canCommit = computed(() => generation.value?.status === 'completed' && generation.value?.commit_status !== 'committed' && generation.value?.files?.length > 0)

onMounted(() => {
  load()
  startTypewriter()
  timer = window.setInterval(() => {
    heartbeat.value = Date.now()
    if (!generation.value || taskRunning.value || (eventsSyncFailed.value && Date.now() >= nextEventSyncAt.value)) load()
  }, 1500)
})
onUnmounted(() => { if (timer) window.clearInterval(timer); stopTypewriter() })

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
    content: ''
  }
  try {
    const response = await api.getAutomationArtifact(route.params.id, projectId.value, item.path)
    if (requestId === artifactRequestId) artifactDialog.value.content = response.data.content || ''
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

function openHealerEvent(event, index) {
  artifactRequestId += 1
  artifactDialog.value = {
    visible: true,
    loading: false,
    title: `修复记录 #${index + 1}`,
    path: `Healer · ${formatDate(event.created_at)}`,
    kindLabel: '修复记录',
    content: event.message || '暂无修复记录内容'
  }
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
  return ({ orchestrator: '', planner: 'warning', generator: 'success', test_run: 'info', healer: 'danger', completed: 'success', failed: 'danger', interrupted: 'danger', queued: 'info' })[stage] ?? 'info'
}
function stageLabel(stage) { return ({ queued: '等待启动', orchestrator: '总控协调', planner: 'Planner 规划探索', generator: 'Generator 生成脚本', test_run: '执行 Playwright 测试', healer: 'Healer 修复', completed: '完成', failed: '失败', interrupted: '已中断' })[stage] || stage || '-' }
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
.generation-detail-page { max-width:1440px; margin:0 auto; height:calc(100dvh - 144px); display:flex; flex-direction:column; gap:16px; min-height:0; }
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; flex:none; }
.header-main { min-width:0; }
.header-actions { display:flex; gap:8px; flex:none; }
.eyebrow { color:#0071e3; font-size:11px; font-weight:700; letter-spacing:1.4px; margin-bottom:6px; }
h1 { margin:0; color:#1d1d1f; font-size:26px; font-weight:650; letter-spacing:-.4px; }
.requirement { margin:8px 0 0; color:#6e6e73; font-size:13.5px; line-height:1.6; white-space:pre-wrap; max-width:860px; }
.requirement.is-collapsed { display:-webkit-box; -webkit-line-clamp:2; line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; white-space:normal; }
.req-toggle { display:inline-flex; align-items:center; gap:6px; margin-top:7px; padding:0; border:0; background:transparent; color:#0071e3; font-size:12.5px; font-weight:500; cursor:pointer; transition:color .15s ease; }
.req-toggle:hover { color:#0077ed; }
.req-caret { width:7px; height:7px; border-right:1.6px solid currentColor; border-bottom:1.6px solid currentColor; transform:rotate(45deg) translateY(-2px); transition:transform .22s ease; }
.req-caret.up { transform:rotate(-135deg) translateY(-1px); }
.status-card { flex:none; }
.status-card :deep(.el-card__body) { padding:18px 22px; }
.status-summary { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.status-tag { font-weight:600; letter-spacing:.2px; }
.stage-label { display:inline-flex; align-items:center; font-weight:600; color:#1d1d1f; font-size:14px; }
.meta-divider { width:1px; height:14px; background:#e4e7ed; flex:none; }
.status-meta { color:#86909c; font-size:12.5px; }
.status-meta.url { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.status-meta b { color:#0071e3; font-weight:700; }
.refresh-btn { margin-left:auto; }
.progress-row { display:flex; align-items:center; gap:14px; margin:16px 0 2px; }
.run-progress { flex:1; }
.run-progress :deep(.el-progress-bar__outer) { border-radius:8px; background:#eef1f6; }
.run-progress :deep(.el-progress-bar__inner) { border-radius:8px; background:linear-gradient(90deg,#0a84ff,#0071e3); transition:width .5s cubic-bezier(.4,0,.2,1); }
.progress-num { font-size:20px; font-weight:700; line-height:1; color:#0071e3; font-variant-numeric:tabular-nums; min-width:66px; text-align:right; font-family:-apple-system,'SF Pro Display',system-ui,sans-serif; }
.progress-num i { font-style:normal; font-size:12px; font-weight:600; margin-left:1px; opacity:.55; }
.progress-num.success { color:#34c759; }
.progress-num.exception { color:#ff3b30; }
.stage-track { display:flex; align-items:center; gap:0; margin-top:18px; }
.stage-item { flex:1; display:flex; align-items:center; gap:8px; color:#a8abb2; font-size:12px; font-weight:500; position:relative; min-width:0; }
.stage-item:not(:last-child)::after { content:''; height:2px; flex:1; margin:0 10px; border-radius:2px; background:#e4e7ed; transition:background .4s ease; }
.stage-item.done:not(:last-child)::after { background:linear-gradient(90deg,#0071e3,#5aa9ff); }
.stage-dot { position:relative; width:18px; height:18px; border-radius:50%; background:#fff; border:2px solid #dcdfe6; flex:none; display:grid; place-items:center; transition:all .3s ease; }
.dot-check { width:10px; height:10px; opacity:0; transform:scale(.4); transition:all .3s ease; color:#fff; }
.stage-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.stage-item.done { color:#0071e3; }
.stage-item.done .stage-dot { background:#0071e3; border-color:#0071e3; }
.stage-item.done .dot-check { opacity:1; transform:scale(1); }
.stage-item.current { color:#0071e3; font-weight:600; }
.stage-item.current .stage-dot { border-color:#0071e3; box-shadow:0 0 0 4px rgba(0,113,227,.14); animation:stage-pulse 1.8s ease-in-out infinite; }
.stage-item.current .stage-dot::after { content:''; width:7px; height:7px; border-radius:50%; background:#0071e3; }
.stage-item.failed { color:#ff3b30; }
.stage-item.failed .stage-dot { background:#ff3b30; border-color:#ff3b30; box-shadow:0 0 0 4px rgba(255,59,48,.14); }
@keyframes stage-pulse { 0%,100% { box-shadow:0 0 0 4px rgba(0,113,227,.14); } 50% { box-shadow:0 0 0 7px rgba(0,113,227,.05); } }
.live-label { display:inline-flex; align-items:center; color:#34c759; font-size:12px; font-weight:500; }
.live-label i { width:7px; height:7px; background:#34c759; border-radius:50%; margin-right:5px; animation:live-blink 1.6s ease-in-out infinite; }
@keyframes live-blink { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.section-header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.section-header > span { font-size:15px; font-weight:650; color:#1d1d1f; }
.section-actions { display:flex; align-items:center; gap:12px; }
.workspace-card { flex:1; min-height:0; display:flex; flex-direction:column; }
.workspace-card :deep(.el-card__header) { flex:none; padding:14px 22px; }
.workspace-card :deep(.el-card__body) { flex:1; min-height:0; display:flex; flex-direction:column; padding:18px 22px; }
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
.field-hint { color:#909399; font-size:11.5px; line-height:1.55; }
.commit-success { color:#34c759; font-size:12px; font-weight:500; }
.log-panel { display:flex; min-width:0; min-height:0; flex-direction:column; }
.log-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:0 0 12px; flex:none; }
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
.log-dot { width:8px; height:8px; border-radius:50%; background:#c0c4cc; flex:none; transition:box-shadow .3s ease; }.log-dot.active { background:#4ade80; box-shadow:0 0 0 3px rgba(74,222,128,.16), 0 0 10px rgba(74,222,128,.55); animation:dot-pulse 1.9s ease-in-out infinite; }.log-dot.waiting { background:#fbbf24; box-shadow:0 0 0 3px rgba(251,191,36,.16); }.log-dot.failed { background:#f87171; box-shadow:0 0 0 3px rgba(248,113,113,.16); }
@keyframes dot-pulse { 0%,100% { box-shadow:0 0 0 3px rgba(74,222,128,.16), 0 0 10px rgba(74,222,128,.55); } 50% { box-shadow:0 0 0 4px rgba(74,222,128,.08), 0 0 16px rgba(74,222,128,.75); } }
.log-stream { flex:1; min-height:0; overflow:auto; padding:12px 18px; background:linear-gradient(180deg,#151e2e 0%,#0f1626 100%); border:1px solid rgba(255,255,255,.06); border-radius:14px; color:#c7d0de; box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 10px 34px rgba(0,0,0,.28); scrollbar-width:thin; scrollbar-color:rgba(255,255,255,.16) transparent; }
.log-stream :deep(.el-empty) { padding:48px 0; }
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
@media (prefers-reduced-motion: reduce) { .log-line { animation:none; } .type-cursor, .log-dot.active, .live-label i, .stage-item.current .stage-dot { animation:none; } }
.log-line-type { color:#7c8aa0; font-size:12px; white-space:nowrap; letter-spacing:.01em; }
.log-line pre { margin:0; color:#d6dfeb; white-space:pre-wrap; word-break:break-word; font:13.5px/1.7 'JetBrains Mono','Cascadia Code','Fira Code',ui-monospace,SFMono-Regular,'SF Mono',Consolas,monospace; letter-spacing:.012em; }
.log-line time { color:#5f6d82; font-size:11.5px; white-space:nowrap; font-family:'JetBrains Mono',ui-monospace,Consolas,monospace; letter-spacing:.02em; }
.log-line-error pre { color:#ffb6b6; }.log-line-error .log-line-type { color:#f87171; }.log-line-success pre { color:#92f0b4; }.log-line-success .log-line-type { color:#4ade80; }
.dialog-meta { display:flex; align-items:center; gap:8px; margin-bottom:10px; color:#909399; font:12px ui-monospace,monospace; }.source-view { margin:0; max-height:70vh; overflow:auto; background:#1f2937; color:#d1d5db; border-radius:6px; padding:16px; white-space:pre-wrap; word-break:break-word; font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; }.dialog-loading { min-height:300px; display:grid; place-items:center; color:#909399; }
@media (max-height:760px) { .generation-detail-page { height:auto; min-height:calc(100dvh - 144px); }.workspace-card { min-height:600px; }.log-stream { min-height:440px; } }
@media (max-width:900px) { .generation-detail-page { height:auto; gap:14px; }.page-header { flex-direction:column; }.header-actions { width:100%; }.execution-layout { grid-template-columns:1fr; }.artifact-sidebar { border-right:0; border-bottom:1px solid #ebeef5; padding:0 0 14px; max-height:none; overflow:visible; }.log-stream { min-height:420px; max-height:70vh; }.stage-track { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; min-width:0; }.stage-item:not(:last-child)::after { display:none; }.log-toolbar-status { flex-wrap:wrap; }.log-meta { white-space:normal; }.status-card { overflow:auto; }.status-meta.url { max-width:200px; } }
</style>
