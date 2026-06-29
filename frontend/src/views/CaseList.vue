<template>
  <div class="case-list-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>测试用例列表</span>
          <div class="header-actions">
            <el-button type="warning" @click="openReviewDialog">
              待审用例
              <el-badge :value="pendingCount" :hidden="pendingCount === 0" class="pending-badge" />
            </el-button>
            <el-select v-model="selectedProject" placeholder="选择项目" class="project-select" @change="onProjectChange">
              <el-option
                v-for="project in projects"
                :key="project.id"
                :label="project.name"
                :value="project.id"
              />
            </el-select>
            <el-select v-model="selectedSprint" placeholder="选择迭代" class="sprint-select" @change="loadTestcases">
              <el-option
                v-for="sprint in sprints"
                :key="sprint.id"
                :label="sprint.name"
                :value="sprint.id"
              />
            </el-select>
            <el-select v-model="selectedImage" placeholder="原型图筛选" class="image-select" @change="loadTestcases" clearable>
              <el-option
                v-for="img in imageOptions"
                :key="img.image_id"
                :label="`${img.name}（${formatTime(img.time)}）`"
                :value="img.image_id"
              />
            </el-select>
            <el-button type="danger" @click="batchDelete" :disabled="selectedCases.length === 0">
              批量删除 ({{ selectedCases.length }})
            </el-button>
            <el-button type="primary" @click="exportXmind" :loading="exporting" :disabled="testcases.length === 0">
              导出XMind
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="paginatedTestcases" style="width: 100%" v-loading="loading"
               @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="55" />
        <el-table-column prop="module" label="模块" width="150" />
        <el-table-column prop="test_point" label="测试点" width="150" />
        <el-table-column prop="title" label="用例标题" min-width="200">
          <template #default="{ row }">
            {{ row.title }}
          </template>
        </el-table-column>
        <el-table-column prop="case_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="getCaseTypeTag(row.case_type)">
              {{ getCaseTypeName(row.case_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <el-tag :type="getPriorityTag(row.priority)">
              {{ row.priority }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right" align="center">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button text @click="viewTestcase(row)">查看</el-button>
              <el-button text type="danger" @click="deleteTestcase(row.id)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadTestcases"
          @current-change="loadTestcases"
        />
      </div>
    </el-card>

    <el-dialog v-model="showDetail" title="用例详情" width="1200px" top="2vh" @closed="showDetail = false">
      <div v-if="currentTestcase.id" class="detail-layout">
        <div class="detail-image-panel">
          <div v-if="currentTestcase.image_source && /[\._](jpg|jpeg|png|webp)$/i.test(currentTestcase.image_source)" class="detail-image-viewer"
               @wheel="handleDetailZoom"
               @mousedown="startDetailDrag"
               @mousemove="onDetailDrag"
               @mouseup="endDetailDrag"
               @mouseleave="endDetailDrag"
          >
            <img
              :src="`/api/images/${currentTestcase.image_source}`"
              :style="{
                transform: `scale(${detailImageScale}) translate(${detailDragOffset.x}px, ${detailDragOffset.y}px)`,
                cursor: detailIsDragging ? 'grabbing' : detailImageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
          </div>
          <div v-else class="detail-no-image">
            <span>未关联原型图</span>
          </div>
          <div class="zoom-controls" v-if="currentTestcase.image_source && /[\._](jpg|jpeg|png|webp)$/i.test(currentTestcase.image_source)">
            <el-button circle size="small" @click="detailImageScale = Math.max(0.2, detailImageScale - 0.2)">-</el-button>
            <span>{{ Math.round(detailImageScale * 100) }}%</span>
            <el-button circle size="small" @click="detailImageScale = Math.min(3, detailImageScale + 0.2)">+</el-button>
            <el-button size="small" @click="detailImageScale = 1; detailDragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="detail-form-panel">
          <el-form :model="detailForm" label-width="100px">
            <el-form-item label="模块">
              <el-input v-model="detailForm.module" />
            </el-form-item>
            <el-form-item label="测试点">
              <el-input v-model="detailForm.test_point" />
            </el-form-item>
            <el-form-item label="用例标题">
              <el-input v-model="detailForm.title" />
            </el-form-item>
            <el-form-item label="优先级">
              <el-select v-model="detailForm.priority">
                <el-option label="P0" value="P0" />
                <el-option label="P1" value="P1" />
                <el-option label="P2" value="P2" />
              </el-select>
            </el-form-item>
            <el-form-item label="前置条件">
              <el-input v-model="detailForm.preconditions" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="测试步骤">
              <div style="width:100%">
                <div v-for="(step, index) in detailForm.stepsList" :key="index" class="step-row">
                  <span class="step-index">{{ index + 1 }}.</span>
                  <el-input v-model="detailForm.stepsList[index]" type="textarea" :rows="2" style="flex:1" />
                  <el-button text type="danger" @click="removeDetailStep(index)" :disabled="detailForm.stepsList.length <= 1" style="align-self:flex-start;margin-top:6px">×</el-button>
                </div>
                <el-button size="small" @click="addDetailStep" style="margin-top:8px">+ 添加步骤</el-button>
              </div>
            </el-form-item>
            <el-form-item label="预期结果">
              <el-input v-model="detailForm.expected" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="用例类型">
              <el-select v-model="detailForm.case_type">
                <el-option label="功能测试" value="functional" />
                <el-option label="UI交互测试" value="ui" />
                <el-option label="边界值测试" value="boundary" />
                <el-option label="异常场景测试" value="exception" />
              </el-select>
            </el-form-item>
            <el-form-item label="AI服务">
              <span class="detail-readonly">{{ currentTestcase.ai_provider || '-' }}</span>
            </el-form-item>
            <el-form-item label="创建时间">
              <span class="detail-readonly">{{ currentTestcase.created_at || '-' }}</span>
            </el-form-item>
          </el-form>
        </div>
      </div>
      <template #footer>
        <el-button @click="showDetail = false">取消</el-button>
        <el-button type="primary" @click="saveDetailTestcase" :loading="savingDetail">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showReview"
      title="审批用例"
      width="1800px"
      top="2vh"
      :close-on-click-modal="false"
      @closed="onReviewClosed"
    >
      <div v-if="reviewForm.id" class="review-layout">
        <div class="review-image-panel">
          <div v-if="isCurrentFileImage" class="image-viewer"
               @wheel="handleZoom"
               @mousedown="startDrag"
               @mousemove="onDrag"
               @mouseup="endDrag"
               @mouseleave="endDrag"
          >
            <img
              :src="`/api/images/${currentImageFile}`"
              :style="{
                transform: `scale(${imageScale}) translate(${dragOffset.x}px, ${dragOffset.y}px)`,
                cursor: isDragging ? 'grabbing' : imageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
          </div>
          <div v-else-if="!currentImageFile && projectImages.length === 0" class="no-image">
            <span>未关联原型图</span>
          </div>
          <div v-else class="doc-viewer">
            <pre>{{ reviewDocContent }}</pre>
          </div>
          <div class="image-thumbs" v-if="projectImages.length > 1">
            <img
              v-for="img in projectImages"
              :key="img.filename"
              :src="`/api/images/${img.filename}`"
              :class="{ active: img.filename === currentImageFile }"
              @click="switchImage(img.filename)"
            />
          </div>
          <div class="zoom-controls" v-if="isCurrentFileImage">
            <el-button circle size="small" @click="zoomOut">-</el-button>
            <span>{{ Math.round(imageScale * 100) }}%</span>
            <el-button circle size="small" @click="zoomIn">+</el-button>
            <el-button size="small" @click="imageScale = 1; dragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="review-form-panel">
          <el-form :model="reviewForm" label-width="100px">
            <el-row :gutter="20">
              <el-col :span="12">
                <el-form-item label="模块">
                  <el-input v-model="reviewForm.module" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="测试点">
                  <el-input v-model="reviewForm.test_point" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="用例标题">
              <el-input v-model="reviewForm.title" />
            </el-form-item>
            <el-row :gutter="20">
              <el-col :span="12">
                <el-form-item label="优先级">
                  <el-select v-model="reviewForm.priority" style="width: 100%">
                    <el-option label="P0" value="P0" />
                    <el-option label="P1" value="P1" />
                    <el-option label="P2" value="P2" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="用例类型">
                  <el-select v-model="reviewForm.case_type" style="width: 100%">
                    <el-option label="功能测试" value="functional" />
                    <el-option label="UI测试" value="ui" />
                    <el-option label="边界测试" value="boundary" />
                    <el-option label="异常测试" value="exception" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="前置条件">
              <el-input v-model="reviewForm.preconditions" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="测试步骤">
              <div class="steps-editor">
                <div v-for="(step, index) in reviewForm.stepsList" :key="index" class="step-item">
                  <span class="step-index">{{ index + 1 }}.</span>
                  <el-input
                    v-model="reviewForm.stepsList[index]"
                    placeholder="请输入测试步骤"
                    style="flex: 1"
                  />
                  <el-button type="danger" :icon="Delete" circle size="small" @click="removeStep(index)" />
                </div>
                <el-button text @click="addStep" class="add-step-btn">
                  + 添加步骤
                </el-button>
              </div>
            </el-form-item>
            <el-form-item label="预期结果">
              <el-input v-model="reviewForm.expected" type="textarea" :rows="2" />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <el-empty v-else description="没有待审用例" />

      <template #footer>
        <div class="review-footer">
          <span class="progress-info">
            <el-button size="small" :disabled="currentReviewIndex <= 0" @click="moveToPrev">上一个</el-button>
            审批进度: {{ reviewForm.id ? currentReviewIndex + 1 : 0 }} / {{ allPendingCount }}
            <el-button size="small" :disabled="currentReviewIndex >= allPendingTestcases.length - 1" @click="moveToNextManual">下一个</el-button>
          </span>
          <div class="review-actions">
            <el-button type="danger" @click="handleFail" :loading="failing" :disabled="!reviewForm.id">
              不通过
            </el-button>
            <el-button type="success" @click="handlePass" :loading="passing" :disabled="!reviewForm.id">
              通过
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import api from '../api'

const route = useRoute()
const loading = ref(false)
const exporting = ref(false)
const projects = ref([])
const testcases = ref([])
const selectedProject = ref('')
const selectedSprint = ref('')
const selectedImage = ref('')
const sprints = ref([])
const imageOptions = ref([])
const showDetail = ref(false)
const currentTestcase = ref({})
const savingDetail = ref(false)
const detailForm = reactive({
  module: '',
  test_point: '',
  title: '',
  priority: 'P2',
  preconditions: '',
  stepsList: [''],
  expected: '',
  case_type: 'functional'
})
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const selectedCases = ref([])

const showReview = ref(false)
const currentReviewIndex = ref(0)
const allPendingTestcases = ref([])
const allPendingCount = ref(0)
const pendingCount = ref(0)
const passing = ref(false)
const failing = ref(false)

const currentImageFile = ref('')
const imageScale = ref(1)
const projectImages = ref([])
const isDragging = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const dragOffset = ref({ x: 0, y: 0 })
const reviewDocContent = ref('')
const isCurrentFileImage = ref(true)

const detailImageScale = ref(1)
const detailIsDragging = ref(false)
const detailDragStart = ref({ x: 0, y: 0 })
const detailDragOffset = ref({ x: 0, y: 0 })

const reviewForm = reactive({
  id: '',
  module: '',
  test_point: '',
  title: '',
  priority: 'P2',
  preconditions: '',
  stepsList: [],
  expected: '',
  case_type: 'functional'
})

const paginatedTestcases = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return testcases.value.slice(start, end)
})

onMounted(() => {
  if (route.query.tab) {
    activeTab.value = route.query.tab
  }
  loadProjects()
})

watch(() => route.path, () => {
  if (route.path === '/cases') {
    loadProjects()
  }
})

async function loadProjects() {
  try {
    const res = await api.getProjects()
    projects.value = res.data.projects

    if (route.query.project_id) {
      selectedProject.value = route.query.project_id
    } else if (projects.value.length > 0) {
      selectedProject.value = projects.value[0].id
    }

    if (selectedProject.value) {
      await loadSprints()
      if (route.query.sprint_id) {
        selectedSprint.value = route.query.sprint_id
      }
      loadTestcases()
      loadPendingCount()
    }
  } catch (error) {
    ElMessage.error('加载项目列表失败')
  }
}

async function loadSprints() {
  if (!selectedProject.value) return
  try {
    const res = await api.getSprints({ project_id: selectedProject.value })
    sprints.value = res.data.sprints || []
  } catch {}
}

function onProjectChange() {
  selectedSprint.value = ''
  selectedImage.value = ''
  loadSprints()
  loadTestcases()
}

async function loadTestcases() {
  if (!selectedProject.value) {
    testcases.value = []
    total.value = 0
    return
  }

  loading.value = true
  testcases.value = []
  total.value = 0

  try {
    const params = {
      project_id: selectedProject.value,
      status: 'approved'
    }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    if (selectedImage.value) params.image_id = selectedImage.value
    const res = await api.getTestcases(params)
    testcases.value = res.data.testcases
    total.value = testcases.value.length
    if (res.data.pending_count !== undefined) {
      pendingCount.value = res.data.pending_count
    }
    imageOptions.value = res.data.image_options || []
  } catch (error) {
    testcases.value = []
    total.value = 0
    ElMessage.error('加载用例列表失败')
  } finally {
    loading.value = false
  }
}

async function loadPendingCount() {
  if (!selectedProject.value) return
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const res = await api.getPendingTestcases(params)
    pendingCount.value = res.data.testcases.length
  } catch {
  }
}

function viewTestcase(testcase) {
  currentTestcase.value = testcase
  const steps = parseSteps(testcase.steps || '[]')
  detailForm.module = testcase.module || ''
  detailForm.test_point = testcase.test_point || ''
  detailForm.title = testcase.title || ''
  detailForm.priority = testcase.priority || 'P2'
  detailForm.preconditions = testcase.preconditions || ''
  detailForm.stepsList = steps.length > 0 ? steps : ['']
  detailForm.expected = testcase.expected || ''
  detailForm.case_type = testcase.case_type || 'functional'
  detailImageScale.value = 1
  detailDragOffset.value = { x: 0, y: 0 }
  showDetail.value = true
}

function addDetailStep() {
  detailForm.stepsList.push('')
}

function removeDetailStep(index) {
  detailForm.stepsList.splice(index, 1)
}

async function saveDetailTestcase() {
  savingDetail.value = true
  try {
    const data = {
      module: detailForm.module,
      test_point: detailForm.test_point,
      title: detailForm.title,
      priority: detailForm.priority,
      preconditions: detailForm.preconditions,
      steps: JSON.stringify(detailForm.stepsList.filter(s => s.trim())),
      expected: detailForm.expected,
      case_type: detailForm.case_type
    }
    await api.updateTestcase(currentTestcase.value.id, data)
    ElMessage.success('保存成功')
    showDetail.value = false
    loadTestcases()
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    savingDetail.value = false
  }
}

async function deleteTestcase(id) {
  try {
    await ElMessageBox.confirm('确定删除该用例吗？', '提示', {
      type: 'warning'
    })

    await api.deleteTestcase(id)
    ElMessage.success('删除成功')
    loadTestcases()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

function handleSelectionChange(selection) {
  selectedCases.value = selection
}

async function batchDelete() {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请先选择要删除的用例')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedCases.value.length} 个用例吗？`,
      '批量删除确认',
      { type: 'warning' }
    )

    const ids = selectedCases.value.map(c => c.id)
    await api.batchDeleteTestcases(ids)

    ElMessage.success(`成功删除 ${selectedCases.value.length} 个用例`)
    selectedCases.value = []
    loadTestcases()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

async function exportXmind() {
  exporting.value = true

  try {
    const res = await api.exportXmind({
      project_id: selectedProject.value
    })

    const downloadRes = await api.downloadFile(res.data.filename)
    const url = window.URL.createObjectURL(new Blob([downloadRes.data]))
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', res.data.filename)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)

    ElMessage.success('导出成功')
  } catch (error) {
    ElMessage.error('导出失败')
  } finally {
    exporting.value = false
  }
}

function parseSteps(steps) {
  if (!steps) return []
  try {
    return JSON.parse(steps)
  } catch {
    return steps.split('\n').filter(s => s.trim())
  }
}

function formatTime(isoString) {
  if (!isoString) return ''
  const d = new Date(isoString)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

function parseStepsForEdit(steps) {
  if (!steps) return ['']
  try {
    const parsed = JSON.parse(steps)
    return parsed.map(s => s.replace(/^步骤\d*[：:]?\s*/, ''))
  } catch {
    return steps.split('\n').filter(s => s.trim())
  }
}

async function openReviewDialog() {
  if (!selectedProject.value) {
    ElMessage.warning('请先选择项目')
    return
  }

  loading.value = true
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const res = await api.getPendingTestcases(params)
    allPendingTestcases.value = res.data.testcases
    allPendingCount.value = allPendingTestcases.value.length
    projectImages.value = res.data.images || []

    if (allPendingTestcases.value.length === 0) {
      ElMessage.info('没有待审用例')
      return
    }

    currentReviewIndex.value = 0
    loadTestCaseToForm(allPendingTestcases.value[0])
    showReview.value = true
  } catch {
    ElMessage.error('加载待审用例失败')
  } finally {
    loading.value = false
  }
}

function loadTestCaseToForm(testcase) {
  reviewForm.id = testcase.id
  reviewForm.module = testcase.module || ''
  reviewForm.test_point = testcase.test_point || ''
  reviewForm.title = testcase.title || ''
  reviewForm.priority = testcase.priority || 'P2'
  reviewForm.preconditions = testcase.preconditions || ''
  reviewForm.stepsList = parseStepsForEdit(testcase.steps)
  if (reviewForm.stepsList.length === 0) {
    reviewForm.stepsList = ['']
  }
  reviewForm.expected = testcase.expected || ''
  reviewForm.case_type = testcase.case_type || 'functional'

  if (testcase.image_source) {
    currentImageFile.value = testcase.image_source
    isCurrentFileImage.value = /[\._](jpg|jpeg|png|webp)$/i.test(testcase.image_source)
  } else if (projectImages.value.length > 0) {
    currentImageFile.value = projectImages.value[0].filename
    isCurrentFileImage.value = /[\._](jpg|jpeg|png|webp)$/i.test(projectImages.value[0].filename)
  } else {
    currentImageFile.value = ''
    isCurrentFileImage.value = false
  }
  imageScale.value = 1

  if (!isCurrentFileImage.value && currentImageFile.value) {
    loadReviewDocContent(currentImageFile.value)
  } else {
    reviewDocContent.value = ''
  }
}

async function loadReviewDocContent(filename) {
  try {
    const res = await api.getDocumentContent(filename)
    reviewDocContent.value = res.data.content || ''
  } catch {
    reviewDocContent.value = '加载失败'
  }
}

function switchImage(filename) {
  currentImageFile.value = filename
  imageScale.value = 1
  dragOffset.value = { x: 0, y: 0 }
}

function zoomIn() {
  imageScale.value = Math.min(imageScale.value + 0.25, 3)
}

function zoomOut() {
  imageScale.value = Math.max(imageScale.value - 0.25, 0.25)
}

function handleZoom(e) {
  e.preventDefault()
  e.deltaY < 0 ? zoomIn() : zoomOut()
}

function startDrag(e) {
  if (imageScale.value <= 1) return
  isDragging.value = true
  dragStart.value = { x: e.clientX, y: e.clientY }
}

function onDrag(e) {
  if (!isDragging.value) return
  dragOffset.value = {
    x: dragOffset.value.x + (e.clientX - dragStart.value.x) / imageScale.value,
    y: dragOffset.value.y + (e.clientY - dragStart.value.y) / imageScale.value
  }
  dragStart.value = { x: e.clientX, y: e.clientY }
}

function endDrag() {
  isDragging.value = false
}

function handleDetailZoom(e) {
  e.preventDefault()
  e.deltaY < 0 ? detailImageScale.value = Math.min(detailImageScale.value + 0.25, 3) : detailImageScale.value = Math.max(detailImageScale.value - 0.25, 0.25)
}

function startDetailDrag(e) {
  if (detailImageScale.value <= 1) return
  detailIsDragging.value = true
  detailDragStart.value = { x: e.clientX, y: e.clientY }
}

function onDetailDrag(e) {
  if (!detailIsDragging.value) return
  detailDragOffset.value = {
    x: detailDragOffset.value.x + (e.clientX - detailDragStart.value.x) / detailImageScale.value,
    y: detailDragOffset.value.y + (e.clientY - detailDragStart.value.y) / detailImageScale.value
  }
  detailDragStart.value = { x: e.clientX, y: e.clientY }
}

function endDetailDrag() {
  detailIsDragging.value = false
}

function addStep() {
  reviewForm.stepsList.push('')
}

function removeStep(index) {
  if (reviewForm.stepsList.length <= 1) {
    ElMessage.warning('至少保留一个步骤')
    return
  }
  reviewForm.stepsList.splice(index, 1)
}

function buildStepsForSubmit() {
  return JSON.stringify(
    reviewForm.stepsList
      .filter(s => s.trim())
      .map(s => `步骤：${s.trim()}`)
  )
}

async function handlePass() {
  if (!reviewForm.title.trim()) {
    ElMessage.warning('用例标题不能为空')
    return
  }

  passing.value = true
  try {
    const data = {
      module: reviewForm.module,
      test_point: reviewForm.test_point,
      title: reviewForm.title,
      priority: reviewForm.priority,
      preconditions: reviewForm.preconditions,
      steps: buildStepsForSubmit(),
      expected: reviewForm.expected,
      case_type: reviewForm.case_type
    }

    await api.approveTestcase(reviewForm.id, data)
    ElMessage.success('审批通过')
    moveToNext()
  } catch (error) {
    ElMessage.error('审批失败: ' + (error.response?.data?.error || error.message))
  } finally {
    passing.value = false
  }
}

async function handleFail() {
  failing.value = true
  try {
    await api.failTestcase(reviewForm.id)
    ElMessage.warning('已标记为不通过')
    moveToNext()
  } catch (error) {
    ElMessage.error('操作失败: ' + (error.response?.data?.error || error.message))
  } finally {
    failing.value = false
  }
}

function moveToNext() {
  currentReviewIndex.value++

  if (currentReviewIndex.value >= allPendingTestcases.value.length) {
    ElMessage.success('所有待审用例已完成审批')
    showReview.value = false
    loadPendingCount()
    loadTestcases()
    return
  }

  loadTestCaseToForm(allPendingTestcases.value[currentReviewIndex.value])
}

function moveToPrev() {
  if (currentReviewIndex.value <= 0) return
  currentReviewIndex.value--
  loadTestCaseToForm(allPendingTestcases.value[currentReviewIndex.value])
}

function moveToNextManual() {
  if (currentReviewIndex.value >= allPendingTestcases.value.length - 1) return
  currentReviewIndex.value++
  loadTestCaseToForm(allPendingTestcases.value[currentReviewIndex.value])
}

function onReviewClosed() {
  reviewForm.id = ''
  loadPendingCount()
  loadTestcases()
}

function getCaseTypeName(type) {
  const map = {
    functional: '功能测试',
    ui: 'UI测试',
    boundary: '边界测试',
    exception: '异常测试'
  }
  return map[type] || type
}

function getCaseTypeTag(type) {
  const map = {
    functional: '',
    ui: 'success',
    boundary: 'warning',
    exception: 'danger'
  }
  return map[type] || ''
}

function getPriorityTag(priority) {
  const map = {
    P0: 'danger',
    P1: 'warning',
    P2: ''
  }
  return map[priority] || ''
}
</script>

<style scoped>
.case-list-page {
  max-width: 1400px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pending-badge {
  margin-left: 6px;
}

.add-step-btn {
  color: #86868b !important;
  font-size: 13px;
}

.add-step-btn:hover {
  color: #1d1d1f !important;
  background: rgba(0, 0, 0, 0.04) !important;
}

.project-select, .sprint-select {
  width: 200px;
}

.pagination {
  margin-top: 20px;
  text-align: right;
}

.steps-editor {
  width: 100%;
}

.step-item,
.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.step-index {
  font-weight: bold;
  color: #606266;
  min-width: 24px;
  text-align: right;
}

.review-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.progress-info {
  color: #909399;
  font-size: 14px;
}

.review-actions {
  display: flex;
  gap: 10px;
}

.review-layout {
  display: flex;
  gap: 20px;
  height: 78vh;
}

.review-image-panel {
  width: 55%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.image-viewer {
  flex: 1;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8e8e8;
  border-radius: 4px;
  min-height: 0;
  user-select: none;
}

.image-viewer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.2s ease;
}

.doc-viewer {
  flex: 1;
  overflow: auto;
  padding: 16px;
  background: #fff;
  border-radius: 4px;
  border: 0.5px solid rgba(0, 0, 0, 0.04);
}

.no-image {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
  border-radius: 4px;
  color: #909399;
  font-size: 14px;
  user-select: none;
}

.doc-viewer pre {
  margin: 0;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #1d1d1f;
  white-space: pre-wrap;
  word-break: break-word;
}

.image-thumbs {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.image-thumbs img {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border: 2px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.image-thumbs img.active {
  border-color: #409EFF;
}

.zoom-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}

.review-form-panel {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.detail-layout {
  display: flex;
  gap: 20px;
  height: 70vh;
}

.detail-image-panel {
  width: 50%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.detail-image-viewer {
  flex: 1;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8e8e8;
  border-radius: 4px;
  min-height: 0;
  user-select: none;
}

.detail-image-viewer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.2s ease;
}

.detail-no-image {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
  border-radius: 4px;
  color: #909399;
  font-size: 14px;
  user-select: none;
}

.detail-form-panel {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}
</style>