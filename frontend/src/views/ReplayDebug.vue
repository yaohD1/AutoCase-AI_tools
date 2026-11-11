<template>
  <div class="dbg-page">
    <header class="dbg-head">
      <button type="button" class="dbg-back" title="回到录制" @click="leave">←</button>
      <div class="dbg-title">
        <h1>调试回放</h1>
        <span class="dbg-sub">{{ engineLabel }}<template v-if="state.total"> · {{ state.total }} 步</template></span>
      </div>
      <span :class="['dbg-badge', state.status]">
        {{ statusText }}<template v-if="state.elapsed"> · {{ state.elapsed }}s</template>
      </span>
      <div class="dbg-head-actions">
        <el-button size="small" :loading="restarting" @click="restart">重新开始</el-button>
        <el-button size="small" text @click="leave">退出</el-button>
      </div>
    </header>

    <div v-if="bootError" class="dbg-boot-error">
      <div class="dbg-boot-title">没能启动调试回放</div>
      <div class="dbg-boot-text">{{ bootError }}</div>
      <el-button size="small" type="primary" @click="restart">重试</el-button>
    </div>

    <div v-else class="dbg-body">
      <section class="dbg-stage">
        <div class="dbg-viewport">
          <div class="dbg-chrome">
            <span :class="['dbg-live', { on: playing }]"></span>
            <span class="dbg-addr" :title="state.url || ''">{{ prettyUrl || 'about:blank' }}</span>
            <span class="dbg-chrome-tag">沙箱</span>
          </div>
          <div class="dbg-canvas">
            <img v-if="streamSrc" :src="streamSrc" class="dbg-img" alt="沙箱中的被测页面" @error="onImgError">
            <!-- 停在第 1 步时页面还是 about:blank，这里要是还写「正在打开」，
                 用户会以为卡在加载上。暂停态直接说清楚它在等谁。 -->
            <div v-if="!streamSrc || !hasFrame" class="dbg-veil">
              <template v-if="paused">
                <span>已暂停在第 {{ state.index || 1 }} 步，点「继续」开始</span>
              </template>
              <template v-else>
                <span class="dbg-spinner"></span>
                <span>正在打开沙箱浏览器…</span>
              </template>
            </div>
          </div>
        </div>

        <div class="dbg-meter">
          <span class="dbg-meter-text">第 {{ state.index || 0 }} / {{ state.total || 0 }} 步</span>
          <div class="dbg-bar"><i :style="{ width: progress + '%' }"></i></div>
          <span class="dbg-meter-hint">{{ statusText }}</span>
        </div>

        <div v-if="state.error" class="dbg-error">
          <span class="dbg-error-dot"></span>
          <span class="dbg-error-text">{{ state.error }}</span>
        </div>
      </section>

      <aside class="dbg-side">
        <div class="dbg-ctrl">
          <button type="button" class="dbg-btn" :disabled="!canPlay" :title="playing ? '暂停' : '继续'"
                  @click="togglePlay">
            <span v-if="playing" class="dbg-ico-pause"><i></i><i></i></span>
            <span v-else class="dbg-ico-play"></span>
          </button>
          <button type="button" class="dbg-btn" :title="'走一步'" :disabled="!canStep" @click="stepOnce">
            <span class="dbg-ico-step"><i></i><b></b></span>
          </button>
          <button type="button" class="dbg-btn" title="停止" :disabled="!canPlay && !canStep" @click="stopRun">
            <span class="dbg-ico-stop"></span>
          </button>
          <div class="dbg-speeds">
            <button v-for="opt in SPEEDS" :key="opt.delay" type="button"
                    :class="['dbg-speed', { on: Math.abs(delay - opt.delay) < 0.01 }]"
                    :title="opt.hint" @click="setSpeed(opt.delay)">{{ opt.label }}</button>
          </div>
        </div>

        <ol ref="stepList" class="dbg-steps">
          <li v-for="(item, i) in state.steps" :key="item.no || i"
              :class="['dbg-step', item.status, { current: isCurrent(i) }]"
              :data-idx="i">
            <span class="dbg-step-dot">
              <template v-if="item.status === 'ok'">✓</template>
              <template v-else-if="item.status === 'failed'">!</template>
              <template v-else-if="isCurrent(i)">▶</template>
            </span>
            <span class="dbg-step-kind">{{ item.type }}</span>
            <span class="dbg-step-name" :title="stepTitle(item)">{{ stepTitle(item) }}</span>
            <span v-if="item.ms != null" class="dbg-step-ms">{{ item.ms }}ms</span>
          </li>
          <li v-if="!state.steps.length" class="dbg-step is-empty">还没有步骤</li>
        </ol>

        <div class="dbg-detail">
          <template v-if="currentStep">
            <div class="dbg-detail-head">
              第 {{ currentStep.no || '—' }} 步 · {{ currentStep.type }}
              <span v-if="currentStep.intent" class="dbg-detail-intent">{{ currentStep.intent }}</span>
            </div>
            <div v-if="currentStep.used" class="dbg-code">{{ currentStep.used.syntax }}</div>
            <div v-else-if="currentStep.url" class="dbg-code">{{ currentStep.url }}</div>
            <div v-else class="dbg-code is-muted">{{ stepTitle(currentStep) }}</div>
            <div class="dbg-detail-foot">
              <template v-if="currentStep.used">
                <span>命中 {{ currentStep.used.matches == null ? '—' : currentStep.used.matches }} 个</span>
                <span class="dbg-sep">·</span>
                <span>{{ currentStep.used.matches > 1 && currentStep.used.nth ? `取第 ${currentStep.used.nth} 个` : '唯一' }}</span>
                <span v-if="currentStep.ms != null" class="dbg-sep">·</span>
                <span v-if="currentStep.ms != null">{{ currentStep.ms }}ms</span>
              </template>
              <span v-else-if="currentStep.status === 'pending'" class="dbg-faint">等待执行</span>
            </div>
            <div v-if="currentStep.error" class="dbg-detail-err">{{ currentStep.error }}</div>
          </template>
          <div v-else class="dbg-faint">回放还没开始</div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const route = useRoute()
