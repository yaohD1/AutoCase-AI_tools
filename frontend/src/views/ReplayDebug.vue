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
      <span v-if="suspectCount" class="dbg-badge warn"
            title="这些步骤执行成功，但命中元素与录制时不一致">
        ⚠ {{ suspectCount }} 步疑似点错
      </span>
      <div class="dbg-head-actions">
        <button type="button" class="dbg-icon" title="模型设置" @click="openModels">⚙</button>
        <button type="button" :class="['dbg-icon', { on: immersive }]"
                :title="immersive ? '退出放大（Esc）' : '放大预览（收起右侧栏）'"
                @click="toggleImmersive">⤢</button>
        <el-button size="small" :loading="restarting" @click="restart">重新开始</el-button>
        <el-button size="small" text @click="leave">退出</el-button>
      </div>
    </header>

    <div v-if="bootError" class="dbg-boot-error">
      <div class="dbg-boot-title">调试回放未能启动</div>
      <div class="dbg-boot-text">{{ bootError }}</div>
      <el-button size="small" type="primary" @click="restart">重试</el-button>
    </div>

    <div v-else :class="['dbg-body', { immersive }]">
      <section class="dbg-stage">
        <div class="dbg-viewport">
          <div class="dbg-chrome">
            <span :class="['dbg-live', { on: playing }]"></span>
            <span class="dbg-addr" :title="state.url || ''">{{ prettyUrl || 'about:blank' }}</span>
            <!-- 步数和进度原来单独占一行（约 28px 高），现在并进地址条：
                 这一步在整条流程里的位置本来就该和 URL 一起看。 -->
            <span class="dbg-stepcount">第 {{ state.index || 0 }} / {{ state.total || 0 }} 步</span>
            <span class="dbg-chrome-tag">沙箱</span>
            <div class="dbg-bar"><i :style="{ width: progress + '%' }"></i></div>
          </div>
          <div class="dbg-canvas">
            <img v-if="streamSrc" :src="streamSrc" class="dbg-img" alt="沙箱中的被测页面" @error="onImgError">
            <!-- 停在第 1 步时页面还是 about:blank，这里要是还写「正在打开」，
                 用户会以为卡在加载上。暂停态直接说清楚它在等谁。 -->
            <div v-if="!streamSrc || !hasFrame" class="dbg-veil">
              <template v-if="paused">
                <span>已暂停在第 {{ state.index || 1 }} 步 · 点「继续」</span>
              </template>
              <template v-else>
                <span class="dbg-spinner"></span>
                <span>正在打开沙箱浏览器…</span>
              </template>
            </div>
          </div>
        </div>

        <div v-if="state.error" class="dbg-error">
          <span class="dbg-error-dot"></span>
          <span class="dbg-error-text">{{ state.error }}</span>
        </div>
      </section>

      <!-- 拖拽条压在 stage 和 side 的接缝上，不占布局宽度 -->
      <div v-if="!immersive" class="dbg-split" title="拖动调整步骤栏宽度，双击复位"
           @pointerdown="startSideDrag" @dblclick="resetSideWidth"></div>

      <aside :class="['dbg-side', { 'has-heal': !!heal }]"
             :style="sideWidth ? { width: sideWidth + 'px' } : null">
        <div class="dbg-ctrl">
          <button type="button" class="dbg-btn" :disabled="!canPlay" :title="playing ? '暂停' : '继续'"
                  @click="togglePlay">
            <span v-if="playing" class="dbg-ico-pause"><i></i><i></i></span>
            <span v-else class="dbg-ico-play"></span>
          </button>
          <button type="button" class="dbg-btn" :title="'单步执行'" :disabled="!canStep" @click="stepOnce">
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
              <template v-else-if="item.status === 'skipped'">–</template>
              <template v-else-if="isCurrent(i)">▶</template>
            </span>
            <span class="dbg-step-kind">{{ item.type }}</span>
            <span class="dbg-step-name" :title="stepTitle(item)">{{ stepTitle(item) }}</span>
            <span v-if="item.healed" class="dbg-step-heal" :title="item.heal_note || '本步已应用修复'">
              已修
            </span>
            <span v-if="item.suspect" class="dbg-step-warn"
                  :title="item.suspect_reason || '命中元素与录制时不一致'">
              疑似点错
            </span>
            <span v-if="item.ms != null" class="dbg-step-ms">{{ item.ms }}ms</span>
          </li>
          <li v-if="!state.steps.length" class="dbg-step is-empty">暂无步骤</li>
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
            <div v-if="currentStep.suspect" class="dbg-detail-warn">
              疑似点错：{{ currentStep.suspect_reason }}
            </div>
          </template>
          <div v-else class="dbg-faint">回放未开始</div>
        </div>

        <!-- 失败后的修复卡片。故意盖在右侧栏底部而不是弹一个遮罩：
             左边要一直看得到出事的页面，否则「为什么失败」就得靠脑补。 -->
        <div v-if="heal" :class="['dbg-heal', heal.status, heal.risk]">
          <div class="dbg-heal-head">
            <span class="dbg-heal-no">第 {{ heal.no }} 步</span>
            <span class="dbg-heal-kind">{{ heal.type }}</span>
            <span class="dbg-heal-name" :title="heal.alias || ''">{{ heal.alias || '' }}</span>
            <span :class="['dbg-heal-pill', heal.status]">{{ healPill }}</span>
          </div>

          <div v-if="heal.status === 'investigating' || heal.status === 'queued'" class="dbg-heal-busy">
            <span class="dbg-spinner"></span>
            <span>AI 在沙箱中执行、定位、改写并复验，通常 1–3 分钟…</span>
          </div>
          <div v-else-if="heal.status === 'idle'" class="dbg-heal-hint">
            执行失败：可让 AI 自愈排查，或直接跳过
          </div>

          <template v-else>
            <div class="dbg-heal-title-row">
              <span :class="['dbg-heal-cat', heal.category]">{{ heal.title }}</span>
              <span v-if="heal.verdict" :class="['dbg-heal-verdict', heal.verdict]">
                {{ verdictText }}
              </span>
              <span v-if="heal.confidence" class="dbg-heal-conf">置信度 {{ heal.confidence }}</span>
              <span v-if="heal.risk === 'medium' || heal.risk === 'high'" class="dbg-heal-risk">
                {{ heal.risk === 'high' ? '风险高' : '需留意' }}
              </span>
            </div>

            <p class="dbg-heal-reason">{{ heal.reason }}</p>

            <div v-if="heal.changes && heal.changes.length" class="dbg-heal-diff">
              <div v-for="(c, i) in heal.changes" :key="i" class="dbg-heal-change">
                <span class="dbg-heal-change-label">{{ c.label }}</span>
                <div class="dbg-heal-before">− {{ c.before || '（未设置）' }}</div>
                <div class="dbg-heal-after">+ {{ c.after || '（未设置）' }}</div>
              </div>
            </div>

            <div v-if="heal.evidence && heal.evidence.length" class="dbg-heal-evidence">
              <div class="dbg-heal-ev-head">身份核对 · 录制时 → 当前页</div>
              <div v-for="(e, i) in heal.evidence" :key="i"
                   :class="['dbg-heal-ev-row', e.level || (e.ok ? 'match' : 'info')]">
                <span class="dbg-heal-ev-ico">{{ e.level === 'conflict' ? '✗' : (e.ok ? '✓' : '·') }}</span>
                <span class="dbg-heal-ev-label">{{ e.label }}</span>
                <span class="dbg-heal-ev-val">
                  {{ e.recorded || '—' }}<template v-if="!e.ok"> → {{ e.actual || '（空）' }}</template>
                </span>
              </div>
            </div>

            <div v-if="heal.verify" :class="['dbg-heal-verify', { ok: heal.verify.ok }]">
              <span class="dbg-heal-verify-ico">{{ heal.verify.ok ? '✓' : '!' }}</span>
              <span>预检：{{ heal.verify.text }}</span>
            </div>
            <div v-if="heal.unsupported && heal.unsupported.length" class="dbg-heal-note">
              现场投影不完整：{{ heal.unsupported.join('；') }}
            </div>
            <div v-if="heal.blocked_reason" class="dbg-heal-blocked">{{ heal.blocked_reason }}</div>

            <details v-if="heal.spec_diff" class="dbg-heal-code">
              <summary>沙箱改动（spec diff）</summary>
              <pre class="dbg-heal-pre"><span v-for="(l, i) in diffLines" :key="i"
                :class="['dl', l.kind]">{{ l.text }}</span></pre>
            </details>

            <details v-if="heal.probes && heal.probes.length" class="dbg-heal-probes">
              <summary>调查记录 {{ heal.probes.length }} 条</summary>
              <div v-for="(p, i) in heal.probes" :key="i">· {{ p }}</div>
            </details>

            <div v-if="heal.attempt > 1" class="dbg-heal-attempt">
              第 {{ heal.attempt }} 次尝试（最多 {{ heal.max_attempt }} 次）
            </div>

            <label v-if="heal.can_apply && heal.writeback_ok" class="dbg-heal-wb">
              <input v-model="writeback" type="checkbox">
              <span>写回第 {{ heal.no }} 步（影响后续生成）</span>
            </label>
            <div v-else-if="heal.writeback_note" class="dbg-heal-note">{{ heal.writeback_note }}</div>

            <div class="dbg-heal-actions">
              <button v-if="canAiHeal" type="button"
                      :class="['dbg-heal-btn', { primary: !heal.can_apply }]"
                      :disabled="healBusy" @click="aiHeal">
                {{ heal.status === 'idle' ? 'AI 自愈' : '重新调查' }}
              </button>
              <button v-if="heal.can_apply" type="button" class="dbg-heal-btn primary"
                      :disabled="healBusy" @click="applyHeal">
                {{ heal.status === 'applying' ? '正在重试…' : '应用并重试' }}
              </button>
              <button type="button" class="dbg-heal-btn" :disabled="healBusy" @click="skipHeal">
                跳过
              </button>
              <button type="button" class="dbg-heal-btn ghost" :disabled="healBusy" @click="stopHeal">
                停止回放
              </button>
            </div>
          </template>
        </div>
      </aside>
    </div>

    <!-- 两个模型分开配：
         healer 是长链路（跑测试、改文件、重跑），能跑单轮的模型未必扛得住多轮工具调用；
         滑块要读图，纯文本模型接了也白接。都不该牵连生成链路的默认模型，
         所以放在回放页，而不是设置页。 -->
    <el-dialog v-model="modelDlg" title="模型设置" width="540px" append-to-body>
      <div class="dbg-model-sec">
        <div class="dbg-model-title">AI 自愈</div>
        <p class="dbg-model-hint">
          AI 自愈所用模型；会实际执行测试并复验
        </p>
        <el-select v-model="modelValue" filterable clearable placeholder="跟随全局默认"
                   class="dbg-model-select">
          <el-option label="跟随全局默认" value="" />
          <el-option v-for="ref in modelRefs" :key="ref" :label="ref" :value="ref" />
        </el-select>
        <p class="dbg-model-default">当前全局默认：{{ modelDefault || '未设置' }}</p>
      </div>

      <div class="dbg-model-sec">
        <div class="dbg-model-title">滑块 / 验证码（视觉模型）</div>
        <p class="dbg-model-hint">
          滑块 / 验证码共用；需支持读图，不跟随全局默认<br>
          仅列已标记可读图的模型（设置 → AI 服务）
        </p>
        <el-select v-model="sliderValue" filterable clearable placeholder="使用内置默认"
                   class="dbg-model-select">
          <el-option :label="`内置默认（${sliderBuiltin || '豆包视觉'}）`" value="" />
          <el-option v-for="ref in sliderRefs" :key="ref" :label="ref" :value="ref" />
        </el-select>
        <p class="dbg-model-default">
          当前生效：{{ sliderModel || '未配置（回放到滑块/验证码会直接报错）' }}
          <span v-if="!sliderExplicit"> · 内置默认</span>
        </p>
        <p class="dbg-model-hint dbg-model-foot">
          仅影响平台内回放；生成代码读项目根
          <code>.env</code>（<code>SLIDER_MODEL_*</code>）
        </p>
      </div>

      <template #footer>
        <el-button @click="modelDlg = false">取消</el-button>
        <el-button type="primary" :loading="modelSaving" @click="saveModel">保存</el-button>
      </template>
    </el-dialog>
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
  { label: '慢', delay: 1.2, hint: '每步停 1.2 秒，观察页面变化' },
  { label: '标准', delay: 0.55, hint: '每步停 0.55 秒' },
  { label: '快', delay: 0.2, hint: '每步停 0.2 秒，只看流程是否走通' },
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
const writeback = ref(true)
const healBusy = ref(false)
let lastHealNo = null
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
  healing: '调查中',
  needs_heal: '等待确认',
  done: '已完成',
  failed: '失败',
  stopped: '已停止',
}
const statusText = computed(() => STATUS_TEXT[status.value] || status.value)

