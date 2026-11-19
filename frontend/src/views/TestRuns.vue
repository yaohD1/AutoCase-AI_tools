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
            <el-button
              v-else
              size="small"
              :icon="Download"
              :disabled="!run.results?.length"
              @click="exportReport"
            >导出报告</el-button>
          </div>

          <!-- 这份报告是什么版本、什么运行时跑出来的。换台机器复现问题时第一个要看它 -->
          <div v-if="envLine" class="run-env">{{ envLine }}</div>
          <div v-if="run.env?.steps_trimmed" class="run-env-note">
            结果体积超过上限，已省略 {{ run.env.steps_trimmed }} 条通过用例的执行步骤；失败用例的步骤都还在。
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
            <!-- 重试后才通过的条数：这一栏比「失败」更能说明用例本身的稳定性 -->
            <div class="run-metric">
              <span class="run-metric-label">不稳定</span>
              <span :class="['run-metric-value', { 'is-warn': run.flaky }]">{{ run.flaky }}</span>
            </div>
            <div class="run-metric">
              <span class="run-metric-label">耗时</span>
              <span class="run-metric-value">{{ run.duration_ms ? seconds(run.duration_ms) : '—' }}</span>
            </div>
          </div>

          <div v-if="run.error" class="run-error">{{ run.error }}</div>

          <!-- 筛选条：状态 + 分组开关。用例上百条时，先筛掉通过的一批比往下翻更快 -->
          <div v-if="run.results?.length" class="run-filters">
            <button
              v-for="tab in statusTabs"
              :key="tab.value"
              type="button"
              :class="['run-filter', { 'is-active': statusFilter === tab.value }]"
              @click="statusFilter = tab.value"
            >{{ tab.label }}<span v-if="tab.count" class="run-filter-count">{{ tab.count }}</span></button>
            <span class="run-filter-spacer"></span>
            <button
              type="button"
              :class="['run-filter', { 'is-active': groupByFile }]"
              :title="groupByFile ? '当前按文件分组，点一下平铺' : '当前平铺，点一下按文件分组'"
              @click="toggleGroupMode"
            >按文件分组</button>
          </div>

          <div v-if="availableTags.length" class="run-tagbar">
            <span class="run-tagbar-label">标签</span>
            <button
              v-for="tag in availableTags"
              :key="tag"
              type="button"
              :class="['run-tag', { 'is-active': tagFilter.has(tag) }]"
              @click="toggleTag(tag)"
            >{{ tag }}</button>
            <button v-if="tagFilter.size" type="button" class="run-tag is-clear" @click="tagFilter.clear()">清除</button>
          </div>

          <div class="run-results">
            <div v-if="!visibleResults.length" class="run-empty-line">
              {{ run.results?.length ? '当前筛选条件下没有用例。' : '这次没有执行到任何用例。' }}
            </div>

            <template v-for="group in groupedResults" :key="group.key">
              <button
                v-if="group.file"
                type="button"
                class="run-group-head"
                :title="group.file"
                @click="toggleGroup(group.key)"
              >
                <span :class="['run-group-caret', { 'is-open': !collapsedGroups.has(group.key) }]">›</span>
                <span class="run-group-name">{{ group.file }}</span>
                <span class="run-group-count">{{ group.items.length }}</span>
                <span v-if="group.failed" class="run-group-bad">{{ group.failed }} 失败</span>
              </button>

              <template v-if="!collapsedGroups.has(group.key)">
                <div
                  v-for="row in group.items"
                  :key="row.id || (row.file + row.title)"
                  :class="['run-case', 'is-' + row.status]"
                >
                  <button type="button" class="run-case-head" @click="toggleCaseCard(row)">
                    <span :class="['run-dot', 'is-' + row.status]"></span>
                    <span class="run-case-title">{{ row.title }}</span>
                    <span v-if="row.retry" class="run-retry">重试 {{ row.retry }}</span>
                    <span
                      v-if="row.steps?.length"
                      class="run-step-count"
                      :title="`共 ${row.steps.length} 步明细`"
                    >{{ stepRootCount(row) }} 步</span>
                    <span class="run-case-cost">{{ row.duration_ms ? seconds(row.duration_ms) : '' }}</span>
                  </button>
                  <div v-if="reportMeta(row)" class="run-case-file">{{ reportMeta(row) }}</div>
                  <div v-if="!isOpen(row) && row.error" class="run-case-preview">{{ firstLine(row.error) }}</div>

                  <div v-if="isOpen(row)" class="run-case-body">
                    <div v-if="row.env_error" class="run-hint is-warn">
                      目标站点不可达，这条失败可能出在环境上，不一定是脚本的问题。
                    </div>
                    <div v-if="row.where" class="run-hint">
                      失败位置 <span class="run-where">{{ row.where }}</span>
                    </div>
                    <pre v-if="row.error" class="run-case-error">{{ row.error }}</pre>
                    <div v-else-if="row.steps?.length" class="run-hint">这条用例没报错，下面是它跑过的每一步。</div>
                    <div v-else class="run-hint">这条用例没有输出错误信息。</div>

                    <!-- 执行步骤：原始 keyword 树是代码级的（一百步里大半是 Call Method 这种胶水），
                         默认只铺顶层，点开才往下钻；失败那条路径自动展开 -->
                    <div v-if="row.steps?.length" class="run-steps">
                      <div class="run-steps-head">
                        <span>执行步骤</span>
                        <span class="run-steps-hint">{{ stepSummary(row) }}</span>
                        <button
                          v-if="hasExpandableSteps(row)"
                          type="button"
                          class="run-steps-fold"
                          @click="toggleAllSteps(row)"
                        >{{ stepsAllOpen(row) ? '收起全部' : '展开全部' }}</button>
                        <button
                          v-if="hasQuietSteps(row)"
                          type="button"
                          class="run-steps-fold"
                          @click="toggleQuietSteps(row)"
                        >{{ showQuiet.has(caseKey(row)) ? '隐藏琐碎步骤' : '显示琐碎步骤' }}</button>
                      </div>
                      <div
                        v-for="step in visibleSteps(row)"
                        :key="step.idx"
                        :title="step.error || ''"
                        :class="['run-step', {
                          'is-bad': step.status === 'failed',
                          'is-quiet': step.category === 'hook' || step.category === 'fixture'
                        }]"
                      >
                        <span
                          :class="['run-step-caret', { 'is-open': expandedSteps.has(stepKey(row, step.idx)) }]"
                          :style="{ marginLeft: (step.depth || 0) * 13 + 'px' }"
                          @click.stop="toggleStep(row, step)"
                        >{{ step.children?.length ? '›' : '' }}</span>
                        <span class="run-step-title">{{ step.title }}</span>
                        <span v-if="step.category" class="run-step-cat">{{ step.category }}</span>
                        <span :class="['run-step-cost', { 'is-slow': isSlowStep(step, row) }]">
                          {{ msText(step.duration_ms) }}
                        </span>
                      </div>
                    </div>

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
import { Download, Refresh, Search } from '@element-plus/icons-vue'
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
// 步骤树的展开状态（key = 用例 + 该步在 steps 里的下标）与「显示琐碎步骤」开关。
// RF 的 keyword 树是代码级的：一个登录用例能有一百步，全铺出来没法看。
const expandedSteps = reactive(new Set())
const showQuiet = reactive(new Set())

