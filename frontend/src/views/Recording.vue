<template>
  <div :class="['recording-page', { 'is-panel': panelMode, 'is-embed': embedMode, 'is-collapsed': panelCollapsed, 'is-resizing': resizing }]">
    <!-- 嵌入模式：左 iframe 是被测系统（占用剩余全部宽度），右侧是可收起的编排面板 -->
    <iframe v-if="embedMode" class="embed-frame" :src="targetUrl" title="被测系统"></iframe>

    <button
      v-if="embedMode && panelCollapsed"
      type="button"
      class="embed-rail"
      title="展开编排面板"
      @click="panelCollapsed = false"
    >
      <span class="rail-caret">‹</span>
      <span class="rail-count">{{ steps.length }}</span>
      <span :class="['rail-dot', { live: isRecording }]"></span>
    </button>

    <div v-if="embedMode && !panelCollapsed" class="embed-resizer" title="拖动调整面板宽度" @pointerdown="startResize"></div>

    <div
      v-show="!embedMode || !panelCollapsed"
      class="page-col"
      :class="{ 'is-panel': embedMode }"
      :style="embedMode ? { width: panelWidth + 'px' } : null"
    >
      <!-- 面板窗口（录制时自动弹出的右侧编排栏）：紧凑标题条，把空间都留给步骤 -->
      <div v-if="compactMode" class="panel-head">
        <span :class="['panel-dot', session && session.status === 'recording' ? (paused ? 'is-paused' : 'is-live') : '']"></span>
        <span class="panel-title">{{ panelTitle }}</span>
        <span v-if="steps.length" class="panel-count">{{ steps.length }} 步</span>
        <span v-if="blockers.length" class="panel-warn">{{ blockers.length }} 处不唯一</span>
        <div class="panel-actions">
          <el-button v-if="session?.panel" text size="small" :icon="Connection" @click="relayoutPanel">并排</el-button>
          <el-button text size="small" @click="configExpanded = !configExpanded">{{ configExpanded ? '收起配置' : '配置' }}</el-button>
          <button v-if="embedMode" type="button" class="panel-collapse" title="收起面板，把宽度还给页面" @click="panelCollapsed = true">
            <span class="collapse-arrow">›</span>
          </button>
        </div>
      </div>

      <div v-if="!compactMode" class="page-header">
      <div class="page-intro">
        <div class="eyebrow">RECORDING · CODE GENERATOR</div>
        <h1>自动化工具</h1>
        <p>
          在真实浏览器里操作，记录步骤与唯一定位；录制时会自动打开编排面板，插入断言后按工作区框架生成脚本。
          <span class="hint-dot" title="写入后可以预览，再提交或撤销；校验失败会自动还原，不留半成品。">?</span>
        </p>
      </div>
      <div class="page-actions">
        <el-button :icon="Tickets" @click="$router.push('/recording/records')">生成记录</el-button>
        <el-button v-if="!isRecording" type="primary" size="large" :loading="starting" @click="startRecording">开始录制</el-button>
        <el-button v-else type="danger" plain size="large" :loading="stopping" @click="stopRecording">停止录制</el-button>
        <el-button @click="$router.push('/')">返回首页</el-button>
      </div>
    </div>

    <!-- 左配置 / 右步骤+生成：一屏内看完，生成记录单独成页 -->
    <div class="recording-body">
    <!-- 目标与配置 -->
    <el-card v-show="!compactMode || configExpanded" class="panel-card config-card">
      <template #header>
        <div class="section-header">
          <span class="card-title">目标与配置</span>
          <span class="field-hint">配置会记在本浏览器里，下次打开自动填入</span>
        </div>
      </template>
      <el-form label-position="top" class="config-form config-form--grid">
        <el-form-item class="span-2" label="录制起始地址">
          <el-input v-model="config.baseUrl" placeholder="http://127.0.0.1:8080/login" clearable />
        </el-form-item>
        <el-form-item class="span-2" label="工作区（Git 目录）">
          <el-input v-model="config.workspace" placeholder="点击「浏览」选择工作区目录" clearable>
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
          <!-- 检测结果内联成一行，不再常驻一整栏 -->
          <div v-if="detect" class="detect-inline">
            <span class="detect-chip" :class="detect.framework ? 'is-ok' : ''">{{ frameworkName(detect.framework) }}</span>
            <span class="detect-chip" :class="detect.python ? 'is-ok' : 'is-warn'" :title="detect.python || ''">
              Python {{ detect.python ? tailPath(detect.python) : '未找到' }}
            </span>
            <span class="detect-chip" :class="detect.seleniumlibrary_version ? 'is-ok' : 'is-warn'">
              SeleniumLibrary {{ detect.seleniumlibrary_version || '未检测到，按主流写法生成' }}
            </span>
            <details v-if="(detect.samples && detect.samples.length) || (detect.evidence && detect.evidence.length)" class="detect-more">
              <summary>依据与参考文件</summary>
              <div v-for="sample in detect.samples || []" :key="sample.path" class="detect-sample">{{ sample.path }}</div>
              <div v-for="(item, idx) in detect.evidence || []" :key="idx" class="evidence-line">
                <span class="evidence-kind">{{ item.kind }}</span>
                <span>{{ item.value === null ? '—' : item.value }}</span>
                <span class="evidence-source">{{ item.source }}</span>
              </div>
            </details>
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
        <el-form-item label="模型（断言解析与代码生成）">
          <el-select v-model="config.model" filterable placeholder="选择模型" style="width: 100%">
            <el-option v-for="m in modelOptions" :key="m.value" :label="m.value" :value="m.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标文件（工作区内相对路径）">
          <el-input v-model="config.targetPath" :placeholder="targetPlaceholder" clearable>
            <template #append>
              <el-button :icon="Document" :disabled="!config.workspace.trim()" @click="openTargetPicker">浏览</el-button>
            </template>
          </el-input>
          <div class="field-hint">
            选已有文件会覆盖（生成前会再次确认）；也可以填新文件名。
            <span v-if="!config.workspace.trim()">需先选择工作区。</span>
          </div>
        </el-form-item>
        <el-form-item label="用例名称">
          <el-input v-model="config.title" placeholder="例如：登录成功" maxlength="200" clearable />
        </el-form-item>
        <div class="span-2 advanced-row">
          <button type="button" class="advanced-toggle" @click="showAdvanced = !showAdvanced">
            {{ showAdvanced ? '收起高级设置' : '高级设置：Python 解释器' }}
          </button>
        </div>
        <el-form-item v-if="showAdvanced" class="span-2" label="Python 解释器（可选，留空则自动查找项目里的 .venv）">
          <el-input v-model="config.python" placeholder="例如 D:/proj/.venv/Scripts/python.exe" clearable />
        </el-form-item>
      </el-form>
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
          <el-button v-if="!isRecording" :type="compactMode ? 'primary' : 'default'" :loading="starting" @click="startRecording">开始录制</el-button>
          <el-button v-else type="danger" plain :loading="stopping" @click="stopRecording">停止录制</el-button>
          <el-button
            v-if="isRecording"
            :type="paused ? 'success' : 'default'"
            :icon="paused ? VideoPlay : VideoPause"
            :loading="pausing"
            :title="paused ? '继续把操作记录为步骤' : '浏览器照常可用，但操作不再记为步骤'"
            @click="togglePause"
          >
            {{ paused ? '继续录制' : '暂停录制' }}
          </el-button>
          <el-button :type="picking ? 'warning' : 'default'" :disabled="!isRecording" @click="togglePick(!picking)">
            {{ picking ? '退出拾取' : '拾取元素' }}
          </el-button>
          <span class="toolbar-sep"></span>
          <el-button :disabled="!canEdit" @click="openDraft('assert')">添加断言</el-button>
          <el-button :disabled="!canEdit" @click="openDraft('wait')">添加等待</el-button>
          <el-button :disabled="!canEdit" @click="openDraft('sleep')">固定等待</el-button>
        </div>

        <div v-if="paused" class="pause-hint">
          已暂停：可以在浏览器里自由操作（关闭弹层、滚动、试填一遍都不算），这些动作不会被记录。点「继续录制」接着录。
        </div>

        <div v-if="picking" class="pick-hint">
          拾取模式已开启：在浏览器里点击元素，它会被记为可引用的元素，不会记为操作步骤。
        </div>
        <el-alert v-if="session && session.error" :title="session.error" type="error" :closable="false" class="session-error" />

        <div v-if="!steps.length && !draft" class="steps-empty">
          <span class="steps-empty-dot"></span>
          <span>还没有步骤 · 点「开始录制」后，在弹出的浏览器里操作</span>
        </div>

        <ol v-if="steps.length" ref="timelineEl" class="timeline">
          <li
            v-for="step in steps"
            :key="step.no"
            :class="['step', { 'is-blocked': effectiveStatus(step) === 'ambiguous' }]"
          >
            <div class="step-no" :title="canEdit ? '按住拖动可排序' : ''">{{ step.no }}</div>
            <div class="step-body">
              <!-- 就地编辑：只改值，不动定位 -->
              <div v-if="editing && editing.no === step.no" class="step-editor">
                <el-input
                  v-if="step.type === 'fill'"
                  v-model="editing.value"
                  size="small"
                  placeholder="输入值"
                  @keyup.enter="saveEdit"
                  @keyup.esc="cancelEdit"
                />
                <template v-else-if="step.type === 'select'">
                  <el-input v-model="editing.value" size="small" placeholder="选项值（value）" @keyup.enter="saveEdit" />
                  <el-input v-model="editing.label" size="small" placeholder="选项文本（可选）" @keyup.enter="saveEdit" />
                </template>
                <el-input
                  v-else-if="step.type === 'goto'"
                  v-model="editing.url"
                  size="small"
                  placeholder="http(s)://..."
                  @keyup.enter="saveEdit"
                  @keyup.esc="cancelEdit"
                />
                <el-input-number
                  v-else-if="step.type === 'sleep'"
                  v-model="editing.seconds"
                  :min="0.1"
                  :max="600"
                  :step="0.5"
                  :precision="1"
                  size="small"
                  controls-position="right"
                />
                <div class="step-editor-actions">
                  <el-button size="small" type="primary" :loading="editing.saving" @click="saveEdit">保存</el-button>
                  <el-button size="small" @click="cancelEdit">取消</el-button>
                </div>
              </div>
              <template v-else>
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
              </template>
            </div>
            <div class="step-actions">
              <el-button
                v-if="editableFields(step).length"
                text
                :icon="EditPen"
                :disabled="!canEdit"
                title="编辑这一步的值"
                @click="startEdit(step)"
              />
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

      <!-- 生成：面板模式下录制中就藏起来，停止后再出现（面板空间留给步骤） -->
      <el-card v-if="!compactMode || isStopped" class="panel-card generate-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">生成</span>
          </div>
        </template>

        <div v-if="isStopped" class="checklist">
          <div :class="['check', { ok: steps.length > 0 }]">
            <span class="check-dot"></span>已录制 {{ steps.length }} 个步骤
          </div>
          <div :class="['check', { ok: isStopped }]">
            <span class="check-dot"></span>录制已停止
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
        <!-- 没停录制时把 4 行检查压成一行，别占掉半屏 -->
        <div v-else class="gen-compact">
          停止录制后可生成 · 已录 <b>{{ steps.length }}</b> 步
          <span v-if="blockers.length" class="gen-compact-bad">· {{ blockers.length }} 处定位不唯一</span>
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
          <div class="gen-result-actions">
            <el-button size="small" type="primary" plain @click="$router.push({ path: '/recording/records', query: { id: genResult.generation_id } })">
              查看生成记录
            </el-button>
            <span class="field-hint">预览文件内容、提交到 Git 或撤销这次写入</span>
          </div>
        </div>
      </el-card>
    </div>
    </div>

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
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowUp, ArrowDown, Delete, Close, Connection, Document, EditPen, FolderOpened, Tickets, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import Sortable from 'sortablejs'
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
// 两种「围绕录制」的布局：
// - panel=1：split 模式下的独立面板窗口，后端用 CDP 和被测页面左右并排；
// - embed=1：嵌入模式，本页就是唯一窗口，左边 iframe 加载 target，右边是编排面板。
const route = useRoute()
const panelMode = computed(() => route.query.panel === '1')
const embedMode = computed(() => route.query.embed === '1')
const compactMode = computed(() => panelMode.value || embedMode.value)
const targetUrl = computed(() => (typeof route.query.target === 'string' ? route.query.target : ''))
const configExpanded = ref(false)
const panelTitle = computed(() => {
  if (!compactMode.value) return ''
  const base = paused.value && isRecording.value
    ? '已暂停'
    : (STATE_LABELS[session.value?.status || 'idle'] || '录制')
  return targetUrl.value ? `${base} · ${shortHost(targetUrl.value)}` : base
})
const modelOptions = ref([])
const detect = ref(null)
const detecting = ref(false)
// 高级设置（Python 解释器）默认收起：绝大多数工作区能自动找到 .venv。
const showAdvanced = ref(false)

