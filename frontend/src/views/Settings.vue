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
        <!-- ---------- AI 服务：直接管理 opencode 的 provider / model ---------- -->
        <section v-if="activeTab === 'ai'" class="set-panel">
          <div class="panel-head">
            <p class="panel-desc">
              模型直接来自 opencode 配置（<span class="mono">opencode.models.json</span>）。改完点「应用」生效，已在运行的会话需要重开。
            </p>
            <div class="head-acts">
              <el-button :loading="applying" @click="applyModels">应用配置</el-button>
              <el-button type="primary" @click="openProviderDialog()">新增服务</el-button>
            </div>
          </div>
          <p class="panel-note">
            多模态 = 模型能读图，用于原型图分析和按截图生成用例。默认关闭，需要时点模型上的
            <span class="mono">多模态</span> 打开；没打开的模型在「原型分析模型」里会置灰、选不了。
          </p>

          <div v-loading="loadingConfigs" class="prov-list">
            <div v-for="p in providers" :key="p.id" :class="['prov-card', { 'is-hidden': p.disabled }]">
              <div class="prov-head">
                <div class="prov-title">
                  <span class="prov-name mono">{{ p.id }}</span>
                  <span :class="['src-tag', p.source === 'local' ? 'is-local' : 'is-global']">
                    {{ p.source === 'local' ? '本地' : '全局' }}
                  </span>
                  <span v-if="p.disabled" class="src-tag is-off">已隐藏</span>
                </div>
                <div class="prov-acts">
                  <button type="button" class="row-act" @click="openProviderDialog(p)">编辑</button>
                  <button type="button" class="row-act" @click="openModelDialog(p)">加模型</button>
                  <button type="button" class="row-act" @click="toggleHidden(p)">
                    {{ p.disabled ? '取消隐藏' : '隐藏' }}
                  </button>
                  <button type="button" class="row-act is-danger" @click="removeProvider(p)">
                    {{ p.source === 'local' ? '删除' : '还原' }}
                  </button>
                </div>
              </div>
              <div class="prov-meta">
                <span class="mono prov-url">{{ p.base_url || '未填 Base URL' }}</span>
                <span class="key-line">{{ p.key_masked || '未填 Key' }}</span>
              </div>
              <div class="model-chips">
                <div
                  v-for="m in p.models"
                  :key="m.id"
                  :class="['mchip', { 'is-default': defaultModel === `${p.id}/${m.id}` }]"
                >
                  <button
                    type="button"
                    class="mchip-main"
                    :title="`点一下设为默认 · ${p.id}/${m.id}${m.name && m.name !== m.id ? ' · ' + m.name : ''}`"
                    @click="setDefaultModel(p, m)"
                  >
                    <span class="mono mchip-name">{{ m.id }}</span>
                    <span v-if="m.source === 'global'" class="mchip-tag">全局</span>
                  </button>
                  <span
                    :class="['mchip-mm', { 'is-on': m.vision }]"
                    :title="m.vision
                      ? '已开启多模态：可用于原型图分析和按截图生成用例。点一下关闭'
                      : '未开启多模态：不能读图，原型图分析和按截图生成用例会跳过它。点一下打开'"
                    @click.stop="toggleVision(p, m)"
                  >
                    <span class="mchip-mm-text">多模态</span>
                    <span class="mchip-mm-state">{{ m.vision ? '开' : '关' }}</span>
                  </span>
                  <span
                    class="mchip-del"
                    title="从列表移除"
                    @click.stop="removeModel(p, m)"
                  >×</span>
                </div>
                <span v-if="!p.models.length" class="mchip-empty">还没加模型</span>
              </div>
            </div>
            <div v-if="!loadingConfigs && !providers.length" class="table-empty">
              <p>还没有可用的模型服务。</p>
              <p class="table-empty-hint">点右上角「新增服务」，填 Base URL 和 API Key，再往里加模型。</p>
            </div>
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

    <!-- ---------- 新增 / 编辑模型服务 ---------- -->
    <el-dialog
      v-model="showProviderDialog"
      :title="editingProvider ? '编辑服务' : '新增服务'"
      width="480px"
      class="form-dialog"
      :close-on-click-modal="false"
    >
      <div class="form-block">
        <div class="form-field">
          <div class="form-label">
            <span>服务名</span>
            <span class="form-note">就是 opencode 里的 provider ID</span>
          </div>
          <div v-if="editingProvider" class="form-static mono">{{ providerForm.provider }}</div>
          <el-input v-else v-model="providerForm.provider" class="form-input" placeholder="如 cctq-codex" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>Base URL</span>
            <span class="form-note">OpenAI 兼容地址，通常以 /v1 结尾</span>
          </div>
          <el-input v-model="providerForm.base_url" class="form-input" placeholder="https://xxx/v1" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>API Key</span>
            <span v-if="editingProvider" class="form-note">留空则不修改</span>
          </div>
          <el-input
            v-model="providerForm.api_key"
            type="password"
            show-password
            class="form-input"
            :placeholder="editingProvider ? '不修改请留空' : 'sk-...'"
          />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>SDK 包</span>
            <span class="form-note">一般不用改</span>
          </div>
          <el-input v-model="providerForm.npm" class="form-input" placeholder="@ai-sdk/openai" />
        </div>
      </div>

      <template #footer>
        <div class="dlg-foot">
          <div class="foot-left">
            <el-button :loading="testing" @click="testProvider">测试连通</el-button>
            <span v-if="testResult !== null" :class="['test-dot', testResult ? 'is-ok' : 'is-bad']">
              {{ testResult ? '连接成功' : '连接失败' }}
            </span>
          </div>
          <div class="foot-right">
            <el-button @click="showProviderDialog = false">取消</el-button>
            <el-button type="primary" @click="saveProvider">{{ editingProvider ? '保存' : '添加' }}</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ---------- 给服务加模型 ---------- -->
    <el-dialog
      v-model="showModelDialog"
      :title="`给 ${modelProvider ? modelProvider.id : ''} 添加模型`"
      width="440px"
      class="form-dialog"
      :close-on-click-modal="false"
    >
      <div class="form-block">
        <div class="form-field">
          <div class="form-label">
            <span>模型 ID</span>
            <span class="form-note">发给服务商的 model 名</span>
          </div>
          <el-input v-model="modelForm.model" class="form-input" placeholder="如 gpt-6-luna" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>显示名</span>
            <span class="form-note">留空就用模型 ID</span>
          </div>
          <el-input v-model="modelForm.name" class="form-input" placeholder="页面上显示的名字" />
        </div>

        <div class="form-field">
          <div class="form-label">
            <span>生成参数</span>
            <span class="form-note">只影响用例生成，不影响 opencode</span>
          </div>
          <div class="form-duo">
            <div class="duo-item">
              <span class="duo-label">温度</span>
              <el-input-number
                v-model="modelForm.temperature"
                :min="0" :max="1" :step="0.1" :precision="1"
                controls-position="right"
                class="form-num"
              />
            </div>
            <div class="duo-item">
              <span class="duo-label">Token</span>
              <el-input-number
                v-model="modelForm.max_tokens"
                :min="100" :max="64000" :step="200"
                controls-position="right"
                class="form-num is-wide"
              />
            </div>
          </div>
        </div>

        <div class="form-toggle">
          <el-switch v-model="modelForm.vision" />
          <span class="form-toggle-text">多模态（能读图）</span>
          <span class="form-toggle-hint">{{ modelForm.vision ? '可用于原型图分析和按截图生成用例' : '只做纯文本生成' }}</span>
        </div>
      </div>

      <template #footer>
        <div class="dlg-foot">
          <div class="foot-left"></div>
          <div class="foot-right">
            <el-button @click="showModelDialog = false">取消</el-button>
            <el-button type="primary" @click="saveModel">添加</el-button>
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

