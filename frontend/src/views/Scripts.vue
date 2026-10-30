<template>
  <div class="scripts-page ui-buttons">
    <!-- 页头：任务记录是一个独立出口（自己的页面），不再挂在配置卡下面当一张列表卡 -->
    <header class="sp-head">
      <div class="sp-head-main">
        <div class="eyebrow">AGENT · OPENCODE</div>
        <h1 class="sp-title">Agent 探索</h1>
        <p class="sp-sub">给一段需求和一个真实页面，Agent 会自己打开浏览器探索、写出 Playwright 脚本，跑失败了自己修。</p>
      </div>
      <div class="sp-head-actions">
        <el-button @click="goRecords">
          <span v-if="runningCount" class="run-dot"></span>任务记录
        </el-button>
        <el-button text @click="$router.push('/')">返回首页</el-button>
      </div>
    </header>

    <nav class="sp-tabs">
      <button type="button" :class="['sp-tab', { 'is-on': mode === 'requirement' }]" @click="onModeChange('requirement')">按需求生成</button>
      <button type="button" :class="['sp-tab', { 'is-on': mode === 'cases' }]" @click="onModeChange('cases')">按用例生成</button>
      <span class="sp-tab-note">
        {{ mode === 'requirement' ? 'planner 先探索页面，再交给 generator' : '跳过探索，把已审批用例直接写成脚本' }}
      </span>
    </nav>

    <div class="sp-grid">
      <!-- ===== 左：任务 ===== -->
      <section class="sp-card">
        <div class="sp-card-head">
          <span class="sp-card-title">{{ mode === 'requirement' ? '测试需求' : '用例来源' }}</span>
          <span v-if="mode === 'requirement'" class="sp-card-meta">{{ (form.requirement || '').length }} 字</span>
          <span v-else-if="caseSource === 'approved'" class="sp-card-meta">已选 {{ selectedCaseIds.length }} 条</span>
        </div>

        <div class="sp-card-body">
          <template v-if="mode === 'requirement'">
            <div class="sp-fill">
              <el-input
                v-model="form.requirement"
                class="form-textarea sp-requirement"
                type="textarea"
                :rows="10"
                maxlength="30000"
                placeholder="例如：验证用户登录、退出和登录失败场景。覆盖正常登录、错误密码、空字段和会话失效后的跳转。"
              />
            </div>
            <!-- 需求区默认是一大块空白画布。底部给三条写作要点，比留一句话更有用，
                 也不会让整张卡看起来「下面还没画完」。 -->
            <div class="sp-tips">
              <div class="sp-tips-label">写作要点</div>
              <ul>
                <li>写清从哪个页面开始、按什么顺序操作、到哪里结束</li>
                <li>反向场景单独列：错误密码、空字段、会话过期</li>
                <li>验收标准写成可判断的结果：出现什么文案、停在哪个 URL</li>
              </ul>
            </div>
          </template>

          <template v-else>
            <div class="sp-src">
              <button
                v-for="opt in CASE_SOURCES"
                :key="opt.value"
                type="button"
                :class="['sp-src-btn', { 'is-on': caseSource === opt.value }]"
                @click="setSource(opt.value)"
              >{{ opt.label }}</button>
            </div>

            <div v-if="caseSource === 'approved'" class="form-block">
              <div class="form-field">
                <div class="form-label">
                  <span>已审批用例</span>
                  <span class="sp-label-aux">{{ approvedCases.length }} 条可选</span>
                </div>
                <el-select
                  v-model="selectedCaseIds"
                  class="form-ctl"
                  multiple
                  filterable
                  collapse-tags
                  collapse-tags-tooltip
                  :loading="casesLoading"
                  placeholder="从已审批用例中选择"
                >
                  <el-option v-for="item in approvedCases" :key="item.id" :label="`${item.priority || ''} ${item.title}`" :value="item.id" />
                </el-select>
                <div class="sp-field-hint">只列出审批通过的用例；生成时按标题、步骤、预期原样拼给 generator。</div>
              </div>

              <!-- 选完能在这里核对一遍：不然多选下拉收起后只剩「已选 N 条」 -->
              <div v-if="pickedCases.length" class="picked">
                <div v-for="item in pickedCases" :key="item.id" class="picked-row">
                  <span class="chip">{{ item.priority || '未定级' }}</span>
                  <span class="picked-title" :title="item.title">{{ item.title }}</span>
                  <button type="button" class="picked-del" title="取消选择" @click="unpickCase(item.id)">×</button>
                </div>
              </div>
              <div v-else-if="!casesLoading && approvedCases.length" class="sp-note">还没有选择用例。可以按标题搜索，或直接选几条。</div>

              <div v-if="!casesLoading && !approvedCases.length" class="sp-note">
                这个项目还没有已审批用例。可以先在用例列表审批，或改用「粘贴 Markdown」。
              </div>
            </div>

            <div v-else-if="caseSource === 'markdown'" class="form-block">
              <div class="form-field">
                <div class="form-label">
                  <span>用例 Markdown</span>
                  <button type="button" class="form-label-act" :disabled="!casesMarkdown.trim() || previewing" @click="previewMarkdown">
                    {{ previewing ? '校验中…' : '预览校验' }}
                  </button>
                </div>
                <el-input
                  v-model="casesMarkdown"
                  class="form-textarea sp-markdown"
                  type="textarea"
                  :rows="12"
                  placeholder="**Seed:** tests/seed.spec.ts&#10;&#10;#### 验证码错误提示&#10;- 步骤：&#10;  1. 输入错误验证码&#10;- 预期：&#10;  - 提示验证码错误"
                  @input="preview = null"
                />
                <div class="sp-field-hint">每条用例以 #### 开头，包含步骤和预期；「步骤：」「预期：」这类标签会被自动去掉。</div>
              </div>
            </div>

            <div v-else class="form-block">
              <div class="fc-list">
                <div v-for="(item, ci) in formCases" :key="item.id" class="fc-item">
                  <div class="fc-head">
                    <span class="fc-no">{{ ci + 1 }}</span>
                    <el-input v-model="item.title" class="form-input fc-title" placeholder="用例标题，例如：验证码错误时登录失败" maxlength="200" />
                    <button type="button" class="fc-del" :disabled="formCases.length <= 1" title="删除这条用例" @click="removeCase(ci)">×</button>
                  </div>
                  <div class="fc-block">
                    <div class="fc-label">步骤</div>
                    <div v-for="(_, si) in item.steps" :key="si" class="fc-row">
                      <span class="fc-row-no">{{ si + 1 }}</span>
                      <el-input v-model="item.steps[si]" class="form-input" placeholder="描述一个操作，例如：输入正确的用户名" />
                      <button type="button" class="fc-del" :disabled="item.steps.length <= 1" title="删除步骤" @click="item.steps.splice(si, 1)">×</button>
                    </div>
                    <button type="button" class="fc-add" :disabled="item.steps.length >= MAX_ROWS" @click="item.steps.push('')">+ 添加步骤</button>
                  </div>
                  <div class="fc-block">
                    <div class="fc-label">预期结果</div>
                    <div v-for="(_, ei) in item.expected" :key="ei" class="fc-row">
                      <span class="fc-row-no is-mark">✓</span>
                      <el-input v-model="item.expected[ei]" class="form-input" placeholder="描述一个检查点，例如：提示验证码错误并停留在登录页" />
                      <button type="button" class="fc-del" :disabled="item.expected.length <= 1" title="删除预期" @click="item.expected.splice(ei, 1)">×</button>
                    </div>
                    <button type="button" class="fc-add" :disabled="item.expected.length >= MAX_ROWS" @click="item.expected.push('')">+ 添加预期</button>
                  </div>
                  <div v-if="showFormErrors && !isBlankCase(item) && caseIssues(item).length" class="fc-error">
                    {{ caseIssues(item).join('；') }}
                  </div>
                </div>
              </div>

              <button type="button" class="fc-add-case" :disabled="formCases.length >= MAX_FORM_CASES" @click="addCase">
                <span class="fc-add-plus">+</span>添加用例
                <span v-if="formCases.length >= MAX_FORM_CASES" class="fc-add-hint">已达上限 {{ MAX_FORM_CASES }} 条</span>
              </button>
            </div>

            <div v-if="preview" :class="['sp-note', preview.success ? 'is-ok' : 'is-error']">
              <template v-if="preview.success">校验通过，共 {{ preview.case_count }} 条用例，Seed：{{ preview.seed_file }}</template>
              <template v-else>
                <div>格式有误，请修改后再提交：</div>
                <div v-for="(err, i) in preview.errors" :key="i">· {{ err }}</div>
              </template>
            </div>
          </template>
        </div>
      </section>

      <!-- ===== 右：Agent 配置 ===== -->
      <section class="sp-card">
        <div class="sp-card-head">
          <span class="sp-card-title">Agent 配置</span>
          <span class="sp-card-meta">按项目保存</span>
        </div>

        <div class="sp-card-body">
          <div class="form-block">
            <div class="form-field">
              <div class="form-label"><span>项目</span></div>
              <el-select v-model="selectedProject" class="form-ctl" placeholder="选择项目" @change="onProjectChange">
                <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
              </el-select>
            </div>

            <div class="form-field">
              <div class="form-label"><span>Base URL</span></div>
              <el-input v-model="form.base_url" class="form-input" placeholder="https://test.example.com" />
            </div>

            <div class="sp-pair">
              <div class="form-field">
                <div class="form-label"><span>浏览器</span></div>
                <el-select v-model="form.browser" class="form-ctl">
                  <el-option label="Chromium" value="chromium" />
                  <el-option label="Firefox" value="firefox" />
                  <el-option label="WebKit" value="webkit" />
                </el-select>
              </div>
              <div class="form-field">
                <div class="form-label"><span>运行环境</span></div>
                <el-input v-model="form.environment_name" class="form-input" placeholder="test" />
              </div>
            </div>

            <div class="form-field">
              <div class="form-label">
                <span>服务端 Git 工作区</span>
                <button type="button" class="form-label-act" @click="openWorkspacePicker">浏览…</button>
              </div>
              <el-input v-model="form.workspace_path" class="form-input" placeholder="例如 project-a" />
              <div class="sp-field-hint">
                AUTOMATION_WORKSPACE_ROOT 下的 Git + Playwright 项目<span v-if="workspaceRoot" class="sp-root"> · {{ workspaceRoot }}</span>
              </div>
            </div>

            <div class="form-field">
              <div class="form-label"><span>测试目录</span></div>
              <el-input v-model="form.specs_path" class="form-input" placeholder="tests" />
            </div>

            <div class="form-field">
              <div class="form-label">
                <span>Seed 文件</span>
                <button type="button" class="form-label-act" :disabled="!form.workspace_path" @click="openSeedPicker">浏览…</button>
              </div>
              <el-input v-model="form.seed_file" class="form-input" placeholder="tests/seed.spec.ts" />
              <div class="sp-field-hint">planner 探索页面时使用的种子测试，相对工作区路径，必须是 .spec.ts。</div>
            </div>

            <div class="sp-pair">
              <div class="form-field">
                <div class="form-label"><span>OpenCode 模型</span><span class="sp-label-aux">可选</span></div>
                <el-select
                  v-model="form.opencode_model"
                  class="form-ctl"
                  filterable
                  clearable
                  allow-create
                  default-first-option
                  placeholder="留空使用默认"
                  @visible-change="onModelDropdown"
                >
                  <el-option v-for="m in modelOptions" :key="m.value" :label="m.value" :value="m.value" />
                </el-select>
              </div>
              <div class="form-field">
                <div class="form-label"><span>最大修复次数</span></div>
                <el-input-number v-model="form.max_heal_attempts" class="form-num" controls-position="right" :min="0" :max="3" />
              </div>
            </div>

            <div class="form-field">
              <div class="form-label"><span>工作区文件策略</span></div>
              <el-select v-model="form.overwrite_policy" class="form-ctl">
                <el-option label="Agent 自行判断并保留未提交状态" value="reject" />
                <el-option label="允许修改已有测试文件" value="overwrite" />
              </el-select>
            </div>
          </div>
          <div v-if="modelsLoadError" class="sp-note">模型列表加载失败，可以直接输入 provider/model。</div>
        </div>
      </section>
    </div>

    <!-- 底栏：全页唯一的蓝色主操作 -->
    <div class="sp-foot">
      <div class="sp-foot-note">
        <template v-if="mode === 'requirement'">planner → generator → healer，通常 5–15 分钟</template>
        <template v-else>generator → healer，跳过页面探索</template>
        <span v-if="selectedProject" class="sp-foot-sep">·</span>
        <span v-if="selectedProject">{{ projectName }}</span>
      </div>
      <div class="sp-foot-actions">
        <el-button :loading="saving" @click="saveConfig()">保存配置</el-button>
        <el-button type="primary" :icon="Promotion" :loading="generating" :disabled="!canGenerate" @click="generate">启动 Agent</el-button>
      </div>
    </div>

    <PathPickerDialog
      v-model="picker.visible"
      :mode="picker.mode"
      :workspace="(form.workspace_path || '').trim()"
      framework="playwright-ts"
      :start-path="picker.start"
      default-name="seed"
      @loaded="onRootLoaded"
      @select="onPathSelected"
    />
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import api from '../api'
import PathPickerDialog from '../components/PathPickerDialog.vue'