const router = useRouter()

const SPEEDS = [
  { label: '慢', delay: 1.2, hint: '每步停 1.2 秒，看清页面怎么变' },
  { label: '标准', delay: 0.55, hint: '每步停 0.55 秒' },
  { label: '快', delay: 0.2, hint: '每步停 0.2 秒，只看流程通不通' },
]

// 后端在没有会话时只回一个 {status:'idle'}，不补齐的话模板里 steps.length 直接炸。
const EMPTY_STATE = { status: 'idle', steps: [], index: 0, total: 0, error: null, url: null,
                      framework: '', elapsed: 0, paused: false }
const state = ref({ ...EMPTY_STATE })
const delay = ref(0.55)
const bootError = ref('')
const restarting = ref(false)
const hasFrame = ref(false)
const streamSrc = ref('')
const stepList = ref(null)
let pollTimer = null
let imgRetry = 0

const status = computed(() => state.value.status || 'idle')
const total = computed(() => state.value.total || state.value.steps.length || 0)
const currentIndex = computed(() => Math.max(0, (state.value.index || 1) - 1))
const currentStep = computed(() => state.value.steps[currentIndex.value] || null)
const progress = computed(() => (total.value ? Math.min(100, (state.value.index || 0) / total.value * 100) : 0))
const playing = computed(() => ['starting', 'running'].includes(status.value))
const paused = computed(() => status.value === 'paused')
const finished = computed(() => ['done', 'failed', 'stopped'].includes(status.value))
const canPlay = computed(() => paused.value || playing.value)
const canStep = computed(() => paused.value || playing.value)
const engineLabel = computed(() => (state.value.framework === 'robot-selenium' ? 'Selenium 沙箱' : 'Playwright 沙箱'))
const prettyUrl = computed(() => {
  const url = state.value.url
  if (!url) return ''
  try {
    const u = new URL(url)
    return u.host + u.pathname + u.search
  } catch (error) {
    return url
  }
})

