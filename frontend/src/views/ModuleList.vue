<template>
  <div class="list-page">
    <div class="list-head">
      <div class="list-head-main">
        <div class="eyebrow">测试资产 · MODULES</div>
        <h1 class="list-title">模块</h1>
        <p class="list-sub">
          共 <b>{{ total }}</b> 个模块<template v-if="pendingCount">，其中 <b>{{ pendingCount }}</b> 个待审</template>
        </p>
      </div>
      <div class="list-head-actions">
        <el-button @click="openPendingReview">
          待审模块
          <span v-if="pendingCount" class="list-count">{{ pendingCount }}</span>
        </el-button>
        <el-button type="primary" :disabled="selection.length === 0" @click="openGenerateDialog">
          生成用例
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
      <el-select v-model="selectedSprint" placeholder="选择迭代" class="sel-sprint" @change="loadModules">
        <el-option
          v-for="sprint in sprints"
          :key="sprint.id"
          :label="sprint.name"
          :value="sprint.id"
        />
      </el-select>
      <el-select v-model="selectedImage" placeholder="原型图筛选" class="sel-image" clearable @change="loadModules">
        <el-option
          v-for="img in imageOptions"
          :key="img.image_id"
          :label="`${img.name}（${formatTime(img.time)}）`"
          :value="img.image_id"
        />
      </el-select>
      <span class="list-filter-note">当前页 <b>{{ paginatedModules.length }}</b> 个 / 共 <b>{{ total }}</b> 个</span>
    </div>

    <div class="list-shell">
      <div v-if="selection.length" class="list-bulk">
        <span class="list-bulk-count">已选 {{ selection.length }} 个模块</span>
        <span class="list-bulk-spacer"></span>
        <button type="button" class="list-act" @click="clearSelection">取消选择</button>
        <button type="button" class="list-act is-danger" @click="batchDeleteModules">删除所选</button>
      </div>

      <el-table ref="tableRef" :data="paginatedModules" style="width: 100%" v-loading="loading" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="48" />
        <el-table-column prop="module" label="模块名称" min-width="180">
          <template #default="{ row }">
            <span class="list-cell-strong">{{ row.module }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="function_description" label="功能描述" min-width="260" show-overflow-tooltip />
        <el-table-column prop="ui_elements" label="UI 元素" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="list-cell-muted">{{ row.ui_elements }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="116">
          <template #default="{ row }">
            <span class="list-cell-muted">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="list-pager">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
        />
      </div>
    </div>

    <!-- 模块审核：导航在右栏顶部，footer 只留「通过 / 不通过」 -->
    <el-dialog
      v-model="showReview"
      class="dlg"
      width="min(1120px, 94vw)"
      top="4vh"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="el-dialog__title">模块审核</div>
        <div class="dlg-sub">确认模块边界、UI 元素与交互流程；通过后才能用来生成用例</div>
      </template>

      <div v-if="pendingModulesForReview.length > 0 && reviewModule" class="dlg-split">
        <div class="dlg-viewer">
          <div
            class="dlg-stage"
            @wheel="handleReviewZoom"
            @mousedown="startReviewDrag"
            @mousemove="onReviewDrag"
            @mouseup="endReviewDrag"
            @mouseleave="endReviewDrag"
          >
            <img
              v-if="reviewModule.image_filename"
              :src="`/api/images/${reviewModule.image_filename}`"
              :style="{
                transform: `scale(${reviewImageScale}) translate(${reviewDragOffset.x}px, ${reviewDragOffset.y}px)`,
                cursor: reviewIsDragging ? 'grabbing' : reviewImageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
            <div v-else class="dlg-stage-empty">暂无原型图</div>
          </div>
          <div v-if="reviewModule.image_filename" class="dlg-zoom">
            <button type="button" class="zoom-btn" title="缩小" @click="reviewZoomOut">−</button>
            <span class="dlg-zoom-val">{{ Math.round(reviewImageScale * 100) }}%</span>
            <button type="button" class="zoom-btn" title="放大" @click="reviewZoomIn">＋</button>
            <button type="button" class="zoom-reset" @click="reviewImageScale = 1; reviewDragOffset = { x: 0, y: 0 }">重置</button>
          </div>
        </div>

        <div class="dlg-panel">
          <div class="dlg-bar">
            <span class="dlg-bar-text">第 <b>{{ currentReviewIndex + 1 }}</b> / {{ pendingModulesForReview.length }} 个待审</span>
            <div class="dlg-bar-nav">
              <button type="button" class="dlg-nav" :disabled="currentReviewIndex <= 0" @click="moveToPrev">上一个</button>
              <button type="button" class="dlg-nav" :disabled="currentReviewIndex >= pendingModulesForReview.length - 1" @click="moveToNext">下一个</button>
            </div>
          </div>
          <div class="dlg-scroll">
            <ModuleFields v-model="reviewModule" />
          </div>
        </div>
      </div>
      <div v-else class="dlg-empty">没有待审模块</div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">「不通过」会把模块标记为废弃，可以稍后重新生成</span>
          <div class="dlg-foot-actions">
            <el-button @click="showReview = false">取消</el-button>
            <el-button type="danger" plain :loading="failing" @click="handleFail">不通过</el-button>
            <el-button type="primary" :loading="passing" @click="handlePass">通过</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 生成用例：逐模块确认参数，最后一并提交 -->
    <el-dialog
      v-model="showGenerate"
      class="dlg"
      width="min(1120px, 94vw)"
      top="4vh"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="el-dialog__title">生成用例</div>
        <div class="dlg-sub">已选 {{ generatingModules.length }} 个模块 · 逐个确认参数后一起生成</div>
      </template>

      <div v-if="generatingModules.length > 0" class="dlg-split">
        <div class="dlg-viewer">
          <div
            class="dlg-stage"
            @wheel="handleGenZoom"
            @mousedown="startGenDrag"
            @mousemove="onGenDrag"
            @mouseup="endGenDrag"
            @mouseleave="endGenDrag"
          >
            <img
              v-if="generatingModules[genIndex] && generatingModules[genIndex].image_filename"
              :src="`/api/images/${generatingModules[genIndex].image_filename}`"
              :style="{
                transform: `scale(${genImageScale}) translate(${genDragOffset.x}px, ${genDragOffset.y}px)`,
                cursor: genIsDragging ? 'grabbing' : genImageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
            <div v-else class="dlg-stage-empty">暂无原型图</div>
          </div>
          <div v-if="generatingModules[genIndex] && generatingModules[genIndex].image_filename" class="dlg-zoom">
            <button type="button" class="zoom-btn" title="缩小" @click="genZoomOut">−</button>
            <span class="dlg-zoom-val">{{ Math.round(genImageScale * 100) }}%</span>
            <button type="button" class="zoom-btn" title="放大" @click="genZoomIn">＋</button>
            <button type="button" class="zoom-reset" @click="genImageScale = 1; genDragOffset = { x: 0, y: 0 }">重置</button>
          </div>
        </div>

        <div class="dlg-panel">
          <div class="dlg-bar">
            <span class="dlg-bar-text">第 <b>{{ genIndex + 1 }}</b> / {{ generatingModules.length }} 个模块</span>
            <div class="dlg-bar-nav">
              <button type="button" class="dlg-nav" :disabled="genIndex <= 0" @click="prevGenModule">上一个</button>
              <button type="button" class="dlg-nav" :disabled="genIndex >= generatingModules.length - 1" @click="nextGenModule">下一个</button>
            </div>
          </div>
          <div class="dlg-scroll">
            <ModuleFields
              v-if="generatingModules[genIndex]"
              v-model="generatingModules[genIndex]"
              mode="generate"
              :vision-supported="moduleGenModelSupportsVision"
            />
          </div>
        </div>
      </div>
      <div v-else class="dlg-empty">没有选中模块</div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">生成期间请勿关闭页面，完成后到用例列表审批</span>
          <div class="dlg-foot-actions">
            <el-button @click="showGenerate = false">取消</el-button>
            <el-button type="primary" :loading="generating" @click="confirmGenerate">确认并生成用例</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ModuleFields from '../components/ModuleFields.vue'
import api from '../api'

const route = useRoute()
const loading = ref(false)
const projects = ref([])
const sprints = ref([])
const modules = ref([])
const selectedProject = ref('')
const selectedSprint = ref('')
const selectedImage = ref('')
const imageOptions = ref([])
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)

const paginatedModules = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return modules.value.slice(start, end)
})