const router = useRouter()
const projects = ref([])
const selectedProject = ref('')
const saving = ref(false)
const generating = ref(false)
const modelOptions = ref([])
const modelsLoadError = ref(false)
let modelsLoaded = false

// 「任务记录」按钮上的小圆点：有任务在跑时提示一下，避免用户以为页面没事发生。
const runningCount = ref(0)
let runPoll = null

const emptyForm = () => ({
  project_id: '', framework: 'playwright', language: 'typescript', workspace_path: '', specs_path: 'autocase/tests',
  base_url: '', environment_name: 'test', browser: 'chromium', opencode_model: '',
  max_heal_attempts: 3, overwrite_policy: 'reject', seed_file: 'tests/seed.spec.ts'
})
const form = ref(emptyForm())
const projectName = computed(() => projects.value.find(item => item.id === selectedProject.value)?.name || '')

// 路径选择：浏览器拿不到本地绝对路径，由后端在 AUTOMATION_WORKSPACE_ROOT 内列目录，前端只填相对路径。
const picker = reactive({ visible: false, mode: 'dir', start: '', target: 'workspace' })
const workspaceRoot = ref('')

function dirnameOf(path) {
  const parts = String(path || '').trim().split('/')
  parts.pop()
  return parts.join('/')
}

function openWorkspacePicker() {
  picker.mode = 'dir'
  picker.target = 'workspace'
  picker.start = dirnameOf(form.value.workspace_path)
  picker.visible = true
}