const STATUS_TEXT = {
  idle: '未开始',
  starting: '启动中',
  running: '运行中',
  paused: '已暂停',
  done: '已完成',
  failed: '失败',
  stopped: '已停止',
}
const statusText = computed(() => STATUS_TEXT[status.value] || status.value)

function stepTitle(item) {
  if (!item) return ''
  return item.alias || item.label || item.url || item.type || ''
}

function isCurrent(i) {
  return i === currentIndex.value && !finished.value
}

function streamUrl() {
  return `/api/automation/recording/debug/stream?r=${Date.now()}`
}

function onImgError() {
  // 会话结束（或中间层掐断）时后端会主动断开，浏览器触发 error。
  // 重连一次就能拿回最后一帧——不重连的话画面停在裂图，看不出跑完了没有。
  if (imgRetry >= 6) return
  imgRetry += 1
  setTimeout(() => { streamSrc.value = streamUrl() }, 500)
}

function apply(data) {
  if (!data || !data.status) return
  const merged = { ...EMPTY_STATE, ...data }
  if (!Array.isArray(merged.steps)) merged.steps = []
  state.value = merged
  if (merged.steps.some(s => s.status !== 'pending')) hasFrame.value = true
  followCurrent()
}

function followCurrent() {
  nextTick(() => {
    const box = stepList.value
    if (!box) return
    const el = box.querySelector('.dbg-step.current')
    if (el) el.scrollIntoView({ block: 'nearest' })
  })
}

async function refresh() {
  try {
    const { data } = await api.getDebugReplayState()
    apply(data)
    return data
  } catch (error) {
    return null
  }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    const data = await refresh()
    if (data && ['done', 'failed', 'stopped', 'idle'].includes(data.status)) stopPoll()
  }, 260)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function boot() {
  bootError.value = ''
  // 已经有一场在跑（刷新页面、从别处回来）就接着看，不要重开一个把人家顶掉。
  const existing = await refresh()
  if (existing && ['starting', 'running', 'paused'].includes(existing.status)) {
    streamSrc.value = streamUrl()
    startPoll()
    return
  }
  try {
    const { data } = await api.startDebugReplay({
      framework: route.query.framework || '',
      delay: delay.value,
      headless: true,
    })
    apply(data)
    imgRetry = 0
    streamSrc.value = streamUrl()
    startPoll()
  } catch (error) {
    bootError.value = errorText(error)
  }
}

async function restart() {
  restarting.value = true
  try {
    await api.controlDebugReplay('stop').catch(() => {})
    await new Promise(resolve => setTimeout(resolve, 600))
    const { data } = await api.startDebugReplay({
      framework: route.query.framework || state.value.framework || '',
      delay: delay.value,
      headless: true,
    })
    apply(data)
    hasFrame.value = false
    imgRetry = 0
    streamSrc.value = streamUrl()
    startPoll()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    restarting.value = false
  }
}

