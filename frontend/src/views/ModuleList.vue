<template>
  <div class="case-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>模块列表</span>
          <div class="header-actions">
            <el-button type="warning" @click="openPendingReview">
              待审模块
              <el-badge :value="pendingCount" :hidden="pendingCount === 0" />
            </el-button>
            <el-select v-model="selectedProject" placeholder="选择项目" class="project-select" @change="onProjectChange">
              <el-option
                v-for="project in projects"
                :key="project.id"
                :label="project.name"
                :value="project.id"
              />
            </el-select>
            <el-select v-model="selectedSprint" placeholder="选择迭代" class="sprint-select" @change="loadModules" clearable>
              <el-option
                v-for="sprint in sprints"
                :key="sprint.id"
                :label="sprint.name"
                :value="sprint.id"
              />
            </el-select>
            <el-button type="primary" @click="openGenerateDialog" :disabled="selection.length === 0">
              生成用例
            </el-button>
            <el-button type="danger" @click="batchDeleteModules" :disabled="selection.length === 0">
              批量删除
            </el-button>
          </div>
        </div>
      </template>

      <div class="batch-bar" v-if="selection.length > 0">
        <span>已选 {{ selection.length }} 项</span>
      </div>
      <el-table :data="paginatedModules" style="width: 100%" v-loading="loading" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="50" />
        <el-table-column prop="module" label="模块名称" min-width="150" />
        <el-table-column prop="function_description" label="功能描述" min-width="250" show-overflow-tooltip />
        <el-table-column prop="ui_elements" label="UI元素" min-width="150" show-overflow-tooltip />
        <el-table-column prop="created_at" label="创建时间" width="160" />
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="showReview"
      title="模块审核"
      width="1400px"
      top="2vh"
      :close-on-click-modal="false"
    >
      <div v-if="pendingModulesForReview.length > 0" class="review-layout">
        <div class="review-image-panel">
          <div class="review-image-viewer"
               @wheel="handleReviewZoom"
               @mousedown="startReviewDrag"
               @mousemove="onReviewDrag"
               @mouseup="endReviewDrag"
               @mouseleave="endReviewDrag">
            <img
              v-if="reviewModule.image_filename"
              :src="`/api/images/${reviewModule.image_filename}`"
              :style="{
                transform: `scale(${reviewImageScale}) translate(${reviewDragOffset.x}px, ${reviewDragOffset.y}px)`,
                cursor: reviewIsDragging ? 'grabbing' : reviewImageScale > 1 ? 'grab' : 'default',
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
                transition: 'transform 0.2s ease',
                userSelect: 'none'
              }"
              draggable="false"
            />
            <div v-else class="no-image">暂无原型图</div>
          </div>
          <div class="review-zoom-controls" v-if="reviewModule.image_filename">
            <el-button circle size="small" @click="reviewZoomOut">-</el-button>
            <span>{{ Math.round(reviewImageScale * 100) }}%</span>
            <el-button circle size="small" @click="reviewZoomIn">+</el-button>
            <el-button size="small" @click="reviewImageScale = 1; reviewDragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="review-form-panel">
          <div class="review-progress">
            <span class="review-progress-text">模块 {{ currentReviewIndex + 1 }} / {{ pendingModulesForReview.length }}</span>
            <el-button size="small" :disabled="currentReviewIndex <= 0" @click="moveToPrev">上一个</el-button>
            <el-button size="small" :disabled="currentReviewIndex >= pendingModulesForReview.length - 1" @click="moveToNext">下一个</el-button>
          </div>
          <el-form v-if="reviewModule" label-width="120px" class="module-form">
            <el-form-item label="模块名称">
              <el-input v-model="reviewModule.module" />
            </el-form-item>
            <el-form-item label="UI元素">
              <el-input v-model="reviewModule.ui_elements" placeholder="用逗号分隔，如：按钮、输入框、下拉菜单" />
            </el-form-item>
            <el-form-item label="功能描述">
              <el-input v-model="reviewModule.function_description" type="textarea" :rows="4" />
            </el-form-item>
            <el-form-item label="交互流程">
              <el-input v-model="reviewModule.interaction_flow" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="测试关注点">
              <el-input v-model="reviewModule.test_focus" type="textarea" :rows="3" placeholder="用逗号分隔" />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <el-empty v-else description="没有待审模块" />
      <template #footer>
        <div class="review-footer">
          <span class="progress-info">
            <el-button size="small" :disabled="currentReviewIndex <= 0" @click="moveToPrev">上一个</el-button>
            审批进度: {{ pendingModulesForReview.length > 0 ? currentReviewIndex + 1 : 0 }} / {{ pendingModulesForReview.length }}
            <el-button size="small" :disabled="currentReviewIndex >= pendingModulesForReview.length - 1" @click="moveToNext">下一个</el-button>
          </span>
          <div class="review-actions">
            <el-button @click="showReview = false">取消</el-button>
            <el-button type="danger" @click="handleFail" :loading="failing">不通过</el-button>
            <el-button type="success" @click="handlePass" :loading="passing">通过</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showGenerate"
      title="生成用例"
      width="1400px"
      top="2vh"
      :close-on-click-modal="false"
    >
      <div v-if="generatingModules.length > 0" class="review-layout">
        <div class="review-image-panel">
          <div class="review-image-viewer"
               @wheel="handleGenZoom"
               @mousedown="startGenDrag"
               @mousemove="onGenDrag"
               @mouseup="endGenDrag"
               @mouseleave="endGenDrag">
            <img
              v-if="generatingModules[genIndex].image_filename"
              :src="`/api/images/${generatingModules[genIndex].image_filename}`"
              :style="{
                transform: `scale(${genImageScale}) translate(${genDragOffset.x}px, ${genDragOffset.y}px)`,
                cursor: genIsDragging ? 'grabbing' : genImageScale > 1 ? 'grab' : 'default',
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
                transition: 'transform 0.2s ease',
                userSelect: 'none'
              }"
              draggable="false"
            />
            <div v-else class="no-image">暂无原型图</div>
          </div>
          <div class="review-zoom-controls" v-if="generatingModules[genIndex].image_filename">
            <el-button circle size="small" @click="genZoomOut">-</el-button>
            <span>{{ Math.round(genImageScale * 100) }}%</span>
            <el-button circle size="small" @click="genZoomIn">+</el-button>
            <el-button size="small" @click="genImageScale = 1; genDragOffset = { x: 0, y: 0 }">重置</el-button>
          </div>
        </div>
        <div class="review-form-panel">
          <div class="review-progress">
            <span class="review-progress-text">模块 {{ genIndex + 1 }} / {{ generatingModules.length }}</span>
            <el-button size="small" :disabled="genIndex <= 0" @click="prevGenModule">上一个</el-button>
            <el-button size="small" :disabled="genIndex >= generatingModules.length - 1" @click="nextGenModule">下一个</el-button>
          </div>
          <el-form v-if="generatingModules[genIndex]" label-width="120px" class="module-form">
            <el-form-item label="模块名称">
              <el-input v-model="generatingModules[genIndex].module" />
            </el-form-item>
            <el-form-item label="UI元素">
              <el-input v-model="generatingModules[genIndex].ui_elements" placeholder="用逗号分隔，如：按钮、输入框、下拉菜单" />
            </el-form-item>
            <el-form-item label="功能描述">
              <el-input v-model="generatingModules[genIndex].function_description" type="textarea" :rows="4" />
            </el-form-item>
            <el-form-item label="交互流程">
              <el-input v-model="generatingModules[genIndex].interaction_flow" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="测试关注点">
              <el-input v-model="generatingModules[genIndex].test_focus" type="textarea" :rows="3" placeholder="用逗号分隔" />
            </el-form-item>
            <el-form-item label="用例类型">
              <el-checkbox-group v-model="generatingModules[genIndex].case_types">
                <el-checkbox label="functional">功能测试</el-checkbox>
                <el-checkbox label="ui">UI交互测试</el-checkbox>
                <el-checkbox label="boundary">边界值测试</el-checkbox>
                <el-checkbox label="exception">异常场景测试</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="生成数量">
              <el-input-number
                v-model="generatingModules[genIndex].case_count"
                :min="1"
                :max="50"
                :step="1"
                :disabled="generatingModules[genIndex].smart_mode"
              />
              <el-switch
                v-model="generatingModules[genIndex].smart_mode"
                active-text="智能"
                style="margin-left: 12px"
              />
            </el-form-item>
            <el-form-item label="附带原图">
              <el-switch
                v-model="generatingModules[genIndex].use_vision"
                :disabled="!moduleGenModelSupportsVision"
              />
              <span class="form-hint" style="margin-left:8px;color:#909399;font-size:12px">
                {{ moduleGenModelSupportsVision ? '附带原图可提升用例质量' : '当前模型不支持图片分析' }}
              </span>
            </el-form-item>
          </el-form>
        </div>
      </div>
      <el-empty v-else description="没有选中模块" />
      <template #footer>
        <div class="review-footer">
          <span class="progress-info">
            <el-button size="small" :disabled="genIndex <= 0" @click="prevGenModule">上一个</el-button>
            模块 {{ genIndex + 1 }} / {{ generatingModules.length }}
            <el-button size="small" :disabled="genIndex >= generatingModules.length - 1" @click="nextGenModule">下一个</el-button>
          </span>
          <div class="review-actions">
            <el-button @click="showGenerate = false">取消</el-button>
            <el-button type="primary" @click="confirmGenerate" :loading="generating">确认并生成用例</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const route = useRoute()