function openSeedPicker() {
  if (!(form.value.workspace_path || '').trim()) {
    ElMessage.warning('请先选择工作区，再选择 Seed 文件')
    return
  }
  picker.mode = 'file'
  picker.target = 'seed'
  picker.start = dirnameOf(form.value.seed_file)
  picker.visible = true
}

function onRootLoaded({ root }) {
  workspaceRoot.value = root || ''
}

function onPathSelected({ path, dirs = [], dirsOf = '' }) {
  if (picker.target === 'workspace') {
    form.value.workspace_path = path
    const seed = (form.value.seed_file || '').trim()
    // 只在用户没动过 Seed（还是默认值）时才改写，避免覆盖手填的内容。
    if (!seed || seed === DEFAULT_SEED) autoFillSeedPath(path, dirs, dirsOf)
    return
  }
  form.value.seed_file = path
}

const DEFAULT_SEED = 'tests/seed.spec.ts'

// 选完工作区后按它是否存在 tests 目录推荐 Seed 路径。
async function autoFillSeedPath(path, dirs, dirsOf) {
  let names = dirsOf === path && dirs.length ? dirs : []
  if (!names.length) {
    try {
      const { data } = await api.browseAutomationFiles(path, '', 'playwright-ts')
      names = (data.dirs || []).map(item => item.name)
    } catch {
      names = []
    }
  }
  const current = (form.value.seed_file || '').trim()
  if (current && current !== DEFAULT_SEED) return
  form.value.seed_file = names.includes('tests') ? 'tests/seed.spec.ts' : 'seed.spec.ts'
}

