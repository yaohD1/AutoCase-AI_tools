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
  
  // 迭代
  getSprints(params) {
    return api.get('/sprints', { params })
  },
  createSprint(data) {
    return api.post('/sprints', data)
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
  savePendingModules(data) {
    return api.post('/pending-modules', data)
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
  }
}