/* ---------- 模型服务（opencode 配置） ----------
   数据不是数据库，是项目根的 opencode.models.json。opencode 启动时读的是
   「opencode.json + 这份」合并出来的 runtime 文件，所以这里的增删改会直接
   影响命令行和生成链路，共用一个真源。 */
const providers = ref([])
const defaultModel = ref('')
const loadingConfigs = ref(false)
const applying = ref(false)

const showProviderDialog = ref(false)
const editingProvider = ref(null)
const providerForm = ref(emptyProviderForm())
const testing = ref(false)
const testResult = ref(null)

const showModelDialog = ref(false)
const modelProvider = ref(null)
const modelForm = ref(emptyModelForm())

function emptyProviderForm() {
  return { provider: '', base_url: '', api_key: '', npm: '@ai-sdk/openai' }
}

function emptyModelForm() {
  return { model: '', name: '', temperature: 0.7, max_tokens: 4000, vision: true }
}

const modelCount = computed(() => providers.value.reduce((n, p) => n + p.models.length, 0))

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
  { name: 'ai', label: '模型服务', count: modelCount.value },
  { name: 'projects', label: '项目与迭代', count: projectList.value.length }
])

onMounted(() => {
  loadModels()
  loadProjectData()
})

function applyView(data) {
  providers.value = data.providers || []
  defaultModel.value = data.default || ''
}