// 面板宽度可拖拽调整；收起后 left iframe 吃掉几乎整个窗口。
const PANEL_WIDTH_KEY = 'autocase.recording.panelWidth'
const panelCollapsed = ref(false)
const panelWidth = ref(loadPanelWidth())
const resizing = ref(false)

function loadPanelWidth() {
  try {
    const saved = Number(localStorage.getItem(PANEL_WIDTH_KEY))
    if (saved >= 340 && saved <= 900) return saved
  } catch {
    // 隐私模式下读不到，用默认值。
  }
  return 600
}

function startResize(event) {
  event.preventDefault()
  const handle = event.currentTarget
  // 指针一旦移到 iframe 上方，事件就进了 iframe 的文档，父窗口收不到。
  // 所以用 pointer capture 把 pointermove 固定在手柄上，并在拖动期间禁用 iframe 的命中测试。
  try {
    handle.setPointerCapture(event.pointerId)
  } catch {
    // 拿不到 capture 时靠下面的 pointer-events 兜底
  }
  resizing.value = true
  const move = (e) => {
    // 面板最宽 900，同时保证被测页面至少还剩 900（页面优先，面板让位）。
    const room = Math.max(340, Math.min(900, window.innerWidth - 900))
    panelWidth.value = Math.min(Math.max(window.innerWidth - e.clientX, 340), room)
  }
  const stop = () => {
    resizing.value = false
    handle.removeEventListener('pointermove', move)
    handle.removeEventListener('pointerup', stop)
    handle.removeEventListener('pointercancel', stop)
    try {
      localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidth.value))
    } catch {
      // 忽略持久化失败
    }
  }
  handle.addEventListener('pointermove', move)
  handle.addEventListener('pointerup', stop)
  handle.addEventListener('pointercancel', stop)
}

