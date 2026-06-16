<template>
  <div class="upload-page">

    <div class="status-bar">
      <div class="status-item">
        <span class="status-num">{{ statusCount.pending }}</span>
        <span class="status-label">待审批</span>
      </div>
      <div class="status-divider"></div>
      <div class="status-item">
        <span class="status-num">{{ statusCount.approved }}</span>
        <span class="status-label">已通过</span>
      </div>
      <div class="status-divider"></div>
      <div class="status-item">
        <span class="status-num">{{ statusCount.total }}</span>
        <span class="status-label">AI生成用例数</span>
      </div>
      <div class="status-divider"></div>
      <div class="status-item">
        <span class="status-num">{{ approvalRate }}</span>
        <span class="status-label">通过率</span>
      </div>
    </div>

    <div class="mode-tabs">
      <div :class="['mode-tab', { active: uploadMode === 'image' }]" @click="switchMode('image')">
        <el-icon><PictureFilled /></el-icon>
        <span>原型图模式</span>
      </div>
      <div :class="['mode-tab', { active: uploadMode === 'doc' }]" @click="switchMode('doc')">
        <el-icon><Document /></el-icon>
        <span>文档模式</span>
      </div>
    </div>

    <el-card class="upload-card">
      <template #header>
        <div class="card-header">
          <span>{{ uploadMode === 'image' ? '上传Figma设计图' : '上传接口文档' }}</span>
        </div>
      </template>

      <div class="card-body-layout">
        <div class="config-panel">
          <div class="section-title">Config</div>
          <el-form :model="form" label-width="100px" class="upload-form">
            <el-form-item label="项目">
              <div class="project-select-row">
                <el-select v-model="form.projectId" placeholder="选择项目" style="flex: 1" @change="loadStatusCount">
                  <el-option
                    v-for="project in projects"
                    :key="project.id"
                    :label="project.name"
                    :value="project.id"
                  />
                </el-select>
                <el-button text :icon="Plus" class="new-project-btn" @click="showCreateProject = true">
                  新建项目
</el-button>
              </div>
            </el-form-item>

            <el-form-item label="迭代">
              <div class="project-select-row">
                <el-select v-model="form.sprintId" placeholder="选择迭代" style="flex: 1" @change="loadStatusCount">
                  <el-option
                    v-for="s in sprints"
                    :key="s.id"
                    :label="s.name"
                    :value="s.id"
                  />
                </el-select>
                <el-button text :icon="Plus" class="new-project-btn" @click="showCreateSprint = true">
                  新建迭代
                </el-button>
              </div>
