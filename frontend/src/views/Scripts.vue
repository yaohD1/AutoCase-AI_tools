<template>
  <div class="scripts-page">
    <div class="page-header">
      <div>
        <div class="eyebrow">AUTOMATION WORKSPACE</div>
        <h1>自动化脚本编写</h1>
        <p>从已审批测试用例生成 Playwright TypeScript 脚本，写入服务端项目工作区。</p>
      </div>
      <el-button @click="$router.push('/')">返回首页</el-button>
    </div>

    <el-card class="scope-card">
      <template #header>
        <div class="section-header">
          <span>生成范围</span>
          <span class="selection-count">已选择 {{ selectedCases.length }} / {{ cases.length }} 个用例</span>
        </div>
      </template>
      <div class="toolbar">
        <el-select v-model="selectedProject" placeholder="选择项目" @change="onProjectChange" style="width: 220px">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
        </el-select>
        <el-select v-model="selectedSprint" placeholder="全部迭代" clearable @change="loadCases" style="width: 200px">
          <el-option v-for="sprint in sprints" :key="sprint.id" :label="sprint.name" :value="sprint.id" />
        </el-select>
        <el-input v-model="caseKeyword" placeholder="筛选标题、模块或测试点" clearable style="width: 280px" />
        <el-button @click="selectVisibleCases" :disabled="filteredCases.length === 0">选择当前结果</el-button>
        <el-button @click="clearSelection" :disabled="selectedCases.length === 0">清空选择</el-button>
      </div>
      <el-table
        ref="caseTable"
        :data="filteredCases"
        v-loading="casesLoading"
        row-key="id"
        max-height="360"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="52" />
        <el-table-column prop="module" label="模块" width="160" />
        <el-table-column prop="test_point" label="测试点" width="180" />
        <el-table-column prop="title" label="用例标题" min-width="280" />
        <el-table-column prop="priority" label="优先级" width="90">
          <template #default="{ row }"><el-tag size="small">{{ row.priority }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="case_type" label="类型" width="120" />
      </el-table>
      <el-empty v-if="!casesLoading && filteredCases.length === 0" description="暂无已审批用例" />
    </el-card>

    <div class="config-layout">
      <el-card>
        <template #header><span>脚本配置</span></template>
        <el-form :model="config" label-position="top" class="config-form">
          <div class="form-grid">
            <el-form-item label="运行环境">
              <el-input v-model="config.environment_name" placeholder="例如 test、staging" />
            </el-form-item>
            <el-form-item label="浏览器">
              <el-select v-model="config.browser" style="width:100%">
                <el-option label="Chromium" value="chromium" />
                <el-option label="Firefox" value="firefox" />
                <el-option label="WebKit" value="webkit" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="Base URL">
            <el-input v-model="config.base_url" placeholder="https://test.example.com" />
          </el-form-item>
          <el-form-item label="服务端工作区路径">
            <el-input v-model="config.workspace_path" placeholder="例如 project-a 或工作区根目录下的相对路径" />
            <div class="field-hint">脚本会写入后端服务器配置的 AUTOMATION_WORKSPACE_ROOT 下，不是浏览器本机目录。</div>
          </el-form-item>
          <el-form-item label="脚本目录">
            <el-input v-model="config.specs_path" placeholder="tests" />
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="认证状态文件（可选）">
              <el-input v-model="config.auth_state_path" placeholder="例如 .auth/user.json" />
            </el-form-item>
            <el-form-item label="AI 转换模型">
              <el-select v-model="config.ai_config_id" clearable placeholder="使用默认启用模型" style="width:100%">
                <el-option v-for="item in aiConfigs" :key="item.id" :label="`${item.provider} / ${item.model}`" :value="item.id" />
              </el-select>
            </el-form-item>
          </div>
          <div class="form-grid three-columns">
            <el-form-item label="操作超时（毫秒）"><el-input-number v-model="config.action_timeout" :min="1000" :max="120000" style="width:100%" /></el-form-item>
            <el-form-item label="导航超时（毫秒）"><el-input-number v-model="config.navigation_timeout" :min="1000" :max="180000" style="width:100%" /></el-form-item>
            <el-form-item label="断言超时（毫秒）"><el-input-number v-model="config.expect_timeout" :min="1000" :max="120000" style="width:100%" /></el-form-item>
          </div>
          <div class="form-grid">
            <el-form-item label="定位策略"><el-select v-model="config.locator_strategy" style="width:100%"><el-option label="优先语义定位" value="role" /></el-select></el-form-item>
            <el-form-item label="同名文件处理"><el-select v-model="config.overwrite_policy" style="width:100%"><el-option label="拒绝覆盖" value="reject" /><el-option label="允许覆盖" value="overwrite" /></el-select></el-form-item>
          </div>
        </el-form>
        <div class="config-actions">
          <el-button @click="saveConfig" :loading="saving">保存项目配置</el-button>
          <el-button type="primary" @click="generate" :loading="generating" :disabled="!canGenerate">生成脚本（{{ selectedCases.length }}）</el-button>
        </div>
      </el-card>

      <el-card class="history-card">
        <template #header><div class="section-header"><span>生成记录</span><el-button text @click="loadGenerations">刷新</el-button></div></template>
        <div v-if="generations.length === 0" class="history-empty">暂无生成记录</div>
        <div v-for="item in generations" :key="item.id" class="history-item">
          <div class="history-main">
            <el-tag :type="statusType(item.status)" size="small">{{ statusLabel(item.status) }}</el-tag>
            <span>{{ formatDate(item.created_at) }}</span>
          </div>
          <div class="history-sub">{{ (item.files || []).length }} 个文件 · {{ (item.warnings || []).length }} 个提醒</div>
          <el-button v-if="item.status === 'completed'" text type="primary" @click="download(item)">下载 ZIP</el-button>
          <div v-if="item.error" class="history-error">{{ item.error }}</div>
        </div>
      </el-card>
    </div>

    <el-dialog v-model="showResult" title="脚本生成结果" width="760px">
      <div v-if="result">
        <el-alert v-if="result.warnings?.length" type="warning" :closable="false" show-icon>
          <template #title>有 {{ result.warnings.length }} 个提醒，生成后请逐条检查 TODO 和定位器。</template>
        </el-alert>
        <div class="result-section">
          <div class="result-title">生成文件</div>
          <div v-for="file in result.files" :key="file.path" class="result-file">
            <span>{{ file.path }}</span><span class="result-source">{{ file.title }}</span>
          </div>
        </div>
        <div v-if="result.warnings?.length" class="result-section warning-list">
          <div class="result-title">提醒</div>
          <div v-for="warning in result.warnings" :key="warning">{{ warning }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showResult = false">关闭</el-button>
        <el-button type="primary" @click="download(result)">下载 ZIP</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const projects = ref([])
const sprints = ref([])
const aiConfigs = ref([])
const cases = ref([])
const selectedCases = ref([])
const selectedProject = ref('')
const selectedSprint = ref('')
const caseKeyword = ref('')
const casesLoading = ref(false)
const saving = ref(false)
const generating = ref(false)
const generations = ref([])
const result = ref(null)
const showResult = ref(false)
const caseTable = ref(null)

const emptyConfig = () => ({
  project_id: '', framework: 'playwright', language: 'typescript', workspace_path: '', specs_path: 'tests',
  base_url: '', environment_name: 'test', browser: 'chromium', auth_state_path: '', locator_strategy: 'role',
  action_timeout: 10000, navigation_timeout: 30000, expect_timeout: 5000, naming_strategy: 'case',
  overwrite_policy: 'reject', ai_config_id: ''
})
const config = ref(emptyConfig())

const filteredCases = computed(() => {
  const keyword = caseKeyword.value.trim().toLowerCase()
  if (!keyword) return cases.value
  return cases.value.filter(item => [item.module, item.test_point, item.title].some(value => String(value || '').toLowerCase().includes(keyword)))
})
const canGenerate = computed(() => Boolean(selectedProject.value && selectedCases.value.length && config.value.base_url && config.value.workspace_path))

onMounted(async () => {
  await loadProjects()
  try {
    const ai = await api.getAIConfigs()
    aiConfigs.value = ai.data.configs || []
  } catch {
    aiConfigs.value = []
  }
})

async function loadProjects() {
  try {
    const response = await api.getProjects()
    projects.value = response.data.projects || []
    selectedProject.value = projects.value[0]?.id || ''
    if (selectedProject.value) await onProjectChange()
  } catch { ElMessage.error('加载项目失败') }
}

async function onProjectChange() {
  selectedSprint.value = ''
  selectedCases.value = []
  if (!selectedProject.value) return
  try {
    const [sprintResponse, configResponse] = await Promise.all([
      api.getSprints({ project_id: selectedProject.value }),
      api.getAutomationConfig(selectedProject.value)
    ])
    sprints.value = sprintResponse.data.sprints || []
    config.value = { ...emptyConfig(), ...(configResponse.data.config || {}), project_id: selectedProject.value }
    await Promise.all([loadCases(), loadGenerations()])
  } catch { ElMessage.error('加载自动化配置失败') }
}

async function loadCases() {
  if (!selectedProject.value) return
  casesLoading.value = true
  selectedCases.value = []
  try {
    const params = { project_id: selectedProject.value, status: 'approved' }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const response = await api.getTestcases(params)
    cases.value = response.data.testcases || []
  } catch { ElMessage.error('加载已审批用例失败') }
  finally { casesLoading.value = false }
}

async function loadGenerations() {
  if (!selectedProject.value) return
  const response = await api.getAutomationGenerations(selectedProject.value)
  generations.value = response.data.generations || []
}

function handleSelectionChange(selection) { selectedCases.value = selection }
function selectVisibleCases() { filteredCases.value.forEach(item => caseTable.value?.toggleRowSelection(item, true)) }
function clearSelection() { caseTable.value?.clearSelection() }

async function saveConfig(showMessage = true) {
  if (!selectedProject.value) return false
  saving.value = true
  try {
    const response = await api.saveAutomationConfig({ ...config.value, project_id: selectedProject.value })
    config.value = response.data.config
    if (showMessage) ElMessage.success('项目脚本配置已保存')
    return true
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '保存配置失败')
    return false
  } finally { saving.value = false }
}