function shortHost(url) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function applyPanelQuery() {
  if (!compactMode.value) return
  const query = route.query
  // 外壳/面板可能是新的浏览器 profile，localStorage 为空，只能靠后端带过来的参数预填。
  if (typeof query.model === 'string' && query.model) config.model = query.model
  if (typeof query.workspace === 'string' && query.workspace) config.workspace = query.workspace
  if (typeof query.framework === 'string' && FRAMEWORKS.some(item => item.value === query.framework)) {
    config.framework = query.framework
  }
  const startUrl = typeof query.base_url === 'string' ? query.base_url : ''
  const target = typeof query.target === 'string' ? query.target : ''
  const filled = startUrl || target
  if (filled) config.baseUrl = filled
}
applyPanelQuery()

const session = ref(null)
const starting = ref(false)
const stopping = ref(false)
const draft = ref(null)
const generating = ref(false)
const genError = ref(null)
const genResult = ref(null)

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
const paused = computed(() => !!session.value?.paused)
const pausing = ref(false)
const stateLabel = computed(() => {
  if (paused.value && isRecording.value) return '已暂停'
  return STATE_LABELS[session.value?.status || 'idle'] || '未知'
})

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
  // 列表结构变了，拖拽实例要跟着（重）建。
  nextTick(initSortable)
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
  // starting 也要轮询：并排面板窗口是在录制启动过程中打开的，抓不到主页面那次状态跳变。
  if (status === 'recording' || status === 'starting') startPolling()
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
    const { data } = await api.startRecording(url, {
      model: config.model,
      workspace: config.workspace.trim(),
      framework: config.framework
    })
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

