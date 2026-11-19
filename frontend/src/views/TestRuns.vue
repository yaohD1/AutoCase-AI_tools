<template>
  <div class="list-page ui-buttons run-page">
    <div class="list-head">
      <div class="list-head-main">
        <div class="eyebrow">录制 · 执行测试</div>
        <h1 class="list-title">执行测试</h1>
        <!-- 解析中不要写「共 0 条」：那读起来像「这个项目没用例」，其实只是还没算完 -->
        <p v-if="loading && !cases.length" class="list-sub">正在解析用例…</p>
        <p v-else class="list-sub">
          共 <b>{{ cases.length }}</b> 条可执行用例<template v-if="selected.size">，已选 <b>{{ selected.size }}</b> 条</template>；
          <template v-if="framework">按 {{ frameworkLabel }} 在工作区里直接执行，产物存到 AutoCase 自己的目录，不写项目的 test-results。</template>
          <template v-else>先选工作区，再挑用例。</template>
        </p>
      </div>
      <div class="list-head-actions">
        <el-button :icon="Refresh" :loading="loading" :disabled="!workspace" @click="reload">刷新用例</el-button>
        <button type="button" class="head-link" @click="$router.push('/recording')">返回自动化工具</button>
      </div>
    </div>

    <!-- 三栏：目录 / 文件（含用例）/ 报告。
         原来是「目录 + 文件 + 用例」挤在一列，508 条用例时根本找不到文件；
         把目录单独拆一列之后，一列只看一层，勾选状态跨目录保留。 -->
    <div class="run-shell">
      <aside class="run-dirs">
        <div class="run-col-head">
          <span>目录</span>
          <span class="run-col-count">{{ cases.length }}</span>
        </div>
        <div class="run-dirs-body">
          <div v-if="!workspace" class="run-tree-empty">还没有选择工作区，回到自动化工具配置一个。</div>
          <div v-else-if="loading" class="run-tree-empty">正在解析用例…</div>
          <div v-else-if="!cases.length" class="run-tree-empty">{{ note || '这个工作区里没有找到可执行的用例' }}</div>

          <template v-else>
            <button
              type="button"
              :class="['dir-row', 'is-root', { 'is-active': activeFolder === '' && !searching, 'is-dim': searching }]"
              @click="pickFolder('')"
            >
              <span class="dir-name">全部用例</span>
              <span class="dir-count">{{ cases.length }}</span>
            </button>

            <div
              v-for="row in visibleFolderRows"
              :key="row.path"
              :class="['dir-row', { 'is-active': activeFolder === row.path, 'is-dim': searching }]"
            >
              <button
                v-if="row.hasChildren"
                type="button"
                class="dir-caret"
                :class="{ 'is-open': isDirOpen(row.path) }"
                :title="isDirOpen(row.path) ? '收起子目录' : '展开子目录'"
                @click="toggleDir(row.path)"
              >›</button>
              <span v-else class="dir-caret is-blank"></span>
              <el-checkbox
                size="small"
                :model-value="checkState(row.cases) === 'all'"
                :indeterminate="checkState(row.cases) === 'some'"
                @change="value => toggleMany(row.cases, value)"
              />
              <button
                type="button"
                class="dir-label"
                :style="{ paddingLeft: (row.depth * 9) + 'px' }"
                :title="row.path"
                @click="pickFolder(row.path)"
              >
                <span class="dir-name">{{ row.name }}</span>
                <span class="dir-count">{{ row.count }}</span>
              </button>
            </div>

            <div v-if="searching" class="run-tree-tip">搜索时不再按目录过滤，中间列显示全部命中。</div>
          </template>
        </div>
      </aside>

      <aside class="run-files">
        <div class="run-files-head">
          <el-input
            v-model="keyword"
            size="small"
            clearable
            :prefix-icon="Search"
            placeholder="搜索用例或文件"
          />
        </div>
        <div class="run-col-head">
          <span class="run-col-title" :title="scopeLabel">{{ scopeLabel }}</span>
          <button
            v-if="fileGroups.length"
            type="button"
            class="list-act"
            @click="toggleScopeAll"
          >{{ scopeAllPicked ? '取消全选' : '全选' }}</button>
        </div>

        <div class="run-files-body">
          <div v-if="!workspace || loading || !cases.length" class="run-tree-empty">　</div>
          <div v-else-if="!fileGroups.length" class="run-tree-empty">
            {{ searching ? `没有匹配「${keyword}」的用例` : '这个目录下没有文件' }}
          </div>

          <div v-for="file in fileGroups" :key="file.path" class="file-group">
            <div :class="['file-row', { 'is-open': isFileOpen(file.path) }]">
              <el-checkbox
                size="small"
                :model-value="checkState(file.cases) === 'all'"
                :indeterminate="checkState(file.cases) === 'some'"
                @change="value => toggleMany(file.cases, value)"
              />
              <button type="button" class="file-label" :title="file.path" @click="toggleFile(file.path)">
                <span :class="['tree-caret', { 'is-open': isFileOpen(file.path) }]">›</span>
                <span class="file-name">{{ file.name }}</span>
              </button>
              <span class="file-count">{{ file.cases.length }}</span>
            </div>

            <div v-if="isFileOpen(file.path)" class="case-list">
              <label
                v-for="item in file.cases"
                :key="item.id"
                :class="['case-row', { 'is-picked': selected.has(item.id) }]"
              >
                <el-checkbox
                  size="small"
                  :model-value="selected.has(item.id)"
                  @change="value => toggleOne(item.id, value)"
                />
                <span class="case-main">
                  <span class="case-title">{{ item.title }}</span>
                  <span v-if="caseMeta(item)" class="case-meta">{{ caseMeta(item) }}</span>
                </span>
                <span v-if="item.generated" class="chip is-warn case-gen">生成</span>
              </label>
            </div>
          </div>
        </div>
      </aside>

      <section class="run-report">
        <div v-if="!run" class="run-empty">
          <div class="run-empty-title">还没有运行记录</div>
          <p>勾选左侧用例后点「执行所选用例」；跑完这里会列出每条用例的状态、耗时和失败截图。</p>
        </div>

        <template v-else>
          <div class="run-bar">
            <span class="run-stamp">{{ formatTime(run.started_at) }}</span>
            <span :class="['chip', statusTone(run.status)]">{{ statusLabel(run.status) }}</span>
            <span v-if="run.duration_ms" class="run-cost">{{ seconds(run.duration_ms) }}</span>
            <span class="run-bar-spacer"></span>
            <el-select
              v-if="history.length > 1"
              :model-value="run.id"
              size="small"
              class="run-history"
              placeholder="运行历史"
              @change="openRun"
            >
              <el-option
                v-for="item in history"
                :key="item.id"
                :label="historyLabel(item)"
                :value="item.id"
              />
            </el-select>
            <el-button v-if="run.status === 'running'" size="small" @click="stopRun">停止</el-button>
          </div>

          <div v-if="run.status === 'running'" class="run-progress">
            <div class="run-progress-text">
              {{ run.progress?.message || '正在执行' }}
              <span v-if="run.progress?.total" class="run-progress-count">
                {{ run.progress.done }} / {{ run.progress.total }}
              </span>
            </div>
            <div class="run-progress-bar"><span :style="{ width: percent + '%' }"></span></div>
          </div>

          <div class="run-metrics">
            <div class="run-metric">
              <span class="run-metric-label">通过</span>
              <span class="run-metric-value is-ok">{{ run.passed }}</span>
            </div>
            <div class="run-metric">
              <span class="run-metric-label">失败</span>
              <span :class="['run-metric-value', { 'is-bad': run.failed }]">{{ run.failed }}</span>
            </div>
            <div class="run-metric">
              <span class="run-metric-label">跳过</span>
              <span class="run-metric-value">{{ run.skipped }}</span>
            </div>
            <div class="run-metric">
              <span class="run-metric-label">耗时</span>
              <span class="run-metric-value">{{ run.duration_ms ? seconds(run.duration_ms) : '—' }}</span>
            </div>
          </div>

          <div v-if="run.error" class="run-error">{{ run.error }}</div>

          <div class="run-results">
            <div v-if="!run.results?.length && run.status !== 'running'" class="run-empty-line">
              这次没有执行到任何用例。
            </div>
            <template v-for="row in run.results || []" :key="row.id || (row.file + row.title)">
              <div :class="['run-case', 'is-' + row.status]">
                <button type="button" class="run-case-head" @click="toggleCaseCard(row)">
                  <span :class="['run-dot', 'is-' + row.status]"></span>
                  <span class="run-case-title">{{ row.title }}</span>
                  <span v-if="row.retry" class="run-retry">重试 {{ row.retry }}</span>
                  <span class="run-case-cost">{{ row.duration_ms ? seconds(row.duration_ms) : '' }}</span>
                </button>
                <div class="run-case-file">{{ row.file || '—' }}<template v-if="row.project"> · {{ row.project }}</template></div>
                <div v-if="!isOpen(row) && row.error" class="run-case-preview">{{ firstLine(row.error) }}</div>

                <div v-if="isOpen(row)" class="run-case-body">
                  <div v-if="row.env_error" class="run-hint is-warn">
                    目标站点不可达，这条失败可能出在环境上，不一定是脚本的问题。
                  </div>
                  <pre v-if="row.error" class="run-case-error">{{ row.error }}</pre>
                  <div v-else class="run-hint">这条用例没有输出错误信息。</div>
                  <div v-if="row.screenshots?.length" class="run-shots">
                    <template v-for="shot in row.screenshots" :key="shot">
                      <a
                        v-if="!brokenShots.has(shot)"
                        :href="artifactUrl(shot)"
                        target="_blank"
                        rel="noopener"
                        class="run-shot"
                      >
                        <img :src="artifactUrl(shot)" :alt="shot" loading="lazy" @error="markShotBroken(shot)" />
                      </a>
                      <!-- 产物只保留最近 20 次：旧运行点开时截图可能已经被清掉了，
                           不要留一个破图标在那儿。 -->
                      <span v-else class="run-shot is-missing">截图已清理</span>
                    </template>
                  </div>
                  <div v-if="row.artifacts?.length" class="run-artifacts">
                    <a
                      v-for="item in row.artifacts.filter(entry => entry.type !== 'image/png')"
                      :key="item.path"
                      :href="artifactUrl(item.path)"
                      target="_blank"
                      rel="noopener"
                      class="list-act"
                    >{{ item.name || item.path }}</a>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </template>
      </section>
    </div>

    <div class="run-foot">
      <span class="run-picked">
        已选 <b>{{ selected.size }}</b> 条<template v-if="selected.size"> · 跨 {{ selectedDirs }} 个目录</template>
      </span>
      <button v-if="selected.size" type="button" class="list-act" @click="clearSelected">清空</button>
      <span class="run-foot-spacer"></span>
      <el-button
        type="primary"
        size="small"
        :loading="starting"
        :disabled="!selected.size || activeRun"
        @click="execute"
      >执行所选用例</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import api from '../api'

