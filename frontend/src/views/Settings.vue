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
        <el-table-column prop="provider" label="服务商" width="150">
          <template #default="{ row }">
            {{ getProviderName(row.provider) }}
          </template>
        </el-table-column>
        <el-table-column prop="api_key" label="API Key" width="200">
          <template #default="{ row }">
            {{ row.api_key }}
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="150" />
        <el-table-column prop="temperature" label="温度参数" width="100" />
        <el-table-column prop="max_tokens" label="最大Token" width="100" />
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? '启用' : '禁用' }}
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
        <el-form-item label="服务商">
          <el-select v-model="configForm.provider" :disabled="!!editingConfig" style="width: 100%">
            <el-option label="豆包" value="volcengine" />
            <el-option label="Kimi-2.6" value="kimi" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="configForm.api_key" type="password" placeholder="请输入API Key" />
        </el-form-item>
        <el-form-item label="API Base URL">
          <el-input v-model="configForm.api_base" placeholder="可选，自定义API地址" />
        </el-form-item>
        <el-form-item label="模型名称">
          <el-input v-model="configForm.model" placeholder="可选，默认模型" />
        </el-form-item>
        <el-form-item label="温度参数">
          <el-slider v-model="configForm.temperature" :min="0" :max="1" :step="0.1" show-input />
        </el-form-item>
        <el-form-item label="最大Token">
          <el-input-number v-model="configForm.max_tokens" :min="100" :max="8000" :step="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelEdit">取消</el-button>
        <el-button type="primary" @click="saveConfig">保存</el-button>
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

const configForm = ref({
  provider: 'volcengine',
  api_key: '',
  api_base: '',
  model: '',
  temperature: 0.7,
  max_tokens: 2000
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
    max_tokens: config.max_tokens || 2000
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
    max_tokens: 2000
  }
}

async function saveConfig() {
  if (!configForm.value.api_key) {
    ElMessage.warning('请输入API Key')
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

function getProviderName(provider) {
    const map = {
        volcengine: '豆包',
        kimi: 'Kimi-2.6'
    }
    return map[provider] || provider
}
</script>

<style scoped>
.settings-page {
  max-width: 1200px;
  margin: 0 auto;
}
</style>