async function togglePlay() {
  const action = playing.value ? 'pause' : 'resume'
  try {
    const { data } = await api.controlDebugReplay(action)
    apply(data)
    if (action === 'resume') startPoll()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function stepOnce() {
  try {
    const { data } = await api.controlDebugReplay('step')
    apply(data)
    startPoll()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function stopRun() {
  try {
    const { data } = await api.controlDebugReplay('stop')
    apply(data)
    stopPoll()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function setSpeed(value) {
  delay.value = value
  try {
    const { data } = await api.controlDebugReplay('speed', { delay: value })
    apply(data)
  } catch (error) {
    // 速度改不动不影响回放本身，静默即可
  }
}

function errorText(error) {
  return (error && error.response && error.response.data && error.response.data.error)
    || (error && error.message) || '操作失败'
}

function leave() {
  api.controlDebugReplay('stop').catch(() => {})
  router.push('/recording')
}

onMounted(boot)
onBeforeUnmount(() => {
  stopPoll()
  api.controlDebugReplay('stop').catch(() => {})
})
</script>

<style scoped>
.dbg-page {
  /* 这个页面挂在 el-main 里，而录制相关页面是 workspace 模式：el-main 有 22/32 的
     内边距、外面还有 56px 顶栏。用 100vh 会多出 100 多像素，底部详情直接被切掉。
     直接钉在顶栏以下，跟 el-main 的内边距彻底解耦。 */
  position: fixed;
  top: 56px;
  right: 0;
  bottom: 0;
  left: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
  color: var(--ink);
}

.dbg-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
  height: 54px;
  padding: 0 18px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--line-soft);
}

.dbg-back {
  width: 28px;
  height: 28px;
  border: 1px solid var(--line-ctl);
  border-radius: 8px;
  background: transparent;
  color: var(--ink-2);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.dbg-back:hover { border-color: var(--line-ctl-hover); color: var(--accent); }

.dbg-title { display: flex; align-items: baseline; gap: 9px; margin-right: 4px; }
.dbg-title h1 { margin: 0; font-size: 15px; font-weight: 600; color: var(--ink); }
.dbg-sub { font-size: 12px; color: var(--muted); }

.dbg-badge {
  font-size: 11.5px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--bg-sunken);
  color: var(--muted);
}

.dbg-badge.running { background: var(--accent-soft); color: var(--accent-ink); }
.dbg-badge.paused { background: #fdf3e3; color: #8a5a12; }
.dbg-badge.done { background: #eaf5ee; color: var(--ok); }
.dbg-badge.failed { background: var(--danger-soft); color: var(--danger); }
.dbg-badge.stopped { background: var(--bg-sunken); color: var(--muted); }

.dbg-meter-hint { font-size: 11.5px; color: var(--faint); flex: none; }
.dbg-head-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }

.dbg-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

.dbg-stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}

.dbg-viewport {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 1px solid var(--line-soft);
  background: var(--bg-card);
  overflow: hidden;
}

/* 地址条：让它看起来就是一扇浏览器，而不是一张飘着的图片。
   用户判断「这一步在哪个页面」靠的就是这一行。 */
.dbg-chrome {
  flex: none;
  display: flex;
  align-items: center;
  gap: 9px;
  height: 32px;
  padding: 0 12px;
  background: var(--bg-sunken);
  border-bottom: 1px solid var(--line-soft);
}

.dbg-live {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
  flex: none;
}

.dbg-live.on { background: var(--ok); box-shadow: 0 0 0 3px rgba(30, 122, 70, .12); }

.dbg-addr {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--ink-2);
  background: var(--bg-card);
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  padding: 2px 10px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dbg-chrome-tag {
  flex: none;
  font-size: 10px;
  color: var(--faint);
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  padding: 0 5px;
  line-height: 15px;
}

.dbg-canvas {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px;
  background: var(--bg-stage);
}

.dbg-img {
  max-width: 100%;
  max-height: 100%;
  display: block;
  object-fit: contain;
  /* 被测页面大多白底，画布也浅色，不描边就分不清哪里是页面边界 */
  box-shadow: 0 0 0 1px var(--line-soft);
  border-radius: 2px;
}

.dbg-veil {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 13px;
  color: var(--muted);
  background: var(--bg-sunken);
}

.dbg-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--line-ctl);
  border-top-color: var(--accent);
  animation: dbg-spin .8s linear infinite;
}

@keyframes dbg-spin { to { transform: rotate(360deg); } }

.dbg-meter { display: flex; align-items: center; gap: 10px; flex: none; }
.dbg-meter-text { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.dbg-bar { flex: 1; height: 3px; border-radius: 2px; background: var(--line-soft); overflow: hidden; }
.dbg-bar i { display: block; height: 100%; background: var(--accent); transition: width .25s ease; }

.dbg-error {
  flex: none;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--danger-soft);
  border: 1px solid var(--danger-line);
}

.dbg-error-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--danger); margin-top: 5px; flex: none; }
.dbg-error-text { font-size: 12.5px; color: var(--danger); line-height: 1.5; word-break: break-word; }

.dbg-side {
  width: clamp(300px, 23%, 392px);
  flex: none;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border-left: 1px solid var(--line-soft);
}

.dbg-ctrl {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-soft);
}

.dbg-btn {
  width: 30px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line-ctl);
  border-radius: 8px;
  background: var(--bg-card);
  cursor: pointer;
}