const STORAGE_KEY = 'autocase.recording.config'
const POLL_MS = 1500

const route = useRoute()
const workspace = ref('')
const cases = ref([])
const framework = ref('')
const note = ref('')
const keyword = ref('')
const loading = ref(false)
const starting = ref(false)

const selected = reactive(new Set())
const expandedFiles = reactive(new Set())
const opened = reactive(new Set())
const brokenShots = reactive(new Set())

const activeFolder = ref('')
const expandedDirs = reactive(new Set())
const run = ref(null)
const history = ref([])
let pollTimer = null

const frameworkLabel = computed(() => ({
  'playwright-ts': 'Playwright TypeScript',
  'robot-selenium': 'Robot Framework'
}[framework.value] || framework.value))

const activeRun = computed(() => run.value && run.value.status === 'running')
const searching = computed(() => !!keyword.value.trim())

const percent = computed(() => {
  const progress = run.value?.progress
  if (!progress || !progress.total) return 8
  return Math.min(100, Math.round((progress.done / progress.total) * 100))
})

const caseIndex = computed(() => {
  const map = new Map()
  for (const item of cases.value) map.set(item.id, item)
  return map
})

// 搜索按标题 / 文件 / 标签过滤，但**不按当前目录**过滤：
// 在 500 条用例里找东西时，用户多半不知道它在哪个目录下。
const matched = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return cases.value
  return cases.value.filter(item => (
    item.title.toLowerCase().includes(text)
    || item.file.toLowerCase().includes(text)
    || (item.tags || []).some(tag => String(tag).toLowerCase().includes(text))
  ))
})