</el-form-item>

            <div class="linked-analysis-bar">
              <el-switch v-model="form.linkedAnalysis" active-text="关联分析" />
              <span class="form-hint" style="margin-left:8px">多图时合并分析，AI理解页面关联流程</span>
            </div>

            <el-form-item label="原型分析模型">
              <el-select v-model="form.analyzeConfigId" placeholder="选择分析模型" style="width: 100%">
                <el-option
                  v-for="config in filteredAIConfigs"
                  :key="config.id"
                  :label="getProviderLabel(config)"
                  :value="config.id"
                />
              </el-select>
            </el-form-item>
            <div v-if="currentAnalyzeModel" class="model-preview-card">
              <div class="model-preview-row">
                <span class="model-preview-value model-preview-name">{{ currentAnalyzeModel.model || '-' }}</span>
                <el-button v-if="!editingAnalyzeModel" link type="primary" size="small" :icon="Edit" @click="editingAnalyzeModel = true" />
                <el-input
                  v-if="editingAnalyzeModel"
                  v-model="currentAnalyzeModel.model"
                  size="small"
                  style="width: 160px"
                  @blur="editingAnalyzeModel = false; saveModelConfig(currentAnalyzeModel)"
                />
              </div>
              <div class="model-preview-divider" />
              <div class="model-preview-row">
                <span class="model-preview-label">温度</span>
                <el-input-number
                  v-model="currentAnalyzeModel.temperature"
                  :min="0" :max="2" :step="0.1"
                  size="small" controls-position="right"
                  style="width: 100px"
                  @change="saveModelConfig(currentAnalyzeModel)"
                />
                <span class="model-preview-label" style="margin-left:16px">Token</span>
                <el-input-number
                  v-model="currentAnalyzeModel.max_tokens"
                  :min="100" :max="32000" :step="200"
                  size="small" controls-position="right"
                  style="width: 120px"
                  @change="saveModelConfig(currentAnalyzeModel)"
                />
              </div>
              <div class="model-preview-row">
                <span class="model-preview-label">API</span>
                <span class="model-preview-value model-preview-url">{{ currentAnalyzeModel.api_base || '-' }}</span>
              </div>
            </div>

            <el-form-item label="用例生成模型">
              <el-select v-model="form.genConfigId" placeholder="选择生成模型" style="width: 100%">
                <el-option
                  v-for="config in aiConfigs"
                  :key="config.id"
                  :label="getProviderLabel(config)"
                  :value="config.id"
                />
              </el-select>
            </el-form-item>
            <div v-if="currentGenModel" class="model-preview-card">
              <div class="model-preview-row">
                <span class="model-preview-value model-preview-name">{{ currentGenModel.model || '-' }}</span>
                <el-button v-if="!editingGenModel" link type="primary" size="small" :icon="Edit" @click="editingGenModel = true" />
                <el-input
                  v-if="editingGenModel"
                  v-model="currentGenModel.model"
                  size="small"
                  style="width: 160px"
                  @blur="editingGenModel = false; saveModelConfig(currentGenModel)"
                />
              </div>
              <div class="model-preview-divider" />
              <div class="model-preview-row">
                <span class="model-preview-label">温度</span>
                <el-input-number
                  v-model="currentGenModel.temperature"
                  :min="0" :max="2" :step="0.1"
                  size="small" controls-position="right"
                  style="width: 100px"
                  @change="saveModelConfig(currentGenModel)"
                />
                <span class="model-preview-label" style="margin-left:16px">Token</span>
                <el-input-number
                  v-model="currentGenModel.max_tokens"
                  :min="100" :max="32000" :step="200"
                  size="small" controls-position="right"
                  style="width: 120px"
                  @change="saveModelConfig(currentGenModel)"
                />
              </div>
              <div class="model-preview-row">
                <span class="model-preview-label">API</span>
                <span class="model-preview-value model-preview-url">{{ currentGenModel.api_base || '-' }}</span>
              </div>
            </div>
          </el-form>
        </div>

        <div class="upload-panel">
          <el-upload
            class="upload-dragger"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :on-remove="handleFileRemove"
            :file-list="fileList"
            :list-type="uploadMode === 'image' ? 'picture' : 'text'"
            :accept="uploadMode === 'image' ? '.jpg,.jpeg,.png,.webp' : '.docx,.doc,.md'"
            multiple
          >
            <el-icon class="el-icon--upload">
              <PictureFilled v-if="uploadMode === 'image'" />
              <Document v-else />
            </el-icon>
            <div class="el-upload__text">
              {{ uploadMode === 'image' ? '拖拽图片到此处或' : '拖拽文档到此处或' }} <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                {{ uploadMode === 'image' ? '支持 JPG/PNG/WebP 格式，单个文件不超过 10MB' : '支持 Word(.docx/.doc) / Markdown(.md) 格式' }}
              </div>
            </template>
          </el-upload>

          <div class="file-previews" v-if="fileList.length > 0">
            <div
              v-for="(file, index) in fileList"
              :key="file.uid"
              class="file-preview-item"
            >
              <img v-if="isImageFile(file)" :src="file.url" />
              <div v-else class="file-doc-preview">
                <el-icon :size="28"><Document /></el-icon>
                <span>{{ file.name }}</span>
              </div>
              <span class="file-preview-badge" @click="removeFile(index)">&times;</span>
            </div>
          </div>
        </div>
      </div>

      <div class="action-buttons">
        <el-button @click="openDescDialog" :disabled="fileList.length === 0" class="btn-secondary">
          填写功能介绍
        </el-button>
        <el-button type="primary" @click="generateCases" :loading="generating" :disabled="!canGenerate">
          生成测试用例
        </el-button>
        <el-button @click="goToCases" :disabled="!form.projectId" class="btn-secondary">
          查看用例列表
        </el-button>
      </div>
    </el-card>

    <el-dialog v-model="showCreateProject" title="新建项目" width="500px">
      <el-form :model="newProject" label-width="100px">
        <el-form-item label="项目名称">
          <el-input v-model="newProject.name" placeholder="请输入项目名称" />
        </el-form-item>
        <el-form-item label="项目描述">
          <el-input v-model="newProject.description" type="textarea" placeholder="请输入项目描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateProject = false">取消</el-button>
        <el-button type="primary" @click="createProject">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreateSprint" title="新建迭代" width="400px">
      <el-form :model="newSprint" label-width="100px">
        <el-form-item label="迭代名称">
          <el-input v-model="newSprint.name" placeholder="如 v1.0、sprint-3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateSprint = false">取消</el-button>
        <el-button type="primary" @click="createSprint">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showDescDialog" title="填写功能介绍" width="1400px" top="3vh" :close-on-click-modal="false">
      <div class="desc-layout" v-if="fileList.length > 0">
        <div class="desc-image-panel">
          <div v-if="isImageFile(currentFile)" class="desc-image-viewer"
               @wheel="handleDescZoom"
               @mousedown="startDescDrag"
               @mousemove="onDescDrag"
               @mouseup="endDescDrag"
               @mouseleave="endDescDrag">
              <img
                :src="getFilePreviewUrl(currentFileIndex)"
                :style="{
                  transform: `scale(${descImageScale}) translate(${descDragOffset.x}px, ${descDragOffset.y}px)`,
                  cursor: descIsDragging ? 'grabbing' : descImageScale > 1 ? 'grab' : 'default'
                }"
                draggable="false"
              />
            </div>
          <div v-else class="desc-doc-viewer">
            <pre>{{ currentDocContent }}</pre>
          </div>
          <div class="desc-image-thumbs" v-if="fileList.length > 1">
            <template v-if="uploadMode === 'image'">
              <img
                v-for="(file, index) in fileList"
                :key="file.uid"
                :src="getFilePreviewUrl(index)"
                :class="{ active: index === currentFileIndex }"
                @click="switchFile(index)"
              />
            </template>
            <template v-else>
              <div
                v-for="(file, index) in fileList"
                :key="file.uid"
                :class="['desc-doc-thumb', { active: index === currentFileIndex }]"
                @click="switchFile(index)"
              >
                {{ file.name }}
              </div>
            </template>
          </div>
          <div class="desc-image-info">
            {{ currentFileIndex + 1 }} / {{ fileList.length }}
          </div>
          <div class="desc-zoom-controls" v-if="isImageFile(currentFile)">
            <el-button circle size="small" @click="descZoomOut">-</el-button>
            <span>{{ Math.round(descImageScale * 100) }}%</span>
            <el-button circle size="small" @click="descZoomIn">+</el-button>
            <el-button size="small" @click="descImageScale = 1; descDragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="desc-form-panel">
          <div class="desc-form-label">功能介绍（可选，帮助AI理解测试目标）</div>
          <el-input
            v-model="imageDescriptions[currentFileIndex]"
            type="textarea"
            :rows="20"
            placeholder="请描述功能介绍、业务逻辑、您关注的测试重点等..."
          />
        </div>
      </div>
      <el-empty v-else description="请先上传文件" />
      <template #footer>
        <el-button @click="showDescDialog = false">完成</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showModuleReview" title="模块审核" width="1400px" top="2vh" :close-on-click-modal="false">
      <div v-if="modules.length > 0" class="module-review-layout">
        <div class="module-review-image-panel">
          <div class="module-image-viewer"
               @wheel="handleModuleZoom"
               @mousedown="startModuleDrag"
               @mousemove="onModuleDrag"
               @mouseup="endModuleDrag"
               @mouseleave="endModuleDrag">
            <img
              :src="getFilePreviewUrl(0)"
              :style="{
                transform: `scale(${moduleImageScale}) translate(${moduleDragOffset.x}px, ${moduleDragOffset.y}px)`,
                cursor: moduleIsDragging ? 'grabbing' : moduleImageScale > 1 ? 'grab' : 'default',
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
                transition: 'transform 0.2s ease',
                userSelect: 'none'
              }"
              draggable="false"
            />
          </div>
          <div class="module-zoom-controls" v-if="uploadMode === 'image'">
            <el-button circle size="small" @click="moduleZoomOut">-</el-button>
            <span>{{ Math.round(moduleImageScale * 100) }}%</span>
            <el-button circle size="small" @click="moduleZoomIn">+</el-button>
            <el-button size="small" @click="moduleImageScale = 1; moduleDragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="module-review-form-panel">
          <div class="module-progress">
            <span class="progress-text">模块 {{ currentModuleIndex + 1 }} / {{ modules.length }}</span>
            <el-button size="small" :disabled="currentModuleIndex <= 0" @click="prevModule">上一个</el-button>
            <el-button size="small" :disabled="currentModuleIndex >= moduleForms.length - 1" @click="nextModule">下一个</el-button>
          </div>
          <el-form v-if="moduleForms[currentModuleIndex]" label-width="120px" class="module-form">
            <el-form-item label="模块名称">
              <el-input v-model="moduleForms[currentModuleIndex].module" />
            </el-form-item>
            <el-form-item label="UI元素">
              <el-input v-model="moduleForms[currentModuleIndex].ui_elements" placeholder="用逗号分隔，如：按钮、输入框、下拉菜单" />
            </el-form-item>
            <el-form-item label="功能描述">
              <el-input v-model="moduleForms[currentModuleIndex].function_description" type="textarea" :rows="4" />
            </el-form-item>
            <el-form-item label="交互流程">
              <el-input v-model="moduleForms[currentModuleIndex].interaction_flow" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="测试关注点">
              <el-input v-model="moduleForms[currentModuleIndex].test_focus" type="textarea" :rows="3" placeholder="用逗号分隔" />
            </el-form-item>
            <el-form-item label="用例类型">
              <el-checkbox-group v-model="moduleForms[currentModuleIndex].case_types">
                <el-checkbox label="functional">功能测试</el-checkbox>
                <el-checkbox label="ui">UI交互测试</el-checkbox>
                <el-checkbox label="boundary">边界值测试</el-checkbox>
                <el-checkbox label="exception">异常场景测试</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="生成数量">
              <el-input-number
                v-model="moduleForms[currentModuleIndex].case_count"
                :min="1"
                :max="50"
                :step="1"
                :disabled="moduleForms[currentModuleIndex].smart_mode"
              />
              <el-switch
                v-model="moduleForms[currentModuleIndex].smart_mode"
                active-text="智能"
                style="margin-left: 12px"
              />
            </el-form-item>
          </el-form>
          <el-empty v-else description="暂无模块数据" />
        </div>
      </div>
      <el-empty v-else description="AI分析中或分析失败，请重试" />
      <template #footer>
        <el-button @click="showModuleReview = false">取消</el-button>
        <el-button type="primary" @click="confirmModules" :loading="confirming" :disabled="modules.length === 0">
          确认并生成用例
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled, Plus, PictureFilled, Document, Edit } from '@element-plus/icons-vue'
import api from '../api'