.dbg-btn:hover:not(:disabled) { border-color: var(--line-ctl-hover); }
.dbg-btn:disabled { opacity: .4; cursor: default; }

.dbg-ico-play { width: 0; height: 0; border-left: 8px solid var(--ink-2); border-top: 5px solid transparent; border-bottom: 5px solid transparent; margin-left: 2px; }
.dbg-ico-pause { display: flex; gap: 3px; }
.dbg-ico-pause i { width: 2.5px; height: 10px; background: var(--accent); }
.dbg-ico-step { display: flex; align-items: center; }
.dbg-ico-step i { width: 0; height: 0; border-left: 6px solid var(--ink-2); border-top: 5px solid transparent; border-bottom: 5px solid transparent; }
.dbg-ico-step b { width: 2px; height: 10px; background: var(--ink-2); margin-left: 1px; }
.dbg-ico-stop { width: 8px; height: 8px; background: var(--ink-2); border-radius: 1.5px; }

.dbg-speeds { margin-left: auto; display: flex; gap: 2px; padding: 2px; border-radius: 7px; background: var(--bg-sunken); }
.dbg-speed {
  border: 0;
  background: transparent;
  font-size: 11.5px;
  color: var(--muted);
  padding: 2px 8px;
  border-radius: 5px;
  cursor: pointer;
}
.dbg-speed.on { background: var(--bg-card); color: var(--accent-ink); }

.dbg-steps {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0;
  padding: 6px 0;
  list-style: none;
  background: var(--bg-sunken);
}

.dbg-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 12.5px;
  color: var(--muted);
  border-left: 2px solid transparent;
}

.dbg-step.current { background: var(--accent-soft); border-left-color: var(--accent); color: var(--accent-ink); }
.dbg-step.ok { color: var(--ink-2); }
.dbg-step.failed { background: var(--danger-soft); border-left-color: var(--danger); color: var(--danger); }

.dbg-step-dot {
  width: 15px;
  height: 15px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line-ctl);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  line-height: 1;
  color: var(--ink-inverse);
}

.dbg-step.ok .dbg-step-dot { background: var(--ok); border-color: var(--ok); }
.dbg-step.failed .dbg-step-dot { background: var(--danger); border-color: var(--danger); }
.dbg-step.current .dbg-step-dot { background: var(--accent); border-color: var(--accent); }

.dbg-step-kind {
  flex: none;
  font-size: 10px;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--muted);
  background: var(--bg-sunken);
  border-radius: 4px;
  padding: 1px 5px;
}

.dbg-step.current .dbg-step-kind { background: var(--accent-soft-hover); color: var(--accent-ink); }
.dbg-step-name { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dbg-step-ms { flex: none; font-size: 10.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.dbg-step.is-empty { color: var(--faint); }

.dbg-detail {
  flex: none;
  min-height: 132px;
  background: var(--bg-card);
  border-top: 1px solid var(--line-soft);
  padding: 11px 12px 13px;
}

.dbg-detail-head {
  font-size: 11.5px;
  color: var(--muted);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.dbg-detail-intent { font-size: 10.5px; color: var(--faint); }

.dbg-code {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-2);
  background: var(--bg-sunken);
  border-radius: 7px;
  padding: 8px 9px;
  min-height: 36px;
  max-height: 118px;
  overflow: auto;
  word-break: break-all;
}

.dbg-code.is-muted { color: var(--faint); }
.dbg-detail-foot { margin-top: 7px; display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); }
.dbg-sep { color: var(--faint); }
.dbg-faint { font-size: 11.5px; color: var(--faint); }
.dbg-detail-err { margin-top: 7px; font-size: 11.5px; color: var(--danger); line-height: 1.5; }

.dbg-boot-error {
  margin: 60px auto;
  max-width: 460px;
  text-align: center;
  padding: 26px;
  border-radius: 14px;
  border: 1px solid var(--line-soft);
  background: var(--bg-card);
}

.dbg-boot-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.dbg-boot-text { font-size: 12.5px; color: var(--muted); line-height: 1.6; margin-bottom: 16px; word-break: break-word; }

@media (max-width: 900px) {
  .dbg-side { width: 280px; }
}
</style>