// 目录树按路径分段重建：后端给的是扁平的 `测试用例层/信令跟踪/N2信令分析`。
// 每个节点都挂着它下面**递归**的全部用例，父目录的勾选与计数才准。
const folderTree = computed(() => {
  const root = { path: '', name: '全部用例', children: new Map(), cases: [] }
  for (const item of cases.value) {
    root.cases.push(item)
    let node = root
    let acc = ''
    for (const part of String(item.dir || '').split('/').filter(Boolean)) {
      acc = acc ? acc + '/' + part : part
      if (!node.children.has(part)) {
        node.children.set(part, { path: acc, name: part, children: new Map(), cases: [] })
      }
      node = node.children.get(part)
      node.cases.push(item)
    }
  }
  return root
})

const folderRows = computed(() => {
  const rows = []
  const walk = (node, depth) => {
    const kids = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh'))
    rows.push({
      path: node.path,
      name: node.name,
      depth,
      count: node.cases.length,
      cases: node.cases,
      hasChildren: kids.length > 0
    })
    for (const kid of kids) walk(kid, depth + 1)
  }
  const top = [...folderTree.value.children.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh'))
  for (const node of top) walk(node, 0)
  return rows
})

// 71 个目录全铺开要滚很久；默认只展开一级，剩下的按需点箭头。
// 注意「折叠」只影响显示：折叠着的目录照样能整组勾选，点目录名也照样筛选右侧文件。
const visibleFolderRows = computed(() => {
  const open = new Set(expandedDirs)
  return folderRows.value.filter(row => {
    for (let depth = 0; depth < row.depth; depth += 1) {
      const parent = parentOf(row.path, depth)
      if (parent && !open.has(parent)) return false
    }
    return true
  })
})

const scopedCases = computed(() => {
  if (searching.value) return matched.value
  if (!activeFolder.value) return cases.value
  const prefix = activeFolder.value + '/'
  return cases.value.filter(item => item.dir === activeFolder.value || item.dir.startsWith(prefix))
})

const fileGroups = computed(() => {
  const files = new Map()
  for (const item of scopedCases.value) {
    if (!files.has(item.file)) {
      files.set(item.file, { path: item.file, name: baseName(item.file), cases: [] })
    }
    files.get(item.file).cases.push(item)
  }
  return [...files.values()].sort((a, b) => a.path.localeCompare(b.path, 'zh'))
})

const scopeLabel = computed(() => {
  if (searching.value) return `搜索结果 · ${fileGroups.value.length} 个文件`
  const name = activeFolder.value ? baseName(activeFolder.value) : '全部用例'
  return `${name} · ${fileGroups.value.length} 个文件`
})

const scopeAllPicked = computed(() => {
  const all = fileGroups.value.flatMap(file => file.cases)
  return all.length > 0 && all.every(item => selected.has(item.id))
})

// 「跨 N 个目录」比「已选 N 条」更能说明这次选得散不散。
const selectedDirs = computed(() => {
  const dirs = new Set()
  for (const id of selected) {
    const item = caseIndex.value.get(id)
    if (item) dirs.add(item.dir || '.')
  }
  return dirs.size
})

onMounted(() => {
  workspace.value = pickWorkspace()
  if (!workspace.value) return
  loadCases()
  loadHistory()
})

onBeforeUnmount(() => {
  clearTimeout(pollTimer)
})

function pickWorkspace() {
  const fromQuery = typeof route.query.workspace === 'string' ? route.query.workspace.trim() : ''
  if (fromQuery) return fromQuery
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return String(saved.workspace || '').trim()
  } catch {
    return ''
  }
}