async function togglePick(on, quiet = false) {
  try {
    const { data } = await api.setRecordingPick(on)
    if (session.value) session.value = { ...session.value, pick: data.pick }
  } catch (error) {
    if (!quiet) ElMessage.error(errorText(error))
  }
}

async function togglePause() {
  if (pausing.value) return
  pausing.value = true
  try {
    const { data } = await api.setRecordingPause(!paused.value)
    if (session.value) session.value = { ...session.value, paused: data.paused }
    ElMessage.success(data.paused ? '已暂停：在浏览器里的操作不会被记录' : '已继续录制')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    pausing.value = false
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

// ---- 就地编辑（只改值，不动定位）----
const editing = ref(null)
const EDITABLE = { fill: ['value'], select: ['value', 'label'], goto: ['url'], sleep: ['seconds'] }

function editableFields(step) {
  if (!canEdit.value) return []
  return EDITABLE[step.type] || []
}

function startEdit(step) {
  editing.value = {
    no: step.no,
    type: step.type,
    saving: false,
    value: step.value ?? '',
    label: step.label ?? '',
    url: step.url ?? '',
    seconds: step.intent && step.intent.seconds != null ? Number(step.intent.seconds) : 1
  }
}

function cancelEdit() {
  editing.value = null
}

async function saveEdit() {
  const current = editing.value
  if (!current || current.saving) return
  const payload = {}
  if (current.type === 'fill') payload.value = current.value
  else if (current.type === 'select') {
    payload.value = current.value
    payload.label = current.label
  } else if (current.type === 'goto') payload.url = (current.url || '').trim()
  else if (current.type === 'sleep') payload.seconds = current.seconds
  else {
    editing.value = null
    return
  }
  current.saving = true
  try {
    const { data } = await api.updateRecordingStep(current.no, payload)
    editing.value = null
    applySession(data)
  } catch (error) {
    current.saving = false
    ElMessage.error(errorText(error))
  }
}

// ---- 拖拽排序（sortablejs）：拖动步骤编号 ----
const timelineEl = ref(null)
let sortable = null

function initSortable() {
  if (!timelineEl.value) return
  // `.timeline` 是 v-if 渲染的，元素变化时重建实例。
  if (sortable && sortable.el === timelineEl.value) {
    sortable.option('disabled', !canEdit.value)
    return
  }
  if (sortable) {
    sortable.destroy()
    sortable = null
  }
  sortable = Sortable.create(timelineEl.value, {
    animation: 150,
    handle: '.step-no',
    ghostClass: 'step-ghost',
    chosenClass: 'step-chosen',
    disabled: !canEdit.value,
    onEnd: async (evt) => {
      const { oldIndex, newIndex, from, item } = evt
      if (oldIndex === newIndex || oldIndex == null || newIndex == null) return
      // 先把 DOM 还原：顺序以后端返回的数据为准，避免 Vue 的 keyed diff 和 sortable 打架。
      // 注意 item 已经被 sortable 移动过，要先把它排除再取 oldIndex 位置，否则会错一位。
      const siblings = Array.from(from.children).filter((el) => el !== item)
      from.insertBefore(item, siblings[oldIndex] || null)
      // sortable 加的临时类不会自动清掉，残留会让某一行一直带阴影。
      from.querySelectorAll('.step-chosen, .step-ghost, .step-drag')
        .forEach((el) => el.classList.remove('step-chosen', 'step-ghost', 'step-drag'))
      const moved = steps.value[oldIndex]
      if (!moved) return
      try {
        const { data } = await api.reorderRecordingStep(moved.no, newIndex)
        applySession(data)
      } catch (error) {
        ElMessage.warning(errorText(error))
      }
    }
  })
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
  // 面板窄栏里草稿在列表下方，打开后滚到底，避免点了「添加断言」却看不到表单。
  nextTick(() => {
    const body = document.querySelector('.steps-card .el-card__body')
    if (body && body.scrollHeight > body.clientHeight) {
      body.scrollTop = body.scrollHeight
      return
    }
    const card = document.querySelector('.draft-card')
    if (card && card.scrollIntoView) card.scrollIntoView({ block: 'nearest' })
  })
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

// 检测结果里的绝对路径太长（Python 解释器常常三四层），chips 里只留末尾两段，完整路径放 title。
function tailPath(path, depth = 2) {
  if (!path) return ''
  const parts = String(path).split(/[\\/]+/).filter(Boolean)
  return parts.slice(-depth).join('/')
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
  } catch (error) {
    const details = (error.response && error.response.data && error.response.data.details) || []
    const isConflict = error.response && error.response.status === 409 && details.some(d => String(d).startsWith('conflict:'))
    if (isConflict) {
      try {
        await ElMessageBox.confirm(
          `目标文件 ${config.targetPath.trim()} 已存在。覆盖后可以在「生成记录」里撤销，恢复原内容。是否覆盖？`,
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

function errorText(error) {
  if (error && error.response && error.response.data && error.response.data.error) return error.response.data.error
  return (error && error.message) || '操作失败'
}

// 用户把两个窗口挪乱后，点「并排」重新摆一次。
async function relayoutPanel() {
  try {
    const { data } = await api.relayoutRecordingPanel()
    if (data.laid_out) ElMessage.success('已重新并排')
    else ElMessage.warning('无法调整窗口位置（可能已被关闭或系统拒绝）')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

// ---- 生命周期 ----
// 窗口变窄（多屏切换、退出全屏）时收一下面板，别把被测页面挤没。
function clampPanelWidth() {
  if (!embedMode.value) return
  const room = Math.max(340, Math.min(900, window.innerWidth - 900))
  if (panelWidth.value > room) panelWidth.value = room
}

onMounted(async () => {
  loadCurrent()
  clampPanelWidth()
  nextTick(initSortable)
  window.addEventListener('resize', clampPanelWidth)
  try {
    const { data } = await api.getAutomationModels()
    modelOptions.value = data.models || []
    if (!config.model && modelOptions.value.length) config.model = modelOptions.value[0].value
  } catch {
    modelOptions.value = []
  }
  if (config.workspace) detectWorkspace()
})

// 页面被 keep-alive 缓存：回到页面时刷新会话状态，离开时停止轮询。
onActivated(() => loadCurrent())
onDeactivated(() => stopPolling())
onUnmounted(() => {
  stopPolling()
  window.removeEventListener('resize', clampPanelWidth)
  if (sortable) {
    sortable.destroy()
    sortable = null
  }
})
</script>

<style scoped>
.recording-page { max-width:1440px; margin:0 auto; animation: page-enter .32s ease both; }
@keyframes page-enter { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

/* ===== 嵌入模式：单窗口，左边被测系统、右边可收起的编排面板 ===== */
.recording-page.is-embed {
  max-width:none; padding:0; height:100vh; display:flex; flex-direction:row;
  animation:none; background:#eef1f5; overflow:hidden;
}
.embed-frame { flex:1; min-width:0; height:100vh; border:0; background:#fff; display:block; }
.is-embed .page-col { flex:none; height:100vh; overflow:hidden; background:#f5f7fa; border-left:1px solid #e2e6ec; }
.is-embed .page-col.is-panel { display:flex; flex-direction:column; gap:10px; padding:10px 12px 12px; box-sizing:border-box; }
/* 拖拽手柄要 position: relative，否则 z-index 不生效、会被 iframe 盖住点不到 */
.embed-resizer { flex:none; position:relative; width:6px; margin-left:-4px; z-index:6; cursor:col-resize; }
.embed-resizer::after { content:''; position:absolute; inset:0 2px; border-radius:2px; background:transparent; transition:background .15s ease; }
.embed-resizer:hover::after { background:#9fc0ee; }
/* 拖动期间让 iframe 不参与命中测试，避免 pointermove 被 iframe 吃掉 */
.is-embed.is-resizing .embed-frame { pointer-events:none; }
.is-embed.is-resizing .embed-resizer { cursor:col-resize; }
.embed-rail {
  flex:none; width:36px; height:100vh; border:0; border-left:1px solid #e2e6ec; background:#fff;
  display:flex; flex-direction:column; align-items:center; gap:12px; padding:14px 0; cursor:pointer; color:#6e6e73;
}
.embed-rail:hover { background:#f4f8ff; color:#0071e3; }
.rail-caret { font-size:16px; line-height:1; }
.rail-count { font-size:11.5px; font-weight:700; font-variant-numeric:tabular-nums; color:#8a8f99; }
.rail-dot { width:7px; height:7px; border-radius:50%; background:#c7ccd6; margin-top:auto; }
.rail-dot.live { background:#34c759; box-shadow:0 0 0 3px rgba(52,199,89,.18); }
.panel-collapse { border:0; background:transparent; color:#8a8f99; cursor:pointer; padding:2px 4px; border-radius:6px; line-height:1; }
.panel-collapse:hover { background:#eef1f5; color:#0071e3; }
.collapse-arrow { font-size:16px; }

/* 面板模式：录制时并排的右侧编排栏 =====
   只有一列，步骤列表吃掉剩余高度；配置默认收起，靠顶部「配置」按钮展开。 */
.recording-page.is-panel {
  max-width:none; height:100vh; padding:10px 12px 12px; display:flex; flex-direction:column; gap:10px;
  animation:none; box-sizing:border-box;
}
.panel-head { flex:none; display:flex; align-items:center; gap:8px; padding:6px 10px; border:1px solid #e6e9ef; border-radius:10px; background:#fff; box-shadow:0 1px 2px rgba(16,24,40,.05); }
.panel-dot { width:8px; height:8px; border-radius:50%; background:#c7ccd6; flex:none; }
.panel-dot.is-live { background:#34c759; box-shadow:0 0 0 3px rgba(52,199,89,.18); animation:panel-pulse 1.8s ease-in-out infinite; }
.panel-dot.is-paused { background:#ff9f0a; box-shadow:0 0 0 3px rgba(255,159,10,.2); }
@keyframes panel-pulse { 0%,100% { opacity:1; } 50% { opacity:.45; } }
.panel-title { font-size:13px; font-weight:700; color:#1d1d1f; }
.panel-count { font-size:11.5px; color:#6e6e73; background:#f2f4f7; border-radius:999px; padding:1px 8px; }
.panel-warn { font-size:11.5px; color:#b45309; background:#fff4e6; border-radius:999px; padding:1px 8px; }
.panel-actions { margin-left:auto; display:flex; gap:2px; }
.is-panel .recording-body, .is-embed .page-col .recording-body { display:flex; flex-direction:column; gap:10px; flex:1; min-height:0; }
.is-panel .main-layout { display:flex; flex-direction:column; gap:10px; flex:1; min-height:0; }
.is-panel .panel-card { margin-bottom:0; border-radius:10px; }
.is-panel .panel-card :deep(.el-card__header) { padding:10px 14px; }
.is-panel .panel-card :deep(.el-card__body) { padding:12px; }
.is-panel .steps-card { flex:1; min-height:0; display:flex; flex-direction:column; }
/* 整个卡片内容区作为唯一的滚动容器：列表和草稿一起滚，工具栏吸顶。
   之前列表自己滚、草稿另算，窄栏里草稿会被卡片裁掉。 */
.is-panel .steps-card :deep(.el-card__body) { flex:1; min-height:0; display:flex; flex-direction:column; overflow-y:auto; }
.is-panel .generate-card { flex:none; }
.is-panel .timeline { flex:none; overflow:visible; margin-top:12px; padding-right:2px; }
/* 顶栏已经有状态和步数，卡片头里的重复信息收起来 */
.is-panel .steps-card .state-pill { display:none; }
/* 工具栏在窄栏里收小一号，尽量一行放完；滚动时吸顶 */
.is-panel .toolbar {
  flex-wrap:wrap; gap:6px; position:sticky; top:-12px; z-index:2;
  background:#fff; padding:12px 0 8px; margin:-12px 0 0;
}
.is-panel .toolbar :deep(.el-button) { padding:6px 10px; font-size:12.5px; height:30px; }
.is-panel .config-card :deep(.el-card__body) { padding:14px; }
/* 面板/嵌入模式只有 600px 宽，配置改回单列 */
.is-panel .config-form--grid, .is-embed .page-col .config-form--grid { grid-template-columns:1fr; gap:0; }

/* 页头：与「自动化脚本」页保持一致 */
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:22px; }
.eyebrow { color:#0071e3; font-size:11px; font-weight:700; letter-spacing:1.4px; margin-bottom:8px; text-transform:uppercase; }
h1 { margin:0; color:#1d1d1f; font-size:22px; font-weight:700; letter-spacing:-.3px; }
.page-header p { margin:4px 0 0; color:#6e6e73; font-size:13px; line-height:1.5; max-width:820px; }
.page-intro { min-width:0; }
.page-actions { display:flex; align-items:center; gap:10px; flex:none; padding-top:2px; }
.hint-dot { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; margin-left:3px; border-radius:50%; background:#eef1f6; color:#86909c; font-size:10px; font-weight:700; cursor:help; vertical-align:1px; }

/* 卡片 */
.panel-card { border:1px solid #ebedf0; border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px rgba(16,24,40,.05); margin-bottom:20px; }
.panel-card :deep(.el-card__header) { padding:11px 16px; border-bottom:1px solid #f0f2f5; background:linear-gradient(180deg,#fcfdff,#f7f9fc); }
.panel-card :deep(.el-card__body) { padding:14px 16px; }
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

/* 配置：两列网格，起始地址/工作区/目标文件占整行；Python 解释器进「高级设置」 */
.config-form--grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:0 20px; align-items:start; }
.config-form--grid .span-2 { grid-column:1 / -1; }
.config-form :deep(.el-form-item__label) { font-size:12.5px; font-weight:600; color:#303133; padding-bottom:4px; }
.config-form :deep(.el-form-item) { margin-bottom:12px; }
/* el-form-item__content 默认是横向 flex，输入框 + 提示 + 检测结果会被挤到一行；这里改成纵向 */
.config-form :deep(.el-form-item__content) { flex-direction:column; align-items:stretch; }
.advanced-row { margin:-2px 0 10px; }
.advanced-toggle { padding:0; border:0; background:transparent; color:#0071e3; font-size:12.5px; font-weight:500; cursor:pointer; }
.advanced-toggle:hover { color:#0077ed; }

/* 检测结果：一行 chips，细节折叠在「依据与参考文件」里 */
.detect-inline { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:8px; }
.detect-chip { display:inline-flex; align-items:center; height:22px; padding:0 9px; border-radius:999px; background:#f2f5f9; color:#5b6472; font-size:11.5px; font-family:ui-monospace, 'Cascadia Code', Consolas, monospace; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.detect-chip.is-ok { background:#eaf7ef; color:#1e7a46; }
.detect-chip.is-warn { background:#fff7e6; color:#ad6800; }
.detect-more { flex-basis:100%; margin-top:2px; font-size:12px; color:#6e6e73; }
.detect-more summary { cursor:pointer; color:#0071e3; font-size:12px; }
code { font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; font-size:12px; }
.detect-sample { color:#1d1d1f; font-family: ui-monospace, Consolas, monospace; font-size:12px; line-height:1.7; }
.evidence-line { display:flex; gap:8px; padding:3px 0; }
.evidence-kind { color:#303133; font-weight:600; min-width:110px; }
.evidence-source { color:#a1a1a6; margin-left:auto; }

/* 主布局 */
.main-layout { display:flex; flex-direction:column; gap:16px; min-width:0; }
/* 左配置 / 右步骤+生成：一屏看完，生成记录单独成页 */
/* 普通模式整页锁一屏：标题常驻，左右两列各自内部滚动，步骤再多也不把页面撑长。
   100dvh - 72(顶栏) - 44(上下留白) = 内容高度 */
.recording-page:not(.is-panel):not(.is-embed) { height:calc(100dvh - 116px); display:flex; flex-direction:column; }
/* page-col 是内容的外壳（embed 模式也用它）：普通模式下必须把高度传下去，链条才不断 */
.recording-page:not(.is-panel):not(.is-embed) .page-col { flex:1; min-height:0; display:flex; flex-direction:column; }
.recording-page:not(.is-panel):not(.is-embed) .page-col > .page-header { flex:none; }
.recording-body { display:grid; grid-template-columns:minmax(340px,400px) minmax(0,1fr); grid-template-rows:minmax(0,1fr); gap:16px; align-items:stretch; flex:1; min-height:0; }
/* 左列配置超高时自己滚 */
.recording-body .config-card { min-height:0; overflow-y:auto; }
/* 右列：步骤吃满剩余高度，生成卡贴底 */
.recording-body .main-layout { height:100%; min-height:0; }
.recording-body .steps-card { flex:1; min-height:0; display:flex; flex-direction:column; }
.recording-body .steps-card :deep(.el-card__body) { flex:1; min-height:0; overflow-y:auto; }
.recording-body .generate-card { flex:none; }
.recording-body .config-form--grid { grid-template-columns:1fr; gap:0; }
.recording-body .panel-card { margin-bottom:0; }
.gen-result-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:8px; }
.gen-result-actions .field-hint { margin-top:0; }

/* 状态 */
.state-pill { display:inline-flex; align-items:center; height:24px; padding:0 10px; border-radius:999px; font-size:12px; font-weight:700; background:#f2f4f7; color:#6e6e73; }
.state-recording { background:#fff1f0; color:#e5484d; }
.state-stopped, .state-idle_timeout, .state-closed { background:#eaf7ef; color:#1e7a46; }
.state-failed, .state-cancelled { background:#fff7e6; color:#ad6800; }

/* 工具栏 */
.toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.toolbar-sep { width:1px; height:22px; background:#e4e7ed; margin:0 4px; }
.pick-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#fff7e6; color:#ad6800; font-size:12.5px; line-height:1.5; }
.pause-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#fff4e6; color:#b45309; border:1px dashed #f3c98b; font-size:12.5px; line-height:1.5; }
.session-error { margin-top:12px; }

/* 空状态：一行提示，别用 el-empty 那张大插画占半屏 */
.steps-empty { display:flex; align-items:center; gap:8px; margin-top:14px; padding:12px 14px; border:1px dashed #e4e7ed; border-radius:10px; background:#fbfcfe; color:#909399; font-size:13px; }
.steps-empty-dot { flex:none; width:7px; height:7px; border-radius:50%; background:#c9d6ea; }

/* 时间线 */
.timeline { list-style:none; margin:12px 0 0; padding:0; display:flex; flex-direction:column; gap:8px; }
.step { display:flex; gap:10px; align-items:flex-start; padding:10px 10px 10px 9px; border:1px solid #ebedf0; border-radius:12px; background:#fff; transition:border-color .18s ease, box-shadow .18s ease; }
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

/* 拖拽排序（sortablejs）：按住编号拖动 */
.step-no { cursor:grab; }
.step-no:active { cursor:grabbing; }
.step-ghost { opacity:.45; background:#f5f9ff; border-style:dashed; border-color:#9fc0ee; }
.step-chosen { box-shadow:0 8px 22px rgba(16,24,40,.12); }
.step-drag { opacity:.95; }

/* 就地编辑 */
.step-editor { display:flex; flex-direction:column; gap:8px; }
.step-editor-actions { display:flex; justify-content:flex-end; gap:8px; }
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
/* 未停止录制时只留一行，别让 4 条「未满足」把整张卡撑高 */
.gen-compact { margin-bottom:14px; padding:10px 12px; border-radius:10px; background:#f7f9fc; color:#6e6e73; font-size:13px; line-height:1.5; }
.gen-compact b { color:#0071e3; font-weight:700; }
.gen-compact-bad { color:#d4380d; }
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



@media (max-height: 620px) {
  .recording-page:not(.is-panel):not(.is-embed) { height:auto; }
}

@media (max-width: 1100px) {
  .recording-body { grid-template-columns:1fr; }
  .config-form--grid { grid-template-columns:1fr; }
  .form-grid { grid-template-columns:1fr; }
}
</style>
