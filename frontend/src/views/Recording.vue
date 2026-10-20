<template>
  <div class="recording-page">
    <div class="page-header">
      <div>
        <div class="eyebrow">RECORDING · CODE GENERATOR</div>
        <h1>自动化工具</h1>
        <p>在真实浏览器里操作，记录步骤与唯一定位。插入断言或等待后，按目标工作区的框架生成脚本并写入指定文件；写入后可以预览，再提交或撤销。</p>
      </div>
      <el-button @click="$router.push('/')">返回首页</el-button>
    </div>

    <!-- 目标与配置 -->
    <el-card class="panel-card config-card">
      <template #header>
        <div class="section-header">
          <span class="card-title">目标与配置</span>
          <span class="field-hint">配置会记在本浏览器里，下次打开自动填入</span>
        </div>
      </template>
      <div class="config-grid">
        <el-form label-position="top" class="config-form">
          <el-form-item label="录制起始地址">
            <el-input v-model="config.baseUrl" placeholder="http://127.0.0.1:8080/login" clearable />
          </el-form-item>
          <el-form-item label="工作区（Git 目录）">
            <el-input v-model="config.workspace" placeholder="点击「浏览」选择，或填写相对 AUTOMATION_WORKSPACE_ROOT 的路径" clearable>
              <!-- 两个按钮要包一层：Element Plus 会给 append 里的 .el-button 加 margin:0 -20px，并排时会重叠 -->
              <template #append>
                <div class="append-group">
                  <el-button :icon="FolderOpened" @click="openWorkspacePicker">浏览</el-button>
                  <span class="append-sep"></span>
                  <el-button :loading="detecting" @click="detectWorkspace">检测</el-button>
                </div>
              </template>
            </el-input>
            <div class="field-hint">
              必须是 AUTOMATION_WORKSPACE_ROOT 内的目录。
              <span v-if="workspaceRoot" class="root-hint">根目录：{{ workspaceRoot }}</span>
            </div>
          </el-form-item>
          <el-form-item label="框架">
            <el-select v-model="config.framework" style="width: 100%">
              <el-option
                v-for="item in FRAMEWORKS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="目标文件（工作区内相对路径）">
            <el-input v-model="config.targetPath" :placeholder="targetPlaceholder" clearable>
              <template #append>
                <el-button :icon="Document" :disabled="!config.workspace.trim()" @click="openTargetPicker">浏览</el-button>
              </template>
            </el-input>
            <div class="field-hint">
              选已有文件会覆盖（生成前会再次确认）；也可以进入目录后填一个新文件名。
              <span v-if="!config.workspace.trim()">需先选择工作区。</span>
            </div>
          </el-form-item>
          <el-form-item label="模型（断言解析与代码生成）">
            <el-select v-model="config.model" filterable placeholder="选择模型" style="width: 100%">
              <el-option v-for="m in modelOptions" :key="m.value" :label="m.value" :value="m.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="用例名称">
            <el-input v-model="config.title" placeholder="例如：登录成功" maxlength="200" clearable />
          </el-form-item>
          <el-form-item label="Python 解释器（可选，留空则自动查找项目里的 .venv）">
            <el-input v-model="config.python" placeholder="例如 D:/proj/.venv/Scripts/python.exe" clearable />
          </el-form-item>
        </el-form>

        <div class="detect-panel">
          <div class="detect-title">工作区检测</div>
          <div v-if="!detect" class="detect-empty">
            输入工作区后点击「检测」，读取它使用的框架、Python 环境和库版本。检测结果只作提示，不会阻止生成。
          </div>
          <template v-else>
            <div class="detect-row">
              <span class="detect-key">框架</span>
              <el-tag :type="detect.framework ? 'success' : 'info'" effect="light">{{ frameworkName(detect.framework) }}</el-tag>
            </div>
            <div class="detect-row">
              <span class="detect-key">Python</span>
              <code class="detect-value">{{ detect.python || '未找到' }}</code>
            </div>
            <div class="detect-row">
              <span class="detect-key">SeleniumLibrary</span>
              <span class="detect-value">{{ detect.seleniumlibrary_version || '未检测到，将按主流写法生成' }}</span>
            </div>
            <div v-if="detect.samples && detect.samples.length" class="detect-row detect-samples">
              <span class="detect-key">参考文件</span>
              <div>
                <div v-for="sample in detect.samples" :key="sample.path" class="detect-sample">{{ sample.path }}</div>
              </div>
            </div>
            <details class="detect-evidence">
              <summary>依据</summary>
              <div v-for="(item, idx) in detect.evidence" :key="idx" class="evidence-line">
                <span class="evidence-kind">{{ item.kind }}</span>
                <span>{{ item.value === null ? '—' : item.value }}</span>
                <span class="evidence-source">{{ item.source }}</span>
              </div>
            </details>
          </template>
        </div>
      </div>
    </el-card>

    <div class="main-layout">
      <!-- 录制步骤 -->
      <el-card class="panel-card steps-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">录制步骤</span>
            <span :class="['state-pill', 'state-' + (session ? session.status : 'idle')]">{{ stateLabel }}</span>
          </div>
        </template>

        <div class="toolbar">
          <el-button v-if="!isRecording" type="primary" :loading="starting" @click="startRecording">开始录制</el-button>
          <el-button v-else type="danger" plain :loading="stopping" @click="stopRecording">停止录制</el-button>
          <el-button v-if="isRecording" plain @click="cancelRecording">取消</el-button>
          <el-button :type="picking ? 'warning' : 'default'" :disabled="!isRecording" @click="togglePick(!picking)">
            {{ picking ? '退出拾取' : '拾取元素' }}
          </el-button>
          <span class="toolbar-sep"></span>
          <el-button :disabled="!canEdit" @click="openDraft('assert')">添加断言</el-button>
          <el-button :disabled="!canEdit" @click="openDraft('wait')">添加等待</el-button>
          <el-button :disabled="!canEdit" @click="openDraft('sleep')">固定等待</el-button>
        </div>

        <div v-if="picking" class="pick-hint">
          拾取模式已开启：在浏览器里点击元素，它会被记为可引用的元素，不会记为操作步骤。
        </div>
        <el-alert v-if="session && session.error" :title="session.error" type="error" :closable="false" class="session-error" />

        <el-empty
          v-if="!steps.length && !draft"
          description="还没有步骤。点击「开始录制」后，在弹出的浏览器里操作。"
          :image-size="90"
        />

        <ol v-if="steps.length" class="timeline">
          <li
            v-for="step in steps"
            :key="step.no"
            :class="['step', { 'is-blocked': effectiveStatus(step) === 'ambiguous' }]"
          >
            <div class="step-no">{{ step.no }}</div>
            <div class="step-body">
              <div class="step-main">
                <span :class="['kind-tag', 'kind-' + stepTone(step.type)]">{{ stepKindLabel(step.type) }}</span>
                <span class="step-text">{{ stepDescription(step) }}</span>
              </div>
              <div v-if="step.text" class="step-note">{{ step.text }}</div>
              <div v-if="step.target" class="step-locator">
                <span class="step-alias">{{ step.target.alias || '未命名元素' }}</span>
                <code class="locator-code">{{ locatorText(step.target) }}</code>
                <el-tag size="small" :type="statusTone(effectiveStatus(step))" effect="light">
                  {{ statusLabel(effectiveStatus(step)) }}
                </el-tag>
              </div>
            </div>
            <div class="step-actions">
              <el-button text :icon="ArrowUp" :disabled="!canEdit" title="上移" @click="moveStep(step.no, 'up')" />
              <el-button text :icon="ArrowDown" :disabled="!canEdit" title="下移" @click="moveStep(step.no, 'down')" />
              <el-button
                text
                type="danger"
                :icon="Delete"
                :disabled="!canEdit"
                title="删除"
                @click="removeStep(step.no)"
              />
            </div>
          </li>
        </ol>

        <!-- 断言 / 等待 / 固定等待 模板 -->
        <div v-if="draft" class="draft-card">
          <div class="draft-head">
            <span class="draft-title">{{ draftTitle }}</span>
            <el-button text :icon="Close" @click="closeDraft" />
          </div>

          <div class="draft-nl">
            <el-input
              v-model="draft.text"
              type="textarea"
              :rows="2"
              maxlength="300"
              placeholder="用自然语言描述，例如：提交成功的提示出现 / 等到加载遮罩消失 / 地址包含 /dashboard"
            />
            <el-button :loading="draft.parsing" :disabled="!config.model || !draft.text.trim()" @click="parseDraft">
              解析
            </el-button>
          </div>
          <div class="field-hint">
            {{ config.model ? '所选模型会把描述解析为固定意图，确认后才会插入。' : '未选择模型：请使用下方的结构化表单。' }}
          </div>

          <div v-if="draft.parsed" class="parsed-preview">
            <div class="parsed-line">
              <span class="parsed-label">理解为</span>
              <span>{{ intentDescription(draft.parsed, null) }}</span>
            </div>
            <div class="parsed-actions">
              <el-button type="primary" size="small" :loading="draft.inserting" @click="insertIntent(draft.parsed, draft.text)">
                确认插入
              </el-button>
            </div>
          </div>

          <el-divider content-position="left">结构化表单</el-divider>
          <div class="form-grid">
            <el-select v-model="draft.intentType" placeholder="意图类型">
              <el-option
                v-for="type in draftTypes"
                :key="type"
                :label="intentLabel(type)"
                :value="type"
              />
            </el-select>
            <el-select
              v-model="draft.target"
              clearable
              placeholder="目标元素"
              :disabled="!needsTarget(draft.intentType)"
            >
              <el-option v-for="alias in aliases" :key="alias" :label="alias" :value="alias" />
            </el-select>
            <el-button
              :type="draft.picking ? 'warning' : 'default'"
              :disabled="!isRecording || !needsTarget(draft.intentType)"
              @click="pickForDraft"
            >
              {{ draft.picking ? '请在浏览器中点击元素…' : '拾取元素' }}
            </el-button>
            <el-input
              v-if="needsValue(draft.intentType)"
              v-model="draft.value"
              placeholder="文字或值"
              maxlength="500"
            />
            <el-input-number
              v-if="draft.intentType === 'sleep'"
              v-model="draft.seconds"
              :min="0.1"
              :max="60"
              :step="0.5"
              controls-position="right"
            />
          </div>
          <div class="draft-actions">
            <el-button type="primary" plain :loading="draft.inserting" @click="insertFromForm">插入表单内容</el-button>
          </div>
        </div>
      </el-card>

      <!-- 生成 -->
      <el-card class="panel-card generate-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">生成</span>
          </div>
        </template>

        <div class="checklist">
          <div :class="['check', { ok: steps.length > 0 }]">
            <span class="check-dot"></span>已录制 {{ steps.length }} 个步骤
          </div>
          <div :class="['check', { ok: isStopped }]">
            <span class="check-dot"></span>{{ isStopped ? '录制已停止' : '需要先停止录制' }}
          </div>
          <div :class="['check', { ok: !blockers.length, bad: blockers.length > 0 }]">
            <span class="check-dot"></span>
            {{ blockers.length ? `第 ${blockers.join('、')} 步定位不唯一，请删除或重新拾取` : '所有步骤都有唯一定位' }}
          </div>
          <div v-if="fragileCount" class="check warn">
            <span class="check-dot"></span>{{ fragileCount }} 个步骤使用结构定位，已标黄，生成后请留意
          </div>
          <div :class="['check', { ok: !!config.workspace && !!config.targetPath && !!config.model }]">
            <span class="check-dot"></span>工作区、目标文件、模型均已填写
          </div>
        </div>

        <el-button
          class="generate-btn"
          type="primary"
          size="large"
          :loading="generating"
          :disabled="!canGenerate"
          @click="generate(false)"
        >
          生成并写入工作区
        </el-button>
        <div class="field-hint">写入的只有目标文件。校验失败会自动还原，不会留下半成品。</div>

        <div v-if="genError" class="gen-error">
          <div class="gen-error-title">{{ genError.message }}</div>
          <ul v-if="genError.details.length" class="gen-error-list">
            <li v-for="(item, idx) in genError.details" :key="idx">{{ detailText(item) }}</li>
          </ul>
        </div>

        <div v-if="genResult" class="gen-result">
          <div class="gen-result-title">已写入 {{ genResult.path }}</div>
          <div class="field-hint">
            {{ genResult.validated ? '校验通过' : '未校验' }} · 框架 {{ frameworkName(genResult.framework) }}
          </div>
          <div class="field-hint">在下方「生成记录」里预览，确认后可以提交或撤销。</div>
        </div>
      </el-card>
    </div>

    <!-- 生成记录 -->
    <el-card class="panel-card records-card">
      <template #header>
        <div class="section-header">
          <span class="card-title">生成记录</span>
          <el-button text :loading="recordsLoading" @click="loadRecords">刷新</el-button>
        </div>
      </template>

      <div class="records-layout">
        <div class="record-list">
          <el-empty v-if="!records.length" description="还没有生成记录" :image-size="70" />
          <div
            v-for="item in records"
            :key="item.id"
            :class="['record-item', { active: selected && selected.id === item.id }]"
            @click="openRecord(item.id)"
          >
            <div class="record-path">{{ item.target_path }}</div>
            <div class="record-meta">
              <el-tag size="small" :type="recordStatusTone(item.status)" effect="light">{{ recordStatusLabel(item.status) }}</el-tag>
              <span>{{ frameworkName(item.framework) }}</span>
              <span>{{ formatDate(item.created_at) }}</span>
            </div>
          </div>
        </div>

        <div class="record-detail">
          <el-empty v-if="!selected" description="选择一条记录查看预览" :image-size="70" />
          <template v-else>
            <div class="detail-head">
              <div>
                <div class="detail-title">{{ selected.target_path }}</div>
                <div class="field-hint">
                  {{ frameworkName(selected.framework) }} · 模型 {{ selected.model || '—' }} · {{ formatDate(selected.created_at) }}
                </div>
              </div>
              <el-tag :type="recordStatusTone(selected.status)" effect="light">{{ recordStatusLabel(selected.status) }}</el-tag>
            </div>

            <el-alert
              v-if="selected.changed_since_generation && selected.status === 'generated'"
              title="文件在生成后已被改动或删除，提交和撤销都会被拒绝。请先确认文件内容。"
              type="warning"
              :closable="false"
              class="detail-alert"
            />
            <el-alert
              v-if="selected.status === 'committed'"
              :title="`已提交：${selected.commit_message || ''}（${(selected.commit_hash || '').slice(0, 8)}）`"
              type="success"
              :closable="false"
              class="detail-alert"
            />

            <div class="detail-section">
              <div class="detail-label">录制的步骤</div>
              <ol class="mini-steps">
                <li v-for="step in selected.steps" :key="step.no">
                  <span class="mini-kind">{{ stepKindLabel(step.type) }}</span>
                  <span>{{ step.text || step.value || '' }}</span>
                </li>
              </ol>
            </div>

            <div class="detail-section">
              <div class="detail-label">文件内容预览{{ selected.truncated ? '（已截断）' : '' }}</div>
              <pre class="code-preview">{{ selected.file_exists ? selected.content : '文件当前不存在' }}</pre>
            </div>

            <div v-if="selected.reply" class="detail-section">
              <div class="detail-label">生成 agent 的说明</div>
              <div class="agent-reply">{{ selected.reply }}</div>
            </div>

            <div v-if="selected.status === 'generated'" class="detail-section commit-box">
              <div class="detail-label">提交说明</div>
              <el-input v-model="commitMessage" maxlength="500" show-word-limit />
              <div class="commit-actions">
                <el-button type="primary" :loading="committing" :disabled="selected.changed_since_generation" @click="commitSelected">
                  提交到 Git
                </el-button>
              </div>
            </div>

            <div v-if="selected.status === 'generated' || selected.status === 'committed'" class="detail-actions">
              <el-button type="danger" plain :loading="reverting" @click="revertSelected">
                {{ selected.status === 'generated' ? '撤销写入' : '回滚这次提交' }}
              </el-button>
            </div>
          </template>
        </div>
      </div>
    </el-card>

    <PathPickerDialog
      v-model="picker.visible"
      :mode="picker.mode"
      :workspace="config.workspace.trim()"
      :framework="config.framework"
      :start-path="picker.start"
      :default-name="suggestedFileName"
      @loaded="onRootLoaded"
      @select="onPathSelected"
    />
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowUp, ArrowDown, Delete, Close, Document, FolderOpened } from '@element-plus/icons-vue'
import api from '../api'
import PathPickerDialog from '../components/PathPickerDialog.vue'