const heal = computed(() => state.value.heal || null)
const healPill = computed(() => {
  const value = heal.value && heal.value.status
  return { idle: '待处理', queued: '排队中', investigating: '调查中',
           applying: '正在重试', ready: '待确认', blocked: '未通过' }[value] || ''
})
// 失败后不再自动诊断，只给一个入口。AI 调查过程中（investigating/queued）不给点，
// 否则用户连点会让后端拒绝并弹一个「已经在调查了」的红条。
const canAiHeal = computed(() => {
  const card = heal.value
  if (!card || card.ai_ready === false) return false
  return !['queued', 'investigating', 'applying'].includes(card.status)
})
const immersive = ref(false)

/* 右侧栏宽度：可拖（240~560），双击复位，记住上次的值。
   0 表示「没拖过」，此时交给 CSS 的 clamp(260px, 18%, 340px) 按窗口自适应——
   1440 下是 260（画面 1149×718），1920 下是 340，不至于在大屏上缩成一条。
   拖过之后就用固定像素，窗口再变也不改，用户的手动选择优先。 */
const SIDE_MIN = 240
const SIDE_MAX = 560
const sideWidth = ref(readSideWidth())

function readSideWidth() {
  try {
    const raw = Number(localStorage.getItem('ac-dbg-side'))
    if (raw >= SIDE_MIN && raw <= SIDE_MAX) return raw
  } catch (error) { /* 无痕模式读不到，用自适应 */ }
  return 0
}

