<template>
  <div class="scripts-page">
    <div class="page-header">
      <div>
        <div class="eyebrow">OPENCODE PLAYWRIGHT WORKFLOW</div>
        <h1>自动化脚本编写</h1>
        <p>输入测试需求，由 planner、generator 和 healer 完成探索、生成、执行和修复。</p>
      </div>
      <el-button @click="$router.push('/')">返回首页</el-button>
    </div>

    <div class="main-layout">
      <el-card class="panel-card">
        <template #header><span class="card-title">测试任务</span></template>
        <el-form :model="form" label-position="top" class="task-form">
          <el-form-item label="项目">
            <el-select v-model="selectedProject" placeholder="选择项目" @change="onProjectChange" style="width:100%">
              <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
            </el-select>
          </el-form-item>
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
          <div class="form-grid">
            <el-form-item label="测试目录">
              <el-input v-model="form.specs_path" placeholder="tests" />
            </el-form-item>
            <el-form-item label="storageState（可选）">
              <el-input v-model="form.auth_state_path" placeholder=".auth/user.json" />
            </el-form-item>
          </div>
          <div class="form-grid">
            <el-form-item label="OpenCode 模型（可选）">
              <el-input v-model="form.opencode_model" placeholder="provider/model，留空使用默认模型" />
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
import { computed, onMounted, ref } from 'vue'
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

const emptyForm = () => ({
  project_id: '', framework: 'playwright', language: 'typescript', workspace_path: '', specs_path: 'tests',
  base_url: '', environment_name: 'test', browser: 'chromium', auth_state_path: '', opencode_model: '',
  max_heal_attempts: 3, overwrite_policy: 'reject'
})
const form = ref(emptyForm())
const canGenerate = computed(() => Boolean(selectedProject.value && form.value.requirement?.trim() && form.value.base_url && form.value.workspace_path))

onMounted(loadProjects)

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
  try {
    const [configResponse, generationResponse] = await Promise.all([
      api.getAutomationConfig(selectedProject.value),
      api.getAutomationGenerations(selectedProject.value)
    ])
    form.value = { ...emptyForm(), ...(configResponse.data.config || {}), project_id: selectedProject.value, requirement: '' }
    generations.value = generationResponse.data.generations || []
  } catch { ElMessage.error('加载自动化配置失败') }
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
  generating.value = true
  try {
    const response = await api.generateAutomation({
      project_id: selectedProject.value,
      requirement: form.value.requirement,
      config: form.value
    })
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
    link.download = `automation_${item.id}.zip`
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