const router = useRouter()

const projects = ref([])
const aiConfigs = ref([])
const sprintNames = ref([])
const sprints = ref([])
const showCreateSprint = ref(false)
const newSprint = ref({ name: '' })
const fileList = ref([])
const generating = ref(false)
const showCreateProject = ref(false)
const showDescDialog = ref(false)
const currentFileIndex = ref(0)
const imageDescriptions = ref([])
const uploadMode = ref('image')
const currentDocContent = ref('')
const descImageScale = ref(1)
const descIsDragging = ref(false)
const descDragStart = ref({ x: 0, y: 0 })
const descDragOffset = ref({ x: 0, y: 0 })
const showModuleReview = ref(false)
const confirming = ref(false)
const moduleImageScale = ref(1)
const moduleIsDragging = ref(false)
const moduleDragStart = ref({ x: 0, y: 0 })
const moduleDragOffset = ref({ x: 0, y: 0 })
const modules = ref([])
const currentModuleIndex = ref(0)
const moduleForms = ref([])
const uploadedImageIds = ref([])
const analyzing = ref(false)
const editingAnalyzeModel = ref(false)
const editingGenModel = ref(false)

const form = ref({
  projectId: '',
  sprintId: '',
  analyzeConfigId: '',
  genConfigId: '',
  linkedAnalysis: true,
  caseTypes: ['functional', 'ui', 'boundary', 'exception'],
  caseCount: 10,
  smartMode: false
})