async function generate() {
  if (!(await saveConfig(false))) return
  generating.value = true
  try {
    const response = await api.generateAutomation({
      project_id: selectedProject.value,
      testcase_ids: selectedCases.value.map(item => item.id),
      config: config.value
    })
    result.value = response.data.generation
    showResult.value = true
    try { await loadGenerations() } catch { /* The generated result is still usable. */ }
    ElMessage.success('脚本生成完成')
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '脚本生成失败')
    try { await loadGenerations() } catch { /* Keep the original generation error visible. */ }
  } finally { generating.value = false }
}

async function download(item) {
  if (!item?.id) return
  try {
    const response = await api.downloadAutomationGeneration(item.id)
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.download = `automation_${item.id}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch { ElMessage.error('下载失败') }
}

function statusLabel(status) { return ({ completed: '完成', running: '生成中', failed: '失败' })[status] || status }
function statusType(status) { return ({ completed: 'success', running: 'warning', failed: 'danger' })[status] || 'info' }
function formatDate(value) { return value ? new Date(value).toLocaleString() : '-' }
</script>

<style scoped>
.scripts-page { max-width: 1440px; margin: 0 auto; }
.page-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:24px; }
.eyebrow { color:#0071e3; font-size:11px; font-weight:700; letter-spacing:1.2px; margin-bottom:8px; }
h1 { margin:0; color:#1d1d1f; font-size:28px; }
.page-header p { margin:8px 0 0; color:#6e6e73; }
.scope-card { margin-bottom:20px; }
.section-header { display:flex; justify-content:space-between; align-items:center; }
.selection-count { color:#86868b; font-size:13px; font-weight:400; }
.toolbar { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
.config-layout { display:grid; grid-template-columns:minmax(0, 1.65fr) minmax(300px, .85fr); gap:20px; align-items:start; }
.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.three-columns { grid-template-columns:repeat(3, 1fr); }
.config-form :deep(.el-form-item) { margin-bottom:16px; }
.field-hint { color:#909399; font-size:12px; line-height:1.5; margin-top:4px; }
.config-actions { display:flex; justify-content:flex-end; gap:10px; border-top:1px solid #ebeef5; padding-top:18px; }
.history-card { min-height:280px; }
.history-empty { color:#909399; padding:35px 0; text-align:center; }
.history-item { border-bottom:1px solid #f0f0f0; padding:14px 0; }
.history-item:last-child { border-bottom:0; }
.history-main { display:flex; align-items:center; gap:8px; color:#606266; font-size:13px; }
.history-sub { color:#909399; font-size:12px; margin:7px 0; }
.history-error { color:#f56c6c; font-size:12px; word-break:break-word; }
.result-section { margin-top:18px; }
.result-title { font-weight:600; color:#303133; margin-bottom:8px; }
.result-file { display:flex; justify-content:space-between; gap:12px; padding:8px 10px; background:#f7f8fa; border-radius:6px; margin-bottom:5px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }
.result-source { color:#909399; font-family:inherit; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.warning-list { color:#8c6b00; font-size:12px; line-height:1.6; max-height:180px; overflow:auto; }
@media (max-width: 900px) { .config-layout { grid-template-columns:1fr; } .form-grid, .three-columns { grid-template-columns:1fr; } .page-header { gap:12px; } }
</style>