function failText(error, fallback) {
  return error.response?.data?.error || error.message || fallback
}

async function loadModels() {
  loadingConfigs.value = true
  try {
    const { data } = await api.getModels()
    applyView(data)
  } catch (error) {
    ElMessage.error('加载模型配置失败：' + failText(error, '未知错误'))
  } finally {
    loadingConfigs.value = false
  }
}

function openProviderDialog(provider = null) {
  editingProvider.value = provider
  providerForm.value = provider
    ? { provider: provider.id, base_url: provider.base_url || '', api_key: '', npm: provider.npm || '@ai-sdk/openai' }
    : emptyProviderForm()
  testResult.value = null
  showProviderDialog.value = true
}

async function saveProvider() {
  const form = providerForm.value
  if (!form.provider.trim()) return ElMessage.warning('请填写服务名')
  if (!editingProvider.value && !form.api_key.trim()) return ElMessage.warning('请填写 API Key')
  try {
    const payload = { base_url: form.base_url.trim(), api_key: form.api_key.trim(), npm: form.npm.trim() }
    const { data } = editingProvider.value
      ? await api.updateModelProvider(form.provider, payload)
      : await api.createModelProvider({ provider: form.provider.trim(), ...payload })
    applyView(data)
    showProviderDialog.value = false
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error('保存失败：' + failText(error, '未知错误'))
  }
}

async function testProvider() {
  const form = providerForm.value
  if (!form.provider.trim()) return ElMessage.warning('请先填服务名')
  testing.value = true
  testResult.value = null
  try {
    const { data } = await api.testModel({
      provider: form.provider.trim(),
      api_key: form.api_key.trim(),
      base_url: form.base_url.trim()
    })
    testResult.value = !!data.success
    if (data.success) ElMessage.success('连接成功：' + (data.model || ''))
    else ElMessage.error('连接失败：' + (data.error || '未知原因'))
  } catch (error) {
    testResult.value = false
    ElMessage.error('连接失败：' + failText(error, '未知错误'))
  } finally {
    testing.value = false
  }
}