// 与后端保持一致的常量（backend/app/services/recording/intent.py、locators.py）
const FRAMEWORKS = [
  { value: 'robot-selenium', label: 'Robot Framework（SeleniumLibrary）' },
  { value: 'playwright-ts', label: 'Playwright TypeScript' }
]
const ASSERT_TYPES = ['visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled', 'url_contains']
const WAIT_TYPES = ['wait_visible', 'wait_hidden']
const SLEEP_TYPES = ['sleep']
const NEEDS_TARGET = new Set(['visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled', 'wait_visible', 'wait_hidden'])
const NEEDS_VALUE = new Set(['text_contains', 'text_equals', 'value_equals', 'url_contains'])
const INTENT_LABELS = {
  visible: '元素可见', hidden: '元素隐藏', text_contains: '文字包含', text_equals: '文字等于',
  value_equals: '值等于', enabled: '元素可用', url_contains: '地址包含',
  wait_visible: '等待出现', wait_hidden: '等待消失', sleep: '固定等待'
}
const STEP_LABELS = {
  goto: '打开页面', click: '点击', fill: '输入', select: '选择', new_page: '新标签页',
  assert: '断言', wait: '等待', sleep: '固定等待'
}
const STEP_TONES = { goto: 'info', click: 'primary', fill: 'primary', select: 'primary', new_page: 'info', assert: 'success', wait: 'warning', sleep: 'danger' }
const STATE_LABELS = {
  idle: '未开始', starting: '启动中', recording: '录制中', stopping: '停止中', stopped: '已停止',
  cancelled: '已取消', failed: '失败', idle_timeout: '超时已停止', closed: '浏览器已关闭'
}
const STORAGE_KEY = 'autocase.recording.config'
// 与后端 locators.py 保持一致：Playwright 与 RF 两组的优先级，以及结构定位（需标黄）。
const PW_ORDER = ['testid', 'role', 'label', 'placeholder', 'text', 'css']
const RF_ORDER = ['rf_id', 'rf_name', 'rf_xpath', 'rf_css']
const FRAGILE_KINDS = ['css', 'rf_css']

