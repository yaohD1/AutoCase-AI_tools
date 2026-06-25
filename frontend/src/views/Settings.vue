<template>
  <div class="settings-page">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="AI服务配置" name="ai">
        <el-card>
          <template #header>
            <span>AI服务配置</span>
          </template>
          
          <el-button type="primary" @click="showAddConfig = true" style="margin-bottom: 20px">
            添加AI配置
          </el-button>
          
          <el-table :data="configs" style="width: 100%">
            <el-table-column prop="provider" label="服务商" width="150" />
            <el-table-column prop="api_key" label="API Key" width="200">
              <template #default="{ row }">
                {{ row.api_key }}
              </template>
            </el-table-column>
            <el-table-column prop="model" label="模型" width="150" />
            <el-table-column prop="temperature" label="温度参数" width="100" />
            <el-table-column prop="max_tokens" label="最大Token" width="100" />
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">
                  {{ row.is_active ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="supports_vision" label="Vision" width="80">
              <template #default="{ row }">
                <el-tag :type="row.supports_vision !== false ? 'success' : 'info'" size="small">
                  {{ row.supports_vision !== false ? '是' : '否' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right" align="center">
              <template #default="{ row }">
                <div class="table-actions">
                  <el-button text @click="editConfig(row)">编辑</el-button>
                  <el-button text @click="toggleActive(row)">
                    {{ row.is_active ? '禁用' : '启用' }}
                  </el-button>
                  <el-button text type="danger" @click="deleteConfig(row.id)">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        
        <el-dialog v-model="showAddConfig" :title="editingConfig ? '编辑配置' : '添加配置'" width="500px">
          <el-form :model="configForm" label-width="120px">
            <el-form-item label="服务商" required>
              <el-input v-model="configForm.provider" :disabled="!!editingConfig" placeholder="如 kimi、volcengine、deepseek" style="width: 100%" />
            </el-form-item>
            <el-form-item label="API Key" required>
              <el-input v-model="configForm.api_key" type="password" placeholder="请输入API Key" />
            </el-form-item>
            <el-form-item label="API Base URL">
              <el-input v-model="configForm.api_base" placeholder="可选，自定义API地址" />
            </el-form-item>
            <el-form-item label="模型名称" required>
              <el-input v-model="configForm.model" placeholder="如 kimi-k2.6" />
            </el-form-item>
            <el-form-item label="温度参数" required>
              <el-slider v-model="configForm.temperature" :min="0" :max="1" :step="0.1" show-input />
            </el-form-item>
            <el-form-item label="最大Token" required>
              <el-input-number v-model="configForm.max_tokens" :min="100" :max="32000" :step="200" />
            </el-form-item>
            <el-form-item label="是否支持Vision">
              <el-switch v-model="configForm.supports_vision" />
              <span class="form-hint" style="margin-left:8px;color:#909399;font-size:12px">
                {{ configForm.supports_vision ? '可用于图片分析' : '仅文本生成' }}
              </span>
            </el-form-item>
            </el-form>
          <template #footer>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div>
                <el-button @click="testConnection" :loading="testing">
                  测试连通
                </el-button>
                <el-tag v-if="testResult !== null" :type="testResult ? 'success' : 'danger'" style="margin-left:8px">
                  {{ testResult ? '连接成功' : '连接失败' }}
                </el-tag>
              </div>
              <div>
                <el-button @click="cancelEdit">取消</el-button>
                <el-button type="primary" @click="saveConfig">保存</el-button>
              </div>
            </div>
          </template>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="项目管理" name="projects">
        <el-card>
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span>项目管理</span>
              <el-button type="primary" @click="showAddProject = true">新建项目</el-button>
            </div>
          </template>
          
          <el-table :data="projectList" style="width: 100%" row-key="id">
            <el-table-column width="40">
              <template #default="{ row }">
                <el-button text size="small" @click="toggleExpand(row)" class="expand-toggle">
                  <el-icon><ArrowRight v-if="!isExpanded(row.id)" /><ArrowDown v-else /></el-icon>
                </el-button>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="项目名称" min-width="150">
              <template #default="{ row }">
                <template v-if="editingProjectId === row.id">
                  <el-input v-model="editProjectName" size="small" style="width:200px" @blur="saveProjectName" @keyup.enter="saveProjectName" />
                  <el-button size="small" @click="saveProjectName" style="margin-left:8px">保存</el-button>
                </template>
                <template v-else>
                  <span @dblclick="startEditProject(row)" style="cursor:pointer">{{ row.name }}</span>
                </template>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" min-width="200">
              <template #default="{ row }">
                <template v-if="editingProjectId === row.id">
                  <el-input v-model="editProjectDesc" size="small" placeholder="描述" />
                </template>
                <template v-else>
                  {{ row.description || '-' }}
                </template>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button text type="danger" @click="confirmDeleteProject(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #append>
              <div v-for="row in projectList" :key="'expand_'+row.id">
                <div v-if="isExpanded(row.id)" class="sprint-expand-row">
                  <div class="sprint-sub-table">
                    <div class="sprint-sub-header">
                      <span class="sprint-sub-title">迭代</span>
                      <el-button text size="small" class="sprint-add-link" @click="showAddSprint(row)">
                        <el-icon><Plus /></el-icon> 新建
                      </el-button>
                    </div>
                    <div v-if="getProjectSprints(row.id).length === 0" class="sprint-empty">暂无迭代</div>
                    <div v-for="(s, si) in getProjectSprints(row.id)" :key="s.id" class="sprint-item">
                      <span class="sprint-dot">{{ si + 1 }}</span>
                      <template v-if="editingSprintId === s.id">
                        <el-input v-model="editSprintName" size="small" style="width:160px" @blur="saveSprintName" @keyup.enter="saveSprintName" />
                        <el-button size="small" text type="primary" @click="saveSprintName" style="margin-right:auto">确定</el-button>
                      </template>
                      <template v-else>
                        <span class="sprint-name" @click="startEditSprint(s)">{{ s.name }}</span>
                        <span class="sprint-stat-divider"></span>
                        <span class="sprint-stat">{{ getSprintStat(s.id).approved }} 已审批</span>
                        <span class="sprint-stat">{{ getSprintStat(s.id).pending }} 待审</span>
                      </template>
                      <div class="sprint-actions">
                        <el-button text size="small" @click="startEditSprint(s)"><el-icon><Edit /></el-icon></el-button>
                        <el-button text size="small" type="danger" @click="confirmDeleteSprint(s)"><el-icon><Close /></el-icon></el-button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </el-table>
        </el-card>

        <el-dialog v-model="showAddProject" title="新建项目" width="400px">
          <el-form label-width="80px">
            <el-form-item label="项目名称" required>
              <el-input v-model="newProject.name" placeholder="请输入项目名称" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="newProject.description" type="textarea" :rows="3" placeholder="可选" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="showAddProject = false">取消</el-button>
            <el-button type="primary" @click="createProject">确定</el-button>
          </template>
        </el-dialog>

        <el-dialog v-model="showAddSprintDialog" title="新建迭代" width="350px">
          <el-form label-width="80px">
            <el-form-item label="所属项目">
              <span>{{ createSprintProjectName }}</span>
            </el-form-item>
            <el-form-item label="迭代名称" required>
              <el-input v-model="newSprintName" placeholder="请输入迭代名称" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="showAddSprintDialog = false">取消</el-button>
            <el-button type="primary" @click="createSprintForProject">确定</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Close, Plus, ArrowRight, ArrowDown } from '@element-plus/icons-vue'
import api from '../api'

const activeTab = ref('ai')

const configs = ref([])
const showAddConfig = ref(false)
const editingConfig = ref(null)
const testing = ref(false)
const testResult = ref(null)

const configForm = ref({
  provider: 'volcengine',
  api_key: '',
  api_base: '',
  model: '',
  temperature: 0.7,
  max_tokens: 2000,
  supports_vision: true
})

const projectList = ref([])
const allSprints = ref([])
const sprintStats = ref({})
const expandedProjects = ref(new Set())
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

onMounted(() => {
  loadConfigs()
  loadProjectData()
})

async function loadConfigs() {
  try {
    const res = await api.getAIConfigs()
    configs.value = res.data.configs
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

async function loadProjectData() {
  try {
    const res = await api.getProjects()
    projectList.value = res.data.projects
    if (projectList.value.length > 0) {
      const sprintResults = await Promise.all(
        projectList.value.map(p => api.getSprints({ project_id: p.id }).catch(() => ({ data: { sprints: [] } })))
      )
      allSprints.value = sprintResults.flatMap(r => r.data.sprints || [])
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
  }
}

function getProjectSprints(projectId) {
  return (allSprints.value || []).filter(s => s.project_id === projectId)
}

function getSprintStat(sprintId) {
  return sprintStats.value[sprintId] || { approved: 0, pending: 0 }
}

function toggleExpand(row) {
  const s = expandedProjects.value
  if (s.has(row.id)) s.delete(row.id); else s.add(row.id)
}

function isExpanded(id) {
  return expandedProjects.value.has(id)
}

function startEditProject(row) {
  editingProjectId.value = row.id
  editProjectName.value = row.name
  editProjectDesc.value = row.description || ''
}

async function saveProjectName() {
  if (!editProjectName.value.trim()) {
    editingProjectId.value = ''
    return
  }
  try {
    await api.updateProject(editingProjectId.value, { name: editProjectName.value.trim(), description: editProjectDesc.value })
    ElMessage.success('已更新')
    editingProjectId.value = ''
    await loadProjectData()
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

async function confirmDeleteProject(row) {
  try {
    await ElMessageBox.confirm('该操作将同时删除该项目下所有用例、模块和迭代，不可恢复。确定继续？', '删除项目', { type: 'warning', confirmButtonText: '确定删除' })
    await api.deleteProject(row.id)
    ElMessage.success('已删除')
    await loadProjectData()
  } catch { if (error !== 'cancel') { ElMessage.error('删除失败') } }
}

async function createProject() {
  const name = newProject.value.name.trim()
  if (!name) { ElMessage.warning('请输入项目名称'); return }
  try {
    await api.createProject({ name, description: newProject.value.description })
    ElMessage.success('项目创建成功')
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
    ElMessage.success('迭代创建成功')
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

async function saveSprintName() {
  if (!editSprintName.value.trim()) { editingSprintId.value = ''; return }
  try {
    await api.updateSprint(editingSprintId.value, { name: editSprintName.value.trim() })
    ElMessage.success('已更新')
    editingSprintId.value = ''
    await loadProjectData()
  } catch {
    ElMessage.error('更新失败')
  }
}

async function confirmDeleteSprint(sprint) {
  try {
    await ElMessageBox.confirm('该操作将同时删除该迭代下所有用例，确定继续？', '删除迭代', { type: 'warning', confirmButtonText: '确定删除' })
    await api.deleteSprint(sprint.id)
    ElMessage.success('已删除')
    await loadProjectData()
  } catch { if (error !== 'cancel') { ElMessage.error('删除失败') } }
}

function editConfig(config) {
  editingConfig.value = config
  configForm.value = {
    provider: config.provider,
    api_key: config.api_key ? config.api_key.replace('***', '') : '',
    api_base: config.api_base || '',
    model: config.model || '',
    temperature: config.temperature || 0.7,
    max_tokens: config.max_tokens || 2000,
    supports_vision: config.supports_vision !== false
  }
  showAddConfig.value = true
}

function cancelEdit() {
  showAddConfig.value = false
  editingConfig.value = null
  configForm.value = {
    provider: 'volcengine',
    api_key: '',
    api_base: '',
    model: '',
    temperature: 0.7,
    max_tokens: 2000,
    supports_vision: true
  }
}

async function saveConfig() {
  if (!configForm.value.provider || !configForm.value.api_key || !configForm.value.model) {
    ElMessage.warning('请填写必填字段（服务商、API Key、模型名称）')
    return
  }
  try {
    if (editingConfig.value) {
      await api.updateAIConfig(editingConfig.value.id, configForm.value)
      ElMessage.success('更新成功')
    } else {
      await api.createAIConfig(configForm.value)
      ElMessage.success('添加成功')
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
    ElMessage[testResult.value ? 'success' : 'error'](
      testResult.value ? '连接成功' : '连接失败'
    )
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
    ElMessage.success('状态更新成功')
    loadConfigs()
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

async function deleteConfig(id) {
  try {
    await ElMessageBox.confirm('确定删除该配置吗？', '提示', { type: 'warning' })
    await api.deleteAIConfig(id)
    ElMessage.success('删除成功')
    loadConfigs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}
</script>

<style scoped>
.settings-page {
  max-width: 1200px;
  margin: 0 auto;
}

.sprint-sub-table {
  padding: 12px 20px 16px;
  max-width: 600px;
}

.sprint-sub-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.sprint-sub-title {
  font-size: 12px;
  color: #909399;
  font-weight: 500;
}

.sprint-empty {
  font-size: 12px;
  color: #c0c4cc;
  padding: 8px 0 8px 24px;
}

.sprint-sub-table {
  padding: 12px 20px 16px;
}

.sprint-sub-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.sprint-sub-title {
  font-size: 11px;
  color: #86868b;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.sprint-add-link {
  font-size: 12px !important;
  color: #86868b !important;
  padding: 2px 6px !important;
}

.sprint-add-link:hover {
  color: #0071e3 !important;
}

.sprint-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}

.sprint-item:last-child {
  border-bottom: none;
}

.sprint-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.04);
  color: #86868b;
  font-size: 11px;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.sprint-name {
  font-size: 14px;
  color: #1d1d1f;
  cursor: pointer;
  flex: 1;
}

.sprint-name:hover {
  color: #0071e3;
}

.sprint-stat-divider {
  width: 1px;
  height: 16px;
  background: rgba(0, 0, 0, 0.06);
}

.sprint-stat {
  font-size: 11px;
  color: #86868b;
  flex-shrink: 0;
}

.sprint-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.sprint-actions .el-button {
  color: #86868b !important;
  font-size: 14px;
}

.sprint-actions .el-button:hover {
  color: #0071e3 !important;
}

.sprint-actions .el-button--danger:hover {
  color: #ff3b30 !important;
}

.sprint-empty {
  font-size: 12px;
  color: #aeaeb2;
  padding: 8px 0;
}

.sprint-expand-row {
  background: #fafafa;
  border-bottom: 1px solid #ebeef5;
}

.sprint-expand-row .sprint-sub-table {
  padding: 12px 40px 16px;
}

.expand-toggle {
  color: #86868b !important;
  font-size: 14px;
}

.expand-toggle:hover {
  color: #0071e3 !important;
}
</style>