const newProject = ref({
  name: '',
  description: ''
})

const canGenerate = computed(() => {
  return form.value.projectId && fileList.value.length > 0 && form.value.analyzeConfigId && form.value.genConfigId
})

const currentAnalyzeModel = computed(() => {
  return aiConfigs.value.find(c => c.id === form.value.analyzeConfigId)
})

const currentGenModel = computed(() => {
  return aiConfigs.value.find(c => c.id === form.value.genConfigId)
})

const statusCount = ref({ pending: 0, approved: 0, total: 0 })

const approvalRate = computed(() => {
  const total = statusCount.value.total
  if (total === 0) return '0%'
  return Math.round(statusCount.value.approved / total * 100) + '%'
})

const filteredAIConfigs = computed(() => {
  return aiConfigs.value.filter(c => c.supports_vision !== false)
})

const currentFile = computed(() => {
  return fileList.value[currentFileIndex.value]
})

function isImageFile(file) {
  const name = file?.name || ''
  return /\.(jpg|jpeg|png|webp)$/i.test(name)
}

onMounted(() => {
  loadProjects()
  loadAIConfigs()
})

async function loadStatusCount() {
  loadSprints()
  if (!form.value.projectId) return
  try {
    const params = { project_id: form.value.projectId }
    if (form.value.sprintId) params.sprint_id = form.value.sprintId
    const [pendingRes, approvedRes] = await Promise.all([
      api.getPendingTestcases(params),
      api.getTestcases({ ...params, status: 'approved' })
    ])
    const pending = pendingRes.data.counts?.pending || 0
    const failed = pendingRes.data.counts?.failed || 0
    const approved = (approvedRes.data.testcases || []).length
    statusCount.value = {
      pending,
      approved,
      total: pending + approved + failed
    }
  } catch {
  }
}

