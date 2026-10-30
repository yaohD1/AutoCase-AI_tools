<template>
  <div class="settings-page">
    <header class="set-head">
      <div class="eyebrow">工作台 · 配置</div>
      <h1>设置</h1>
      <p class="set-sub">模型服务和项目迭代都收在这里，改动即时生效。</p>
    </header>

    <div class="set-body">
      <!-- 左侧分组用竖排导航：设置页以后还会加项，
           顶部 tab 每加一项就要挤一次，而且 tab 名和卡片标题总会重复一遍 -->
      <nav class="set-nav">
        <button
          v-for="t in tabs"
          :key="t.name"
          type="button"
          :class="['set-nav-item', { 'is-active': activeTab === t.name }]"
          @click="activeTab = t.name"
        >
          <span>{{ t.label }}</span>
          <span class="set-nav-count">{{ t.count }}</span>
        </button>
      </nav>

      <div class="set-main">
        <!-- ---------- AI 服务 ---------- -->
        <section v-if="activeTab === 'ai'" class="set-panel">
          <div class="panel-head">
            <p class="panel-desc">所有 AI 调用都走这里的配置。标了 Vision 的才能用于原型图分析。</p>
            <el-button type="primary" @click="openAddConfig">添加服务</el-button>
          </div>

          <div class="flat-table set-table">
            <el-table :data="configs" v-loading="loadingConfigs">
              <el-table-column label="服务商" min-width="190">
                <template #default="{ row }">
                  <div class="cell-stack">
                    <span class="cell-main">{{ row.provider }}</span>
                    <!-- 后端只返回「前 6 + 后 4」，这里直接显示。
                         要换 Key 只能进编辑弹窗重填，列表不提供展开入口。 -->
                    <span class="key-line">{{ displayKey(row.api_key) }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="模型" min-width="250">
                <template #default="{ row }">
                  <span class="mono cell-model">{{ row.model || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="采样" width="112" class-name="col-opt">
                <template #default="{ row }">
                  <span class="mono num">{{ row.temperature ?? '—' }} · {{ row.max_tokens ?? '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="能力" width="88" class-name="col-opt">
                <template #default="{ row }">
                  <span :class="['cap', { 'is-vision': row.supports_vision !== false }]">
                    {{ row.supports_vision !== false ? 'Vision' : '纯文本' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="84">
                <template #default="{ row }">
                  <span :class="['cap', row.is_active ? 'is-on' : 'is-off']">
                    {{ row.is_active ? '启用' : '停用' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" align="right">
                <template #default="{ row }">
                  <div class="row-acts">
                    <button type="button" class="row-act" @click="editConfig(row)">编辑</button>
                    <button type="button" class="row-act" @click="toggleActive(row)">
                      {{ row.is_active ? '停用' : '启用' }}
                    </button>
                    <button type="button" class="row-act is-danger" @click="deleteConfig(row.id)">删除</button>
                  </div>
                </template>
              </el-table-column>
              <template #empty>
                <div class="table-empty">
                  <p>还没有配置模型服务。</p>
                  <p class="table-empty-hint">至少添加一个，首页的原型图分析和用例生成才能用。</p>
                </div>
              </template>
            </el-table>
          </div>
        </section>

        <!-- ---------- 项目与迭代 ---------- -->
        <section v-else class="set-panel">
          <div class="panel-head">
            <p class="panel-desc">展开项目可以管理它的迭代。用例挂在迭代下，删项目会连同用例一起删。</p>
            <el-button type="primary" @click="showAddProject = true">新建项目</el-button>
          </div>

          <div class="flat-table set-table">
            <el-table :data="projectList" row-key="id" v-loading="loadingProjects">
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="sprint-list">
                    <div class="sprint-list-head">
                      <span>迭代 · {{ getProjectSprints(row.id).length }}</span>
                      <button type="button" class="row-act" @click="showAddSprint(row)">＋ 新建迭代</button>
                    </div>

                    <div v-if="getProjectSprints(row.id).length === 0" class="sprint-empty">
                      这个项目还没有迭代。生成用例前至少要有一个迭代。
                    </div>

                    <div v-for="s in getProjectSprints(row.id)" :key="s.id" class="sprint-row">
                      <template v-if="editingSprintId === s.id">
                        <el-input
                          v-model="editSprintName"
                          class="form-input sprint-edit"
                          @keyup.enter="saveSprintName"
                        />
                        <div class="sprint-acts">
                          <button type="button" class="row-act" @click="saveSprintName">保存</button>
                          <button type="button" class="row-act" @click="cancelEditSprint">取消</button>
                        </div>
                      </template>
                      <template v-else>
                        <span class="sprint-name">{{ s.name }}</span>
                        <span class="sprint-stat"><b>{{ getSprintStat(s.id).approved }}</b> 已通过</span>
                        <span class="sprint-stat"><b>{{ getSprintStat(s.id).pending }}</b> 待审</span>
                        <div class="sprint-acts">
                          <button type="button" class="row-act" @click="startEditSprint(s)">重命名</button>
                          <button type="button" class="row-act is-danger" @click="confirmDeleteSprint(s)">删除</button>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <!-- 描述并进项目名第二行：单独一列时大多是空的，
                   却要占到 260px，项目名反而被挤窄 -->
              <el-table-column label="项目" min-width="260">
                <template #default="{ row }">
                  <template v-if="editingProjectId === row.id">
                    <div class="edit-stack">
                      <el-input
                        v-model="editProjectName"
                        class="form-input"
                        placeholder="项目名称"
                        @keyup.enter="saveProjectName"
                      />
                      <el-input v-model="editProjectDesc" class="form-input" placeholder="描述（可选）" />
                    </div>
                  </template>
                  <div v-else class="cell-stack">
                    <span class="cell-main">{{ row.name }}</span>
                    <span class="cell-sub">{{ row.description || '暂无描述' }}</span>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="迭代" width="76">
                <template #default="{ row }">
                  <span class="mono num">{{ getProjectSprints(row.id).length }}</span>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="156" align="right">
                <template #default="{ row }">
                  <div class="row-acts">
                    <template v-if="editingProjectId === row.id">
                      <button type="button" class="row-act" @click="saveProjectName">保存</button>
                      <button type="button" class="row-act" @click="cancelEditProject">取消</button>
                    </template>
                    <template v-else>
                      <button type="button" class="row-act" @click="startEditProject(row)">重命名</button>
                      <button type="button" class="row-act is-danger" @click="confirmDeleteProject(row)">删除</button>
                    </template>
                  </div>
                </template>
              </el-table-column>

              <template #empty>
                <div class="table-empty">
                  <p>还没有项目。</p>
                  <p class="table-empty-hint">项目是迭代和用例的容器，先建一个。</p>
                </div>
              </template>
            </el-table>
          </div>
        </section>
      </div>
    </div>

    <!-- ---------- 添加 / 编辑服务 ---------- -->
    <el-dialog
      v-model="showAddConfig"
      :title="editingConfig ? '编辑服务' : '添加服务'"
      width="480px"
      class="form-dialog"
      :close-on-click-modal="false"
    >
      <div class="form-block">
        <div class="form-field">
          <div class="form-label">
            <span>服务商</span>
            <span v-if="editingConfig" class="form-note">不可修改</span>
          </div>
          <div v-if="editingConfig" class="form-static mono">{{ configForm.provider }}</div>
          <el-input v-else v-model="configForm.provider" class="form-input" placeholder="kimi / volcengine / deepseek" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>API Key</span>
            <span v-if="editingConfig" class="form-note">留空则不修改</span>
          </div>
          <el-input
            v-model="configForm.api_key"
            type="password"
            show-password
            class="form-input"
            :placeholder="editingConfig ? '不修改请留空' : 'sk-...'"
          />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>API Base</span>
            <span class="form-note">可选</span>
          </div>
          <el-input v-model="configForm.api_base" class="form-input" placeholder="留空则用服务商默认地址" />
        </div>

        <div class="form-field">
          <div class="form-label"><span>模型名称</span></div>
          <el-input v-model="configForm.model" class="form-input" placeholder="如 kimi-k2.6" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>采样参数</span>
            <span class="form-note">温度越高越发散</span>
          </div>
          <div class="form-duo">
            <div class="duo-item">
              <span class="duo-label">温度</span>
              <el-input-number
                v-model="configForm.temperature"
                :min="0" :max="1" :step="0.1" :precision="1"
                controls-position="right"
                class="form-num"
              />
            </div>
            <div class="duo-item">
              <span class="duo-label">Token</span>
              <el-input-number
                v-model="configForm.max_tokens"
                :min="100" :max="32000" :step="200"
                controls-position="right"
                class="form-num is-wide"
              />
            </div>
          </div>
        </div>

        <div class="form-toggle">
          <el-switch v-model="configForm.supports_vision" />
          <span class="form-toggle-text">支持图片分析</span>
          <span class="form-toggle-hint">{{ configForm.supports_vision ? '可用于原型图模式' : '只做文本生成' }}</span>
        </div>
      </div>

      <template #footer>
        <div class="dlg-foot">
          <div class="foot-left">
            <el-button :loading="testing" @click="testConnection">测试连通</el-button>
            <span v-if="testResult !== null" :class="['test-dot', testResult ? 'is-ok' : 'is-bad']">
              {{ testResult ? '连接成功' : '连接失败' }}
            </span>
          </div>
          <div class="foot-right">
            <el-button @click="cancelEdit">取消</el-button>
            <el-button type="primary" @click="saveConfig">{{ editingConfig ? '保存' : '添加' }}</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ---------- 新建项目 ---------- -->
    <el-dialog v-model="showAddProject" title="新建项目" width="440px" class="form-dialog">
      <div class="form-block">
        <div class="form-field">
          <div class="form-label"><span>项目名称</span></div>
          <el-input v-model="newProject.name" class="form-input" placeholder="例如：测试资产平台" />
        </div>
        <div class="form-field">
          <div class="form-label"><span>描述</span><span class="form-note">可选</span></div>
          <el-input v-model="newProject.description" type="textarea" :rows="3" class="form-textarea" placeholder="一句话说明这个项目测什么" />
        </div>
      </div>
      <template #footer>
        <el-button @click="showAddProject = false">取消</el-button>
        <el-button type="primary" @click="createProject">创建项目</el-button>
      </template>
    </el-dialog>

    <!-- ---------- 新建迭代 ---------- -->
    <el-dialog v-model="showAddSprintDialog" title="新建迭代" width="400px" class="form-dialog">
      <div class="form-block">
        <div class="form-field">
          <div class="form-label"><span>所属项目</span></div>
          <div class="form-static">{{ createSprintProjectName }}</div>
        </div>
        <div class="form-field">
          <div class="form-label"><span>迭代名称</span></div>
          <el-input v-model="newSprintName" class="form-input" placeholder="如 v1.0、sprint-3" />
        </div>
      </div>
      <template #footer>
        <el-button @click="showAddSprintDialog = false">取消</el-button>
        <el-button type="primary" @click="createSprintForProject">创建迭代</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const activeTab = ref('ai')

const configs = ref([])
const loadingConfigs = ref(false)
const showAddConfig = ref(false)
const editingConfig = ref(null)
const testing = ref(false)
const testResult = ref(null)

function emptyConfigForm() {
  return {
    provider: 'volcengine',
    api_key: '',
    api_base: '',
    model: '',
    temperature: 0.7,
    max_tokens: 2000,
    supports_vision: true
  }
}

const configForm = ref(emptyConfigForm())

const projectList = ref([])
const loadingProjects = ref(false)
const allSprints = ref([])
const sprintStats = ref({})
const showAddProject = ref(false)
const showAddSprintDialog = ref(false)
const createSprintProjectId = ref('')
const createSprintProjectName = ref('')
const newSprintName = ref('')
const newProject = ref({ name: '', description: '' })
const editingProjectId = ref('')
const editingSprintId = ref('')
const editProjectName = ref('')
const editProjectDesc = ref('')
const editSprintName = ref('')

const tabs = computed(() => [
  { name: 'ai', label: '模型服务', count: configs.value.length },
  { name: 'projects', label: '项目与迭代', count: projectList.value.length }
])

onMounted(() => {
  loadConfigs()
  loadProjectData()
})

/* ---------- Key 展示 ----------
   后端 AIConfig.to_dict() 只会给「前 6 + 后 4」。
   这里同时兼容旧接口的 `前20 + ***`：先剥掉尾部的星号再重新裁，
   这样无论后端是哪个版本，列表里最多露出 10 位。 */
function displayKey(key) {
  if (!key) return '未设置'
  const k = String(key).replace(/\*+$/, '')
  if (!k) return '未设置'
  if (k.includes('••')) return k
  if (k.length <= 12) return '•'.repeat(k.length)
  return `${k.slice(0, 6)}••••${k.slice(-4)}`
}

/* ---------- AI 配置 ---------- */
async function loadConfigs() {
  loadingConfigs.value = true
  try {
    const res = await api.getAIConfigs()
    configs.value = res.data.configs
  } catch (error) {
    ElMessage.error('加载配置失败')
  } finally {
    loadingConfigs.value = false
  }
}

function openAddConfig() {
  editingConfig.value = null
  configForm.value = emptyConfigForm()
  testResult.value = null
  showAddConfig.value = true
}

function editConfig(config) {
  editingConfig.value = config
  configForm.value = {
    provider: config.provider,
    api_key: '',
    api_base: config.api_base || '',
    model: config.model || '',
    temperature: config.temperature ?? 0.7,
    max_tokens: config.max_tokens ?? 2000,
    supports_vision: config.supports_vision !== false
  }
  testResult.value = null
  showAddConfig.value = true
}

function cancelEdit() {
  showAddConfig.value = false
  editingConfig.value = null
  configForm.value = emptyConfigForm()
  testResult.value = null
}

async function saveConfig() {
  const isEdit = !!editingConfig.value
  if (!configForm.value.provider || !configForm.value.model) {
    ElMessage.warning('请填写服务商和模型名称')
    return
  }
  if (!isEdit && !configForm.value.api_key) {
    ElMessage.warning('请填写 API Key')
    return
  }
  try {
    const data = { ...configForm.value }
    if (isEdit && !data.api_key) delete data.api_key
    if (isEdit) {
      await api.updateAIConfig(editingConfig.value.id, data)
      ElMessage.success('已保存')
    } else {
      await api.createAIConfig(data)
      ElMessage.success('已添加')
    }
    cancelEdit()
    loadConfigs()
  } catch (error) {
    ElMessage.error('保存失败：' + (error.response?.data?.error || error.message))
  }
}

async function testConnection() {
  if (!configForm.value.api_key) {
    ElMessage.warning('请先输入 API Key')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testAIConnection({
      api_key: configForm.value.api_key,
      api_base: configForm.value.api_base,
      model: configForm.value.model
    })
    testResult.value = res.data.success
  } catch (error) {
    testResult.value = false
    ElMessage.error('连接失败：' + (error.response?.data?.error || error.message))
  } finally {
    testing.value = false
  }
}

async function toggleActive(config) {
  try {
    await api.updateAIConfig(config.id, { is_active: !config.is_active })
    ElMessage.success('状态已更新')
    loadConfigs()
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

async function deleteConfig(id) {
  try {
    await ElMessageBox.confirm('删除后使用该配置的分析和生成都会失败，确定删除？', '删除服务', {
      type: 'warning',
      confirmButtonText: '删除'
    })
    await api.deleteAIConfig(id)
    ElMessage.success('已删除')
    loadConfigs()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('删除失败')
  }
}

/* ---------- 项目 / 迭代 ---------- */
async function loadProjectData() {
  loadingProjects.value = true
  try {
    const res = await api.getProjects()
    projectList.value = res.data.projects
    if (projectList.value.length > 0) {
      const sprintResults = await Promise.all(
        projectList.value.map(p => api.getSprints({ project_id: p.id }).catch(() => ({ data: { sprints: [] } })))
      )
      allSprints.value = sprintResults.flatMap(r => r.data.sprints || [])
    } else {
      allSprints.value = []
    }
    const statPromises = allSprints.value.map(s =>
      Promise.all([
        api.getTestcases({ project_id: s.project_id, sprint_id: s.id, status: 'approved' }).catch(() => ({ data: { testcases: [] } })),
        api.getPendingTestcases({ project_id: s.project_id, sprint_id: s.id }).catch(() => ({ data: { counts: { pending: 0 } } }))
      ]).then(([approvedRes, pendingRes]) => {
        sprintStats.value[s.id] = {
          approved: (approvedRes.data.testcases || []).length,
          pending: pendingRes.data.counts?.pending || 0
        }
      })
    )
    await Promise.all(statPromises)
  } catch {
    ElMessage.error('加载项目失败')
  } finally {
    loadingProjects.value = false
  }
}

function getProjectSprints(projectId) {
  return (allSprints.value || []).filter(s => s.project_id === projectId)
}

function getSprintStat(sprintId) {
  return sprintStats.value[sprintId] || { approved: 0, pending: 0 }
}

function startEditProject(row) {
  editingProjectId.value = row.id
  editProjectName.value = row.name
  editProjectDesc.value = row.description || ''
}

function cancelEditProject() {
  editingProjectId.value = ''
}

async function saveProjectName() {
  // 编辑态可能已被取消（比如按了 Esc），没有 id 就不要发请求
  if (!editingProjectId.value) return
  const name = editProjectName.value.trim()
  if (!name) {
    editingProjectId.value = ''
    return
  }
  try {
    await api.updateProject(editingProjectId.value, { name, description: editProjectDesc.value })
    ElMessage.success('已更新')
    editingProjectId.value = ''
    await loadProjectData()
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

async function confirmDeleteProject(row) {
  try {
    await ElMessageBox.confirm('该项目下的所有用例、模块和迭代都会一起删除，不可恢复。', '删除项目', {
      type: 'warning',
      confirmButtonText: '删除'
    })
    await api.deleteProject(row.id)
    ElMessage.success('已删除')
    await loadProjectData()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('删除失败')
  }
}

async function createProject() {
  const name = newProject.value.name.trim()
  if (!name) { ElMessage.warning('请输入项目名称'); return }
  try {
    await api.createProject({ name, description: newProject.value.description })
    ElMessage.success('项目已创建')
    showAddProject.value = false
    newProject.value = { name: '', description: '' }
    await loadProjectData()
  } catch (error) {
    ElMessage.error('创建失败：' + (error.response?.data?.error || error.message))
  }
}

function showAddSprint(row) {
  createSprintProjectId.value = row.id
  createSprintProjectName.value = row.name
  newSprintName.value = ''
  showAddSprintDialog.value = true
}

async function createSprintForProject() {
  const name = newSprintName.value.trim()
  if (!name) { ElMessage.warning('请输入迭代名称'); return }
  try {
    await api.createSprint({ project_id: createSprintProjectId.value, name })
    ElMessage.success('迭代已创建')
    showAddSprintDialog.value = false
    await loadProjectData()
  } catch (error) {
    ElMessage.error('创建失败')
  }
}

function startEditSprint(sprint) {
  editingSprintId.value = sprint.id
  editSprintName.value = sprint.name
}

function cancelEditSprint() {
  editingSprintId.value = ''
}

async function saveSprintName() {
  if (!editingSprintId.value) return
  const name = editSprintName.value.trim()
  if (!name) { editingSprintId.value = ''; return }
  try {
    await api.updateSprint(editingSprintId.value, { name })
    ElMessage.success('已更新')
    editingSprintId.value = ''
    await loadProjectData()
  } catch {
    ElMessage.error('更新失败')
  }
}

async function confirmDeleteSprint(sprint) {
  try {
    await ElMessageBox.confirm('该迭代下的所有用例会一起删除，不可恢复。', '删除迭代', {
      type: 'warning',
      confirmButtonText: '删除'
    })
    await api.deleteSprint(sprint.id)
    ElMessage.success('已删除')
    await loadProjectData()
  } catch (error) {
    // 原实现这里写的是 catch { ... error ... }，error 未定义，取消时会抛 ReferenceError
    if (error !== 'cancel') ElMessage.error('删除失败')
  }
}
</script>

<style scoped>
.settings-page {
  max-width: 1120px;
  margin: 0 auto;
}

/* ---------- 页头 ---------- */
.set-head {
  margin-bottom: 28px;
}

.set-head h1 {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -.3px;
}

.set-sub {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--muted);
}

/* ---------- 两栏：左导航 + 右内容 ---------- */
.set-body {
  display: flex;
  align-items: flex-start;
  gap: 28px;
}

.set-nav {
  flex: none;
  width: 152px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-right: 22px;
  border-right: 1px solid var(--line-soft);
}

.set-nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 34px;
  padding: 0 10px;
  margin-left: -10px;
  border: 0;
  border-radius: 7px;
  background: none;
  font-size: 13px;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  transition: color .14s ease, background .14s ease;
}

.set-nav-item:hover {
  color: var(--ink);
  background: rgba(0, 0, 0, .03);
}

/* 选中只加深字重和一点点底，不铺蓝：这一栏是导航不是按钮 */
.set-nav-item.is-active {
  color: var(--ink);
  font-weight: 500;
  background: rgba(0, 0, 0, .045);
}

.set-nav-count {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
  font-weight: 400;
}

.set-main {
  flex: 1;
  min-width: 0;
}

/* ---------- 面板头 ----------
   只留一句说明 + 主操作。分组名交给左侧导航，
   这里再放 h2 会和页面 h1「设置」只差 20px，读起来像两个同级标题。 */
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.panel-head .el-button {
  flex: none;
}

/* 窄屏时这句说明比按钮长得多，不压住按钮就会被挤出容器 */
.panel-desc {
  min-width: 0;
}

.panel-desc {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.55;
}

/* ---------- 表格 ---------- */
.set-table {
  background: #fff;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  overflow: hidden;
}

/* 展开行 Element 默认给浅灰底 + 20/50 的内边距，
   灰底会让迭代列表看起来像另一张表；内边距由 .sprint-list 自己控制才能对齐列 */
.set-table :deep(.el-table__expanded-cell) {
  padding: 0;
  background: #fff;
}

.set-table :deep(.el-table__expanded-cell:hover) {
  background: #fff;
}

.cell-stack {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.cell-main {
  font-size: 13px;
  color: var(--ink);
}

.cell-sub {
  font-size: 11.5px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 编辑态：名称一行、描述一行，竖着排，不要挤在同一行 */
.edit-stack {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cell-model {
  font-size: 12.5px;
  color: var(--ink-2);
}

/* Key：只读展示，mono 小字。做不成按钮，因为后端不再返回完整值 */
.key-line {
  font-family: var(--mono);
  font-size: 10.5px;
  letter-spacing: .02em;
  color: var(--faint);
}

.mono {
  font-family: var(--mono);
}

.num {
  font-size: 12.5px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}

/* 能力 / 状态两列共用「小圆点 + 文字」：
   一列药丸、一列文字会显得两列不是一套东西；只用点+字时，
   行的注意力不会被色块打断，靠点的颜色区分状态。 */
.cap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--faint);
  white-space: nowrap;
}

.cap::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.cap.is-vision {
  color: var(--ink-2);
}

.cap.is-vision::before {
  background: var(--accent);
}

.cap.is-on {
  color: var(--ink-2);
}

.cap.is-on::before {
  background: #2ba471;
}

.cap.is-off {
  color: var(--faint);
}

/* 操作列：文字按钮，一页十几行也不吵 */
.row-acts {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
}

.row-act {
  border: 0;
  background: none;
  padding: 0;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
  transition: color .14s ease;
}

.row-act:hover {
  color: var(--accent);
}

.row-act.is-danger:hover {
  color: #ff3b30;
}

.table-empty {
  padding: 26px 0 30px;
  text-align: center;
}

.table-empty p {
  margin: 0;
  font-size: 13px;
  color: var(--ink-2);
}

.table-empty-hint {
  margin-top: 5px !important;
  font-size: 12px !important;
  color: var(--faint) !important;
}

/* ---------- 展开行里的迭代 ----------
   左缩进 80 = 展开列 48 + 单元格 14 + 18。
   只写 62 的话迭代名会正好和上面的项目名齐平，展开区看起来像另一张表；
   多出的 18px 才读得出「这是那个项目底下的迭代」。
   右边距 14 = 单元格内边距，这样迭代的「删除」和项目行的「删除」上下对齐。 */
.sprint-list {
  padding: 6px 14px 14px 80px;
}

.sprint-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line-faint);
  font-size: 11.5px;
  color: var(--muted);
}

.sprint-row {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-faint);
}

.sprint-row:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.sprint-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sprint-stat {
  flex: none;
  font-size: 11.5px;
  color: var(--faint);
  white-space: nowrap;
}

.sprint-stat b {
  font-family: var(--mono);
  font-weight: 500;
  color: var(--ink-2);
}

.sprint-acts {
  flex: none;
  display: flex;
  gap: 12px;
}

.sprint-edit {
  flex: 1;
  min-width: 0;
}

.sprint-empty {
  padding: 14px 0 4px;
  font-size: 12.5px;
  color: var(--faint);
}

/* ---------- 弹窗 ---------- */
.form-note {
  font-size: 11px;
  color: var(--faint);
  font-weight: 400;
}

.form-duo {
  display: flex;
  align-items: center;
  gap: 26px;
  padding-top: 2px;
}

.duo-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.duo-label {
  font-size: 12px;
  color: var(--muted);
}

.dlg-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.foot-left,
.foot-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 测试连通的结果用一个小圆点 + 文字，不再是一颗 tag 药丸 */
.test-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.test-dot::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.test-dot.is-ok {
  color: #1a7f37;
}

.test-dot.is-bad {
  color: #d93026;
}

/* ---------- 窄屏 ---------- */
@media (max-width: 900px) {
  /* 六列加起来 922px，900 屏只有 844；隐藏两列次要信息，
     避免操作列被裁掉（宁少两列，也不能点不到删除） */
  .set-table :deep(.col-opt) {
    display: none;
  }

  /* 竖排后 align-items 若还是 flex-start，右栏宽度会收缩到表格内容宽
     （表格就被挤成一条），必须改成 stretch 让它铺满 */
  .set-body {
    flex-direction: column;
    align-items: stretch;
    gap: 18px;
  }

  .set-nav {
    width: 100%;
    flex-direction: row;
    gap: 6px;
    padding: 0 0 12px;
    border-right: 0;
    border-bottom: 1px solid var(--line-soft);
  }

  .set-nav-item {
    margin-left: 0;
  }

  .sprint-list {
    padding: 4px 16px 12px;
  }
}
</style>