function baseName(path) {
  const text = String(path || '')
  const cut = text.lastIndexOf('/')
  return cut >= 0 ? text.slice(cut + 1) : text
}

function seconds(ms) {
  const value = Number(ms) / 1000
  if (!Number.isFinite(value)) return ''
  if (value < 60) return value.toFixed(1) + 's'
  const min = Math.floor(value / 60)
  return min + 'm' + String(Math.round(value - min * 60)).padStart(2, '0') + 's'
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value.endsWith('Z') ? value : value + 'Z')
  if (Number.isNaN(date.getTime())) return value
  const pad = num => String(num).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const STATUS_LABELS = {
  running: '执行中',
  passed: '全部通过',
  failed: '有失败',
  stopped: '已停止',
  error: '未能执行'
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || '—'
}

function statusTone(status) {
  if (status === 'passed') return 'is-ok'
  if (status === 'failed') return 'is-danger'
  if (status === 'running') return 'is-accent'
  if (status === 'error') return 'is-warn'
  return ''
}

function historyLabel(item) {
  const time = formatTime(item.started_at).slice(6)
  return `${time} · ${item.passed}/${item.total} 通过${item.status === 'failed' ? ' · 有失败' : ''}`
}

function checkState(list) {
  if (!list.length) return 'none'
  let hit = 0
  for (const item of list) if (selected.has(item.id)) hit += 1
  if (hit === 0) return 'none'
  return hit === list.length ? 'all' : 'some'
}

