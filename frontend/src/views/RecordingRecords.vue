<template>
  <div class="records-page workspace-buttons">
    <div class="page-header">
      <div class="page-intro">
        <div class="eyebrow">RECORDING · GENERATIONS</div>
        <h1>生成记录</h1>
        <p>录制生成写入的每一次都在这里：可以预览文件内容、提交到 Git，或撤销这次写入。</p>
      </div>
      <div class="page-actions">
        <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
        <el-button type="primary" @click="$router.push('/recording')">返回录制</el-button>
      </div>
    </div>

    <el-card class="panel-card">
      <div class="records-layout">
        <div class="record-list">
          <el-empty v-if="!records.length && !loading" description="还没有生成记录" :image-size="60" />
          <button
            v-for="item in records"
            :key="item.id"
            type="button"
            :class="['record-item', { active: selected && selected.id === item.id }]"
            @click="openRecord(item.id)"
          >
            <span class="record-path" :title="item.target_path">{{ item.target_path }}</span>
            <span class="record-meta">
              <el-tag size="small" :type="recordStatusTone(item.status)" effect="light">{{ recordStatusLabel(item.status) }}</el-tag>
              <span class="record-time">{{ formatDate(item.created_at) }}</span>
            </span>
          </button>
        </div>

        <div class="record-detail">
          <el-empty v-if="!selected" description="选择一条记录查看预览" :image-size="60" />
          <template v-else>
            <div class="detail-head">
              <div class="detail-head-main">
                <div class="detail-title">{{ selected.target_path }}</div>
                <div class="detail-meta">
                  {{ frameworkName(selected.framework) }} · 模型 {{ selected.model || '—' }} · {{ formatDate(selected.created_at) }}
                </div>
              </div>
              <el-tag :type="recordStatusTone(selected.status)" effect="light">{{ recordStatusLabel(selected.status) }}</el-tag>
            </div>

            <el-alert
              v-if="selected.changed_since_generation && selected.status === 'generated'"
              title="文件在生成后已被改动或删除，提交和撤销都会被拒绝。请先确认文件内容。"
              type="warning"
              :closable="false"
              class="detail-alert"
            />
            <el-alert
              v-if="selected.status === 'committed'"
              :title="`已提交：${selected.commit_message || ''}（${(selected.commit_hash || '').slice(0, 8)}）`"
              type="success"
              :closable="false"
              class="detail-alert"
            />

            <div v-if="(selected.steps || []).length" class="detail-section">
              <div class="detail-label">录制的步骤（{{ selected.steps.length }}）</div>
              <ol class="mini-steps">
                <li v-for="step in selected.steps" :key="step.no">
                  <span class="mini-kind">{{ stepKindLabel(step.type) }}</span>
                  <span class="mini-text">{{ step.text || step.value || '' }}</span>
                </li>
              </ol>
            </div>

            <div class="detail-section">
              <div class="detail-label">文件内容预览{{ selected.truncated ? '（已截断）' : '' }}</div>
              <pre :class="['code-preview', { 'is-empty': !selected.file_exists }]">{{ selected.file_exists ? selected.content : '文件当前不存在（生成后可能已被删除或撤回）' }}</pre>
            </div>

            <div v-if="selected.reply" class="detail-section">
              <div class="detail-label">生成 agent 的说明</div>
              <div class="agent-reply">{{ selected.reply }}</div>
            </div>

            <div v-if="selected.status === 'generated'" class="detail-section commit-box">
              <div class="detail-label">提交说明</div>
              <el-input v-model="commitMessage" maxlength="500" show-word-limit />
              <div class="commit-actions">
                <el-button type="primary" :loading="committing" :disabled="selected.changed_since_generation" @click="commitSelected">
                  提交到 Git
                </el-button>
              </div>
            </div>

            <div v-if="selected.status === 'generated' || selected.status === 'committed'" class="detail-actions">
              <el-button type="danger" plain :loading="reverting" @click="revertSelected">
                {{ selected.status === 'generated' ? '撤销写入' : '回滚这次提交' }}
              </el-button>
            </div>
          </template>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api from '../api'

const FRAMEWORKS = [
  { value: 'robot-selenium', label: 'Robot Framework（SeleniumLibrary）' },
  { value: 'playwright-ts', label: 'Playwright TypeScript' }
]
const STEP_LABELS = {
  goto: '打开页面', click: '点击', fill: '输入', select: '选择', new_page: '新标签页',
  assert: '断言', wait: '等待', sleep: '固定等待'
}

const records = ref([])
const loading = ref(false)
const selected = ref(null)
const commitMessage = ref('')
const committing = ref(false)
const reverting = ref(false)
// 从录制页带 ?id= 跳过来时优先选中那一条
const route = useRoute()
const wantedId = ref(typeof route.query.id === 'string' ? route.query.id : '')

onMounted(load)

