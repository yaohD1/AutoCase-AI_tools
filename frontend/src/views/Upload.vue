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
        <span class="status-label">AI用例通过率</span>
      </div>
    </div>

    <el-card class="upload-card">
      <template #header>
        <div class="card-header">
          <span>上传Figma设计图</span>
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

            <el-form-item label="AI服务">
              <el-select v-model="form.aiProvider" placeholder="选择AI服务" style="width: 100%">
                <el-option
                  v-for="config in aiConfigs"
                  :key="config.provider"
                  :label="getProviderLabel(config)"
                  :value="config.provider"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="用例类型">
              <el-checkbox-group v-model="form.caseTypes">
                <el-checkbox label="functional">功能测试</el-checkbox>
                <el-checkbox label="ui">UI交互测试</el-checkbox>
                <el-checkbox label="boundary">边界值测试</el-checkbox>
                <el-checkbox label="exception">异常场景测试</el-checkbox>
              </el-checkbox-group>
            </el-form-item>

            <el-form-item label="生成数量">
              <el-input-number
                v-model="form.caseCount"
                :min="1"
                :max="50"
                :step="1"
              />
              <span class="form-hint">每种类型的用例数量（建议5-15个）</span>
            </el-form-item>
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
            list-type="picture"
            accept=".jpg,.jpeg,.png,.webp"
            multiple
          >
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">
              拖拽图片到此处或 <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 JPG/PNG/WebP 格式，单个文件不超过 10MB
              </div>
            </template>
          </el-upload>

          <div class="file-previews" v-if="fileList.length > 0">
            <div
              v-for="(file, index) in fileList"
              :key="file.uid"
              class="file-preview-item"
            >
              <img :src="file.url" />
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

    <el-dialog v-model="showDescDialog" title="填写功能介绍" width="1200px" top="3vh" :close-on-click-modal="false">
      <div class="desc-layout" v-if="fileList.length > 0">
        <div class="desc-image-panel">
          <div class="desc-image-viewer">
            <img :src="getFilePreviewUrl(currentFileIndex)" />
          </div>
          <div class="desc-image-thumbs" v-if="fileList.length > 1">
            <img
              v-for="(file, index) in fileList"
              :key="file.uid"
              :src="getFilePreviewUrl(index)"
              :class="{ active: index === currentFileIndex }"
              @click="currentFileIndex = index"
            />
          </div>
          <div class="desc-image-info">
            {{ currentFileIndex + 1 }} / {{ fileList.length }}
          </div>
        </div>
        <div class="desc-form-panel">
          <div class="desc-form-label">功能介绍（可选，帮助AI理解测试目标）</div>
          <el-input
            v-model="imageDescriptions[currentFileIndex]"
            type="textarea"
            :rows="20"
            placeholder="请描述这张设计图的功能、业务逻辑、您关注的测试重点等...&#10;&#10;例如：&#10;- 这是登录注册流程，包含手机号验证和微信一键登录&#10;- 重点关注密码校验规则和验证码发送频率限制&#10;- 需要测试多种登录方式的切换逻辑"
          />
        </div>
      </div>
      <el-empty v-else description="请先上传图片" />
      <template #footer>
        <el-button @click="showDescDialog = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled, Plus } from '@element-plus/icons-vue'
import api from '../api'
import { useCaseStore } from '../stores/caseStore'

const router = useRouter()
const store = useCaseStore()

const projects = ref([])
const aiConfigs = ref([])
const fileList = ref([])
const generating = ref(false)
const showCreateProject = ref(false)
const showDescDialog = ref(false)
const currentFileIndex = ref(0)
const imageDescriptions = ref([])

const form = ref({
  projectId: '',
  aiProvider: '',
  caseTypes: [],
  caseCount: 10
})

const newProject = ref({
  name: '',
  description: ''
})

const canGenerate = computed(() => {
  return form.value.projectId && fileList.value.length > 0
})

const statusCount = ref({ pending: 0, approved: 0, total: 0 })

const approvalRate = computed(() => {
  const total = statusCount.value.total
  if (total === 0) return '0%'
  return Math.round(statusCount.value.approved / total * 100) + '%'
})

onMounted(() => {
  loadProjects()
  loadAIConfigs()
})

async function loadStatusCount() {
  if (!form.value.projectId) return
  try {
    const [pendingRes, approvedRes] = await Promise.all([
      api.getPendingTestcases({ project_id: form.value.projectId }),
      api.getTestcases({ project_id: form.value.projectId, status: 'approved' })
    ])
    const pending = pendingRes.data.counts?.pending || 0
    const failed = pendingRes.data.counts?.failed || 0
    const approved = (approvedRes.data.testcases || []).length
    const totalGen = pendingRes.data.counts?.total_generated || 0
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

async function loadAIConfigs() {
  try {
    const res = await api.getAIConfigs()
    aiConfigs.value = (res.data.configs || []).filter(c => c.is_active)
  } catch (error) {
    ElMessage.error('加载AI配置失败')
  }
}

function getProviderLabel(config) {
  const nameMap = { volcengine: '豆包', kimi: 'Kimi-2.6' }
  const name = nameMap[config.provider] || config.provider
  const model = config.model ? ` / ${config.model}` : ''
  return `${name}${model}`
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
  }
}

function handleFileRemove(file, newFileList) {
  fileList.value = newFileList
  imageDescriptions.value = newFileList.map((_, i) => imageDescriptions.value[i] || '')
}

function removeFile(index) {
  const newList = fileList.value.filter((_, i) => i !== index)
  const removedFile = fileList.value[index]
  handleFileRemove(removedFile, newList)
}

async function generateCases() {
  if (!canGenerate.value) {
    return
  }

  generating.value = true

  try {
    const uploadedImageIds = []

    for (const file of fileList.value) {
      const formData = new FormData()
      formData.append('file', file.raw)
      formData.append('project_id', form.value.projectId)

      const uploadRes = await api.uploadFile(formData)
      uploadedImageIds.push(uploadRes.data.image.id)
    }

    for (let i = 0; i < uploadedImageIds.length; i++) {
      await api.generateTestcases({
        image_id: uploadedImageIds[i],
        project_id: form.value.projectId,
        provider: form.value.aiProvider,
        case_types: form.value.caseTypes,
        case_count: form.value.caseCount,
        description: imageDescriptions.value[i] || ''
      })
    }

    ElMessageBox.alert('生成完成，请到用例列表进行审批', '成功', {
      confirmButtonText: '前往查看',
      callback: () => {
        router.push({
          path: '/cases',
          query: { project_id: form.value.projectId }
        })
      }
    })

  } catch (error) {
    ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
  } finally {
    generating.value = false
  }
}

function openDescDialog() {
  if (fileList.value.length === 0) return
  currentFileIndex.value = 0
  showDescDialog.value = true
}

function getFilePreviewUrl(index) {
  const file = fileList.value[index]
  return file?.url || ''
}

function goToCases() {
  router.push({
    path: '/cases',
    query: { project_id: form.value.projectId }
  })
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
</style>