function toggleOne(id, value) {
  if (value) selected.add(id)
  else selected.delete(id)
}

function toggleMany(list, value) {
  for (const item of list) {
    if (value) selected.add(item.id)
    else selected.delete(item.id)
  }
}

function toggleScopeAll() {
  toggleMany(fileGroups.value.flatMap(file => file.cases), !scopeAllPicked.value)
}

function clearSelected() {
  selected.clear()
}

function pickFolder(path) {
  activeFolder.value = path
  // 换目录就清搜索：否则用户会以为新目录里一条用例都没有。
  keyword.value = ''
}

function parentOf(path, depth) {
  const parts = String(path || '').split('/')
  if (parts.length <= depth + 1) return ''
  return parts.slice(0, depth + 1).join('/')
}

function isDirOpen(path) {
  return expandedDirs.has(path)
}

function toggleDir(path) {
  if (expandedDirs.has(path)) expandedDirs.delete(path)
  else expandedDirs.add(path)
}

// 搜索时直接展开命中文件：只给一串文件名的话，用户还得再点开才知道哪条命中了。
// 命中文件太多（宽泛关键词）时不展开，否则一次铺出上百条用例反而更看不清。
const searchExpands = computed(() => searching.value && fileGroups.value.length <= 30)

function isFileOpen(path) {
  return searchExpands.value || expandedFiles.has(path)
}

function toggleFile(path) {
  if (expandedFiles.has(path)) expandedFiles.delete(path)
  else expandedFiles.add(path)
}