async function loadProjects() {
  try {
    const res = await api.getProjects()
    projects.value = res.data.projects
  } catch (error) {
    ElMessage.error('加载项目列表失败')
  }
}

async function saveModelConfig(config) {
  if (!config || !config.id) return
  try {
    await api.updateAIConfig(config.id, {
      model: config.model,
      temperature: config.temperature,
      max_tokens: config.max_tokens
    })
  } catch {
  }
}

async function loadAIConfigs() {
  try {
    const res = await api.getAIConfigs()
    aiConfigs.value = (res.data.configs || []).filter(c => c.is_active)
  } catch (error) {
    ElMessage.error('加载AI配置失败')
  }
}

async function loadSprints() {
  if (!form.value.projectId) return
  try {
    const res = await api.getSprints({ project_id: form.value.projectId })
    sprints.value = res.data.sprints || []
  } catch {}
}

function onSprintChange() {
}

async function createSprint() {
  const name = newSprint.value.name.trim()
  if (!name) { ElMessage.warning('请输入迭代名称'); return }
  if (sprints.value.some(s => s.name === name)) { ElMessage.warning('迭代名称已存在'); return }
  try {
    const res = await api.createSprint({ project_id: form.value.projectId, name })
    sprints.value.unshift(res.data.sprint)
    form.value.sprintId = res.data.sprint.id
    showCreateSprint.value = false
    newSprint.value.name = ''
    ElMessage.success('迭代创建成功')
  } catch (error) {
    ElMessage.error('创建迭代失败')
  }
}

function getProviderLabel(config) {
  const name = config.provider || config.id
  const model = config.model ? ` / ${config.model}` : ''
  return `${name}${model}`
}

function switchMode(mode) {
  uploadMode.value = mode
  fileList.value = []
imageDescriptions.value = []
  currentDocContent.value = ''
  form.value.analyzeConfigId = ''
  form.value.genConfigId = ''
}

async function createProject() {
  const trimmedName = newProject.value.name.trim()
  if (!trimmedName) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (projects.value.some(p => p.name === trimmedName)) {
    ElMessage.warning('项目名称已存在')
    return
  }
  try {
    const res = await api.createProject({ name: trimmedName, description: newProject.value.description })
    projects.value.unshift(res.data.project)
    form.value.projectId = res.data.project.id
    showCreateProject.value = false
    newProject.value = { name: '', description: '' }
    ElMessage.success('项目创建成功')
    loadStatusCount()
  } catch (error) {
    const msg = error.response?.data?.error || '创建项目失败'
    ElMessage.error(msg)
  }
}

function handleFileChange(file, newFileList) {
  const oldLen = fileList.value.length
  fileList.value = newFileList
  if (newFileList.length > oldLen) {
    imageDescriptions.value.push('')
    currentFileIndex.value = newFileList.length - 1
    showDescDialog.value = true
    if (uploadMode.value === 'doc') {
      loadDocContent(newFileList.length - 1)
    }
  }
}

function handleFileRemove(file, newFileList) {
  fileList.value = newFileList
  imageDescriptions.value = newFileList.map((_, i) => imageDescriptions.value[i] || '')
}

function removeFile(index) {
  const newList = fileList.value.filter((_, i) => i !== index)
  handleFileRemove(null, newList)
}

function switchFile(index) {
  currentFileIndex.value = index
  descImageScale.value = 1
  descDragOffset.value = { x: 0, y: 0 }
  if (uploadMode.value === 'doc') {
    loadDocContent(index)
  }
}

async function loadDocContent(index) {
  const file = fileList.value[index]
  if (!file) return
  currentDocContent.value = ''
  try {
    if (file.raw && file.raw instanceof File) {
      const text = await file.raw.text()
      currentDocContent.value = text.slice(0, 10000)
    }
  } catch {
    currentDocContent.value = '无法读取文件内容'
  }
}

function handleDescZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  descImageScale.value = Math.max(0.5, Math.min(3, descImageScale.value + delta))
  if (descImageScale.value <= 1) {
    descDragOffset.value = { x: 0, y: 0 }
  }
}

function descZoomIn() {
  descImageScale.value = Math.min(3, descImageScale.value + 0.2)
}

