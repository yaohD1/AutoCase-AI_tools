<template>
  <div class="settings-page">
    <el-card>
      <template #header>
        <span>AI服务配置</span>
      </template>
      
      <el-button type="primary" @click="showAddConfig = true" style="margin-bottom: 20px">
        添加AI配置
      </el-button>
      
      <el-table :data="configs" style="width: 100%">
        <el-table-column prop="provider" label="服务商" width="150" />
        <el-table-column prop="api_key" label="API Key" width="200">
          <template #default="{ row }">
            {{ row.api_key }}
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="150" />
        <el-table-column prop="temperature" label="温度参数" width="100" />
        <el-table-column prop="max_tokens" label="最大Token" width="100" />
        <el-table-column prop="is_active" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="supports_vision" label="Vision" width="80">
          <template #default="{ row }">
            <el-tag :type="row.supports_vision !== false ? 'success' : 'info'" size="small">
              {{ row.supports_vision !== false ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right" align="center">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button text @click="editConfig(row)">编辑</el-button>
              <el-button text @click="toggleActive(row)">
                {{ row.is_active ? '禁用' : '启用' }}
              </el-button>
              <el-button text type="danger" @click="deleteConfig(row.id)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    
    <el-dialog v-model="showAddConfig" :title="editingConfig ? '编辑配置' : '添加配置'" width="500px">
      <el-form :model="configForm" label-width="120px">
        <el-form-item label="服务商" required>
          <el-input v-model="configForm.provider" :disabled="!!editingConfig" placeholder="如 kimi、volcengine、deepseek" style="width: 100%" />
        </el-form-item>
        <el-form-item label="API Key" required>
          <el-input v-model="configForm.api_key" type="password" placeholder="请输入API Key" />
        </el-form-item>
        <el-form-item label="API Base URL">
          <el-input v-model="configForm.api_base" placeholder="可选，自定义API地址" />
        </el-form-item>
        <el-form-item label="模型名称" required>
          <el-input v-model="configForm.model" placeholder="如 kimi-k2.6" />
        </el-form-item>
        <el-form-item label="温度参数" required>
          <el-slider v-model="configForm.temperature" :min="0" :max="1" :step="0.1" show-input />
        </el-form-item>
        <el-form-item label="最大Token" required>
          <el-input-number v-model="configForm.max_tokens" :min="100" :max="32000" :step="200" />
        </el-form-item>
        <el-form-item label="是否支持Vision">
          <el-switch v-model="configForm.supports_vision" />
          <span class="form-hint" style="margin-left:8px;color:#909399;font-size:12px">
            {{ configForm.supports_vision ? '可用于图片分析' : '仅文本生成' }}
          </span>
        </el-form-item>
        </el-form>
      <template #footer>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <el-button @click="testConnection" :loading="testing">
              测试连通
            </el-button>
            <el-tag v-if="testResult !== null" :type="testResult ? 'success' : 'danger'" style="margin-left:8px">
              {{ testResult ? '连接成功' : '连接失败' }}
            </el-tag>
          </div>
          <div>
            <el-button @click="cancelEdit">取消</el-button>
            <el-button type="primary" @click="saveConfig">保存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const configs = ref([])
const showAddConfig = ref(false)
const editingConfig = ref(null)
const testing = ref(false)
const testResult = ref(null)

const configForm = ref({
  provider: 'volcengine',
  api_key: '',
  api_base: '',
  model: '',
  temperature: 0.7,
  max_tokens: 2000,
  supports_vision: true
})

onMounted(() => {
  loadConfigs()
})

async function loadConfigs() {
  try {
    const res = await api.getAIConfigs()
    configs.value = res.data.configs
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

function editConfig(config) {
  editingConfig.value = config
  configForm.value = {
    provider: config.provider,
    api_key: config.api_key ? config.api_key.replace('***', '') : '',
    api_base: config.api_base || '',
    model: config.model || '',
    temperature: config.temperature || 0.7,
    max_tokens: config.max_tokens || 2000,
    supports_vision: config.supports_vision !== false
  }
  showAddConfig.value = true
}

function cancelEdit() {
  showAddConfig.value = false
  editingConfig.value = null
  configForm.value = {
    provider: 'volcengine',
    api_key: '',
    api_base: '',
    model: '',
    temperature: 0.7,
    max_tokens: 2000,
    supports_vision: true
  }
}

async function saveConfig() {
  if (!configForm.value.provider || !configForm.value.api_key || !configForm.value.model) {
    ElMessage.warning('请填写必填字段（服务商、API Key、模型名称）')
    return
  }
  
  try {
    if (editingConfig.value) {
      await api.updateAIConfig(editingConfig.value.id, configForm.value)
      ElMessage.success('更新成功')
    } else {
      await api.createAIConfig(configForm.value)
      ElMessage.success('添加成功')
    }
    
    cancelEdit()
    loadConfigs()
  } catch (error) {
    ElMessage.error('保存失败：' + (error.response?.data?.error || error.message))
  }
}

async function testConnection() {
  if (!configForm.value.api_key) {
    ElMessage.warning('请先输入 API Key')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testAIConnection({
      api_key: configForm.value.api_key,
      api_base: configForm.value.api_base,
      model: configForm.value.model
    })
    testResult.value = res.data.success
    ElMessage[testResult.value ? 'success' : 'error'](
      testResult.value ? '连接成功' : '连接失败'
    )
  } catch (error) {
    testResult.value = false
    ElMessage.error('连接失败：' + (error.response?.data?.error || error.message))
  } finally {
    testing.value = false
  }
}

async function toggleActive(config) {
  try {
    await api.updateAIConfig(config.id, { is_active: !config.is_active })
    ElMessage.success('状态更新成功')
    loadConfigs()
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

async function deleteConfig(id) {
  try {
    await ElMessageBox.confirm('确定删除该配置吗？', '提示', {
      type: 'warning'
    })
    
    await api.deleteAIConfig(id)
    ElMessage.success('删除成功')
    loadConfigs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}
</script>

<style scoped>
.settings-page {
  max-width: 1200px;
  margin: 0 auto;
}
</style>