// Robot 的 suite 就是目录路径，跟左边分组重复；Playwright 的 suite 是 describe 链，得留着。
function caseMeta(item) {
  const bits = []
  if (item.framework !== 'robot-selenium' && item.suite) bits.push(item.suite)
  if (item.project) bits.push(item.project)
  if ((item.tags || []).length) bits.push(item.tags.join(' / '))
  return bits.join(' · ')
}

function firstLine(text) {
  return String(text || '').split('\n').find(line => line.trim()) || ''
}

function markShotBroken(path) {
  brokenShots.add(path)
}

function isOpen(row) {
  return opened.has(row.id || row.title)
}

function toggleCaseCard(row) {
  const key = row.id || row.title
  if (opened.has(key)) opened.delete(key)
  else opened.add(key)
}

// 失败少的时候直接把错误摊开——否则用户只看到一行红，还得再点一下才知道为什么。
// 失败多的时候（比如整批挂）全部展开会把报告冲垮，留给用户自己点。
function autoOpenFailures(data) {
  const failed = (data.results || []).filter(row => ['failed', 'timedOut', 'interrupted', 'error'].includes(row.status))
  if (!failed.length || failed.length > 3) return
  for (const row of failed) opened.add(row.id || row.title)
}

function artifactUrl(path) {
  return api.testRunArtifactUrl(run.value.id, path)
}

async function loadCases(refresh = false) {
  loading.value = true
  try {
    const { data } = await api.listTestCases(workspace.value, refresh)
    cases.value = data.cases || []
    framework.value = data.framework || ''
    note.value = data.note || ''
    // 刷新后保留仍然存在的勾选，别把用户挑好的清掉。
    const alive = new Set(cases.value.map(item => item.id))
    for (const id of [...selected]) if (!alive.has(id)) selected.delete(id)
    if (activeFolder.value && !folderRows.value.some(row => row.path === activeFolder.value)) {
      activeFolder.value = ''
    }
    // 一级目录默认展开：进页面就能看到 17 个模块，而不是只有一行「测试用例层」。
    if (!expandedDirs.size) {
      for (const row of folderRows.value) if (row.depth === 0) expandedDirs.add(row.path)
    }
    if (!history.value.length) await loadHistory()
  } catch (error) {
    cases.value = []
    framework.value = ''
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

function reload() {
  loadCases(true)
}

async function loadHistory() {
  if (!workspace.value) return
  try {
    const { data } = await api.listTestRuns(workspace.value, 20)
    history.value = data.runs || []
    // 没有正在看的运行时，默认落在最近一次（可能是执行中）上，免得右半屏永远空着。
    if (!run.value && history.value.length) await showRun(history.value[0].id)
  } catch {
    history.value = []
  }
}

async function openRun(id) {
  if (!id || id === run.value?.id) return
  await showRun(id)
}

async function showRun(id) {
  try {
    const { data } = await api.getTestRun(id)
    run.value = data
    if (data.status === 'running') {
      schedulePoll()
    } else {
      clearTimeout(pollTimer)
      autoOpenFailures(data)
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(pollOnce, POLL_MS)
}

async function pollOnce() {
  if (!run.value || run.value.status !== 'running') return
  try {
    const { data } = await api.getTestRun(run.value.id)
    run.value = data
    if (data.status === 'running') {
      schedulePoll()
    } else {
      autoOpenFailures(data)
      await loadHistory()
      if (data.failed) ElMessage.warning(`执行完成：${data.failed} 条失败`)
      else ElMessage.success(`执行完成：${data.passed} 条通过`)
    }
  } catch {
    // 网络抖一下不该把界面卡在「执行中」，下次轮询接着来。
    schedulePoll()
  }
}

async function execute() {
  if (!selected.size || activeRun.value) return
  starting.value = true
  try {
    const { data } = await api.startTestRun({
      workspace: workspace.value,
      framework: framework.value,
      cases: [...selected]
    })
    run.value = data
    opened.clear()
    await loadHistory()
    schedulePoll()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    starting.value = false
  }
}

async function stopRun() {
  if (!run.value) return
  try {
    await api.stopTestRun(run.value.id)
    ElMessage.info('已请求停止，等当前这条用例收尾')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function errorText(error) {
  return error?.response?.data?.error || error?.message || '操作失败'
}
</script>

<style scoped>
.run-page { height: calc(100dvh - 132px); }
.head-link {
  border: 0;
  background: none;
  padding: 0 2px;
  font: inherit;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
}
.head-link:hover { color: var(--accent); }

.run-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 186px 256px minmax(0, 1fr);
  background: var(--bg-card);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-card);
  overflow: hidden;
}

/* ---------- 目录列 ---------- */
.run-dirs {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--line-soft);
}
.run-col-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  border-bottom: 1px solid var(--line-faint);
  font-size: 11.5px;
  color: var(--muted);
}
.run-col-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-col-count { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--faint); }
.run-dirs-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px 6px 10px;
}
.run-dirs-body::-webkit-scrollbar { width: 8px; }
.run-dirs-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