function saveSideWidth() {
  if (!sideWidth.value) return
  try { localStorage.setItem('ac-dbg-side', String(sideWidth.value)) } catch (error) { /* 忽略 */ }
}

let sideDragX = 0
let sideDragW = 0

function startSideDrag(event) {
  // 第一次拖的时候 sideWidth 还是 0（自适应），得先量出侧栏当前的真实宽度，
  // 否则一按下去就会跳到 0 或者 240。
  const aside = event.currentTarget.nextElementSibling
  sideDragX = event.clientX
  sideDragW = sideWidth.value || (aside ? Math.round(aside.getBoundingClientRect().width) : SIDE_MIN)
  window.addEventListener('pointermove', onSideDrag)
  window.addEventListener('pointerup', endSideDrag)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  event.preventDefault()
}

function onSideDrag(event) {
  // 往左拖 → 侧栏变宽（分割条在侧栏左边）
  const next = sideDragW - (event.clientX - sideDragX)
  sideWidth.value = Math.min(SIDE_MAX, Math.max(SIDE_MIN, Math.round(next)))
}

function endSideDrag() {
  window.removeEventListener('pointermove', onSideDrag)
  window.removeEventListener('pointerup', endSideDrag)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  saveSideWidth()
}

function resetSideWidth() {
  // 双击回到自适应，而不是某个固定值：换到更大的窗口时它还能继续长
  sideWidth.value = 0
  try { localStorage.removeItem('ac-dbg-side') } catch (error) { /* 忽略 */ }
}