const showReview = ref(false)
const reviewModule = ref(null)
const currentReviewIndex = ref(0)
const passing = ref(false)
const failing = ref(false)
const pendingCount = ref(0)
const pendingModulesForReview = ref([])
const reviewImageScale = ref(1)
const reviewDragOffset = ref({ x: 0, y: 0 })
const reviewIsDragging = ref(false)
let reviewDragStart = { x: 0, y: 0 }

const selection = ref([])
const tableRef = ref(null)
function clearSelection() {
  tableRef.value?.clearSelection()
}
const showGenerate = ref(false)
const generatingModules = ref([])
const genIndex = ref(0)
const generating = ref(false)
const genImageScale = ref(1)
const genDragOffset = ref({ x: 0, y: 0 })
const genIsDragging = ref(false)
let genDragStart = { x: 0, y: 0 }

const aiConfigList = ref([])
const moduleGenModelSupportsVision = computed(() => {
  const activeConfig = aiConfigList.value.find(c => c.is_active)
  return activeConfig?.supports_vision !== false
})

function onSelectionChange(val) {
  selection.value = val
}

async function batchDeleteModules() {
  try {
    await api.batchDeleteModules(selection.value.map(m => m.id))
    ElMessage.success(`已删除 ${selection.value.length} 个模块`)
    selection.value = []
    await loadModules()
    await loadPendingCount()
  } catch (error) {
    console.error('批量删除失败:', error)
    ElMessage.error('批量删除失败')
  }
}