function descZoomOut() {
  descImageScale.value = Math.max(0.5, descImageScale.value - 0.2)
  if (descImageScale.value <= 1) {
    descDragOffset.value = { x: 0, y: 0 }
  }
}

function startDescDrag(e) {
  if (descImageScale.value <= 1) return
  e.preventDefault()
  descIsDragging.value = true
  descDragStart.value = { x: e.clientX, y: e.clientY }
}

function onDescDrag(e) {
  if (!descIsDragging.value) return
  descDragOffset.value = {
    x: descDragOffset.value.x + (e.clientX - descDragStart.value.x) / descImageScale.value,
    y: descDragOffset.value.y + (e.clientY - descDragStart.value.y) / descImageScale.value
  }
  descDragStart.value = { x: e.clientX, y: e.clientY }
}

function endDescDrag() {
  descIsDragging.value = false
}

async function openDescDialog() {
  if (fileList.value.length === 0) return
  currentFileIndex.value = 0
  showDescDialog.value = true
  if (uploadMode.value === 'doc') {
    loadDocContent(0)
  }
}

function getFilePreviewUrl(index) {
  const file = fileList.value[index]
  return file?.url || ''
}

async function generateCases() {
  if (!canGenerate.value) return

  generating.value = true
  uploadedImageIds.value = []

  try {
    for (const file of fileList.value) {
      const formData = new FormData()
      formData.append('file', file.raw)
      formData.append('project_id', form.value.projectId)
      const uploadRes = await api.uploadFile(formData)
      uploadedImageIds.value.push(uploadRes.data.image.id)
    }

    analyzing.value = true
    try {
      const payload = {
        config_id: form.value.analyzeConfigId,
        description: imageDescriptions.value[0] || ''
      }
      if (form.value.linkedAnalysis && uploadedImageIds.value.length > 1) {
        payload.image_ids = uploadedImageIds.value
      } else {
        payload.image_id = uploadedImageIds.value[0]
      }
      const res = await api.analyzeImage(payload)
      modules.value = res.data.modules || []
      moduleForms.value = modules.value.map(m => ({
        module: m.module || '',
        ui_elements: (m.ui_elements || []).join(', '),
        function_description: m.function_description || '',
        interaction_flow: m.interaction_flow || '',
        test_focus: (m.test_focus || []).join(', '),
        case_types: [...form.value.caseTypes],
        case_count: form.value.caseCount,
        smart_mode: form.value.smartMode
      }))
      currentModuleIndex.value = 0
      showModuleReview.value = true
    } catch (err) {
      ElMessage.error('图片分析失败：' + (err.response?.data?.error || err.message))
    } finally {
      analyzing.value = false
    }

  } catch (error) {
    ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
  } finally {
    generating.value = false
  }
}

async function confirmModules() {
  confirming.value = true
  try {
    generating.value = true
    for (let i = 0; i < uploadedImageIds.value.length; i++) {
      for (let j = 0; j < moduleForms.value.length; j++) {
        const mod = moduleForms.value[j]
        await api.generateTestcases({
          image_id: uploadedImageIds.value[i],
          project_id: form.value.projectId,
          config_id: form.value.genConfigId,
          case_types: mod.case_types || [],
          case_count: mod.case_count || 10,
          description: imageDescriptions.value[i] || '',
          sprint_id: form.value.sprintId || '',
          modules: [{
            module: mod.module,
            ui_elements: mod.ui_elements.split(',').map(s => s.trim()).filter(Boolean),
            function_description: mod.function_description,
            interaction_flow: mod.interaction_flow,
            test_focus: mod.test_focus.split(',').map(s => s.trim()).filter(Boolean)
          }],
          smart_mode: mod.smart_mode || false
        })
      }
    }
    showModuleReview.value = false

    ElMessageBox.alert('生成完成，请到用例列表进行审批', '成功', {
      confirmButtonText: '前往查看',
      callback: () => {
        router.push({ path: '/cases', query: { project_id: form.value.projectId, tab: 'pending' } })
      }
    })
  } catch (error) {
    ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
  } finally {
    generating.value = false
    confirming.value = false
  }
}

function prevModule() {
  if (currentModuleIndex.value > 0) {
    currentModuleIndex.value--
  }
}

function nextModule() {
  if (currentModuleIndex.value < moduleForms.value.length - 1) {
    currentModuleIndex.value++
  }
}

function handleModuleZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  moduleImageScale.value = Math.max(0.5, Math.min(3, moduleImageScale.value + delta))
  if (moduleImageScale.value <= 1) moduleDragOffset.value = { x: 0, y: 0 }
}