// 两种生成模式共用右侧的 Agent 配置；只有左侧的任务输入不同。
// requirement：按需求生成（planner → generator → healer）；cases：按用例生成（generator → healer）。
const mode = ref('requirement')
const approvedCases = ref([])
const selectedCaseIds = ref([])
const casesLoading = ref(false)
const caseSource = ref('approved')
const casesMarkdown = ref('')
const preview = ref(null)
const previewing = ref(false)

const CASE_SOURCES = [
  { value: 'approved', label: '已审批用例' },
  { value: 'markdown', label: '粘贴 Markdown' },
  { value: 'form', label: '表单录入' }
]

// 表单录入：每条用例是一块，只填标题、步骤和预期，不填优先级。上限 5 条。
const MAX_FORM_CASES = 5
const MAX_ROWS = 20
let caseSeq = 0
const newFormCase = () => ({ id: ++caseSeq, title: '', steps: [''], expected: [''] })
const formCases = ref([newFormCase()])
const showFormErrors = ref(false)

const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim()

// 完全没填的一块视为“还没用到”，不校验、不提交；只要填了一部分，就必须填完整。
const isBlankCase = item => !clean(item.title) && !item.steps.some(clean) && !item.expected.some(clean)
const activeFormCases = computed(() => formCases.value.filter(item => !isBlankCase(item)))

function caseIssues(item) {
  const issues = []
  if (!clean(item.title)) issues.push('缺少用例标题')
  if (!item.steps.some(step => clean(step))) issues.push('至少填写一个步骤')
  if (!item.expected.some(value => clean(value))) issues.push('至少填写一条预期结果')
  return issues
}