const diffLines = computed(() => {
  const text = (heal.value && heal.value.spec_diff) || ''
  if (!text) return []
  const rows = text.split('\n')
  if (rows.length && rows[rows.length - 1] === '') rows.pop()
  return rows.map((line) => ({
    text: line || ' ',
    kind: line.startsWith('+++') || line.startsWith('---') ? 'meta'
      : line.startsWith('@@') ? 'hunk'
        : line.startsWith('+') ? 'add'
          : line.startsWith('-') ? 'del' : '',
  }))
})

const VERDICT_TEXT = {
  verified: '身份已验证',
  probable: '仅弱特征吻合',
  conflict: '身份冲突',
  unknown: '身份存疑',
  state: '状态待恢复',
}
const verdictText = computed(() => VERDICT_TEXT[heal.value && heal.value.verdict] || '')
const suspectCount = computed(() => state.value.steps.filter(item => item.suspect).length)

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
  syncHeal(merged.heal)
  followCurrent()
}

function syncHeal(card) {
  if (!card) {
    lastHealNo = null
    healBusy.value = false
    return
  }
  // AI 调查期间卡片会换成 investigating：这时候不能把 busy 放掉，
  // 否则「让 AI 自愈」会被判成可点，用户连点两下就收到一个后端拒绝。
  if (['queued', 'investigating', 'applying'].includes(card.status)) {
    healBusy.value = true
    return
  }
  // 同一步可能失败两次（修完还是不通），用「步号 + 第几次」当身份，
  // 只靠步号的话第二次进来勾选状态不会重置。
  const key = `${card.no}:${card.attempt || 1}`
  if (key !== lastHealNo) {
    lastHealNo = key
    // 默认勾选「写回」只给后端算过的安全情形：写回本身没错，错补丁写回去
    // 会永久把录制改成别的元素。后端没给 writeback_default 时退回旧行为。
    writeback.value = card.writeback_default != null
      ? !!card.writeback_default
      : !!card.writeback_ok
    healBusy.value = false
  }
}