const activeFolder = ref('')
const expandedDirs = reactive(new Set())
const run = ref(null)
const history = ref([])
let pollTimer = null

// 报告区的筛选状态。换一次运行就重置——上一批的标签在下一批里往往不存在，
// 留着会把新结果筛成空列表，看起来像「没跑出来」。
const statusFilter = ref('all')
const groupByFile = ref(true)
const collapsedGroups = reactive(new Set())
const tagFilter = reactive(new Set())

const FAILED_STATES = ['failed', 'timedOut', 'interrupted', 'error']

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

// 环境一行：版本 + 运行时 + project。换台机器复现失败时，第一眼看的就是这个。
const envLine = computed(() => {
  const env = run.value?.env || {}
  const bits = []
  if (env.tool || env.version) bits.push(`${env.tool || 'Playwright'} ${env.version || ''}`.trim())
  if (env.runtime) bits.push(env.runtime)
  if ((env.projects || []).length) bits.push(`projects ${env.projects.join(', ')}`)
  return bits.join(' · ')
})

const statusTabs = computed(() => {
  const rows = run.value?.results || []
  const count = list => rows.filter(row => list.includes(row.status)).length
  return [
    { value: 'all', label: '全部', count: rows.length },
    { value: 'failed', label: '失败', count: count(FAILED_STATES) },
    { value: 'passed', label: '通过', count: count(['passed']) },
    { value: 'skipped', label: '跳过', count: count(['skipped']) }
  ]
})