const pickedCases = computed(() => {
  const map = new Map(approvedCases.value.map(item => [item.id, item]))
  return selectedCaseIds.value.map(id => map.get(id)).filter(Boolean)
})

function unpickCase(id) {
  selectedCaseIds.value = selectedCaseIds.value.filter(value => value !== id)
}

const formCasesValid = computed(() => activeFormCases.value.length > 0 && activeFormCases.value.every(item => caseIssues(item).length === 0))

// 把表单内容渲染成后端认可的 Markdown，之后和手写 Markdown 走同一条校验与提交链路。
function renderFormCases() {
  const lines = [`**Seed:** \`${form.value.seed_file || 'tests/seed.spec.ts'}\``, '']
  for (const item of activeFormCases.value) {
    lines.push(`#### ${clean(item.title)}`)
    lines.push('- 步骤：')
    item.steps.map(clean).filter(Boolean).forEach((step, i) => lines.push(`  ${i + 1}. ${step}`))
    lines.push('- 预期：')
    item.expected.map(clean).filter(Boolean).forEach(value => lines.push(`  - ${value}`))
    lines.push('')
  }
  return lines.join('\n')
}

const casesMarkdownText = computed(() => (caseSource.value === 'form' ? renderFormCases() : casesMarkdown.value))

const canGenerate = computed(() => {
  if (!selectedProject.value || !form.value.base_url || !form.value.workspace_path) return false
  if (mode.value === 'requirement') return Boolean(form.value.requirement?.trim())
  if (caseSource.value === 'approved') return selectedCaseIds.value.length > 0
  // 表单只要有内容就允许点击，缺什么由点击后的逐条校验提示，比直接禁用更清楚。
  if (caseSource.value === 'form') return formCases.value.some(item => clean(item.title) || item.steps.some(clean) || item.expected.some(clean))
  return Boolean(casesMarkdown.value.trim())
})

function addCase() {
  if (formCases.value.length >= MAX_FORM_CASES) return
  formCases.value.push(newFormCase())
}

function removeCase(index) {
  if (formCases.value.length <= 1) return
  formCases.value.splice(index, 1)
}

function setSource(value) {
  if (caseSource.value === value) return
  caseSource.value = value
  onSourceChange()
}

function onSourceChange() {
  preview.value = null
  showFormErrors.value = false
}

// 表单内容一变，之前的预览结果就过期了。
watch(formCases, () => { preview.value = null }, { deep: true })

onMounted(() => { loadProjects(); loadModels() })
// keep-alive 下页面还在内存里：离开时停掉轮询，回来再续上。
onActivated(startRunPoll)
onDeactivated(stopRunPoll)
onUnmounted(stopRunPoll)

function startRunPoll() {
  refreshRuns()
  if (!runPoll) runPoll = window.setInterval(refreshRuns, 15000)
}
function stopRunPoll() {
  if (runPoll) { window.clearInterval(runPoll); runPoll = null }
}

async function refreshRuns() {
  if (!selectedProject.value) return
  try {
    const { data } = await api.getAutomationGenerations(selectedProject.value)
    runningCount.value = (data.generations || []).filter(item => ['running', 'pending'].includes(item.status)).length
  } catch {
    runningCount.value = 0
  }
}

function goRecords() {
  router.push({ path: '/scripts/records', query: selectedProject.value ? { project_id: selectedProject.value } : {} })
}

async function loadModels(force = false) {
  if (modelsLoaded && !force) return
  try {
    const response = await api.getAutomationModels(force)
    modelOptions.value = response.data.models || []
    modelsLoadError.value = false
    modelsLoaded = true
  } catch {
    modelsLoadError.value = true
  }
}

function onModelDropdown(open) {
  if (open) loadModels()
}

async function loadProjects() {
  try {
    const response = await api.getProjects()
    projects.value = response.data.projects || []
    selectedProject.value = projects.value[0]?.id || ''
    if (selectedProject.value) await onProjectChange()
  } catch { ElMessage.error('加载项目失败') }
}

async function onProjectChange() {
  if (!selectedProject.value) return
  selectedCaseIds.value = []
  preview.value = null
  try {
    const configResponse = await api.getAutomationConfig(selectedProject.value)
    form.value = { ...emptyForm(), ...(configResponse.data.config || {}), project_id: selectedProject.value, requirement: '' }
  } catch { ElMessage.error('加载自动化配置失败') }
  if (mode.value === 'cases') await loadApprovedCases()
  refreshRuns()
}