const config = reactive(loadConfig())
const modelOptions = ref([])
const detect = ref(null)
const detecting = ref(false)

const session = ref(null)
const starting = ref(false)
const stopping = ref(false)
const draft = ref(null)
const generating = ref(false)
const genError = ref(null)
const genResult = ref(null)

const records = ref([])
const recordsLoading = ref(false)
const selected = ref(null)
const commitMessage = ref('')
const committing = ref(false)
const reverting = ref(false)

let pollTimer = null

// ---- 配置持久化（仅浏览器本地便捷设置，不含敏感信息）----
function loadConfig() {
  const defaults = { baseUrl: '', workspace: '', framework: 'robot-selenium', targetPath: '', model: '', title: '', python: '' }
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') }
  } catch {
    return defaults
  }
}

watch(config, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // 隐私模式或存储被禁用时忽略，页面照常工作。
  }
}, { deep: true })

const targetPlaceholder = computed(() =>
  config.framework === 'robot-selenium' ? 'tests/login.robot' : 'tests/login.spec.ts'
)

// ---- 路径选择（浏览工作区目录 / 目标文件）----
// 浏览器拿不到本地绝对路径，所以由后端在 AUTOMATION_WORKSPACE_ROOT 内列目录，前端只填相对路径。
const picker = reactive({ visible: false, mode: 'dir', start: '' })
const workspaceRoot = ref('')
const EXT_BY_FRAMEWORK = { 'robot-selenium': '.robot', 'playwright-ts': '.spec.ts' }
const KNOWN_EXTS = ['.spec.ts', '.test.ts', '.robot', '.resource', '.ts']
const suggestedFileName = computed(() => fileSlug(config.title) || 'recorded_flow')