function moduleZoomIn() { moduleImageScale.value = Math.min(3, moduleImageScale.value + 0.2) }
function moduleZoomOut() {
  moduleImageScale.value = Math.max(0.5, moduleImageScale.value - 0.2)
  if (moduleImageScale.value <= 1) moduleDragOffset.value = { x: 0, y: 0 }
}

function startModuleDrag(e) {
  if (moduleImageScale.value <= 1) return
  e.preventDefault()
  moduleIsDragging.value = true
  moduleDragStart.value = { x: e.clientX, y: e.clientY }
}

function onModuleDrag(e) {
  if (!moduleIsDragging.value) return
  moduleDragOffset.value = {
    x: moduleDragOffset.value.x + (e.clientX - moduleDragStart.value.x) / moduleImageScale.value,
    y: moduleDragOffset.value.y + (e.clientY - moduleDragStart.value.y) / moduleImageScale.value
  }
  moduleDragStart.value = { x: e.clientX, y: e.clientY }
}

function endModuleDrag() { moduleIsDragging.value = false }

function goToCases() {
  router.push({ path: '/cases', query: { project_id: form.value.projectId } })
}
</script>

<style scoped>
.upload-page {
  max-width: 1200px;
  margin: 0 auto;
}

.status-bar {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 32px;
  margin-bottom: 24px;
}

.status-item {
  text-align: center;
}

.status-num {
  font-size: 28px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -1px;
  display: block;
  transition: transform 0.2s, color 0.2s;
}

.status-item:hover .status-num {
  transform: scale(1.1);
  color: #0071e3;
}

.status-label {
  font-size: 10px;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-weight: 500;
  margin-top: 2px;
  display: block;
}

.status-divider {
  width: 0.5px;
  height: 36px;
  background: rgba(0, 0, 0, 0.12);
}

.mode-tabs {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.mode-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 24px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.06);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: #86868b;
  transition: all 0.25s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.mode-tab:hover {
  border-color: rgba(0, 0, 0, 0.12);
  color: #1d1d1f;
}

.mode-tab.active {
  background: #0071e3;
  border-color: #0071e3;
  color: #fff;
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.25);
}

.upload-card {
  border-radius: 16px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04), 0 12px 32px rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: #fff;
}

.upload-card :deep(.el-card__header) {
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  padding: 20px 32px;
}

.upload-card :deep(.el-card__body) {
  padding: 0;
}

.card-header {
  font-size: 18px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.3px;
}

.card-body-layout {
  display: flex;
  gap: 0;
  min-height: 420px;
}

.config-panel {
  flex: 4;
  padding: 28px 32px 20px;
  border-right: 1px solid rgba(0, 0, 0, 0.05);
}

.upload-panel {
  flex: 5;
  padding: 28px 32px 20px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  margin-bottom: 22px;
}

.upload-form :deep(.el-form-item) {
  margin-bottom: 20px;
}

.upload-form :deep(.el-form-item__label) {
  font-size: 13px;
}

.project-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.new-project-btn {
  color: #86868b !important;
  padding: 0 12px !important;
  flex-shrink: 0;
}

.new-project-btn:hover {
  color: #1d1d1f !important;
  background: rgba(0, 0, 0, 0.04) !important;
}

.form-hint {
  margin-left: 10px;
  color: #999;
  font-size: 12px;
}

.upload-dragger {
  width: 100%;
}

