import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 300000,
  headers: {
    'Content-Type': 'application/json'
  }
})

api.interceptors.response.use(
  response => response,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

export default {
  // 项目相关
  getProjects() {
    return api.get('/projects')
  },
  createProject(data) {
    return api.post('/projects', data)
  },
  getProject(id) {
    return api.get(`/projects/${id}`)
  },
  updateProject(id, data) {
    return api.put(`/projects/${id}`, data)
  },
  deleteProject(id) {
    return api.delete(`/projects/${id}`)
  },
  
  // 迭代
  getSprints(params) {
    return api.get('/sprints', { params })
  },
  createSprint(data) {
    return api.post('/sprints', data)
  },
  updateSprint(id, data) {
    return api.put(`/sprints/${id}`, data)
  },
  deleteSprint(id) {
    return api.delete(`/sprints/${id}`)
  },
  
  // 文件上传
  uploadFile(formData, onUploadProgress) {
    return api.post('/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress
    })
  },
  deleteFile(id) {
    return api.delete(`/upload/${id}`)
  },
  getFile(id) {
    return api.get(`/upload/${id}`)
  },
  
  // 用例生成
  generateTestcases(data) {
    return api.post('/generate', data)
  },
  // 首页：分析时挂起会话，用户改完模块再用同一个会话接着生成
  analyzeSession(data) {
    return api.post('/analyze-session', data, { timeout: 600000 })
  },
  generateSession(data) {
    return api.post('/generate-session', data, { timeout: 900000 })
  },
  analyzeImage(data) {
    return api.post('/analyze-image', data)
  },
  getTestcases(params) {
    return api.get('/cases', { params })
  },
  getTestcase(id) {
    return api.get(`/cases/${id}`)
  },
  updateTestcase(id, data) {
    return api.put(`/cases/${id}`, data)
  },
  deleteTestcase(id) {
    return api.delete(`/cases/${id}`)
  },
batchDeleteTestcases(ids) {
    return api.post('/cases/batch-delete', { ids })
  },

  // 待审用例
  getPendingTestcases(params) {
    return api.get('/cases/pending', { params })
  },
  approveTestcase(id, data) {
    return api.post(`/cases/${id}/approve`, data)
  },
  failTestcase(id) {
    return api.post(`/cases/${id}/fail`)
  },

  // 待审模块
  getPendingModules(params) {
    return api.get('/pending-modules', { params })
  },
  approvePendingModule(id, data) {
    return api.post(`/pending-modules/${id}/approve`, data)
  },
  failPendingModule(id) {
    return api.post(`/pending-modules/${id}/fail`)
  },
  batchDeleteModules(ids) {
    return api.post('/pending-modules/batch-delete', { ids })
  },
  savePendingModules(data) {
    return api.post('/pending-modules', data)
  },
  getApprovedModules(params) {
    return api.get('/pending-modules/approved', { params })
  },

// 导出
  exportXmind(data) {
    return api.post('/export', data)
  },
  downloadFile(filename) {
    return api.get(`/download/${filename}`, { responseType: 'blob' })
  },

  // 文档
  getDocumentContent(filename) {
    return api.get(`/documents/${filename}/content`)
  },

  // 模型服务（直接管理 opencode 的 provider / model）
  getModels() {
    return api.get('/models')
  },
  createModelProvider(data) {
    return api.post('/models/providers', data)
  },
  updateModelProvider(provider, data) {
    return api.put(`/models/providers/${encodeURIComponent(provider)}`, data)
  },
  deleteModelProvider(provider) {
    return api.delete(`/models/providers/${encodeURIComponent(provider)}`)
  },
  hideModelProvider(provider, hidden) {
    return api.post(`/models/providers/${encodeURIComponent(provider)}/hidden`, { hidden })
  },
  createModel(provider, data) {
    return api.post(`/models/providers/${encodeURIComponent(provider)}/models`, data)
  },
  updateModel(provider, model, data) {
    return api.patch(`/models/providers/${encodeURIComponent(provider)}/models/${encodeURIComponent(model)}`, data)
  },
  deleteModel(provider, model) {
    return api.delete(`/models/providers/${encodeURIComponent(provider)}/models/${encodeURIComponent(model)}`)
  },
  setDefaultModel(model) {
    return api.put('/models/default', { model })
  },
  testModel(data) {
    return api.post('/models/test', data)
  },
  applyModels() {
    return api.post('/models/apply')
  },

  // 知识库
  uploadKnowledgeFile(formData) {
    return api.post('/knowledge/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  getKnowledgeFiles(params) {
    return api.get('/knowledge/files', { params })
  },
  getKnowledgeFile(id) {
    return api.get(`/knowledge/files/${id}`)
  },
   deleteKnowledgeFile(id) {
     return api.delete(`/knowledge/files/${id}`)
   },

  // 自动化工具（录制生成，与「自动化脚本」的 generation 完全独立）
  getRecordingCurrent() {
    return api.get('/automation/recording/current')
  },
  getRecordingEvents(after = -1) {
    // 增量游标：录制中用它当「有新步骤」的信号，比每次拉完整快照轻得多。
    return api.get('/automation/recording/events', { params: { after } })
  },
  startRecording(baseUrl, extra = {}) {
    return api.post('/automation/recording/start', { base_url: baseUrl, ...extra })
  },
  focusRecordingSheet(open) {
    // 任务书浮层：让后端把面板窗口铺宽 / 收回并排。调不动也不影响浮层显示。
    return api.post('/automation/recording/panel/sheet', { open: !!open })
  },
  gotoRecordingStepDoc(no) {
    // 回到这一步录制时所在的页面：页面跑掉之后重新框选定位用。
    return api.post('/automation/recording/goto-doc', { no })
  },
  gotoRecordingBack() {
    return api.post('/automation/recording/goto-back')
  },
  updateRecordingStep(no, payload) {
    return api.patch(`/automation/recording/steps/${no}`, payload)
  },
  reorderRecordingStep(no, position) {
    return api.post(`/automation/recording/steps/${no}/order`, { position })
  },
  setRecordingPick(on) {
    return api.post('/automation/recording/pick', { on })
  },
  setRecordingPause(paused) {
    return api.post('/automation/recording/pause', { paused })
  },
  stopRecording() {
    return api.post('/automation/recording/stop')
  },
  // 停止后浏览器仍然开着，需要显式关掉
  closeRecordingBrowser() {
    return api.post('/automation/recording/close')
  },
  // 插入前验证：只看当前页面这一刻的状态，不回放
  verifyRecordingIntent(intent) {
    return api.post('/automation/recording/verify', { intent }, { timeout: 30000 })
  },
  deleteRecordingStep(no) {
    return api.delete(`/automation/recording/steps/${no}`)
  },
  moveRecordingStep(no, direction) {
    return api.post(`/automation/recording/steps/${no}/move`, { direction })
  },
  // 封装：把连续的几步换成一个具名动作，原位置变成一次调用。
  // params 每项 {step, key, label, field}，不传就是不参数化。
  groupRecordingAction(steps, name, desc = '', params = []) {
    return api.post('/automation/recording/actions', { steps, name, desc, params })
  },
  renameRecordingAction(actionId, payload) {
    return api.patch(`/automation/recording/actions/${actionId}`, payload)
  },
  deleteRecordingAction(actionId) {
    return api.delete(`/automation/recording/actions/${actionId}`)
  },
  // 插入一次调用；args 缺的键会用第一次调用时的值补上。
  insertRecordingCall(actionId, afterNo = null, args = null) {
    return api.post(`/automation/recording/actions/${actionId}/calls`, { after_no: afterNo, args })
  },
  // 解封：把这一次调用还原成它包含的原始步骤（动作定义保留）。
  ungroupRecordingStep(no) {
    return api.post(`/automation/recording/steps/${no}/ungroup`)
  },
  updateRecordingCallArgs(no, args) {
    return api.patch(`/automation/recording/steps/${no}/args`, { args })
  },
  addRecordingStep(payload) {
    return api.post('/automation/recording/steps', payload)
  },
  // 刷新页面要后端点一次浏览器（录制中会真的刷），所以有独立接口，超时给宽一点。
  addRecordingReload(afterNo = null) {
    return api.post('/automation/recording/reload', { after_no: afterNo }, { timeout: 30000 })
  },
  // 在浏览器里把这一步的匹配元素全部框出来并编号，供人眼确认；index 省略就按当前选中的那个。
  highlightRecordingStep(no, index = null, framework = null) {
    return api.post('/automation/recording/highlight', { no, index, framework }, { timeout: 30000 })
  },
  clearRecordingHighlight() {
    return api.post('/automation/recording/highlight/clear', {}, { timeout: 30000 })
  },
  // 用户看过框之后指定「就是这个（第 index 个匹配）」；desc 必传，index 离开候选没有意义。
  chooseRecordingTarget(no, desc, index) {
    return api.post(`/automation/recording/steps/${no}/chosen`, { desc, index }, { timeout: 30000 })
  },
  // 定位详情：采用的定位 + 此刻页面上的存在性 / 唯一性。顺带在页面上把匹配框出来。
  locateRecordingStep(no, framework) {
    return api.post(`/automation/recording/steps/${no}/locate`, { framework }, { timeout: 30000 })
  },
  // 「允许降级」：这一步不再追问，生成时按最窄候选 + 录制时的序号写。
  degradeRecordingTarget(no) {
    return api.post(`/automation/recording/steps/${no}/degrade`, {}, { timeout: 30000 })
  },
  // 按下那一刻的视口截图。直接给 <img> 用，所以返回地址而不是请求。
  recordingStepShotUrl(no, shotId) {
    return `/api/automation/recording/steps/${no}/shot?v=${encodeURIComponent(shotId || '')}`
  },
  parseRecordingIntent(text, model) {
    return api.post('/automation/recording/intent', { text, model }, { timeout: 180000 })
  },
  detectRecordingWorkspace(workspacePath, python) {
    const params = { workspace_path: workspacePath }
    if (python) params.python = python
    return api.get('/automation/recording/detect', { params })
  },
  // 项目画像：录制开始时后端会在后台学一份，这里只读它学到的东西。
  getRecordingMemory(workspacePath, framework) {
    const params = { workspace_path: workspacePath }
    if (framework) params.framework = framework
    return api.get('/automation/recording/memory', { params })
  },
  refreshRecordingMemory(data) {
    // 学习要读 6~8 个文件，但接口只是「排进后台」就返回，所以超时不用给长。
    return api.post('/automation/recording/memory/refresh', data, { timeout: 30000 })
  },
  forgetRecordingMemory(workspacePath) {
    return api.delete('/automation/recording/memory', { params: { workspace_path: workspacePath } })
  },
  planRecording(data) {
    // 规划只读项目，不再限制读多少文件（最长 15 分钟）；超时要跟着放宽，
    // 否则前端先断开、后端还在跑，用户会看到一条假的失败。
    return api.post('/automation/recording/plan', data, { timeout: 960000 })
  },
  // 中止正在进行的规划 / 生成：后端会 abort 会话并杀掉 serve。
  cancelRecordingEmit() {
    return api.post('/automation/recording/emit/cancel', {}, { timeout: 15000 })
  },
  generateRecording(data) {
    return api.post('/automation/recording/generate', data, { timeout: 1900000 })
  },
  // 还原这次生成里越界改动过的文件；paths 为空表示清单里的全部。
  revertRecordingBackup(backupId, paths, workspacePath) {
    return api.post(`/automation/recording/backups/${encodeURIComponent(backupId)}/revert`,
      { paths, workspace_path: workspacePath }, { timeout: 60000 })
  },
  // 回放校验要起浏览器把录制重跑一遍，10 步大概十几秒，超时给宽一点
  replayRecording(data) {
    return api.post('/automation/recording/replay', data, { timeout: 300000 })
  },
  listRecordingGenerations() {
    return api.get('/automation/recording/generations')
  },
  getRecordingGeneration(id) {
    return api.get(`/automation/recording/generations/${id}`)
  },
  commitRecordingGeneration(id, message) {
    return api.post(`/automation/recording/generations/${id}/commit`, { message })
  },
  revertRecordingGeneration(id) {
    return api.post(`/automation/recording/generations/${id}/revert`)
  },
  // 目录浏览（只读）：工作区路径相对 AUTOMATION_WORKSPACE_ROOT，目标文件路径相对工作区
  browseAutomationDirs(path = '') {
    return api.get('/automation/browse', { params: { path } })
  },
  browseAutomationFiles(workspace, path = '', framework = '') {
    return api.get('/automation/browse/files', { params: { workspace, path, framework } })
  },

  // 自动化脚本
  getAutomationConfig(projectId) {
    return api.get('/automation/config', { params: { project_id: projectId } })
  },
  getAutomationModels(refresh = false) {
    return api.get('/automation/models', { params: refresh ? { refresh: 1 } : {} })
  },
  saveAutomationConfig(data) {
    return api.put('/automation/config', data)
  },
  generateAutomation(data) {
    return api.post('/automation/generate', data, { timeout: 1900000 })
  },
  previewAutomationCases(data) {
    return api.post('/automation/cases/preview', data)
  },
  generateAutomationFromCases(data) {
    return api.post('/automation/generate-from-cases', data, { timeout: 1900000 })
  },
  getAutomationGenerations(projectId, limit = 20) {
    return api.get('/automation/generations', { params: { project_id: projectId, limit } })
  },
  getAutomationGeneration(id, projectId) {
    return api.get(`/automation/generations/${id}`, { params: { project_id: projectId } })
  },
  getAutomationGenerationEvents(id, projectId, after = 0) {
    return api.get(`/automation/generations/${id}/events`, { params: { project_id: projectId, after } })
  },
  cancelAutomationGeneration(id, projectId) {
    return api.post(`/automation/generations/${id}/cancel`, null, { params: { project_id: projectId } })
  },
  retryAutomationGeneration(id, projectId) {
    return api.post(`/automation/generations/${id}/retry`, null, { params: { project_id: projectId }, timeout: 1900000 })
  },
  getAutomationArtifacts(id, projectId, kind) {
    return api.get(`/automation/generations/${id}/artifacts`, { params: { project_id: projectId, kind } })
  },
  getAutomationArtifact(id, projectId, path) {
    return api.get(`/automation/generations/${id}/artifact`, { params: { project_id: projectId, path } })
  },
  commitAutomationGeneration(id, projectId, message) {
    return api.post(`/automation/generations/${id}/commit`, { message }, { params: { project_id: projectId } })
  },
  revertAutomationGeneration(id, projectId, force = false) {
    return api.post(`/automation/generations/${id}/revert`, { force }, { params: { project_id: projectId } })
  },
  downloadAutomationGeneration(id, projectId) {
    return api.get(`/automation/generations/${id}/download`, { params: { project_id: projectId }, responseType: 'blob' })
  }
 }
