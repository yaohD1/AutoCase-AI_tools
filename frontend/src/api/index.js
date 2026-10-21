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

  // AI配置
  getAIConfigs() {
    return api.get('/ai-configs')
  },
  createAIConfig(data) {
    return api.post('/ai-configs', data)
  },
  updateAIConfig(id, data) {
    return api.put(`/ai-configs/${id}`, data)
  },
  testAIConnection(data) {
    return api.post('/ai-configs/test-connection', data)
  },
  deleteAIConfig(id) {
    return api.delete(`/ai-configs/${id}`)
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
  startRecording(baseUrl, extra = {}) {
    return api.post('/automation/recording/start', { base_url: baseUrl, ...extra })
  },
  relayoutRecordingPanel() {
    return api.post('/automation/recording/panel/relayout')
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
  cancelRecording() {
    return api.post('/automation/recording/cancel')
  },
  deleteRecordingStep(no) {
    return api.delete(`/automation/recording/steps/${no}`)
  },
  moveRecordingStep(no, direction) {
    return api.post(`/automation/recording/steps/${no}/move`, { direction })
  },
  addRecordingStep(payload) {
    return api.post('/automation/recording/steps', payload)
  },
  parseRecordingIntent(text, model) {
    return api.post('/automation/recording/intent', { text, model }, { timeout: 180000 })
  },
  detectRecordingWorkspace(workspacePath, python) {
    const params = { workspace_path: workspacePath }
    if (python) params.python = python
    return api.get('/automation/recording/detect', { params })
  },
  generateRecording(data) {
    return api.post('/automation/recording/generate', data, { timeout: 1900000 })
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
  getAutomationGenerations(projectId) {
    return api.get('/automation/generations', { params: { project_id: projectId } })
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