onMounted(() => {
  loadProjects()
  loadAIConfigs()
})

watch(() => route.path, () => {
  if (route.path === '/modules') {
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
      await loadModules()
    }
  } catch (error) {
    console.error('加载项目失败:', error)
    ElMessage.error('加载项目失败')
  }
}

async function loadAIConfigs() {
  try {
    const res = await api.getAIConfigs()
    aiConfigList.value = res.data.configs || []
  } catch {}
}

async function loadSprints() {
  if (!selectedProject.value) return
  try {
    const res = await api.getSprints({ project_id: selectedProject.value })
    sprints.value = res.data.sprints || []
  } catch (error) {
    console.error('加载迭代失败:', error)
    ElMessage.error('加载迭代失败')
  }
}

async function onProjectChange() {
  selectedSprint.value = ''
  selectedImage.value = ''
  await loadSprints()
  await loadModules()
}

async function loadModules() {
  if (!selectedProject.value) return
  loading.value = true
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    if (selectedImage.value) params.image_id = selectedImage.value
    const res = await api.getApprovedModules(params)
    modules.value = res.data.modules || []
    total.value = modules.value.length
    imageOptions.value = res.data.image_options || []
    await loadPendingCount()
  } catch (error) {
    console.error('加载模块失败:', error)
    ElMessage.error('加载模块失败')
  } finally {
    loading.value = false
  }
}

async function loadPendingCount() {
  if (!selectedProject.value) return
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const res = await api.getPendingModules(params)
    pendingCount.value = (res.data.modules || []).length
  } catch (error) {
    console.error('加载待审数量失败:', error)
  }
}

async function openPendingReview() {
  if (!selectedProject.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const res = await api.getPendingModules(params)
    pendingModulesForReview.value = res.data.modules || []
    if (pendingModulesForReview.value.length === 0) {
      ElMessage.info('没有待审模块')
      return
    }
    currentReviewIndex.value = 0
    switchReviewModule(0)
    showReview.value = true
  } catch (error) {
    console.error('加载待审模块失败:', error)
    ElMessage.error('加载待审模块失败')
  }
}

async function handlePass() {
  passing.value = true
  try {
    const mod = pendingModulesForReview.value[currentReviewIndex.value]
    await api.approvePendingModule(mod.id, {
      module: reviewModule.value.module,
      ui_elements: reviewModule.value.ui_elements,
      function_description: reviewModule.value.function_description,
      interaction_flow: reviewModule.value.interaction_flow,
      test_focus: reviewModule.value.test_focus
    })
    ElMessage.success('模块审批通过')
    moveToNextModule()
  } catch (error) {
    console.error('审批失败:', error)
    ElMessage.error('审批失败')
  } finally {
    passing.value = false
  }
}

async function handleFail() {
  failing.value = true
  try {
    const mod = pendingModulesForReview.value[currentReviewIndex.value]
    await api.failPendingModule(mod.id)
    ElMessage.warning('模块不通过')
    moveToNextModule()
  } catch (error) {
    console.error('操作失败:', error)
    ElMessage.error('操作失败')
  } finally {
    failing.value = false
  }
}

function switchReviewModule(index) {
  currentReviewIndex.value = index
  const mod = pendingModulesForReview.value[index]
  reviewModule.value = { ...mod }
  reviewImageScale.value = 1
  reviewDragOffset.value = { x: 0, y: 0 }
}

function moveToNextModule() {
  currentReviewIndex.value++
  loadModules()
  loadPendingCount()
  if (currentReviewIndex.value >= pendingModulesForReview.value.length) {
    ElMessage.success('所有模块已完成审批')
    showReview.value = false
    return
  }
  switchReviewModule(currentReviewIndex.value)
}

function moveToPrev() {
  if (currentReviewIndex.value <= 0) return
  switchReviewModule(currentReviewIndex.value - 1)
}

