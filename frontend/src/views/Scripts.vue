<template>
  <div class="scripts-page">
    <div class="page-header">
      <div>
        <div class="eyebrow">OPENCODE PLAYWRIGHT WORKFLOW</div>
        <h1>自动化脚本编写</h1>
        <p>按需求生成时由 planner、generator、healer 完成探索、生成、执行和修复；按用例生成时跳过探索，直接把已审批用例写成脚本。</p>
      </div>
      <el-button @click="$router.push('/')">返回首页</el-button>
    </div>

    <div class="main-layout">
      <el-card class="panel-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">测试任务</span>
            <el-segmented
              :model-value="mode"
              :options="[{ label: '按需求生成', value: 'requirement' }, { label: '按用例生成', value: 'cases' }]"
              @change="onModeChange"
            />
          </div>
        </template>
        <el-form :model="form" label-position="top" class="task-form">
          <el-form-item label="项目">
            <el-select v-model="selectedProject" placeholder="选择项目" @change="onProjectChange" style="width:100%">
              <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
            </el-select>
          </el-form-item>

          <template v-if="mode === 'requirement'">
            <el-form-item label="测试需求" required class="requirement-item">
              <el-input
                v-model="form.requirement"
                type="textarea"
                :rows="10"
                maxlength="30000"
                show-word-limit
                placeholder="例如：验证用户登录、退出和登录失败场景。覆盖正常登录、错误密码、空字段和会话失效后的跳转。"
              />
              <div class="field-hint">描述业务流程、正向/反向场景和验收标准。Agent 会先访问真实页面再生成脚本。</div>
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item label="用例来源">
              <el-radio-group v-model="caseSource" @change="onSourceChange">
                <el-radio-button value="approved">已审批用例</el-radio-button>
                <el-radio-button value="markdown">粘贴 Markdown</el-radio-button>
                <el-radio-button value="form">表单录入</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <el-form-item v-if="caseSource === 'approved'" label="选择用例" required class="requirement-item">
              <el-select
                v-model="selectedCaseIds"
                multiple
                filterable
                collapse-tags
                collapse-tags-tooltip
                :loading="casesLoading"
                placeholder="从已审批用例中选择"
                style="width:100%"
              >
                <el-option v-for="item in approvedCases" :key="item.id" :label="`${item.priority || ''} ${item.title}`" :value="item.id" />
              </el-select>
              <div class="field-hint">已选 {{ selectedCaseIds.length }} 条。用例直接来自审批通过的数据，格式已规范化。</div>
            </el-form-item>

            <el-form-item v-else-if="caseSource === 'markdown'" label="用例 Markdown" required class="requirement-item">
              <el-input
                v-model="casesMarkdown"
                type="textarea"
                :rows="10"
                placeholder="**Seed:** tests/seed.spec.ts&#10;&#10;#### 验证码错误提示&#10;- 步骤：&#10;  1. 输入错误验证码&#10;- 预期：&#10;  - 提示验证码错误"
                @input="preview = null"
              />
              <div class="cases-actions">
                <el-button size="small" :loading="previewing" :disabled="!casesMarkdown.trim()" @click="previewMarkdown">预览校验</el-button>
              </div>
              <div class="field-hint">每条用例以 #### 开头，包含步骤和预期。标签（步骤：、预期：）会被自动去掉。</div>
            </el-form-item>

            <el-form-item v-else label="用例表单" required class="requirement-item">
              <div class="form-cases">
                <div class="form-cases-head">
                  <span class="form-cases-count">{{ formCases.length }} / {{ MAX_FORM_CASES }}</span>
                  <span class="field-hint">每条用例填写标题、步骤和预期结果，最多 {{ MAX_FORM_CASES }} 条</span>
                </div>

                <div v-for="(item, ci) in formCases" :key="item.id" class="case-card">
                  <div class="case-card-head">
                    <span class="case-index">{{ ci + 1 }}</span>
                    <el-input v-model="item.title" class="case-title-input" placeholder="用例标题，例如：验证码错误时登录失败" maxlength="200" />
                    <button
                      type="button"
                      class="icon-btn is-danger"
                      :disabled="formCases.length <= 1"
                      title="删除这条用例"
                      @click="removeCase(ci)"
                    >×</button>
                  </div>

                  <div class="case-block">
                    <div class="block-label">步骤</div>
                    <div v-for="(_, si) in item.steps" :key="si" class="case-row">
                      <span class="row-num">{{ si + 1 }}</span>
                      <el-input v-model="item.steps[si]" placeholder="描述一个操作，例如：输入正确的用户名" />
                      <button type="button" class="icon-btn" :disabled="item.steps.length <= 1" title="删除步骤" @click="item.steps.splice(si, 1)">×</button>
                    </div>
                    <button type="button" class="add-row" :disabled="item.steps.length >= MAX_ROWS" @click="item.steps.push('')">+ 添加步骤</button>
                  </div>

                  <div class="case-block">
                    <div class="block-label">预期结果</div>
                    <div v-for="(_, ei) in item.expected" :key="ei" class="case-row">
                      <span class="row-mark">✓</span>
                      <el-input v-model="item.expected[ei]" placeholder="描述一个检查点，例如：提示验证码错误并停留在登录页" />
                      <button type="button" class="icon-btn" :disabled="item.expected.length <= 1" title="删除预期" @click="item.expected.splice(ei, 1)">×</button>
                    </div>
                    <button type="button" class="add-row" :disabled="item.expected.length >= MAX_ROWS" @click="item.expected.push('')">+ 添加预期</button>
                  </div>

                  <div v-if="showFormErrors && !isBlankCase(item) && caseIssues(item).length" class="case-error">
                    {{ caseIssues(item).join('；') }}
                  </div>
                </div>

                <button type="button" class="add-case" :disabled="formCases.length >= MAX_FORM_CASES" @click="addCase">
                  <span class="add-case-plus">+</span>
                  <span>添加用例</span>
                  <span v-if="formCases.length >= MAX_FORM_CASES" class="add-case-hint">已达上限 {{ MAX_FORM_CASES }} 条</span>
                </button>
              </div>
            </el-form-item>

            <div v-if="preview && caseSource !== 'approved'" :class="['preview-result', preview.success ? 'is-ok' : 'is-error']">
              <template v-if="preview.success">校验通过，共 {{ preview.case_count }} 条用例，Seed：{{ preview.seed_file }}</template>
              <template v-else>
                <div>格式有误，请修改后再提交：</div>
                <div v-for="(err, i) in preview.errors" :key="i">· {{ err }}</div>
              </template>
            </div>
          </template>
        </el-form>
      </el-card>

      <el-card class="panel-card">
        <template #header><span class="card-title">Agent 配置</span></template>
        <el-form :model="form" label-position="top" class="config-form">
          <el-form-item label="Base URL" required>
            <el-input v-model="form.base_url" placeholder="https://test.example.com" />
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="浏览器">
              <el-select v-model="form.browser" style="width:100%">
                <el-option label="Chromium" value="chromium" />
                <el-option label="Firefox" value="firefox" />
                <el-option label="WebKit" value="webkit" />
              </el-select>
            </el-form-item>
            <el-form-item label="运行环境">
              <el-input v-model="form.environment_name" placeholder="test" />
            </el-form-item>
          </div>
          <el-form-item label="服务端 Git 工作区" required>
            <el-input v-model="form.workspace_path" placeholder="例如 project-a" />
            <div class="field-hint">必须是后端服务器上、AUTOMATION_WORKSPACE_ROOT 下的 Git + Playwright 项目。</div>
          </el-form-item>
          <el-form-item label="测试目录">
            <el-input v-model="form.specs_path" placeholder="tests" />
          </el-form-item>
          <el-form-item label="Seed 文件">
            <el-input v-model="form.seed_file" placeholder="tests/seed.spec.ts" />
            <div class="field-hint">planner 探索页面时使用的种子测试，相对工作区路径，必须是 .spec.ts 文件。</div>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="OpenCode 模型（可选）">
              <el-select
                v-model="form.opencode_model"
                filterable
                clearable
                allow-create
                default-first-option
                placeholder="留空使用默认模型"
                style="width:100%"
                @visible-change="onModelDropdown"
              >
                <el-option v-for="m in modelOptions" :key="m.value" :label="m.value" :value="m.value" />
              </el-select>
              <div v-if="modelsLoadError" class="field-hint">模型列表加载失败，可手动输入 provider/model。</div>
            </el-form-item>
            <el-form-item label="最大修复次数">
              <el-input-number v-model="form.max_heal_attempts" :min="0" :max="3" style="width:100%" />
            </el-form-item>
          </div>
          <el-form-item label="工作区文件策略">
            <el-select v-model="form.overwrite_policy" style="width:100%">
              <el-option label="Agent 自行判断并保留未提交状态" value="reject" />
              <el-option label="允许修改已有测试文件" value="overwrite" />
            </el-select>
          </el-form-item>
        </el-form>
        <div class="config-actions">
          <el-button @click="saveConfig" :loading="saving">保存配置</el-button>
          <el-button type="primary" @click="generate" :loading="generating" :disabled="!canGenerate">启动 Agent</el-button>
        </div>
      </el-card>
    </div>

    <el-card class="history-card">
      <template #header>
        <div class="section-header">
          <div class="history-head-left">
            <span class="card-title">任务记录</span>
            <span v-if="generations.length" class="history-count">{{ generations.length }}</span>
          </div>
          <el-button text @click="loadGenerations">刷新</el-button>
        </div>
      </template>
      <el-empty v-if="!generations.length" description="暂无自动化任务" />
      <div v-else class="history-list">
        <div v-for="item in generations" :key="item.id" :class="['history-item', `is-${item.status || 'pending'}`]">
          <div class="history-top">
            <el-tag :type="statusType(item.status)" size="small" effect="light" round>{{ statusLabel(item.status) }}</el-tag>
            <span class="history-url" :title="item.config?.base_url || ''">{{ item.config?.base_url || '未设置 Base URL' }}</span>
            <span class="history-date">{{ formatDate(item.created_at) }}</span>
          </div>
          <p class="history-requirement">{{ item.requirement || '无需求文本' }}</p>
          <div class="history-bottom">
            <div class="history-meta">
              <span class="chip"><i>批次</i>{{ item.run_key || item.id?.slice(0, 8) }}</span>
              <span class="chip"><i>阶段</i>{{ item.stage || '-' }}</span>
              <span class="chip"><i>文件</i>{{ (item.files || []).length }}</span>
              <span class="chip"><i>修复</i>{{ item.heal_attempts || 0 }}</span>
            </div>
            <div class="history-actions">
              <el-button text size="small" @click="showDetails(item)">查看详情</el-button>
              <el-button v-if="item.status === 'completed'" text size="small" type="primary" @click="download(item)">下载 ZIP</el-button>
            </div>
          </div>
          <div v-if="item.error" class="history-error">{{ item.error }}</div>
        </div>
      </div>
    </el-card>

    <el-dialog v-model="showResult" title="Agent 执行结果" width="820px">
      <div v-if="result" class="result-content">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="状态">{{ statusLabel(result.status) }}</el-descriptions-item>
          <el-descriptions-item label="阶段">{{ result.stage || '-' }}</el-descriptions-item>
          <el-descriptions-item label="修复次数">{{ result.heal_attempts || 0 }}</el-descriptions-item>
          <el-descriptions-item label="生成时间">{{ formatDate(result.completed_at || result.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <div class="result-section">
          <div class="result-title">Git 变更文件</div>
          <div v-if="!result.files?.length" class="muted">没有检测到工作区变更。</div>
          <div v-for="file in result.files" :key="file.path" class="result-file">
            <span>{{ file.path }}</span><el-tag size="small">{{ file.status }}</el-tag>
          </div>
        </div>
        <div class="result-section">
          <div class="result-title">OpenCode 输出</div>
          <pre class="event-log">{{ result.event_log || '无输出' }}</pre>
        </div>
      </div>
      <template #footer>
        <el-button @click="showResult = false">关闭</el-button>
        <el-button v-if="result?.status === 'completed'" type="primary" @click="download(result)">下载 ZIP</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const projects = ref([])
const router = useRouter()
const selectedProject = ref('')
const generations = ref([])
const saving = ref(false)
const generating = ref(false)
const result = ref(null)
const showResult = ref(false)
const modelOptions = ref([])
const modelsLoadError = ref(false)
let modelsLoaded = false

const emptyForm = () => ({
  project_id: '', framework: 'playwright', language: 'typescript', workspace_path: '', specs_path: 'autocase/tests',
  base_url: '', environment_name: 'test', browser: 'chromium', opencode_model: '',
  max_heal_attempts: 3, overwrite_policy: 'reject', seed_file: 'tests/seed.spec.ts'
})
const form = ref(emptyForm())

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

// 表单录入：每条用例是一张卡片，只填标题、步骤和预期，不填优先级。上限 5 条。
const MAX_FORM_CASES = 5
const MAX_ROWS = 20
let caseSeq = 0
const newFormCase = () => ({ id: ++caseSeq, title: '', steps: [''], expected: [''] })
const formCases = ref([newFormCase()])
const showFormErrors = ref(false)

const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim()

// 完全没填的卡片视为“还没用到”，不校验、不提交；只要填了一部分，就必须填完整。
const isBlankCase = item => !clean(item.title) && !item.steps.some(clean) && !item.expected.some(clean)
const activeFormCases = computed(() => formCases.value.filter(item => !isBlankCase(item)))

function caseIssues(item) {
  const issues = []
  if (!clean(item.title)) issues.push('缺少用例标题')
  if (!item.steps.some(step => clean(step))) issues.push('至少填写一个步骤')
  if (!item.expected.some(value => clean(value))) issues.push('至少填写一条预期结果')
  return issues
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

function onSourceChange() {
  preview.value = null
  showFormErrors.value = false
}

// 表单内容一变，之前的预览结果就过期了。
watch(formCases, () => { preview.value = null }, { deep: true })

onMounted(() => { loadProjects(); loadModels() })

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
    const [configResponse, generationResponse] = await Promise.all([
      api.getAutomationConfig(selectedProject.value),
      api.getAutomationGenerations(selectedProject.value)
    ])
    form.value = { ...emptyForm(), ...(configResponse.data.config || {}), project_id: selectedProject.value, requirement: '' }
    generations.value = generationResponse.data.generations || []
  } catch { ElMessage.error('加载自动化配置失败') }
  if (mode.value === 'cases') await loadApprovedCases()
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

async function loadGenerations() {
  if (!selectedProject.value) return
  try {
    const response = await api.getAutomationGenerations(selectedProject.value)
    generations.value = response.data.generations || []
  } catch { ElMessage.error('加载任务记录失败') }
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
    result.value = response.data.generation
    router.push({ path: `/scripts/generations/${response.data.generation.id}`, query: { project_id: selectedProject.value } })
    await loadGenerations()
    ElMessage.success('Agent 任务已启动')
  } catch (error) {
    const generation = error.response?.data?.generation
    if (generation) {
      result.value = generation
      showResult.value = true
    }
    ElMessage.error(error.response?.data?.error || 'Agent 工作流失败')
    await loadGenerations()
  } finally { generating.value = false }
}

function casesPayload() {
  const base = { project_id: selectedProject.value, config: form.value }
  if (caseSource.value === 'approved') return { ...base, case_ids: selectedCaseIds.value }
  return { ...base, cases_markdown: casesMarkdownText.value }
}

function showDetails(item) {
  router.push({ path: `/scripts/generations/${item.id}`, query: { project_id: item.project_id || selectedProject.value } })
}

async function download(item) {
  if (!item?.id) return
  try {
    const response = await api.downloadAutomationGeneration(item.id, item.project_id || selectedProject.value)
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.download = `automation_${item.run_key || item.id}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch { ElMessage.error('下载失败') }
}

function statusLabel(status) { return ({ completed: '完成', running: '执行中', failed: '失败', pending: '等待中', interrupted: '已中断' })[status] || status }
function statusType(status) { return ({ completed: 'success', running: 'warning', failed: 'danger', pending: 'info', interrupted: 'danger' })[status] || 'info' }
function formatDate(value) { return value ? new Date(value).toLocaleString() : '-' }
</script>

<style scoped>
.scripts-page { max-width:1440px; margin:0 auto; }

/* Page header */
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:22px; }
.eyebrow { color:#0071e3; font-size:11px; font-weight:700; letter-spacing:1.4px; margin-bottom:8px; text-transform:uppercase; }
h1 { margin:0; color:#1d1d1f; font-size:28px; font-weight:700; letter-spacing:-.3px; }
.page-header p { margin:8px 0 0; color:#6e6e73; font-size:14px; line-height:1.55; max-width:760px; }

/* Shared card look */
.panel-card, .history-card { border:1px solid #ebedf0; border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px rgba(16,24,40,.05); }
.panel-card :deep(.el-card__header), .history-card :deep(.el-card__header) { padding:15px 20px; border-bottom:1px solid #f0f2f5; background:linear-gradient(180deg,#fcfdff,#f7f9fc); }
.panel-card :deep(.el-card__body) { padding:20px; }
.card-title { position:relative; padding-left:12px; font-size:15px; font-weight:700; color:#1d1d1f; }
.card-title::before { content:''; position:absolute; left:0; top:50%; transform:translateY(-50%); width:4px; height:15px; border-radius:3px; background:linear-gradient(180deg,#0a84ff,#0071e3); }

/* Main layout: equal-height, bottom-aligned cards */
.main-layout { display:grid; grid-template-columns:minmax(0,1.08fr) minmax(380px,.92fr); gap:20px; align-items:stretch; }
.main-layout .panel-card { display:flex; flex-direction:column; }
.main-layout .panel-card :deep(.el-card__body) { flex:1; display:flex; flex-direction:column; }

/* Forms */
.task-form, .config-form { display:flex; flex-direction:column; }
.task-form { flex:1; }
.task-form :deep(.el-form-item), .config-form :deep(.el-form-item) { margin-bottom:16px; }
.task-form :deep(.el-form-item__label), .config-form :deep(.el-form-item__label) { font-size:13px; font-weight:600; color:#303133; padding-bottom:6px; }
.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.field-hint { color:#909399; font-size:12px; line-height:1.5; margin-top:6px; }
.task-form :deep(.el-textarea__inner), .config-form :deep(.el-input__wrapper), .config-form :deep(.el-select__wrapper), .config-form :deep(.el-input-number) { border-radius:8px; }

/* Requirement textarea grows so both cards share the same height */
.requirement-item { flex:1; display:flex; flex-direction:column; }
.requirement-item :deep(.el-form-item__content) { flex:1; display:flex; flex-direction:column; }
.requirement-item :deep(.el-textarea) { flex:1; }
.requirement-item :deep(.el-textarea__inner) { height:100% !important; min-height:240px; resize:none; }

/* Mode switch sits in the card header, title on the left */
.panel-card :deep(.section-header) { gap:12px; flex-wrap:wrap; }

/* Markdown cases: preview action and result */
.cases-actions { display:flex; justify-content:flex-end; margin-top:8px; }
.preview-result { margin-top:10px; padding:10px 12px; border-radius:8px; font-size:12.5px; line-height:1.6; }
.preview-result.is-ok { background:#f0faf4; color:#1e7a46; }
.preview-result.is-error { background:#fff1f0; color:#e5484d; }

/* Form-entry cases: one card per case, capped at 5 */
.form-cases { display:flex; flex-direction:column; gap:12px; width:100%; }
.form-cases-head { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.form-cases-count { display:inline-flex; align-items:center; height:22px; padding:0 10px; border-radius:999px; background:#eaf3ff; color:#0071e3; font-size:12px; font-weight:700; font-variant-numeric:tabular-nums; }
.case-card { position:relative; border:1px solid #ebedf0; border-radius:12px; padding:14px 14px 4px; background:#fff; box-shadow:0 1px 2px rgba(16,24,40,.04); transition:border-color .18s ease, box-shadow .18s ease; }
.case-card:hover { border-color:#dce7fb; box-shadow:0 4px 14px rgba(16,24,40,.06); }
.case-card-head { display:flex; align-items:center; gap:10px; }
.case-index { flex-shrink:0; width:24px; height:24px; border-radius:50%; background:linear-gradient(135deg,#0a84ff,#0071e3); color:#fff; font-size:12px; font-weight:700; display:flex; align-items:center; justify-content:center; box-shadow:0 2px 6px rgba(0,113,227,.25); }
.case-title-input { flex:1; min-width:0; }
.case-block { margin-top:12px; padding-top:10px; border-top:1px dashed #eef0f4; }
.block-label { font-size:12px; font-weight:700; color:#6e6e73; margin-bottom:8px; letter-spacing:.2px; }
.case-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
.row-num, .row-mark { flex-shrink:0; width:20px; text-align:center; font-size:12px; font-weight:700; }
.row-num { color:#a1a1a6; font-variant-numeric:tabular-nums; }
.row-mark { color:#34c759; }
.icon-btn { flex-shrink:0; width:26px; height:26px; border:none; border-radius:7px; background:transparent; color:#a1a1a6; font-size:16px; line-height:1; cursor:pointer; transition:background .15s ease, color .15s ease; }
.icon-btn:hover:not(:disabled) { background:#f2f4f7; color:#606266; }
.icon-btn.is-danger:hover:not(:disabled) { background:#fff1f0; color:#e5484d; }
.icon-btn:disabled { opacity:.35; cursor:not-allowed; }
.add-row { margin:2px 0 0 28px; padding:2px 6px; border:none; background:none; color:#0071e3; font-size:12.5px; font-weight:600; cursor:pointer; border-radius:6px; }
.add-row:hover:not(:disabled) { background:#eaf3ff; }
.add-row:disabled { color:#c0c4cc; cursor:not-allowed; }
.case-error { margin:6px 0 10px; padding:7px 10px; border-radius:8px; background:#fff1f0; color:#e5484d; font-size:12px; line-height:1.5; }
.add-case { display:flex; align-items:center; justify-content:center; gap:8px; width:100%; padding:13px; border:1.5px dashed #c9d6ea; border-radius:12px; background:#fbfdff; color:#0071e3; font-size:13.5px; font-weight:600; cursor:pointer; transition:background .18s ease, border-color .18s ease; }
.add-case:hover:not(:disabled) { background:#eef5ff; border-color:#0071e3; }
.add-case:disabled { color:#a1a1a6; border-color:#e4e7ed; background:#f7f8fa; cursor:not-allowed; }
.add-case-plus { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:50%; background:#0071e3; color:#fff; font-size:16px; line-height:1; }
.add-case:disabled .add-case-plus { background:#c0c4cc; }
.add-case-hint { font-size:12px; font-weight:500; color:#a1a1a6; margin-left:4px; }

/* Config actions anchored to card bottom */
.config-actions { display:flex; justify-content:flex-end; gap:10px; border-top:1px solid #f0f2f5; padding-top:16px; margin-top:auto; }

/* History */
.history-card { margin-top:20px; }
.section-header { display:flex; justify-content:space-between; align-items:center; }
.history-head-left { display:flex; align-items:center; }
.history-count { display:inline-flex; align-items:center; justify-content:center; min-width:20px; height:20px; padding:0 7px; margin-left:9px; border-radius:10px; background:#eef1f6; color:#6e6e73; font-size:12px; font-weight:600; }
.history-list { max-height:600px; overflow-y:auto; overscroll-behavior:contain; padding:2px 6px 2px 2px; }
.history-list::-webkit-scrollbar { width:8px; }
.history-list::-webkit-scrollbar-thumb { background:#dcdfe6; border-radius:4px; }
.history-list::-webkit-scrollbar-thumb:hover { background:#c0c4cc; }

.history-item { position:relative; border:1px solid #eef0f4; border-radius:12px; padding:14px 16px 12px 18px; margin-bottom:12px; background:#fff; overflow:hidden; transition:border-color .18s ease, background .18s ease; }
.history-item::before { content:''; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--accent,#c0c4cc); }
.history-item:last-child { margin-bottom:2px; }
.history-item:hover { border-color:#dce7fb; background:#fbfdff; }
.is-completed { --accent:#34c759; }
.is-running { --accent:#ff9f0a; }
.is-running::before { animation:accent-pulse 1.6s ease-in-out infinite; }
.is-failed, .is-interrupted { --accent:#ff453a; }
.is-pending { --accent:#8e8e93; }
@keyframes accent-pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }

.history-top { display:flex; align-items:center; gap:10px; }
.history-url { flex:1; min-width:0; color:#1d1d1f; font-weight:600; font-size:13.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.history-date { color:#a1a1a6; font-size:12px; white-space:nowrap; }
.history-requirement { margin:8px 0 10px; color:#6e6e73; font-size:13px; line-height:1.55; white-space:pre-wrap; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.history-bottom { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.history-meta { display:flex; flex-wrap:wrap; gap:6px; min-width:0; }
.chip { display:inline-flex; align-items:center; gap:5px; height:24px; padding:0 9px; border-radius:6px; background:#f4f6f9; color:#606266; font-size:12px; white-space:nowrap; }
.chip i { color:#a8abb2; font-style:normal; font-size:11px; }
.history-actions { display:flex; gap:2px; flex-shrink:0; margin-left:auto; }
.history-error { margin-top:10px; padding:8px 10px; border-radius:8px; background:#fff1f0; color:#e5484d; font-size:12px; line-height:1.5; }

/* Result dialog */
.result-section { margin-top:18px; }
.result-title { font-weight:700; color:#1d1d1f; margin-bottom:8px; font-size:14px; }
.result-file { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 12px; background:#f7f8fa; border-radius:8px; margin-bottom:6px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }
.muted { color:#909399; font-size:13px; }
.event-log { max-height:280px; overflow:auto; white-space:pre-wrap; word-break:break-word; background:#1f2937; color:#d1d5db; border-radius:8px; padding:14px; font-size:11px; line-height:1.6; }

@media (max-width: 900px) { .main-layout { grid-template-columns:1fr; } .form-grid { grid-template-columns:1fr; } .page-header { flex-direction:column; gap:12px; } .requirement-item :deep(.el-textarea__inner) { min-height:180px; } }
</style>