function fileSlug(text) {
  return String(text || '').trim().replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 50)
}

function dirnameOf(path) {
  const parts = String(path || '').trim().split('/')
  parts.pop()
  return parts.join('/')
}

function openWorkspacePicker() {
  // 从当前工作区的上级开始浏览，方便对比同级目录。
  picker.mode = 'dir'
  picker.start = dirnameOf(config.workspace.trim())
  picker.visible = true
}

function openTargetPicker() {
  if (!config.workspace.trim()) {
    ElMessage.warning('请先选择工作区，再选择目标文件')
    return
  }
  picker.mode = 'file'
  picker.start = dirnameOf(config.targetPath.trim())
  picker.visible = true
}

function onRootLoaded({ root }) {
  workspaceRoot.value = root || ''
}

function onPathSelected({ path, dirs = [], dirsOf = '' }) {
  if (picker.mode === 'dir') {
    const changed = path !== config.workspace.trim()
    config.workspace = path
    if (changed) detect.value = null
    if (!config.targetPath.trim()) autoFillTargetPath(path, dirs, dirsOf)
    detectWorkspace()
    return
  }
  config.targetPath = path
}

// 选完工作区后按它是否存在 tests/specs 目录推荐目标路径；dirs 不属于刚选中的目录时补一次查询。
async function autoFillTargetPath(path, dirs, dirsOf) {
  const names = dirsOf === path && dirs.length ? dirs : await workspaceDirs(path)
  if (!config.targetPath.trim()) config.targetPath = suggestTargetPath(names)
}

async function workspaceDirs(path) {
  try {
    const { data } = await api.browseAutomationFiles(path, '', config.framework)
    return (data.dirs || []).map(item => item.name)
  } catch {
    return []
  }
}

function suggestTargetPath(dirs) {
  const dir = dirs.includes('tests') ? 'tests' : dirs.includes('specs') ? 'specs' : ''
  const name = `${suggestedFileName.value}${EXT_BY_FRAMEWORK[config.framework] || '.robot'}`
  return dir ? `${dir}/${name}` : name
}

// 切换框架时同步目标文件扩展名，避免出现「Robot 框架 + .spec.ts」这种组合。
watch(() => config.framework, (next) => {
  const current = (config.targetPath || '').trim()
  if (!current) return
  const matched = KNOWN_EXTS.filter(ext => current.endsWith(ext)).sort((a, b) => b.length - a.length)[0]
  const expected = EXT_BY_FRAMEWORK[next]
  if (!matched || !expected || current.endsWith(expected)) return
  config.targetPath = current.slice(0, -matched.length) + expected
})