function moveToNext() {
  if (currentReviewIndex.value >= pendingModulesForReview.value.length - 1) return
  switchReviewModule(currentReviewIndex.value + 1)
}

function handleReviewZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  reviewImageScale.value = Math.min(3, Math.max(0.5, reviewImageScale.value + delta))
}

function startReviewDrag(e) {
  if (reviewImageScale.value <= 1) return
  reviewIsDragging.value = true
  reviewDragStart = { x: e.clientX - reviewDragOffset.value.x, y: e.clientY - reviewDragOffset.value.y }
}

function onReviewDrag(e) {
  if (!reviewIsDragging.value) return
  reviewDragOffset.value = { x: e.clientX - reviewDragStart.x, y: e.clientY - reviewDragStart.y }
}

function endReviewDrag() {
  reviewIsDragging.value = false
}

function reviewZoomIn() {
  reviewImageScale.value = Math.min(3, reviewImageScale.value + 0.1)
}

function reviewZoomOut() {
  reviewImageScale.value = Math.max(0.5, reviewImageScale.value - 0.1)
}

function openGenerateDialog() {
  generatingModules.value = selection.value.map(m => ({
    ...m,
    case_types: typeof m.case_types === 'string' ? m.case_types.split(',').filter(Boolean) : (m.case_types || []),
    case_count: m.case_count || 10,
    smart_mode: m.smart_mode || false,
    use_vision: false,
    smart_case_type: false
  }))
  genIndex.value = 0
  genImageScale.value = 1
  genDragOffset.value = { x: 0, y: 0 }
  showGenerate.value = true
}

function prevGenModule() {
  if (genIndex.value <= 0) return
  genIndex.value--
  genImageScale.value = 1
  genDragOffset.value = { x: 0, y: 0 }
}

function nextGenModule() {
  if (genIndex.value >= generatingModules.value.length - 1) return
  genIndex.value++
  genImageScale.value = 1
  genDragOffset.value = { x: 0, y: 0 }
}

async function confirmGenerate() {
  const hasEmptyTypes = generatingModules.value.some(m => !m.smart_case_type && (!m.case_types || m.case_types.length === 0))
  if (hasEmptyTypes) {
    ElMessage.warning('请为每个模块至少选择一种用例类型')
    return
  }
  generating.value = true
  try {
    const modelSupportsVision = moduleGenModelSupportsVision.value
    const batchTime = new Date().toISOString()
    const promises = generatingModules.value.map(mod => {
      const payload = {
        project_id: selectedProject.value,
        sprint_id: selectedSprint.value || '',
        generated_at: batchTime,
        modules: [{
          image_id: mod.image_id || '',
          module: mod.module,
          case_types: mod.smart_case_type ? [] : mod.case_types,
          case_count: mod.case_count,
          smart_mode: mod.smart_mode,
          ui_elements: mod.ui_elements.split(',').map(s => s.trim()).filter(Boolean),
          function_description: mod.function_description,
          interaction_flow: mod.interaction_flow,
          test_focus: mod.test_focus.split(',').map(s => s.trim()).filter(Boolean)
        }]
      }
      if (mod.use_vision && modelSupportsVision && mod.image_id) {
        payload.image_id = mod.image_id
      }
      return api.generateTestcases(payload)
    })
    await Promise.all(promises)
    ElMessage.success('用例生成完成')
    showGenerate.value = false
    selection.value = []
  } catch (error) {
    console.error('生成用例失败:', error)
    ElMessage.error('生成用例失败')
  } finally {
    generating.value = false
  }
}

function handleGenZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  genImageScale.value = Math.min(3, Math.max(0.5, genImageScale.value + delta))
}

function startGenDrag(e) {
  if (genImageScale.value <= 1) return
  genIsDragging.value = true
  genDragStart = { x: e.clientX - genDragOffset.value.x, y: e.clientY - genDragOffset.value.y }
}

function onGenDrag(e) {
  if (!genIsDragging.value) return
  genDragOffset.value = { x: e.clientX - genDragStart.x, y: e.clientY - genDragStart.y }
}

function endGenDrag() {
  genIsDragging.value = false
}

function genZoomIn() {
  genImageScale.value = Math.min(3, genImageScale.value + 0.1)
}

function genZoomOut() {
  genImageScale.value = Math.max(0.5, genImageScale.value - 0.1)
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
</script>

<style scoped>
/* 页面骨架在 styles/list-page.css，弹窗外壳在 styles/dialog.css，
   字段在 styles/forms.css —— 这里不再留页面私有样式。 */
</style>