.upload-dragger :deep(.el-upload-dragger) {
  border: 1.5px dashed rgba(0, 0, 0, 0.1);
  border-radius: 16px;
  background: #fafafa;
  transition: all 0.3s ease;
  padding: 52px 24px;
  min-height: 280px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.upload-dragger :deep(.el-upload-dragger:hover) {
  border-color: rgba(0, 0, 0, 0.2);
  background: #f5f5f7;
}

.upload-dragger :deep(.el-icon--upload) {
  font-size: 40px;
  color: #86868b;
  margin-bottom: 12px;
  transition: color 0.3s;
}

.upload-dragger :deep(.el-upload-dragger:hover .el-icon--upload) {
  color: #0071e3;
}

.upload-dragger :deep(.el-upload__text) {
  font-size: 15px;
  color: #1d1d1f;
  font-weight: 500;
}

.upload-dragger :deep(.el-upload__text em) {
  color: #0071e3;
  font-style: normal;
}

.upload-dragger :deep(.el-upload__tip) {
  font-size: 12px;
  color: #86868b;
  margin-top: 8px;
}

.file-previews {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.file-preview-item {
  width: 72px;
  height: 72px;
  border-radius: 10px;
  overflow: hidden;
  position: relative;
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #f5f5f7;
}

.file-preview-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.file-doc-preview {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: #86868b;
}

.file-doc-preview span {
  font-size: 9px;
  text-align: center;
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60px;
}

.file-preview-badge {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 20px;
  height: 20px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.2s;
}

.file-preview-badge:hover {
  background: rgba(0, 0, 0, 0.75);
}

.action-buttons {
  display: flex;
  justify-content: center;
  gap: 12px;
  padding: 20px 32px 28px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
}

.action-buttons .el-button {
  margin: 0;
  padding: 12px 28px;
  font-size: 14px;
}

.action-buttons .el-button--primary {
  padding: 12px 36px;
  font-size: 15px;
}

.action-buttons .el-button--primary:hover {
  transform: translateY(-1px);
}

.btn-secondary {
  background: #f5f5f7 !important;
  border-color: rgba(0, 0, 0, 0.08) !important;
  color: #1d1d1f !important;
}

.btn-secondary:hover {
  background: #e8e8ed !important;
}

.desc-layout {
  display: flex;
  gap: 20px;
  height: 60vh;
}

.desc-image-panel {
  width: 50%;
  display: flex;
  flex-direction: column;
  background: #fafafa;
  border-radius: 14px;
  padding: 12px;
  border: 0.5px solid rgba(0, 0, 0, 0.06);
}

.desc-image-viewer {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border-radius: 10px;
  overflow: hidden;
  border: 0.5px solid rgba(0, 0, 0, 0.04);
}

.desc-image-viewer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.2s ease;
  user-select: none;
}

.desc-zoom-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}

.desc-doc-viewer {
  flex: 1;
  background: #fff;
  border-radius: 10px;
  border: 0.5px solid rgba(0, 0, 0, 0.04);
  overflow: auto;
  padding: 16px;
}

.desc-doc-viewer pre {
  margin: 0;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #1d1d1f;
  white-space: pre-wrap;
  word-break: break-word;
}

.desc-image-thumbs {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.desc-image-thumbs img {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border: 2px solid rgba(0, 0, 0, 0.06);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s;
}

.desc-image-thumbs img.active {
  border-color: #0071e3;
}

.desc-image-thumbs img:hover {
  border-color: rgba(0, 0, 0, 0.15);
}

.desc-doc-thumb {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12px;
  color: #86868b;
  background: #f5f5f7;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.desc-doc-thumb.active {
  background: #0071e3;
  color: #fff;
}

.desc-doc-thumb:hover:not(.active) {
  background: #e8e8ed;
  color: #1d1d1f;
}

.desc-image-info {
  text-align: center;
  font-size: 12px;
  color: #86868b;
  margin-top: 6px;
}

.desc-form-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.desc-form-label {
  font-size: 14px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 12px;
  letter-spacing: -0.2px;
}

.desc-form-panel .el-textarea {
  flex: 1;
}

.desc-form-panel .el-textarea :deep(.el-textarea__inner) {
  height: 100% !important;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
  background: #f5f5f7;
  border: 0.5px solid rgba(0, 0, 0, 0.08);
}

.desc-form-panel .el-textarea :deep(.el-textarea__inner:focus) {
  background: #fff;
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.1);
}

.module-review-layout {
  display: flex;
  gap: 20px;
  height: 70vh;
}

.module-review-image-panel {
  width: 45%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.module-image-viewer {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border-radius: 4px;
  overflow: hidden;
}

.module-review-form-panel {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.module-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.progress-text {
  font-weight: 500;
  color: #409EFF;
  margin-right: auto;
}

.module-form .el-form-item {
  margin-bottom: 16px;
}

.module-zoom-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}

.model-preview-card {
  margin: -4px 0 16px 100px;
  padding: 12px 16px;
  background: #fafbfc;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,0.08);
  font-size: 13px;
}

.model-preview-row {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}
.model-preview-row:last-child { margin-bottom: 0; }

.model-preview-label {
  color: #909399;
  margin-right: 8px;
  font-size: 12px;
  flex-shrink: 0;
}

.model-preview-value {
  color: #303133;
  font-weight: 500;
}

.model-preview-name {
  font-size: 14px;
  font-weight: 600;
  color: #1d1d1f;
  flex: 1;
}

.model-preview-url {
  font-size: 11px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.model-preview-divider {
  height: 1px;
  background: rgba(0,0,0,0.06);
  margin: 6px 0;
}

.edit-icon {
  margin-left: 6px;
  cursor: pointer;
  color: #909399;
  font-size: 14px;
}
.edit-icon:hover { color: #0071e3; }

.linked-analysis-bar {
  margin: -8px 0 16px 100px;
  display: flex;
  align-items: center;
}
</style>