// ---- 会话状态 ----
const isRecording = computed(() => session.value?.status === 'recording')
const isStopped = computed(() => ['stopped', 'idle_timeout', 'closed'].includes(session.value?.status))
const canEdit = computed(() => isRecording.value || isStopped.value)
const steps = computed(() => session.value?.steps || [])
const aliases = computed(() => session.value?.aliases || [])
const picking = computed(() => !!session.value?.pick)
const stateLabel = computed(() => STATE_LABELS[session.value?.status || 'idle'] || '未知')

const blockers = computed(() => steps.value.filter(s => s.target && effectiveStatus(s) === 'ambiguous').map(s => s.no))
const fragileCount = computed(() => steps.value.filter(s => s.target && effectiveStatus(s) === 'fragile').length)
const canGenerate = computed(() =>
  isStopped.value && steps.value.length > 0 && blockers.value.length === 0 &&
  !!config.workspace.trim() && !!config.targetPath.trim() && !!config.model && !generating.value
)

function applySession(data) {
  session.value = data
  // 拾取模式下，新出现的拾取元素会被草稿自动选中，然后关闭拾取。
  if (draft.value && draft.value.picking && data.picks && data.picks.length > draft.value.pickBaseline) {
    const latest = data.picks[data.picks.length - 1]
    draft.value.target = latest.target.alias
    draft.value.picking = false
    if (data.pick) togglePick(false, true)
  }
}

