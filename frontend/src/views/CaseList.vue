<template>
  <div class="list-page">
    <!-- 页面头：一件陈述句代替原来挤在卡片标题栏里的六个控件。
         筛选归筛选行，动作只留两个（待审 / 导出），主行动只有一个蓝色。 -->
    <div class="list-head">
      <div class="list-head-main">
        <div class="eyebrow">测试资产 · CASES</div>
        <h1 class="list-title">测试用例</h1>
        <p class="list-sub">
          共 <b>{{ total }}</b> 条用例<template v-if="selectedProject"> · 当前项目下</template>
          <template v-if="pendingCount">，其中 <b>{{ pendingCount }}</b> 条待审</template>
        </p>
      </div>
      <div class="list-head-actions">
        <el-button @click="openReviewDialog">
          待审用例
          <span v-if="pendingCount" class="list-count">{{ pendingCount }}</span>
        </el-button>
        <el-button type="primary" :loading="exporting" :disabled="testcases.length === 0" @click="exportXmind">
          导出 XMind
        </el-button>
      </div>
    </div>

    <div class="list-filters">
      <el-select v-model="selectedProject" placeholder="选择项目" class="sel-project" @change="onProjectChange">
        <el-option
          v-for="project in projects"
          :key="project.id"
          :label="project.name"
          :value="project.id"
        />
      </el-select>
      <el-select v-model="selectedSprint" placeholder="选择迭代" class="sel-sprint" @change="loadTestcases">
        <el-option
          v-for="sprint in sprints"
          :key="sprint.id"
          :label="sprint.name"
          :value="sprint.id"
        />
      </el-select>
      <el-select v-model="selectedImage" placeholder="原型图筛选" class="sel-image" clearable @change="loadTestcases">
        <el-option
          v-for="img in imageOptions"
          :key="img.image_id"
          :label="`${img.name}（${formatTime(img.time)}）`"
          :value="img.image_id"
        />
      </el-select>
      <span class="list-filter-note">当前页 <b>{{ paginatedTestcases.length }}</b> 条 / 共 <b>{{ total }}</b> 条</span>
    </div>

    <div class="list-shell">
      <div v-if="selectedCases.length" class="list-bulk">
        <span class="list-bulk-count">已选 {{ selectedCases.length }} 条</span>
        <span class="list-bulk-spacer"></span>
        <button type="button" class="list-act" @click="clearSelection">取消选择</button>
        <button type="button" class="list-act is-danger" @click="batchDelete">删除所选</button>
      </div>

      <el-table ref="tableRef" :data="paginatedTestcases" style="width: 100%" v-loading="loading"
               @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="48" />
        <el-table-column prop="module" label="模块" width="190" show-overflow-tooltip />
        <el-table-column prop="test_point" label="测试点" width="180" show-overflow-tooltip />
        <el-table-column prop="title" label="用例标题" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="list-cell-strong">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="case_type" label="类型" width="104">
          <template #default="{ row }">
            <span class="chip">{{ getCaseTypeName(row.case_type) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="84">
          <template #default="{ row }">
            <span :class="['chip', getPriorityClass(row.priority)]">{{ row.priority }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="112" fixed="right">
          <template #default="{ row }">
            <button type="button" class="list-act" @click="viewTestcase(row)">查看</button>
            <button type="button" class="list-act is-danger" @click="deleteTestcase(row.id)">删除</button>
          </template>
        </el-table-column>
      </el-table>

      <div class="list-pager">
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
    </div>

    <!-- 用例详情：左原型图 / 右字段，标题与操作区各用一条横线隔开 -->
    <el-dialog v-model="showDetail" class="dlg" width="min(1100px, 94vw)" top="4vh">
      <template #header>
        <div class="el-dialog__title">用例详情</div>
        <div class="dlg-sub">
          {{ currentTestcase.module || '未分模块' }} · {{ currentTestcase.priority || '未定级' }} · 创建于 {{ formatTime(currentTestcase.created_at) || '—' }}
        </div>
      </template>

      <div v-if="currentTestcase.id" class="dlg-split">
        <div class="dlg-viewer">
          <div
            class="dlg-stage"
            @wheel="handleDetailZoom"
            @mousedown="startDetailDrag"
            @mousemove="onDetailDrag"
            @mouseup="endDetailDrag"
            @mouseleave="endDetailDrag"
          >
            <img
              v-if="detailHasImage"
              :src="`/api/images/${currentTestcase.image_source}`"
              :style="{
                transform: `scale(${detailImageScale}) translate(${detailDragOffset.x}px, ${detailDragOffset.y}px)`,
                cursor: detailIsDragging ? 'grabbing' : detailImageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
            <div v-else class="dlg-stage-empty">未关联原型图</div>
          </div>
          <div v-if="detailHasImage" class="dlg-zoom">
            <button type="button" class="zoom-btn" title="缩小" @click="detailImageScale = Math.max(0.2, detailImageScale - 0.2)">−</button>
            <span class="dlg-zoom-val">{{ Math.round(detailImageScale * 100) }}%</span>
            <button type="button" class="zoom-btn" title="放大" @click="detailImageScale = Math.min(3, detailImageScale + 0.2)">＋</button>
            <button type="button" class="zoom-reset" @click="detailImageScale = 1; detailDragOffset = { x: 0, y: 0 }">重置</button>
          </div>
        </div>

        <div class="dlg-panel">
          <div class="dlg-bar">
            <span class="dlg-bar-text">已被批准的用例</span>
            <span class="dlg-bar-note">可直接修改后保存</span>
          </div>
          <div class="dlg-scroll">
            <CaseFields
              v-model="detailForm"
              :meta-label="currentTestcase.ai_provider"
              :meta-time="formatTime(currentTestcase.created_at)"
            />
          </div>
        </div>
      </div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">保存直接覆盖原用例，不会再走一次审批</span>
          <div class="dlg-foot-actions">
            <el-button @click="showDetail = false">取消</el-button>
            <el-button type="primary" :loading="savingDetail" @click="saveDetailTestcase">保存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 审批用例：导航从 footer 移到右栏顶部，footer 只留「通过 / 不通过」 -->
    <el-dialog
      v-model="showReview"
      class="dlg"
      width="min(1180px, 94vw)"
      top="4vh"
      :close-on-click-modal="false"
      @closed="onReviewClosed"
    >
      <template #header>
        <div class="el-dialog__title">审批用例</div>
        <div class="dlg-sub">逐条核对标题、步骤和预期；通过后写入用例库</div>
      </template>

      <div v-if="reviewForm.id" class="dlg-split">
        <div class="dlg-viewer">
          <div
            class="dlg-stage"
            @wheel="handleZoom"
            @mousedown="startDrag"
            @mousemove="onDrag"
            @mouseup="endDrag"
            @mouseleave="endDrag"
          >
            <img
              v-if="isCurrentFileImage"
              :src="`/api/images/${currentImageFile}`"
              :style="{
                transform: `scale(${imageScale}) translate(${dragOffset.x}px, ${dragOffset.y}px)`,
                cursor: isDragging ? 'grabbing' : imageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
            <div v-else-if="!currentImageFile && projectImages.length === 0" class="dlg-stage-empty">未关联原型图</div>
            <div v-else class="dlg-doc"><pre>{{ reviewDocContent }}</pre></div>
          </div>
          <div v-if="projectImages.length > 1" class="dlg-thumbs">
            <img
              v-for="img in projectImages"
              :key="img.filename"
              :src="`/api/images/${img.filename}`"
              :class="{ 'is-active': img.filename === currentImageFile }"
              :title="img.filename"
              @click="switchImage(img.filename)"
            />
          </div>
          <div v-if="isCurrentFileImage" class="dlg-zoom">
            <button type="button" class="zoom-btn" title="缩小" @click="zoomOut">−</button>
            <span class="dlg-zoom-val">{{ Math.round(imageScale * 100) }}%</span>
            <button type="button" class="zoom-btn" title="放大" @click="zoomIn">＋</button>
            <button type="button" class="zoom-reset" @click="imageScale = 1; dragOffset = { x: 0, y: 0 }">重置</button>
          </div>
        </div>

        <div class="dlg-panel">
          <div class="dlg-bar">
            <span class="dlg-bar-text">第 <b>{{ currentReviewIndex + 1 }}</b> / {{ allPendingCount }} 条待审</span>
            <div class="dlg-bar-nav">
              <button type="button" class="dlg-nav" :disabled="currentReviewIndex <= 0" @click="moveToPrev">上一个</button>
              <button type="button" class="dlg-nav" :disabled="currentReviewIndex >= allPendingTestcases.length - 1" @click="moveToNextManual">下一个</button>
            </div>
          </div>
          <div class="dlg-scroll">
            <CaseFields v-model="reviewForm" />
          </div>
        </div>
      </div>
      <div v-else class="dlg-empty">没有待审用例</div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">「不通过」只把这条标记为废弃，不改动内容</span>
          <div class="dlg-foot-actions">
            <el-button type="danger" plain :disabled="!reviewForm.id" :loading="failing" @click="handleFail">不通过</el-button>
            <el-button type="primary" :disabled="!reviewForm.id" :loading="passing" @click="handlePass">通过</el-button>
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
import CaseFields from '../components/CaseFields.vue'
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

// 详情弹窗只用判断「有没有原型图」，原来在模板里重复写了三遍正则
const detailHasImage = computed(() => {
  const source = currentTestcase.value.image_source || ''
  return /[\._](jpg|jpeg|png|webp)$/i.test(source)
})

const paginatedTestcases = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return testcases.value.slice(start, end)
})

// 批量条上的「取消选择」需要直接操作表格实例，光清数组表格里的勾还在
const tableRef = ref(null)
function clearSelection() {
  tableRef.value?.clearSelection()
}

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
      project_id: selectedProject.value,
      sprint_id: selectedSprint.value || '',
      image_id: selectedImage.value || ''
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

// 类型是分类，不是告警：全部用一个中性方片，颜色只留给优先级一列
function getCaseTypeName(type) {
  const map = {
    functional: '功能测试',
    ui: 'UI测试',
    boundary: '边界测试',
    exception: '异常测试'
  }
  return map[type] || type
}

function getPriorityClass(priority) {
  const map = {
    P0: 'is-danger',
    P1: 'is-warn',
    P2: ''
  }
  return map[priority] || ''
}
</script>

<style scoped>
/* 页面骨架在 styles/list-page.css，弹窗外壳在 styles/dialog.css，
   字段在 styles/forms.css —— 这里不再留页面私有样式。 */
</style>