async function removeProvider(provider) {
  const isLocal = provider.source === 'local'
  try {
    await ElMessageBox.confirm(
      isLocal
        ? `删除「${provider.id}」和它的全部模型？`
        : `「${provider.id}」来自全局配置，这里只会移除本地覆盖，之后会恢复成全局的样子。`,
      isLocal ? '删除服务' : '还原服务',
      { type: 'warning', confirmButtonText: isLocal ? '删除' : '还原' }
    )
    const { data } = await api.deleteModelProvider(provider.id)
    applyView(data)
    ElMessage.success(isLocal ? '已删除' : '已还原')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('操作失败：' + failText(error, '未知错误'))
  }
}

async function toggleHidden(provider) {
  try {
    const { data } = await api.hideModelProvider(provider.id, !provider.disabled)
    applyView(data)
    ElMessage.success(provider.disabled ? '已恢复' : '已隐藏')
  } catch (error) {
    ElMessage.error('操作失败：' + failText(error, '未知错误'))
  }
}

function openModelDialog(provider) {
  modelProvider.value = provider
  modelForm.value = emptyModelForm()
  showModelDialog.value = true
}

async function saveModel() {
  const form = modelForm.value
  if (!form.model.trim()) return ElMessage.warning('请填写模型 ID')
  if (form.model.includes('/')) return ElMessage.warning('模型 ID 里不要带 /')
  try {
    const { data } = await api.createModel(modelProvider.value.id, {
      model: form.model.trim(),
      name: form.name.trim() || undefined,
      temperature: form.temperature,
      max_tokens: form.max_tokens,
      vision: form.vision
    })
    applyView(data)
    showModelDialog.value = false
    ElMessage.success('已添加')
  } catch (error) {
    ElMessage.error('添加失败：' + failText(error, '未知错误'))
  }
}

async function removeModel(provider, model) {
  const tip = model.source === 'global'
    ? `「${model.name}」是全局配置里带的，这里会把它从本项目的可用列表里移掉（全局文件不动）。`
    : `从「${provider.id}」里删掉「${model.name}」？`
  try {
    await ElMessageBox.confirm(tip, '移除模型', { type: 'warning', confirmButtonText: '移除' })
    const { data } = await api.deleteModel(provider.id, model.id)
    applyView(data)
    ElMessage.success('已移除')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('移除失败：' + failText(error, '未知错误'))
  }
}

async function setDefaultModel(provider, model) {
  const ref = `${provider.id}/${model.id}`
  if (defaultModel.value === ref) return
  try {
    const { data } = await api.setDefaultModel(ref)
    applyView(data)
    ElMessage.success('默认模型已切到 ' + ref)
  } catch (error) {
    ElMessage.error('设置失败：' + failText(error, '未知错误'))
  }
}

async function toggleVision(provider, model) {
  // 这个开关直接决定 opencode 收不收图：不开的话，原型图分析和图生用例会在
  // 「当前模型不支持图像输入」这一步被挡下来。所以改完立刻重建 runtime 配置，
  // 不留「忘了点应用」的空档。
  try {
    const { data } = await api.updateModel(provider.id, model.id, { vision: !model.vision })
    applyView(data)
    await api.applyModels()
    ElMessage.success(model.vision ? '已关闭多模态' : '已开启多模态')
  } catch (error) {
    ElMessage.error('设置失败：' + failText(error, '未知错误'))
  }
}