async function onModeChange(value) {
  mode.value = value
  preview.value = null
  if (value === 'cases') await loadApprovedCases()
}

async function loadApprovedCases() {
  if (!selectedProject.value) return
  casesLoading.value = true
  try {
    const response = await api.getTestcases({ project_id: selectedProject.value, status: 'approved' })
    approvedCases.value = response.data.testcases || []
  } catch { ElMessage.error('加载已审批用例失败') }
  finally { casesLoading.value = false }
}

// 预览只做解析和校验，不启动任务；有错误时列出来，用户改完再提交。
async function previewMarkdown() {
  if (!casesMarkdownText.value.trim()) return
  previewing.value = true
  try {
    const response = await api.previewAutomationCases({ project_id: selectedProject.value, cases_markdown: casesMarkdownText.value })
    preview.value = response.data
  } catch (error) {
    preview.value = { success: false, errors: [error.response?.data?.error || '预览失败'] }
  } finally { previewing.value = false }
}

async function saveConfig(showMessage = true) {
  if (!selectedProject.value) return false
  saving.value = true
  try {
    const response = await api.saveAutomationConfig({ ...form.value, project_id: selectedProject.value })
    form.value = { ...form.value, ...response.data.config }
    if (showMessage) ElMessage.success('自动化配置已保存')
    return true
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '保存配置失败')
    return false
  } finally { saving.value = false }
}

async function generate() {
  if (!(await saveConfig(false))) return
  if (mode.value === 'cases' && caseSource.value !== 'approved') {
    // 手写或表单的用例必须先通过校验，避免把格式错误的用例送进 generator。
    if (caseSource.value === 'form') {
      showFormErrors.value = true
      if (!formCasesValid.value) {
        ElMessage.warning('请把每条用例的标题、步骤和预期填写完整')
        return
      }
    }
    await previewMarkdown()
    if (!preview.value?.success) return
  }
  generating.value = true
  try {
    const response = mode.value === 'requirement'
      ? await api.generateAutomation({
        project_id: selectedProject.value,
        requirement: form.value.requirement,
        config: form.value
      })
      : await api.generateAutomationFromCases(casesPayload())
    const generation = response.data.generation
    ElMessage.success('Agent 任务已启动')
    // 直接进任务详情：失败也在同一页看错误和日志，不再弹一个信息更少的对话框。
    router.push({ path: `/scripts/generations/${generation.id}`, query: { project_id: selectedProject.value } })
  } catch (error) {
    ElMessage.error(error.response?.data?.error || 'Agent 工作流启动失败')
    refreshRuns()
  } finally { generating.value = false }
}

function casesPayload() {
  const base = { project_id: selectedProject.value, config: form.value }
  if (caseSource.value === 'approved') return { ...base, case_ids: selectedCaseIds.value }
  return { ...base, cases_markdown: casesMarkdownText.value }
}
</script>

<style scoped>
/* ---------- 页面骨架：一屏工作台，卡片内部滚动，底栏常驻 ---------- */
.scripts-page {
  max-width: 1440px;
  margin: 0 auto;
  height: calc(100dvh - 116px);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.sp-head {
  flex: none;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  padding-bottom: 16px;
}
.sp-head-main { min-width: 0; }
.sp-title {
  margin: 5px 0 0;
  font-size: 21px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -.02em;
  color: var(--ink);
}
.sp-sub {
  margin: 7px 0 0;
  max-width: 620px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--muted);
}
.sp-head-actions { flex: none; display: flex; align-items: center; gap: 8px; padding-bottom: 1px; }

/* 任务记录按钮上的执行中提示：一个小蓝点，比红点安静 */
.run-dot {
  width: 6px;
  height: 6px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: run-pulse 1.8s ease-in-out infinite;
}
@keyframes run-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 113, 227, .35); }
  50% { box-shadow: 0 0 0 4px rgba(0, 113, 227, 0); }
}

/* ---------- 模式切换：下划线 tab，和首页同一套 ---------- */
.sp-tabs {
  flex: none;
  display: flex;
  align-items: center;
  gap: 26px;
  border-bottom: 1px solid var(--line-soft);
  margin-bottom: 16px;
}
.sp-tab {
  position: relative;
  padding: 0 0 10px;
  border: 0;
  background: none;
  font-size: 13.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.sp-tab:hover { color: var(--ink); }
.sp-tab.is-on { color: var(--ink); font-weight: 600; }
.sp-tab.is-on::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  border-radius: 2px 2px 0 0;
  background: var(--accent);
}
.sp-tab-note {
  margin-left: auto;
  padding-bottom: 9px;
  font-size: 11.5px;
  color: var(--faint);
}