const availableTags = computed(() => {
  const tags = new Set()
  for (const row of run.value?.results || []) {
    for (const tag of row.tags || []) tags.add(String(tag))
  }
  return [...tags].sort()
})

const visibleResults = computed(() => {
  const rows = run.value?.results || []
  return rows.filter(row => {
    if (statusFilter.value === 'failed' && !FAILED_STATES.includes(row.status)) return false
    if (statusFilter.value === 'passed' && row.status !== 'passed') return false
    if (statusFilter.value === 'skipped' && row.status !== 'skipped') return false
    if (tagFilter.size && !(row.tags || []).some(tag => tagFilter.has(String(tag)))) return false
    return true
  })
})

// 分组后组内失败排前面：一个文件里十几条用例，先看到红的那几条。
const groupedResults = computed(() => {
  const rows = visibleResults.value
  if (!groupByFile.value) return [{ key: '__all', file: '', items: rows, failed: 0 }]
  const groups = new Map()
  for (const row of rows) {
    const key = row.file || '（未归属文件）'
    if (!groups.has(key)) groups.set(key, { key, file: key, items: [], failed: 0 })
    const group = groups.get(key)
    group.items.push(row)
    if (FAILED_STATES.includes(row.status)) group.failed += 1
  }
  const rank = row => (FAILED_STATES.includes(row.status) ? 0 : (row.status === 'skipped' ? 1 : 2))
  for (const group of groups.values()) group.items.sort((a, b) => rank(a) - rank(b))
  return [...groups.values()]
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

// 报告行的元信息。分组视图下文件名已经在组头上，这里不重复；平铺时才补回来。
function reportMeta(row) {
  const bits = []
  if (!groupByFile.value && row.file) bits.push(row.file)
  if (row.project) bits.push(`project ${row.project}`)
  if ((row.tags || []).length) bits.push(row.tags.join(' / '))
  return bits.join(' · ')
}

// 步骤多是毫秒级，用通用格式化会全变成 0.0s
function msText(value) {
  const millis = Number(value || 0)
  return millis < 1000 ? `${millis}ms` : `${(millis / 1000).toFixed(1)}s`
}

// 单步耗时占这条用例最慢一步的 60% 以上且超过 200ms → 标黄。
// 「Go To 花了 7 秒、其余几步加起来 0.3 秒」这种一眼就能看出来。
function isSlowStep(step, row) {
  let max = 0
  for (const item of row.steps || []) max = Math.max(max, item.duration_ms || 0)
  const cost = step.duration_ms || 0
  return cost > 200 && cost >= max * 0.6
}

/* ---------------------------------------------------------------- 步骤树
   RF 的 keyword 树、Playwright 的 step 树都是代码级嵌套：一个登录用例能拍出
   一百步，其中大半是 Call Method / Evaluate 这种 0ms 胶水。这里按 depth 还原成树，
   默认只铺顶层，点开才往下钻；0ms 的叶子默认藏掉，失败的永远显示。 */

const QUIET_STEP_MS = 10
const stepTreeCache = new WeakMap()

function caseKey(row) {
  return row.id || `${row.file}|${row.title}`
}

function stepKey(row, index) {
  return `${caseKey(row)}:${index}`
}

// 只数顶层不建树：用例列表每行都要用，508 条 × 上百步也不能在渲染里卡住
function stepRootCount(row) {
  let count = 0
  for (const step of row.steps || []) if (!step.depth) count += 1
  return count
}

function hasExpandableSteps(row) {
  return (row.steps || []).some(step => (step.depth || 0) > 0)
}

function hasQuietSteps(row) {
  return (row.steps || []).some(step => step.status === 'passed' && (step.duration_ms || 0) < QUIET_STEP_MS)
}

function stepTree(row) {
  const steps = row.steps || []
  const cached = stepTreeCache.get(row)
  if (cached && cached.src === steps) return cached.tree
  const roots = []
  const stack = []
  steps.forEach((step, idx) => {
    const node = { ...step, idx, children: [] }
    const depth = Math.max(0, step.depth || 0)
    const parent = depth > 0 ? stack[depth - 1] : null
    if (parent) parent.children.push(node)
    else roots.push(node)
    stack[depth] = node
    stack.length = depth + 1
  })
  stepTreeCache.set(row, { src: steps, tree: roots })
  return roots
}

// 0ms 且没有子节点的通过步骤默认不显示；有子节点的必须留着，否则整棵子树被藏没
function isQuietLeaf(node, row) {
  if (showQuiet.has(caseKey(row))) return false
  return node.status === 'passed' && (node.duration_ms || 0) < QUIET_STEP_MS && !node.children.length
}

function visibleSteps(row) {
  const out = []
  const walk = nodes => {
    for (const node of nodes) {
      if (isQuietLeaf(node, row)) continue
      out.push(node)
      if (node.children.length && expandedSteps.has(stepKey(row, node.idx))) walk(node.children)
    }
  }
  walk(stepTree(row))
  return out
}

function toggleStep(row, node) {
  if (!node.children.length) return
  const key = stepKey(row, node.idx)
  if (expandedSteps.has(key)) expandedSteps.delete(key)
  else expandedSteps.add(key)
}

function walkStepNodes(row, visit) {
  const walk = nodes => {
    for (const node of nodes) {
      visit(node)
      if (node.children.length) walk(node.children)
    }
  }
  walk(stepTree(row))
}

function stepsAllOpen(row) {
  const roots = stepTree(row).filter(node => node.children.length)
  return roots.length > 0 && roots.every(node => expandedSteps.has(stepKey(row, node.idx)))
}

function toggleAllSteps(row) {
  const open = !stepsAllOpen(row)
  walkStepNodes(row, node => {
    if (!node.children.length) return
    const key = stepKey(row, node.idx)
    if (open) expandedSteps.add(key)
    else expandedSteps.delete(key)
  })
}

function toggleQuietSteps(row) {
  const key = caseKey(row)
  if (showQuiet.has(key)) showQuiet.delete(key)
  else showQuiet.add(key)
}

function stepSummary(row) {
  const total = (row.steps || []).length
  const roots = stepRootCount(row)
  if (roots >= total) return `共 ${total} 步，最慢的一步已标黄`
  return `${roots} 个顶层步骤 · 共 ${total} 步明细`
}

// 失败那条路径自动展开：点开用例就看到挂在哪个关键字里，不用一层层扒。
function seedFailedSteps(row) {
  const walk = (nodes, trail) => {
    for (const node of nodes) {
      if (node.status === 'failed') {
        for (const idx of trail) expandedSteps.add(stepKey(row, idx))
      }
      if (node.children.length) walk(node.children, [...trail, node.idx])
    }
  }
  walk(stepTree(row), [])
}

function toggleTag(tag) {
  if (tagFilter.has(tag)) tagFilter.delete(tag)
  else tagFilter.add(tag)
}

function toggleGroup(key) {
  if (collapsedGroups.has(key)) collapsedGroups.delete(key)
  else collapsedGroups.add(key)
}

function toggleGroupMode() {
  groupByFile.value = !groupByFile.value
  collapsedGroups.clear()
}

function exportReport() {
  if (!run.value) return
  // 接口带 Content-Disposition: attachment，浏览器会直接下载，页面不会跳走
  window.location.href = api.testRunExportUrl(run.value.id)
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
  else {
    opened.add(key)
    if (row.steps?.length) seedFailedSteps(row)
  }
}

// 失败少的时候直接把错误摊开——否则用户只看到一行红，还得再点一下才知道为什么。
// 失败多的时候（比如整批挂）全部展开会把报告冲垮，留给用户自己点。
function autoOpenFailures(data) {
  const failed = (data.results || []).filter(row => ['failed', 'timedOut', 'interrupted', 'error'].includes(row.status))
  if (!failed.length || failed.length > 3) return
  for (const row of failed) {
    opened.add(row.id || row.title)
    if (row.steps?.length) seedFailedSteps(row)
  }
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
    // 换了运行就重置筛选：上一批的标签往往不在下一批里，留着会把结果筛成空列表。
    statusFilter.value = 'all'
    tagFilter.clear()
    collapsedGroups.clear()
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
.run-metric-value.is-warn { color: var(--warn); }

/* 环境一行：压在统计卡上方，字号刻意比正文小一档，不跟结果抢注意力 */
.run-env {
  flex: none;
  margin-top: 10px;
  padding: 6px 10px;
  border-radius: 7px;
  background: var(--bg-sunken);
  color: var(--ink-2);
  font-size: 11.5px;
  line-height: 1.6;
  word-break: break-all;
}

/* 结果过大的兜底提示：说明报告里少了什么，别让人以为步骤凭空没了 */
.run-env-note {
  flex: none;
  margin-top: -4px;
  padding: 0 10px;
  font-size: 11px;
  color: var(--warn);
  line-height: 1.6;
}

/* 筛选条：一排按钮而不是下拉，常用项一次点中 */
.run-filters { flex: none; display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.run-filter {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--line-ctl);
  border-radius: 7px;
  background: var(--bg-card);
  color: var(--ink-2);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color .14s ease, color .14s ease, background .14s ease;
}
.run-filter:hover { border-color: var(--accent); color: var(--accent); }
.run-filter.is-active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.run-filter-count { font-size: 10.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.run-filter.is-active .run-filter-count { color: var(--accent); }
.run-filter-spacer { flex: 1; }

.run-tagbar { flex: none; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.run-tagbar-label { font-size: 11px; color: var(--faint); }
.run-tag {
  height: 21px;
  padding: 0 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.run-tag:hover { border-color: var(--accent); color: var(--accent); }
.run-tag.is-active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.run-tag.is-clear { border-style: dashed; }

/* 文件分组头：一行承担「哪个文件、几条、几条失败」，点一下收起 */
.run-group-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  margin-top: 6px;
  padding: 4px 2px;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  text-align: left;
}
.run-group-head:hover { color: var(--ink-2); }
.run-group-caret {
  flex: none;
  width: 10px;
  font-size: 11px;
  color: var(--faint);
  transition: transform .14s ease;
}
.run-group-caret.is-open { transform: rotate(90deg); }
.run-group-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, Consolas, monospace;
}
.run-group-count { flex: none; font-size: 10.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.run-group-bad { flex: none; font-size: 10.5px; color: var(--danger); }

/* 步骤树：缩进表达层级，耗时分列右对齐；最慢的一步标黄 */
.run-steps {
  margin-top: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  overflow: hidden;
  /* 失败卡片整块是淡红底，步骤区要铺自己的底色：
     否则每一行都泡在红里，层级和最慢步都看不出来。 */
  background: var(--bg-card);
  /* 一条用例可能有上百步：限高让卡片保持可读，靠内部滚动看剩下的 */
  max-height: 340px;
  overflow-y: auto;
}
.run-steps-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 10px;
  background: var(--bg-sunken);
  font-size: 11.5px;
  color: var(--ink-2);
}
.run-steps-hint { font-size: 10.5px; color: var(--faint); }
.run-steps-fold {
  flex: none;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: 10.5px;
  color: var(--accent);
  cursor: pointer;
}
.run-steps-fold:first-of-type { margin-left: auto; }
.run-steps-fold + .run-steps-fold { margin-left: 10px; }
.run-steps-fold:hover { text-decoration: underline; }
/* 折叠三角：没有子步骤的位置留空占位，同层标题才对齐；缩进靠 marginLeft 跟着层级走 */
.run-step-caret {
  flex: none;
  width: 11px;
  text-align: center;
  color: var(--faint);
  cursor: pointer;
  user-select: none;
  transition: transform 0.12s ease;
}
.run-step-caret.is-open { transform: rotate(90deg); }
.run-step {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 10px;
  border-top: 1px solid var(--line-soft);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  color: var(--ink-2);
}
.run-step.is-bad { background: var(--danger-soft); color: var(--danger); }
/* Browser launch / fixture 这类框架内部步骤淡化掉：
   真正关心的「点了哪个元素、等哪个断言」才需要跳出来。 */
.run-step.is-quiet { color: var(--faint); }
.run-step-title { flex: 0 1 auto; min-width: 0; word-break: break-word; }
/* 库名紧跟步骤名（靠左），耗时单独靠右：两个都挤到右边会读成一句话 */
.run-step-cat { flex: none; margin-right: auto; font-size: 10px; color: var(--faint); }
.run-step-cost { flex: none; margin-left: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.run-step-cost.is-slow { color: var(--warn); font-weight: 600; }
.run-where { font-family: ui-monospace, Consolas, monospace; color: var(--accent); word-break: break-all; }

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
/* 步骤数：告诉用户这一条点开有执行树可看（跑过的都存了） */
.run-step-count { flex: none; font-size: 11px; color: var(--faint); }
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