async function applyModels() {
  applying.value = true
  try {
    const { data } = await api.applyModels()
    ElMessage.success(data.hint || '配置已生效')
  } catch (error) {
    ElMessage.error('应用失败：' + failText(error, '未知错误'))
  } finally {
    applying.value = false
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
  background: var(--bg-hover);
}

/* 选中只加深字重和一点点底，不铺蓝：这一栏是导航不是按钮 */
.set-nav-item.is-active {
  color: var(--ink);
  font-weight: 500;
  background: var(--bg-active);
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
  flex: 1;
}

.panel-desc {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.55;
}

/* ---------- 表格 ---------- */
.set-table {
  background: var(--bg-card);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  overflow: hidden;
}

/* 展开行 Element 默认给浅灰底 + 20/50 的内边距，
   灰底会让迭代列表看起来像另一张表；内边距由 .sprint-list 自己控制才能对齐列 */
.set-table :deep(.el-table__expanded-cell) {
  padding: 0;
  background: var(--bg-card);
}

.set-table :deep(.el-table__expanded-cell:hover) {
  background: var(--bg-card);
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
  background: var(--ok);
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
  color: var(--danger);
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
  color: var(--ok);
}

.test-dot.is-bad {
  color: var(--danger);
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
/* ---------- 模型服务：按 provider 分组的卡片 ----------
   不用表格是因为信息是两层的：provider 一行元数据、下面一组模型。
   塞进 el-table 只能靠展开行，读起来比这更乱。 */
.head-acts {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.prov-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 60px;
}

.prov-card {
  border: 1px solid var(--line-soft);
  border-radius: 11px;
  background: var(--bg-card);
  padding: 12px 14px 13px;
  transition: border-color .16s ease, opacity .16s ease;
}

.prov-card:hover {
  border-color: var(--line-ctl);
}

.prov-card.is-hidden {
  opacity: .55;
}

.prov-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.prov-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.prov-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.src-tag {
  font-size: 10.5px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 5px;
  border: 1px solid var(--line-soft);
  color: var(--muted);
  white-space: nowrap;
}

.src-tag.is-local {
  color: var(--accent);
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.src-tag.is-off {
  color: var(--danger);
  border-color: var(--danger-line);
  background: var(--danger-soft);
}

.prov-acts {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: none;
}

.prov-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.prov-url {
  font-size: 11.5px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.model-chips {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 11px;
}

.panel-note {
  margin: -4px 0 12px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--muted);
}

/* 一个模型 = 一个容器：左边点名字设默认，右边多模态开关各管各的。
   以前整块是一个 button，开关只能做成里面一个 span 假装按钮，看不见也点不准。 */
.mchip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 5px 3px 3px;
  border: 1px solid var(--line-ctl);
  border-radius: 7px;
  background: var(--bg-sunken);
  color: var(--ink-2);
  font-size: 12px;
  transition: border-color .14s ease, color .14s ease, background .14s ease;
}

.mchip:hover {
  border-color: var(--accent-line);
}

.mchip.is-default {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.mchip-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 3px 2px 5px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.mchip-main:hover {
  color: var(--ink);
}

.mchip.is-default .mchip-main:hover {
  color: var(--accent);
}

.mchip-name {
  font-size: 12px;
}

.mchip-tag {
  font-size: 10px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
  background: var(--bg-hover);
  color: var(--muted);
}

.mchip.is-default .mchip-tag {
  background: var(--bg-card);
}

/* 多模态开关：写清「开 / 关」两个字，不只靠颜色区分——色弱用户和截图里
   都还能看出状态。关闭时是虚线灰框，一眼能看出「这里可以点开」。 */
.mchip-mm {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  border: 1px dashed var(--line-ctl);
  border-radius: 5px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
  transition: color .14s ease, border-color .14s ease, background .14s ease;
}

.mchip-mm:hover {
  border-color: var(--accent-line);
  background: var(--bg-card);
  color: var(--accent);
}

.mchip-mm.is-on {
  border-style: solid;
  border-color: var(--accent-line);
  background: var(--bg-card);
  color: var(--accent);
}

.mchip.is-default .mchip-mm.is-on {
  background: var(--bg-card);
}

.mchip-mm-state {
  font-weight: 500;
}

/* 删除按钮做成小圆点，hover 才红：模型 chip 是「可点选」的，
   常驻的红色叉号会让整排看起来像待清理的垃圾。 */
.mchip-del {
  font-size: 13px;
  line-height: 1;
  color: var(--faint);
  padding: 0 1px;
}

.mchip-del:hover {
  color: var(--danger);
}

.mchip-empty {
  font-size: 12px;
  color: var(--faint);
}

</style>