.run-tree-empty { padding: 18px 10px; font-size: 12px; line-height: 1.7; color: var(--muted); }
.run-tree-tip { margin-top: 8px; padding: 0 6px; font-size: 11px; line-height: 1.6; color: var(--faint); }

.dir-caret {
  flex: none;
  width: 12px;
  height: 18px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: 11px;
  line-height: 18px;
  color: var(--faint);
  cursor: pointer;
  transition: transform .14s ease;
}
.dir-caret.is-open { transform: rotate(90deg); }
.dir-caret.is-blank { cursor: default; }

.dir-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 27px;
  padding: 1px 4px;
  border-radius: 7px;
}
.dir-row:hover { background: var(--bg-hover); }
.dir-row.is-active { background: var(--accent-soft); }
.dir-row.is-dim { opacity: .5; }
.dir-row.is-root { width: 100%; border: 0; background: none; font: inherit; cursor: pointer; text-align: left; }
.dir-row.is-root.is-active { background: var(--accent-soft); }

.dir-label {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex: 1;
  padding: 2px 0;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
  color: inherit;
}
.dir-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-row.is-active .dir-name { color: var(--accent-ink); font-weight: 600; }
.dir-count {
  flex: none;
  font-size: 10.5px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}

/* ---------- 文件列 ---------- */
.run-files {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--line-soft);
}
.run-files-head { flex: none; padding: 10px 10px 8px; border-bottom: 1px solid var(--line-faint); }
.run-files-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px 6px 10px;
}
.run-files-body::-webkit-scrollbar { width: 8px; }
.run-files-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