const loading = ref(false)
const projects = ref([])
const sprints = ref([])
const modules = ref([])
const selectedProject = ref('')
const selectedSprint = ref('')
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
  await loadSprints()
  await loadModules()
}

async function loadModules() {
  if (!selectedProject.value) return
  loading.value = true
  try {
    const params = { project_id: selectedProject.value }
    if (selectedSprint.value) params.sprint_id = selectedSprint.value
    const res = await api.getApprovedModules(params)
    modules.value = res.data.modules || []
    total.value = modules.value.length
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
    case_types: typeof m.case_types === 'string' ? m.case_types.split(',').filter(Boolean) : (m.case_types || ['functional', 'ui', 'boundary', 'exception']),
    case_count: m.case_count || 10,
    smart_mode: m.smart_mode || false,
    use_vision: false
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
  generating.value = true
  try {
    for (const mod of generatingModules.value) {
      await api.generateTestcases({
        image_id: (mod.use_vision && moduleGenModelSupportsVision.value) ? (mod.image_id || undefined) : undefined,
        project_id: selectedProject.value,
        case_types: mod.case_types,
        case_count: mod.case_count,
        smart_mode: mod.smart_mode,
        sprint_id: selectedSprint.value || '',
        modules: [{
          module: mod.module,
          ui_elements: mod.ui_elements.split(',').map(s => s.trim()).filter(Boolean),
          function_description: mod.function_description,
          interaction_flow: mod.interaction_flow,
          test_focus: mod.test_focus.split(',').map(s => s.trim()).filter(Boolean)
        }]
      })
    }
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
</script>

<style scoped>
.case-page {
  max-width: 1200px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.project-select, .sprint-select {
  width: 180px;
}

.pagination {
  margin-top: 20px;
  text-align: right;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f0f9ff;
  border-radius: 4px;
  font-size: 13px;
  color: #409EFF;
}

.review-layout {
  display: flex;
  gap: 20px;
  height: 70vh;
}

.review-image-panel {
  width: 45%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.review-image-viewer {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border-radius: 4px;
  overflow: hidden;
}

.review-form-panel {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.review-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.review-progress-text {
  font-weight: 500;
  color: #409EFF;
  margin-right: auto;
}

.review-zoom-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}

.no-image {
  color: #999;
  font-size: 14px;
}

.module-form .el-form-item {
  margin-bottom: 16px;
}

.review-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.progress-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
  font-size: 14px;
}

.review-actions {
  display: flex;
  gap: 10px;
}
</style>