async function load() {
  loading.value = true
  try {
    const { data } = await api.listRecordingGenerations()
    records.value = data.generations || []
    // 默认选中最新一条：避免右半屏永远是「选择一条记录」的空态。
    const target = records.value.find(item => item.id === wantedId.value) || records.value[0]
    if (target && (!selected.value || selected.value.id !== target.id)) await openRecord(target.id)
    if (!target) selected.value = null
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

// 从录制页带着 generation_id 跳过来时，选中那一条。
async function reload() {
  if (selected.value) wantedId.value = selected.value.id
  await load()
}

async function openRecord(id) {
  try {
    const { data } = await api.getRecordingGeneration(id)
    selected.value = data
    commitMessage.value = `录制生成：${data.title || data.target_path}`
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function commitSelected() {
  committing.value = true
  try {
    await api.commitRecordingGeneration(selected.value.id, commitMessage.value)
    ElMessage.success('已提交')
    await openRecord(selected.value.id)
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    committing.value = false
  }
}

async function revertSelected() {
  const committed = selected.value.status === 'committed'
  try {
    await ElMessageBox.confirm(
      committed ? '将生成一个回滚提交，撤销这次录制生成的文件。确定吗？' : '将删除新写入的文件，或恢复生成前的原内容。确定吗？',
      committed ? '回滚提交' : '撤销写入',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  reverting.value = true
  try {
    await api.revertRecordingGeneration(selected.value.id)
    ElMessage.success('已撤销')
    await openRecord(selected.value.id)
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    reverting.value = false
  }
}

function frameworkName(value) {
  return (FRAMEWORKS.find(f => f.value === value) || {}).label || (value || '—')
}

function stepKindLabel(type) { return STEP_LABELS[type] || type }

function recordStatusLabel(status) {
  return ({ generated: '未提交', committed: '已提交', reverted: '已撤销' })[status] || status
}

function recordStatusTone(status) {
  return ({ generated: 'warning', committed: 'success', reverted: 'info' })[status] || 'info'
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '-'
}

function errorText(error) {
  if (error && error.response && error.response.data && error.response.data.error) return error.response.data.error
  return (error && error.message) || '操作失败'
}
</script>

<style scoped>
/* 与录制页同一套视觉：浅底、细边框、紧凑行 */
.records-page { max-width:1440px; margin:0 auto; }
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:16px; }
.page-intro { min-width:0; }
.eyebrow { color:#0071e3; font-size:10.5px; font-weight:700; letter-spacing:1.2px; margin-bottom:3px; text-transform:uppercase; }
h1 { margin:0; color:#1d1d1f; font-size:22px; font-weight:700; letter-spacing:-.3px; }
.page-header p { margin:4px 0 0; color:#6e6e73; font-size:13px; line-height:1.5; max-width:760px; }
.page-actions { display:flex; align-items:center; gap:10px; flex:none; padding-top:2px; }

.panel-card { border:1px solid #ebedf0; border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px rgba(16,24,40,.05); }
.panel-card :deep(.el-card__body) { padding:12px; }

.records-layout { display:grid; grid-template-columns:340px minmax(0,1fr); gap:12px; align-items:start; }

/* 列表：一行一条，路径 + 状态 + 时间 */
.record-list { display:flex; flex-direction:column; gap:2px; min-height:0; max-height:calc(100dvh - 320px); padding-right:8px; border-right:1px solid #f0f2f5; overflow-y:auto; overscroll-behavior:contain; }
.record-item {
  display:flex; flex-direction:column; gap:2px; width:100%; min-width:0; text-align:left; cursor:pointer;
  padding:7px 9px; border:0; border-radius:8px; background:transparent; transition:background .15s ease;
}
.record-item:hover { background:#f6f9ff; }
.record-item.active { background:rgba(0,113,227,.08); }
.record-path { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:12px; color:#303133; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.record-item.active .record-path { color:#0057b8; font-weight:600; }
.record-meta { display:flex; align-items:center; gap:6px; }
.record-time { color:#a1a1a6; font-size:11px; }

/* 详情 */
.record-detail { min-width:0; min-height:0; max-height:calc(100dvh - 320px); padding:2px 4px 4px 6px; overflow-y:auto; }
.detail-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.detail-head-main { min-width:0; }
.detail-title { color:#1d1d1f; font-size:14px; font-weight:700; font-family:ui-monospace, Consolas, monospace; word-break:break-all; }
.detail-meta { margin-top:2px; color:#909399; font-size:11.5px; }
.detail-alert { margin-bottom:10px; }
.detail-section { margin-top:12px; }
.detail-label { font-size:11.5px; font-weight:700; color:#6e6e73; margin-bottom:6px; letter-spacing:.2px; }
.mini-steps { margin:0; padding-left:16px; color:#303133; font-size:12px; line-height:1.7; max-height:180px; overflow-y:auto; }
.mini-kind { display:inline-block; min-width:60px; color:#0071e3; font-weight:600; }
.mini-text { word-break:break-word; }
.code-preview.is-empty { background:#f7f9fc; color:#909399; border:1px dashed #e4e7ed; }
.code-preview { margin:0; max-height:420px; overflow:auto; padding:12px; border-radius:8px; background:#0f172a; color:#e2e8f0; font-size:12px; line-height:1.6; font-family:ui-monospace, Consolas, monospace; white-space:pre; }
.agent-reply { padding:9px 11px; border-radius:8px; background:#f7f9fc; color:#303133; font-size:12px; line-height:1.6; white-space:pre-wrap; }
.commit-box { padding:11px; border:1px solid #ebedf0; border-radius:9px; }
.commit-actions { margin-top:8px; display:flex; justify-content:flex-end; }
.detail-actions { margin-top:12px; display:flex; justify-content:flex-end; }

@media (max-width: 960px) {
  .records-layout { grid-template-columns:1fr; }
  .record-list, .record-detail { max-height:none; }
  .record-list { border-right:0; border-bottom:1px solid #f0f2f5; padding:0 0 8px; max-height:280px; }
  .record-detail { padding:8px 0 0; }
}
</style>
