<template>
  <!-- 模块字段块：用例审批、模块审核、模块生成、首页模块弹窗四处共用。
       以前这段在四个地方各写一遍，改一个标签要改四次。 -->
  <div class="form-block">
    <div class="form-field">
      <div class="form-label"><span>模块名称</span></div>
      <el-input v-model="modelValue.module" class="form-input" placeholder="如：登录" />
    </div>

    <div class="form-field">
      <div class="form-label"><span>UI 元素</span></div>
      <el-input v-model="modelValue.ui_elements" class="form-input" placeholder="用逗号分隔，如：按钮、输入框、下拉菜单" />
    </div>

    <div class="form-field">
      <div class="form-label"><span>功能描述</span></div>
      <el-input v-model="modelValue.function_description" type="textarea" :rows="descRows" class="form-textarea" />
    </div>

    <div class="form-field">
      <div class="form-label"><span>交互流程</span></div>
      <el-input v-model="modelValue.interaction_flow" type="textarea" :rows="2" class="form-textarea" />
    </div>

    <div class="form-field">
      <div class="form-label"><span>测试关注点</span></div>
      <el-input v-model="modelValue.test_focus" type="textarea" :rows="2" class="form-textarea" placeholder="用逗号分隔" />
    </div>

    <template v-if="mode === 'generate'">
      <div class="form-field">
        <div class="form-label"><span>用例类型</span></div>
        <div class="field-inline">
          <el-checkbox-group v-model="modelValue.case_types" :disabled="modelValue.smart_case_type">
            <el-checkbox value="functional">功能测试</el-checkbox>
            <el-checkbox value="ui">UI 交互</el-checkbox>
            <el-checkbox value="boundary">边界值</el-checkbox>
            <el-checkbox value="exception">异常场景</el-checkbox>
          </el-checkbox-group>
          <span class="field-inline-switch">
            <el-switch v-model="modelValue.smart_case_type" size="small" />
            <span class="field-inline-text">智能</span>
          </span>
        </div>
      </div>

      <!-- 数量和开关都是短控件，并成一行，省下的高度正好让九项塞进一屏 -->
      <div class="form-pair">
        <div class="form-field">
          <div class="form-label"><span>生成数量</span></div>
          <div class="field-inline">
            <el-input-number
              v-model="modelValue.case_count"
              class="form-num"
              controls-position="right"
              :min="1"
              :max="50"
              :step="1"
              :disabled="modelValue.smart_mode"
            />
            <span class="field-inline-text">条</span>
            <span class="field-inline-switch">
              <el-switch v-model="modelValue.smart_mode" size="small" />
              <span class="field-inline-text">智能</span>
            </span>
          </div>
        </div>

        <div class="form-field">
          <div class="form-label"><span>附带原图</span></div>
          <div class="field-inline">
            <el-switch v-model="modelValue.use_vision" :disabled="!visionSupported" />
            <span class="field-inline-text">
              {{ visionSupported ? '原型图一起给模型' : '当前模型不支持图片' }}
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
  // review：只审核五个文本字段；generate：额外给出用例类型、数量、原图
  mode: { type: String, default: 'review' },
  visionSupported: { type: Boolean, default: true }
})

// 生成弹窗里字段更多，功能描述少一行才不会把最后两行挤出可视区
const descRows = computed(() => (props.mode === 'generate' ? 2 : 3))
</script>

<style scoped>
.field-inline-text {
  font-size: 11.5px;
  color: var(--faint);
}
.field-inline-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
</style>