async function decideHeal(decision) {
  healBusy.value = true
  try {
    const { data } = await api.decideDebugHeal(decision, writeback.value)
    apply(data)
    if (decision !== 'stop') startPoll()
  } catch (error) {
    ElMessage.error(errorText(error))
    healBusy.value = false
    refresh()
  }
}

function applyHeal() { return decideHeal('apply') }
function skipHeal() { return decideHeal('skip') }
function stopHeal() { return decideHeal('stop') }
function aiHeal() { return decideHeal('ai') }

// ---------- 预览放大 ----------

function toggleImmersive() {
  immersive.value = !immersive.value
}

function onKeydown(event) {
  if (event.key === 'Escape' && immersive.value) {
    immersive.value = false
  }
}

// ---------- 模型设置（AI 自愈 + 滑块验证）----------

const modelDlg = ref(false)
const modelValue = ref('')
const modelRefs = ref([])
const modelDefault = ref('')
const modelSaving = ref(false)
// 滑块模型：和 healer 完全独立。空 = 用后端内置的豆包视觉模型。
const sliderValue = ref('')
const sliderRefs = ref([])
const sliderBuiltin = ref('')
const sliderModel = ref('')
const sliderExplicit = ref(false)

async function openModels() {
  modelDlg.value = true
  try {
    const [healer, slider] = await Promise.all([
      api.getHealerModel(),
      api.getSliderModel(),
    ])
    modelValue.value = healer.data.model || ''
    modelRefs.value = healer.data.refs || []
    modelDefault.value = healer.data.default || ''

    sliderValue.value = slider.data.explicit || ''
    sliderRefs.value = slider.data.refs || []
    sliderBuiltin.value = slider.data.builtin || ''
    sliderModel.value = slider.data.model || ''
    sliderExplicit.value = !!slider.data.explicit
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function saveModel() {
  modelSaving.value = true
  try {
    const [healer, slider] = await Promise.all([
      api.setHealerModel(modelValue.value),
      api.setSliderModel(sliderValue.value),
    ])
    modelValue.value = healer.data.model || ''
    modelDefault.value = healer.data.default || ''
    sliderModel.value = slider.data.model || ''
    sliderExplicit.value = !!slider.data.explicit
    modelDlg.value = false
    ElMessage.success('已保存，下次回放生效')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    modelSaving.value = false
  }
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

async function leave() {
  api.controlDebugReplay('stop').catch(() => {})
  // 由录制页开出来的独立窗口：退出就是关窗。不要在这里 router.push('/recording')，
  // 那会在回放窗口里又渲染一份录制页，看起来像「录制页被重置了」。
  if (route.query.window === '1') {
    try {
      await api.closeReplayWindow()
      return
    } catch (error) {
      // 服务端关不掉时退回浏览器自己的能力；仍失败就明确告诉用户手动关。
      window.close()
      ElMessage.warning('窗口未能自动关闭，请手动关闭此窗口')
      return
    }
  }
  router.push('/recording')
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  boot()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  // 拖到一半直接切页面时，这两个监听会把已卸载的组件继续往回调
  window.removeEventListener('pointermove', onSideDrag)
  window.removeEventListener('pointerup', endSideDrag)
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
  height: 44px;
  padding: 0 16px;
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
.dbg-badge.healing { background: var(--accent-soft); color: var(--accent-ink); }
.dbg-badge.needs_heal { background: #fdf3e3; color: #8a5a12; }
/* 疑似点错：步骤是绿的，但点到的东西和录制时不是同一个。用琥珀色而不是红色，
   因为它不是失败，是需要人确认的「绿得可疑」。 */
.dbg-badge.warn { background: #fdf3e3; color: #8a5a12; border: 1px solid #e8cfa6; }

.dbg-head-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }

/* 页头的小方按钮：齿轮（模型）和放大。和「重新开始」不是一个量级的操作，
   不做成 el-button，否则右侧会排出一排同等权重的按钮，主次就没了。 */
.dbg-icon {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line-ctl);
  border-radius: 8px;
  background: transparent;
  color: var(--ink-2);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.dbg-icon:hover { border-color: var(--line-ctl-hover); color: var(--accent); }
.dbg-icon.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }

.dbg-body {
  flex: 1;
  min-height: 0;
  display: flex;
  /* 沉浸模式下右侧栏是绝对定位的浮层，父级必须有定位上下文 */
  position: relative;
}

.dbg-stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
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
  position: relative;
  flex: none;
  display: flex;
  align-items: center;
  gap: 9px;
  height: 32px;
  padding: 0 12px;
  background: var(--bg-sunken);
  border-bottom: 1px solid var(--line-soft);
}

/* 步数从原来单独一行的进度条里搬过来，靠右、等宽数字 */
.dbg-stepcount {
  flex: none;
  font-size: 11.5px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}

/* 进度条变成地址条底下的一条 2px 细线，不再占 28px 的独立一行 */
.dbg-chrome .dbg-bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  border-radius: 0;
  background: transparent;
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
  padding: 6px;
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

.dbg-bar { flex: 1; height: 2px; border-radius: 1px; background: var(--line-soft); overflow: hidden; }
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
  width: clamp(260px, 18%, 340px);
  flex: none;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border-left: 1px solid var(--line-soft);
}

/* 分割条：宽度为 0 的 flex 项，热区用伪元素往两边各扩 3px，
   这样既不占画面宽度，又比 1px 的线好点中。 */
.dbg-split {
  flex: none;
  width: 0;
  position: relative;
  cursor: col-resize;
  z-index: 6;
}

.dbg-split::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  width: 7px;
  background: transparent;
  transition: background .12s ease;
}

.dbg-split:hover::after { background: var(--accent); }

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
/* 跳过不是成功：颜色要偏灰，否则整轮跑完看着像全绿。 */
.dbg-step.skipped { background: var(--bg-sunken); color: var(--faint); border-left-color: var(--line-ctl); }
.dbg-step.skipped .dbg-step-dot { border-style: dashed; color: var(--faint); }

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

.dbg-step-heal {
  flex: none;
  font-size: 10px;
  line-height: 1.5;
  padding: 0 4px;
  border-radius: 4px;
  background: #eaf5ee;
  color: #1c5f38;
}
.dbg-step-warn {
  flex: none;
  font-size: 10px;
  line-height: 1.5;
  padding: 0 4px;
  border-radius: 4px;
  background: #fdf3e3;
  color: #8a5a12;
  border: 1px solid #e8cfa6;
  white-space: nowrap;
}
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
.dbg-detail-warn { margin-top: 7px; font-size: 11.5px; color: #8a5a12; line-height: 1.5; }

/* ---- 失败修复卡片 ----
   盖在右侧栏底部而不是居中遮罩：左边那半屏是出事现场，判断「要不要改」
   全靠对照它，遮住就等于让人盲选。 */
.dbg-side { position: relative; }

/* 沉浸模式：右侧栏变成浮在画面上的抽屉，预览吃满整宽。
   1440×900 的截图在这个宽度下缩放比从 ~0.67 提到 0.95 以上；
   不把步骤列表藏起来——收起来就得来回切，比看小一点更费劲。 */
.dbg-body.immersive .dbg-side {
  position: absolute;
  top: 12px;
  right: 12px;
  bottom: 12px;
  width: 340px;
  max-width: 42vw;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  box-shadow: 0 18px 46px rgba(0, 0, 0, .26);
  overflow: hidden;
  z-index: 5;
  background: var(--bg-card);
}

.dbg-body.immersive .dbg-side.has-heal { width: 380px; }

.dbg-heal {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 4;
  max-height: calc(100% - 48px);
  overflow-y: auto;
  padding: 12px 13px 13px;
  background: var(--bg-card);
  border-top: 1px solid var(--line-soft);
  box-shadow: 0 -10px 26px rgba(20, 24, 32, .10);
}

.dbg-heal.blocked { border-top-color: var(--danger-line); }
.dbg-heal.applying { opacity: .82; }

.dbg-heal-head {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 9px;
}

.dbg-heal-no { font-size: 12px; font-weight: 600; color: var(--ink); }
.dbg-heal-kind {
  font-size: 10px;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--muted);
  background: var(--bg-sunken);
  border-radius: 4px;
  padding: 1px 5px;
}
.dbg-heal-name {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--muted);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dbg-heal-pill {
  flex: none;
  font-size: 10.5px;
  border-radius: 5px;
  padding: 1px 7px;
  background: var(--bg-sunken);
  color: var(--muted);
}
.dbg-heal-pill.investigating { background: var(--accent-soft); color: var(--accent-ink); }
.dbg-heal-pill.ready { background: #fdf3e3; color: #8a5a12; }
.dbg-heal-pill.applying { background: var(--accent-soft); color: var(--accent-ink); }
.dbg-heal-pill.blocked { background: var(--danger-soft); color: var(--danger); }

.dbg-heal-busy {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  padding: 6px 0 2px;
}

.dbg-heal-title-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

.dbg-heal-cat {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 5px;
  background: var(--bg-sunken);
  color: var(--ink-2);
}
.dbg-heal-cat.locator_changed { background: #fdf3e3; color: #8a5a12; }
.dbg-heal-cat.frame_changed { background: var(--accent-soft); color: var(--accent-ink); }
.dbg-heal-cat.transient { background: #eaf5ee; color: var(--ok); }
.dbg-heal-cat.assertion,
.dbg-heal-cat.ambiguous { background: var(--danger-soft); color: var(--danger); }
.dbg-heal-cat.element_replaced,
.dbg-heal-cat.row_gone,
.dbg-heal-cat.element_hidden { background: var(--danger-soft); color: var(--danger); }
.dbg-heal-cat.env { background: var(--bg-sunken); color: var(--muted); }

.dbg-heal-verdict {
  font-size: 10.5px;
  line-height: 1.6;
  padding: 0 5px;
  border-radius: 4px;
  border: 1px solid var(--line-ctl);
  color: var(--muted);
}
.dbg-heal-verdict.verified { background: #eaf5ee; color: #1c5f38; border-color: #cfe6da; }
.dbg-heal-verdict.probable,
.dbg-heal-verdict.state { background: #fdf3e3; color: #8a5a12; border-color: #e8cfa6; }
.dbg-heal-verdict.conflict,
.dbg-heal-verdict.unknown { background: var(--danger-soft); color: var(--danger); border-color: var(--danger-line); }

.dbg-heal-conf { font-size: 10.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.dbg-heal-risk {
  font-size: 10.5px;
  color: #8a5a12;
  border: 1px solid #e8cfa6;
  border-radius: 4px;
  padding: 0 4px;
}

.dbg-heal-reason {
  margin: 7px 0 9px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-2);
  word-break: break-word;
}

.dbg-heal-diff {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-bottom: 9px;
}

.dbg-heal-change-label {
  display: block;
  font-size: 10.5px;
  color: var(--faint);
  margin-bottom: 3px;
}

.dbg-heal-before,
.dbg-heal-after {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  line-height: 1.55;
  padding: 3px 7px;
  border-radius: 5px;
  /* 定位串里全是括号和引号，break-all 会把 getByRole 拆成两截，反而更难对读。
     anywhere 只在放不下时才断，优先在空格和标点处换行。 */
  overflow-wrap: anywhere;
  word-break: normal;
}

/* 不用删除线：整串定位被划掉之后基本认不出改的是哪一截，红底 +「−」已经够区分了。 */
.dbg-heal-before { background: var(--danger-soft); color: #8d2f28; opacity: .85; }
.dbg-heal-after { background: #eaf5ee; color: #1c5f38; margin-top: 2px; }

/* ---- 身份核对 ----
   一行一个信号：对得上给绿勾，明确冲突给红叉，录制时有、现在没有的给灰点
   （信息项，不算错）。有这一块，用户不用读定位串也能判断「是不是同一个元素」。 */
.dbg-heal-evidence {
  margin-bottom: 9px;
  padding: 7px 8px;
  border-radius: 7px;
  background: var(--bg-sunken);
}

.dbg-heal-ev-head {
  font-size: 10.5px;
  color: var(--faint);
  margin-bottom: 5px;
}

.dbg-heal-ev-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  line-height: 1.65;
  color: var(--muted);
}

.dbg-heal-ev-ico { flex: none; width: 10px; text-align: center; }
.dbg-heal-ev-row.match .dbg-heal-ev-ico { color: var(--ok); }
.dbg-heal-ev-row.conflict .dbg-heal-ev-ico { color: var(--danger); }
.dbg-heal-ev-label { flex: none; min-width: 46px; color: var(--faint); }
.dbg-heal-ev-val {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
.dbg-heal-ev-row.conflict .dbg-heal-ev-val { color: #8d2f28; }

.dbg-heal-verify {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 11.5px;
  line-height: 1.5;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg-sunken);
  color: var(--muted);
}
.dbg-heal-verify.ok { background: #eaf5ee; color: #1c5f38; }
.dbg-heal-verify-ico { flex: none; font-weight: 600; }

.dbg-heal-blocked {
  margin-top: 7px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--danger);
  background: var(--danger-soft);
  border-radius: 6px;
  padding: 6px 8px;
}

.dbg-heal-probes {
  margin-top: 8px;
  font-size: 11px;
  color: var(--muted);
}
.dbg-heal-probes summary { cursor: pointer; color: var(--faint); }
.dbg-heal-probes div { margin-top: 3px; line-height: 1.5; }

/* 失败后的第一张卡片只有事实，没有建议——这段是给它补一句「接下来能做什么」。 */
.dbg-heal-hint {
  margin: 9px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.65;
}

/* AI 在沙箱里改的 spec。深色面板，和日志、终端同一套：
   两套主题下都保持深色，浅色下才不会和卡片糊在一起。 */
.dbg-heal-code { margin-top: 9px; font-size: 11.5px; }
.dbg-heal-code summary { cursor: pointer; color: var(--ink-2); padding: 3px 0; }
.dbg-heal-code summary:hover { color: var(--accent); }
.dbg-heal-pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  border-radius: 8px;
  background: var(--bg-code);
  color: #c9d4e3;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  line-height: 1.65;
  white-space: pre;
}
.dbg-heal-pre .dl { display: block; }
.dbg-heal-pre .dl.add { color: #6fd39a; }
.dbg-heal-pre .dl.del { color: #e8898a; }
.dbg-heal-pre .dl.hunk { color: #7fa7d9; }
.dbg-heal-pre .dl.meta { color: #8290a3; }

.dbg-model-hint { margin: 0 0 10px; font-size: 12.5px; color: var(--muted); line-height: 1.7; }
/* 紧跟在「当前生效」后面的补充说明：默认没有上边距，会粘成一段。 */
.dbg-model-hint.dbg-model-foot { margin-top: 4px; }
/* 两组配置用一条细线分开：healer 和滑块各管各的，改错一处不该影响另一处 */
.dbg-model-sec + .dbg-model-sec {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}
.dbg-model-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.dbg-model-select { width: 100%; }
.dbg-model-default { margin: 8px 0 0; font-size: 11.5px; color: var(--faint); }

.dbg-heal-attempt { margin-top: 7px; font-size: 11px; color: #8a5a12; }
.dbg-heal-note { margin-top: 8px; font-size: 11px; color: var(--faint); }

.dbg-heal-wb {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ink-2);
  cursor: pointer;
}
.dbg-heal-wb input { margin-top: 2px; flex: none; }

.dbg-heal-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 11px;
}

.dbg-heal-btn {
  font-size: 12px;
  line-height: 1;
  padding: 7px 12px;
  border-radius: 7px;
  border: 1px solid var(--line-ctl);
  background: var(--bg-card);
  color: var(--ink-2);
  cursor: pointer;
}
.dbg-heal-btn:hover:not(:disabled) { border-color: var(--line-ctl-hover); }
.dbg-heal-btn:disabled { opacity: .45; cursor: default; }
.dbg-heal-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--ink-inverse);
  font-weight: 500;
}
.dbg-heal-btn.ghost { color: var(--muted); border-color: transparent; }
.dbg-heal-btn.ghost:hover:not(:disabled) { color: var(--danger); }

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