async function loadCurrent() {
  try {
    const { data } = await api.getRecordingCurrent()
    applySession(data)
  } catch (error) {
    if (error.response && error.response.status === 404) session.value = null
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(loadCurrent, 1000)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

watch(() => session.value?.status, (status) => {
  if (status === 'recording') startPolling()
  else stopPolling()
})

async function startRecording() {
  const url = (config.baseUrl || '').trim()
  if (!/^https?:\/\//.test(url)) {
    ElMessage.warning('请先填写以 http:// 或 https:// 开头的录制起始地址')
    return
  }
  starting.value = true
  genResult.value = null
  genError.value = null
  try {
    const { data } = await api.startRecording(url)
    draft.value = null
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
    await loadCurrent()
  } finally {
    starting.value = false
  }
}

async function stopRecording() {
  stopping.value = true
  try {
    const { data } = await api.stopRecording()
    if (draft.value && draft.value.picking) draft.value.picking = false
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    stopping.value = false
  }
}

async function cancelRecording() {
  try {
    await ElMessageBox.confirm('取消会丢弃本次录制的全部步骤，确定吗？', '取消录制', {
      confirmButtonText: '确定取消', cancelButtonText: '继续录制', type: 'warning'
    })
  } catch {
    return
  }
  try {
    const { data } = await api.cancelRecording()
    draft.value = null
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function togglePick(on, quiet = false) {
  try {
    const { data } = await api.setRecordingPick(on)
    if (session.value) session.value = { ...session.value, pick: data.pick }
  } catch (error) {
    if (!quiet) ElMessage.error(errorText(error))
  }
}

async function removeStep(no) {
  try {
    const { data } = await api.deleteRecordingStep(no)
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function moveStep(no, direction) {
  try {
    const { data } = await api.moveRecordingStep(no, direction)
    applySession(data)
  } catch (error) {
    ElMessage.warning(errorText(error))
  }
}

// ---- 断言 / 等待 模板 ----
function draftKindTypes(kind) {
  if (kind === 'wait') return WAIT_TYPES
  if (kind === 'sleep') return SLEEP_TYPES
  return ASSERT_TYPES
}

const draftTypes = computed(() => draft.value ? draftKindTypes(draft.value.kind) : [])
const draftTitle = computed(() => ({ assert: '添加断言', wait: '添加等待', sleep: '固定等待' })[draft.value?.kind] || '')

function openDraft(kind) {
  const first = draftKindTypes(kind)[0]
  draft.value = {
    kind,
    text: '',
    intentType: first,
    target: null,
    value: '',
    seconds: 1,
    parsed: null,
    parsing: false,
    inserting: false,
    picking: false,
    pickBaseline: 0
  }
}

function closeDraft() {
  if (draft.value && draft.value.picking) togglePick(false, true)
  draft.value = null
}

function needsTarget(type) { return NEEDS_TARGET.has(type) }
function needsValue(type) { return NEEDS_VALUE.has(type) }

async function parseDraft() {
  draft.value.parsing = true
  draft.value.parsed = null
  try {
    const { data } = await api.parseRecordingIntent(draft.value.text.trim(), config.model)
    draft.value.parsed = data.intent
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    draft.value.parsing = false
  }
}

async function insertIntent(intent, text) {
  draft.value.inserting = true
  try {
    const { data } = await api.addRecordingStep({ intent, text: (text || '').trim() || null })
    draft.value = null
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    if (draft.value) draft.value.inserting = false
  }
}

function buildFormIntent(current) {
  const type = current.intentType
  const intent = { type, target: null, value: null, seconds: null }
  if (needsTarget(type)) {
    if (!current.target) throw new Error('请选择目标元素，或点击「拾取元素」')
    intent.target = current.target
  }
  if (needsValue(type)) {
    const value = String(current.value || '').trim()
    if (!value) throw new Error('请填写文字或值')
    intent.value = value
  }
  if (type === 'sleep') {
    const seconds = Number(current.seconds)
    if (!(seconds > 0 && seconds <= 60)) throw new Error('固定等待需在 0 到 60 秒之间')
    intent.seconds = seconds
  }
  return intent
}

function insertFromForm() {
  let intent
  try {
    intent = buildFormIntent(draft.value)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  insertIntent(intent, draft.value.text)
}

async function pickForDraft() {
  draft.value.pickBaseline = (session.value?.picks || []).length
  draft.value.picking = true
  await togglePick(true)
}

// ---- 步骤展示 ----
function frameworkName(value) {
  return (FRAMEWORKS.find(f => f.value === value) || {}).label || (value ? value : '未识别')
}

function intentLabel(type) { return INTENT_LABELS[type] || type }

function intentDescription(intent, target) {
  if (!intent) return ''
  const parts = [intentLabel(intent.type)]
  const alias = intent.target || (target && target.alias)
  if (alias) parts.push(`「${alias}」`)
  if (intent.value !== null && intent.value !== undefined && intent.value !== '') parts.push(`：${intent.value}`)
  if (intent.type === 'sleep') parts.push(`${intent.seconds} 秒`)
  return parts.join(' ')
}

function stepKindLabel(type) { return STEP_LABELS[type] || type }
function stepTone(type) { return STEP_TONES[type] || 'info' }

function stepDescription(step) {
  switch (step.type) {
    case 'goto': return `打开 ${step.url || ''}`
    case 'click': return `点击「${step.target ? step.target.alias : '元素'}」`
    case 'fill': return `在「${step.target ? step.target.alias : '输入框'}」中输入 ${step.value ?? ''}`
    case 'select': return `在「${step.target ? step.target.alias : '下拉框'}」中选择 ${step.label || step.value || ''}`
    case 'new_page': return '切换到新打开的标签页'
    case 'sleep': return `固定等待 ${step.intent ? step.intent.seconds : ''} 秒`
    case 'assert':
    case 'wait': return intentDescription(step.intent, step.target)
    default: return step.type
  }
}

// 与后端 locators.pick_for 一致：按当前框架挑出第一个唯一的候选。
function pickFor(framework, candidates = []) {
  const order = framework === 'robot-selenium' ? RF_ORDER : PW_ORDER
  for (const kind of order) {
    const hit = candidates.find(c => c.kind === kind && c.count === 1)
    if (hit) return hit
  }
  return null
}

function effectiveStatus(step) {
  if (!step.target) return null
  const chosen = pickFor(config.framework, step.target.candidates || [])
  if (!chosen) return 'ambiguous'
  if (FRAGILE_KINDS.includes(chosen.kind)) return 'fragile'
  if (step.target.status === 'stale') return 'stale'
  return 'unique'
}

function locatorText(target) {
  const chosen = pickFor(config.framework, target.candidates || [])
  return chosen ? chosen.desc : '无唯一定位'
}

function statusLabel(status) {
  return ({ unique: '唯一', fragile: '结构定位', ambiguous: '不唯一', stale: '页面已变化' })[status] || status
}

function statusTone(status) {
  return ({ unique: 'success', fragile: 'warning', ambiguous: 'danger', stale: 'info' })[status] || 'info'
}

// ---- 工作区检测 ----
async function detectWorkspace() {
  const workspace = (config.workspace || '').trim()
  if (!workspace) return
  detecting.value = true
  try {
    const { data } = await api.detectRecordingWorkspace(workspace, (config.python || '').trim())
    detect.value = data
    if (data.framework) config.framework = data.framework
  } catch (error) {
    detect.value = null
    ElMessage.warning(errorText(error))
  } finally {
    detecting.value = false
  }
}

// ---- 生成 ----
async function generate(overwrite) {
  generating.value = true
  genError.value = null
  genResult.value = null
  try {
    const { data } = await api.generateRecording({
      model: config.model,
      workspace_path: config.workspace.trim(),
      framework: config.framework,
      target_path: config.targetPath.trim(),
      title: config.title.trim() || 'recorded flow',
      python: (config.python || '').trim() || undefined,
      overwrite
    })
    genResult.value = data
    ElMessage.success(`已写入 ${data.path}`)
    await loadRecords()
    if (data.generation_id) await openRecord(data.generation_id)
  } catch (error) {
    const details = (error.response && error.response.data && error.response.data.details) || []
    const isConflict = error.response && error.response.status === 409 && details.some(d => String(d).startsWith('conflict:'))
    if (isConflict) {
      try {
        await ElMessageBox.confirm(
          `目标文件 ${config.targetPath.trim()} 已存在。覆盖后可以在下方记录里撤销，恢复原内容。是否覆盖？`,
          '文件已存在',
          { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' }
        )
      } catch {
        generating.value = false
        return
      }
      await generate(true)
      return
    }
    genError.value = { message: errorText(error), details }
  } finally {
    generating.value = false
  }
}

function detailText(item) {
  const text = String(item)
  if (text.startsWith('ambiguous:')) return `第 ${text.split(':')[1]} 步定位不唯一`
  if (text.startsWith('conflict:')) return `目标文件已存在：${text.slice('conflict:'.length)}`
  return text
}

// ---- 生成记录 ----
async function loadRecords() {
  recordsLoading.value = true
  try {
    const { data } = await api.listRecordingGenerations()
    records.value = data.generations || []
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    recordsLoading.value = false
  }
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

async function commitSelected() {
  committing.value = true
  try {
    await api.commitRecordingGeneration(selected.value.id, commitMessage.value)
    ElMessage.success('已提交')
    await openRecord(selected.value.id)
    await loadRecords()
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
    await loadRecords()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    reverting.value = false
  }
}

function recordStatusLabel(status) {
  return ({ generated: '未提交', committed: '已提交', reverted: '已撤销' })[status] || status
}

function recordStatusTone(status) {
  return ({ generated: 'warning', committed: 'success', reverted: 'info' })[status] || 'info'
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '-'
}

function errorText(error) {
  if (error && error.response && error.response.data && error.response.data.error) return error.response.data.error
  return (error && error.message) || '操作失败'
}

// ---- 生命周期 ----
onMounted(async () => {
  loadCurrent()
  try {
    const { data } = await api.getAutomationModels()
    modelOptions.value = data.models || []
    if (!config.model && modelOptions.value.length) config.model = modelOptions.value[0].value
  } catch {
    modelOptions.value = []
  }
  if (config.workspace) detectWorkspace()
  loadRecords()
})

// 页面被 keep-alive 缓存：回到页面时刷新会话状态，离开时停止轮询。
onActivated(() => loadCurrent())
onDeactivated(() => stopPolling())
onUnmounted(() => stopPolling())
</script>

<style scoped>
.recording-page { max-width:1440px; margin:0 auto; animation: page-enter .32s ease both; }
@keyframes page-enter { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

/* 页头：与「自动化脚本」页保持一致 */
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:22px; }
.eyebrow { color:#0071e3; font-size:11px; font-weight:700; letter-spacing:1.4px; margin-bottom:8px; text-transform:uppercase; }
h1 { margin:0; color:#1d1d1f; font-size:28px; font-weight:700; letter-spacing:-.3px; }
.page-header p { margin:8px 0 0; color:#6e6e73; font-size:14px; line-height:1.55; max-width:760px; }

/* 卡片 */
.panel-card { border:1px solid #ebedf0; border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px rgba(16,24,40,.05); margin-bottom:20px; }
.panel-card :deep(.el-card__header) { padding:15px 20px; border-bottom:1px solid #f0f2f5; background:linear-gradient(180deg,#fcfdff,#f7f9fc); }
.panel-card :deep(.el-card__body) { padding:20px; }
.section-header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.card-title { position:relative; padding-left:12px; font-size:15px; font-weight:700; color:#1d1d1f; }
.card-title::before { content:''; position:absolute; left:0; top:50%; transform:translateY(-50%); width:4px; height:15px; border-radius:3px; background:linear-gradient(180deg,#0a84ff,#0071e3); }
.field-hint { color:#909399; font-size:12px; line-height:1.5; margin-top:6px; }
.root-hint { margin-left:6px; color:#a8abb2; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11.5px; }

/* 覆盖 Element Plus 对 append 内按钮的 margin:0 -20px + flex:1，否则并排按钮会叠在一起。
   两个按钮固定同高、文字居中，中间加一条竖线，避免看起来像一个按钮。 */
:deep(.el-input-group__append) { padding:0 6px; }
:deep(.el-input-group__append) .append-group { display:flex; align-items:center; }
:deep(.el-input-group__append) .append-group .el-button {
  flex:0 0 auto; margin:0; height:26px; padding:0 8px; border-radius:6px;
  font-weight:500; line-height:1;
}
:deep(.el-input-group__append) .append-group .el-button:hover { background:#eaf3ff; color:#0071e3; }
.append-sep { flex:0 0 auto; width:1px; height:14px; margin:0 4px; background:#dfe3e9; }

/* 配置 */
.config-grid { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(280px,.9fr); gap:24px; }
.config-form :deep(.el-form-item__label) { font-size:13px; font-weight:600; color:#303133; padding-bottom:6px; }
.config-form :deep(.el-form-item) { margin-bottom:14px; }
.detect-panel { background:#f7f9fc; border:1px solid #ebedf0; border-radius:12px; padding:16px; }
.detect-title { font-size:13px; font-weight:700; color:#303133; margin-bottom:10px; }
.detect-empty { color:#909399; font-size:12.5px; line-height:1.6; }
.detect-row { display:flex; gap:12px; align-items:flex-start; margin-bottom:10px; font-size:13px; }
.detect-key { flex-shrink:0; width:110px; color:#6e6e73; font-weight:600; }
.detect-value { color:#1d1d1f; word-break:break-all; }
.detect-value code, code { font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; font-size:12px; }
.detect-sample { color:#1d1d1f; font-family: ui-monospace, Consolas, monospace; font-size:12px; line-height:1.7; }
.detect-evidence { margin-top:8px; font-size:12px; color:#6e6e73; }
.detect-evidence summary { cursor:pointer; color:#0071e3; }
.evidence-line { display:flex; gap:8px; padding:3px 0; }
.evidence-kind { color:#303133; font-weight:600; min-width:110px; }
.evidence-source { color:#a1a1a6; margin-left:auto; }

/* 主布局 */
.main-layout { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(330px,.75fr); gap:20px; align-items:start; }

/* 状态 */
.state-pill { display:inline-flex; align-items:center; height:24px; padding:0 10px; border-radius:999px; font-size:12px; font-weight:700; background:#f2f4f7; color:#6e6e73; }
.state-recording { background:#fff1f0; color:#e5484d; }
.state-stopped, .state-idle_timeout, .state-closed { background:#eaf7ef; color:#1e7a46; }
.state-failed, .state-cancelled { background:#fff7e6; color:#ad6800; }

/* 工具栏 */
.toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.toolbar-sep { width:1px; height:22px; background:#e4e7ed; margin:0 4px; }
.pick-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#fff7e6; color:#ad6800; font-size:12.5px; line-height:1.5; }
.session-error { margin-top:12px; }

/* 时间线 */
.timeline { list-style:none; margin:16px 0 0; padding:0; display:flex; flex-direction:column; gap:10px; }
.step { display:flex; gap:12px; align-items:flex-start; padding:12px 12px 12px 10px; border:1px solid #ebedf0; border-radius:12px; background:#fff; transition:border-color .18s ease, box-shadow .18s ease; }
.step:hover { border-color:#dce7fb; box-shadow:0 4px 14px rgba(16,24,40,.06); }
.step.is-blocked { border-color:#ffccc7; background:#fffbfa; }
.step-no { flex-shrink:0; width:24px; height:24px; border-radius:50%; background:#eef4ff; color:#0071e3; font-size:12px; font-weight:700; display:flex; align-items:center; justify-content:center; font-variant-numeric:tabular-nums; }
.step-body { flex:1; min-width:0; }
.step-main { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.step-text { color:#1d1d1f; font-size:14px; line-height:1.5; word-break:break-word; }
.step-note { margin-top:4px; color:#6e6e73; font-size:12.5px; }
.step-locator { margin-top:8px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.step-alias { color:#303133; font-size:12.5px; font-weight:600; }
.locator-code { color:#6e6e73; font-size:11.5px; background:#f7f9fc; border:1px solid #eef0f4; border-radius:6px; padding:2px 6px; word-break:break-all; }
.step-actions { display:flex; flex-direction:row; align-items:center; gap:2px; flex-shrink:0; }
.kind-tag { flex-shrink:0; display:inline-flex; align-items:center; height:22px; padding:0 9px; border-radius:6px; font-size:12px; font-weight:700; }
.kind-primary { background:#eaf3ff; color:#0071e3; }
.kind-success { background:#eaf7ef; color:#1e7a46; }
.kind-warning { background:#fff7e6; color:#ad6800; }
.kind-danger { background:#fff1f0; color:#e5484d; }
.kind-info { background:#f2f4f7; color:#6e6e73; }

/* 断言 / 等待 模板 */
.draft-card { margin-top:16px; border:1.5px dashed #c9d6ea; border-radius:14px; padding:16px; background:#fbfdff; }
.draft-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.draft-title { font-size:14px; font-weight:700; color:#1d1d1f; }
.draft-nl { display:flex; gap:10px; align-items:flex-start; }
.draft-nl .el-textarea { flex:1; }
.parsed-preview { margin-top:12px; padding:12px; border-radius:10px; background:#f0faf4; border:1px solid #d6f0df; }
.parsed-line { display:flex; gap:10px; font-size:13.5px; color:#1d1d1f; }
.parsed-label { color:#1e7a46; font-weight:700; flex-shrink:0; }
.parsed-actions { margin-top:10px; display:flex; justify-content:flex-end; }
.form-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px; }
.draft-actions { margin-top:12px; display:flex; justify-content:flex-end; }

/* 生成 */
.checklist { display:flex; flex-direction:column; gap:10px; margin-bottom:18px; }
.check { display:flex; align-items:flex-start; gap:10px; font-size:13px; color:#6e6e73; line-height:1.5; }
.check-dot { flex-shrink:0; width:9px; height:9px; margin-top:6px; border-radius:50%; background:#d0d5dd; }
.check.ok { color:#1e7a46; }
.check.ok .check-dot { background:#34c759; }
.check.bad { color:#e5484d; }
.check.bad .check-dot { background:#e5484d; }
.check.warn { color:#ad6800; }
.check.warn .check-dot { background:#ffb020; }
.generate-btn { width:100%; }
.gen-error { margin-top:14px; padding:12px; border-radius:10px; background:#fff1f0; color:#e5484d; font-size:13px; }
.gen-error-title { font-weight:700; }
.gen-error-list { margin:8px 0 0; padding-left:18px; font-size:12.5px; line-height:1.6; }
.gen-result { margin-top:14px; padding:12px; border-radius:10px; background:#f0faf4; }
.gen-result-title { color:#1e7a46; font-weight:700; font-size:13.5px; }

/* 生成记录 */
.records-layout { display:grid; grid-template-columns:minmax(260px,.8fr) minmax(0,1.6fr); gap:20px; min-height:200px; }
.record-list { display:flex; flex-direction:column; gap:8px; max-height:560px; overflow:auto; }
.record-item { padding:12px; border:1px solid #ebedf0; border-radius:10px; cursor:pointer; background:#fff; transition:border-color .15s ease, background .15s ease; }
.record-item:hover { border-color:#dce7fb; background:#fbfdff; }
.record-item.active { border-color:#0071e3; background:#f5f9ff; }
.record-path { color:#1d1d1f; font-size:13px; font-weight:600; font-family: ui-monospace, Consolas, monospace; word-break:break-all; }
.record-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px; color:#8e8e93; font-size:12px; }
.record-detail { min-width:0; }
.detail-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }
.detail-title { color:#1d1d1f; font-size:15px; font-weight:700; font-family: ui-monospace, Consolas, monospace; word-break:break-all; }
.detail-alert { margin-bottom:12px; }
.detail-section { margin-top:14px; }
.detail-label { font-size:12px; font-weight:700; color:#6e6e73; margin-bottom:8px; letter-spacing:.2px; }
.mini-steps { margin:0; padding-left:18px; color:#303133; font-size:12.5px; line-height:1.8; }
.mini-kind { display:inline-block; min-width:64px; color:#0071e3; font-weight:600; }
.code-preview { margin:0; max-height:360px; overflow:auto; padding:14px; border-radius:10px; background:#0f172a; color:#e2e8f0; font-size:12px; line-height:1.6; font-family: ui-monospace, Consolas, monospace; white-space:pre; }
.agent-reply { padding:10px 12px; border-radius:9px; background:#f7f9fc; color:#303133; font-size:12.5px; line-height:1.6; white-space:pre-wrap; }
.commit-box { padding:12px; border:1px solid #ebedf0; border-radius:10px; }
.commit-actions { margin-top:10px; display:flex; justify-content:flex-end; }
.detail-actions { margin-top:14px; display:flex; justify-content:flex-end; }

@media (max-width: 1100px) {
  .config-grid, .main-layout, .records-layout { grid-template-columns:1fr; }
  .form-grid { grid-template-columns:1fr; }
}
</style>