/* ---------- 两列：左任务、右配置 ---------- */
.sp-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 460px);
  gap: 16px;
  align-items: stretch;
}
.sp-card {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #fff;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
}
.sp-card-head {
  flex: none;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 20px 8px;
}
.sp-card-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: .02em;
}
.sp-card-meta { font-size: 11.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.sp-card-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 20px 10px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.sp-card-body::-webkit-scrollbar { width: 8px; }
.sp-card-body::-webkit-scrollbar-thumb { background: #e2e6ec; border-radius: 4px; }
.sp-card-body::-webkit-scrollbar-thumb:hover { background: #d3d9e2; }

/* 需求输入要吃掉左卡剩余高度，而不是固定 10 行后留一片空白 */
.sp-fill { flex: 1; min-height: 260px; display: flex; flex-direction: column; padding-top: 6px; }
.sp-requirement { flex: 1; display: flex; flex-direction: column; }
.sp-requirement :deep(.el-textarea__inner) {
  flex: 1;
  min-height: 240px;
  padding: 4px 0 8px;
  font-size: 13.5px;
  line-height: 1.75;
}
.sp-markdown :deep(.el-textarea__inner) {
  min-height: 280px;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.7;
}
.sp-tips {
  flex: none;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line-faint);
}
.sp-tips-label {
  margin-bottom: 5px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--faint);
}
.sp-tips ul { margin: 0; padding-left: 14px; }
.sp-tips li { font-size: 11.5px; line-height: 1.8; color: var(--muted); }
.sp-field-hint {
  margin-top: 5px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--faint);
}
.sp-root { font-family: var(--mono); font-size: 11px; color: #c3c9d2; }
.sp-label-aux { font-size: 11px; color: var(--faint); }
.form-label-act:disabled { color: #ccd2da; cursor: not-allowed; }
.form-label-act:disabled:hover { color: #ccd2da; }

/* 配置字段比通用表单再紧一档：一行标签 + 一行控件占 60px 才放得下九项。
   控件高度 34 → 32，字段上下留白 13/7 → 10/6。 */
.sp-card .form-field { padding: 9px 0 5px; }
.sp-card .form-label { margin-bottom: 2px; }
.el-select.form-ctl,
.el-input.form-input,
.el-input-number.form-num { height: 32px; }
.el-select.form-ctl .el-select__wrapper { min-height: 32px; height: 32px; }
.el-input.form-input .el-input__wrapper { height: 32px; }
.el-input-number.form-num .el-input__wrapper { height: 32px; }
.sp-card .sp-field-hint { margin-top: 3px; font-size: 11px; line-height: 1.45; }

/* 两列短字段：共用一条底线，中间留 20px，避免两条线断开像两张表 */
.sp-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  padding-bottom: 5px;
  border-bottom: 1px solid rgba(0, 0, 0, .09);
}
.sp-pair .form-field { border-bottom: 0; padding-bottom: 0; }

/* ---------- 用例来源：轻量分段控件 ---------- */
.sp-src {
  flex: none;
  display: inline-flex;
  align-self: flex-start;
  gap: 2px;
  padding: 2px;
  margin: 2px 0 14px;
  background: #f4f6f9;
  border-radius: 7px;
}
.sp-src-btn {
  height: 26px;
  padding: 0 11px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  transition: background .14s ease, color .14s ease;
}
.sp-src-btn:hover { color: var(--ink-2); }
.sp-src-btn.is-on {
  background: #fff;
  color: var(--ink);
  font-weight: 500;
  box-shadow: 0 0 0 1px var(--line-soft) inset;
}

/* ---------- 表单录入的用例：靠细线分块，不再每块套一个白盒子 ---------- */
.fc-list { display: flex; flex-direction: column; padding-top: 4px; }
.fc-item { padding: 12px 0 6px; border-bottom: 1px solid var(--line-soft); }
.fc-item:last-child { border-bottom: 0; }
.fc-head { display: flex; align-items: center; gap: 10px; }
.fc-no {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  background: #f4f6f9;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.fc-title { flex: 1; min-width: 0; }
.fc-block { margin: 10px 0 0 30px; }
.fc-label { margin-bottom: 2px; font-size: 11.5px; color: var(--muted); }
.fc-row { display: flex; align-items: center; gap: 8px; }
.fc-row :deep(.el-input.form-input .el-input__wrapper),
.fc-title :deep(.el-input.form-input .el-input__wrapper) { border-bottom-color: rgba(0, 0, 0, .09); }
.fc-row-no {
  flex: none;
  width: 18px;
  text-align: right;
  font-size: 11px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.fc-row-no.is-mark { color: #b7c0cc; }
.fc-del {
  flex: none;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #a8b1be;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  transition: background .14s ease, color .14s ease;
}
.fc-del:hover:not(:disabled) { background: #f4f6f9; color: #e0352b; }
.fc-del:disabled { opacity: .3; cursor: not-allowed; }
.fc-add {
  margin: 2px 0 0 26px;
  padding: 2px 6px;
  border: 0;
  border-radius: 5px;
  background: none;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease, background .14s ease;
}
.fc-add:hover:not(:disabled) { color: var(--accent); background: var(--accent-soft); }
.fc-add:disabled { color: #ccd2da; cursor: not-allowed; }
.fc-error {
  margin: 8px 0 4px 30px;
  font-size: 11.5px;
  line-height: 1.5;
  color: #c9342b;
}
.fc-add-case {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 40px;
  margin: 14px 0 4px;
  border: 1px dashed #d5dbe4;
  border-radius: 9px;
  background: transparent;
  font: inherit;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  transition: border-color .14s ease, color .14s ease, background .14s ease;
}
.fc-add-case:hover:not(:disabled) { border-color: #b9c9dd; color: var(--accent); background: #fbfdff; }
.fc-add-case:disabled { color: #ccd2da; cursor: not-allowed; }
.fc-add-plus { font-size: 14px; line-height: 1; }
.fc-add-hint { margin-left: 6px; font-size: 11px; color: var(--faint); }

/* 多选里的标签按小方片的样子压平：默认的灰药丸和整页的方片不是一套 */
.sp-card :deep(.el-select__selection .el-tag) {
  height: 20px;
  padding: 0 6px;
  border-color: transparent;
  border-radius: var(--radius-chip);
  background: #f4f6f9;
  color: #5b6472;
  font-size: 11px;
}
.sp-card :deep(.el-select__selection .el-tag .el-tag__close) { color: #9aa3b1; }
.sp-card :deep(.el-select__selection .el-tag .el-tag__close:hover) { background: #dfe4ea; color: var(--ink-2); }

/* ---------- 已选用例：核对区 ---------- */
.picked {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin-top: 10px;
}
.picked-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line-faint);
  min-width: 0;
}
.picked-row:last-child { border-bottom: 0; }
.picked-title {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picked-del {
  flex: none;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #a8b1be;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: background .14s ease, color .14s ease;
}
.picked-del:hover { background: #f4f6f9; color: #e0352b; }

/* ---------- 校验结果 ---------- */
.sp-note {
  flex: none;
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 7px;
  background: #f8fafc;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
}
.sp-note.is-ok { background: var(--ok-soft); color: var(--ok); }
.sp-note.is-error { background: #fdeeed; color: #c9342b; }

/* ---------- 底栏 ---------- */
.sp-foot {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding-top: 14px;
}
.sp-foot-note {
  min-width: 0;
  font-size: 11.5px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sp-foot-sep { margin: 0 6px; }
.sp-foot-actions { flex: none; display: flex; align-items: center; gap: 8px; }

/* 矮屏（≤840）：不再锁一屏，让整页滚动。
   否则配置卡会被截在中间，还要靠一条几乎看不见的内部滚动条才能看到最后两项。 */
@media (max-height: 840px) {
  .scripts-page { height: auto; }
  .sp-card-body { overflow: visible; }
}

/* ---------- 窄屏：解除一屏锁定，两列改一列 ---------- */
/* 980 以上仍保持两列（1024 的笔记本窗口还很常见），只是右列收到 340 */
@media (max-width: 980px) {
  .scripts-page { height: auto; }
  .sp-grid { grid-template-columns: 1fr; }
  .sp-card-body { overflow: visible; }
  .sp-fill { min-height: 220px; }
  .sp-requirement :deep(.el-textarea__inner) { min-height: 200px; }
  .sp-tab-note { display: none; }
}
@media (max-width: 720px) {
  .sp-head { flex-direction: column; align-items: flex-start; gap: 12px; }
  .sp-head-actions { padding-bottom: 0; }
  .sp-pair { grid-template-columns: 1fr; gap: 0; padding-bottom: 0; border-bottom: 0; }
  .sp-pair .form-field { border-bottom: 1px solid rgba(0, 0, 0, .09); padding-bottom: 7px; }
  .fc-block { margin-left: 0; }
  .fc-add { margin-left: 26px; }
}
</style>