.file-group + .file-group { margin-top: 1px; }
.file-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 2px 6px;
  border-radius: 7px;
}
.file-row:hover { background: var(--bg-hover); }
.file-row.is-open { background: var(--bg-sunken); }
.file-label {
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
  flex: 1;
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  text-align: left;
  cursor: pointer;
  color: inherit;
}
.file-name {
  min-width: 0;
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-count {
  flex: none;
  min-width: 14px;
  text-align: right;
  font-size: 10.5px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.tree-caret {
  flex: none;
  width: 9px;
  font-size: 11px;
  color: var(--faint);
  transition: transform .14s ease;
}
.tree-caret.is-open { transform: rotate(90deg); }

.case-list { padding: 2px 4px 4px 24px; }
.case-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 5px;
  border-radius: 6px;
  cursor: pointer;
}
.case-row:hover { background: var(--bg-hover); }
.case-row.is-picked { background: var(--accent-soft); }
.case-main { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
.case-title { font-size: 12px; line-height: 1.45; color: var(--ink-2); word-break: break-word; }
.case-row.is-picked .case-title { color: var(--ink); }
.case-meta {
  font-size: 10.5px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.case-gen { flex: none; }

/* ---------- 底部操作条 ---------- */
.run-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 2px 0;
}
.run-picked { font-size: 12px; color: var(--muted); }
.run-picked b { color: var(--ink-2); font-weight: 600; font-variant-numeric: tabular-nums; }
.run-foot-spacer { flex: 1; }

/* ---------- 右侧报告 ---------- */
.run-report {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px 16px 16px;
  overflow-y: auto;
}
.run-report::-webkit-scrollbar { width: 8px; }
.run-report::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

.run-empty { margin: auto; max-width: 360px; text-align: center; color: var(--muted); font-size: 12.5px; line-height: 1.7; }
.run-empty-title { font-size: 14px; color: var(--ink-2); margin-bottom: 6px; }

.run-bar { display: flex; align-items: center; gap: 8px; flex: none; }
.run-stamp { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.run-cost { font-size: 11.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.run-bar-spacer { flex: 1; }
.run-history { width: 180px; }
.run-history :deep(.el-select__wrapper) { min-height: 26px; font-size: 12px; }

.run-progress { flex: none; margin-top: 10px; }
.run-progress-text {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--ink-2);
}
.run-progress-count { color: var(--faint); font-variant-numeric: tabular-nums; }
.run-progress-bar {
  margin-top: 6px;
  height: 3px;
  border-radius: 2px;
  background: var(--bg-sunken);
  overflow: hidden;
}
.run-progress-bar span {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width .3s ease;
}

.run-metrics {
  flex: none;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0;
}
.run-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 11px;
  border-radius: 10px;
  background: var(--bg-sunken);
}
.run-metric-label { font-size: 11.5px; color: var(--muted); }
.run-metric-value { font-size: 19px; line-height: 1.3; color: var(--ink); font-variant-numeric: tabular-nums; }
.run-metric-value.is-ok { color: var(--ok); }
.run-metric-value.is-bad { color: var(--danger); }

.run-error {
  flex: none;
  margin-bottom: 10px;
  padding: 9px 11px;
  border-radius: 9px;
  background: var(--warn-soft);
  color: var(--warn);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.run-results { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; }
.run-empty-line { padding: 18px 4px; font-size: 12.5px; color: var(--muted); }

.run-case {
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  padding: 8px 11px 7px;
}
.run-case.is-failed, .run-case.is-timedOut, .run-case.is-interrupted, .run-case.is-error {
  background: var(--danger-soft);
  border-color: var(--danger-line);
}
.run-case-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.run-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
.run-dot.is-passed { background: var(--ok); }
.run-dot.is-failed, .run-dot.is-timedOut, .run-dot.is-interrupted, .run-dot.is-error { background: var(--danger); }
.run-dot.is-skipped { background: var(--faint); }
.run-dot.is-flaky { background: var(--warn); }
.run-case-title {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-retry { flex: none; font-size: 11px; color: var(--warn); }
.run-case-cost { flex: none; font-size: 11px; color: var(--faint); font-variant-numeric: tabular-nums; }
.run-case-file {
  margin: 2px 0 0 16px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-case-preview {
  margin: 3px 0 0 16px;
  font-size: 11.5px;
  color: var(--danger);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-case-body { margin: 8px 0 0 16px; }
.run-hint { font-size: 11.5px; color: var(--muted); }
.run-hint.is-warn { color: var(--warn); margin-bottom: 6px; }
.run-case-error {
  margin: 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  border-radius: 8px;
  background: var(--bg-sunken);
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.run-shots { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.run-shot {
  display: block;
  width: 168px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
}
.run-shot img { display: block; width: 100%; height: auto; }
.run-shot.is-missing {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 64px;
  border-style: dashed;
  font-size: 11.5px;
  color: var(--faint);
}
.run-artifacts { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; }

@media (max-width: 1240px) {
  .run-shell { grid-template-columns: 164px 226px minmax(0, 1fr); }
  .run-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
