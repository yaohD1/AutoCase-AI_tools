<template>
  <!-- 用例字段块：详情与审批两个弹窗共用，避免同一套字段写两遍后各自漂移。 -->
  <div class="form-block">
    <div class="form-pair">
      <div class="form-field">
        <div class="form-label"><span>模块</span></div>
        <el-input v-model="modelValue.module" class="form-input" />
      </div>
      <div class="form-field">
        <div class="form-label"><span>测试点</span></div>
        <el-input v-model="modelValue.test_point" class="form-input" />
      </div>
    </div>

    <div class="form-field">
      <div class="form-label"><span>用例标题</span></div>
      <el-input v-model="modelValue.title" class="form-input" />
    </div>

    <div class="form-pair">
      <div class="form-field">
        <div class="form-label"><span>优先级</span></div>
        <el-select v-model="modelValue.priority" class="form-ctl">
          <el-option label="P0" value="P0" />
          <el-option label="P1" value="P1" />
          <el-option label="P2" value="P2" />
        </el-select>
      </div>
      <div class="form-field">
        <div class="form-label"><span>用例类型</span></div>
        <el-select v-model="modelValue.case_type" class="form-ctl">
          <el-option label="功能测试" value="functional" />
          <el-option label="UI 交互测试" value="ui" />
          <el-option label="边界值测试" value="boundary" />
          <el-option label="异常场景测试" value="exception" />
        </el-select>
      </div>
    </div>

    <div class="form-field">
      <div class="form-label"><span>前置条件</span></div>
      <el-input v-model="modelValue.preconditions" type="textarea" :rows="2" class="form-textarea" />
    </div>

    <div class="form-field">
      <div class="form-label">
        <span>测试步骤（{{ modelValue.stepsList.length }}）</span>
        <button type="button" class="form-label-act" @click="addStep">＋ 添加步骤</button>
      </div>
      <div class="line-editor">
        <div v-for="(step, index) in modelValue.stepsList" :key="index" class="line-row">
          <span class="line-no">{{ index + 1 }}</span>
          <el-input v-model="modelValue.stepsList[index]" class="form-input" placeholder="这一步做什么" />
          <button
            type="button"
            class="line-del"
            title="删除这一步"
            :disabled="modelValue.stepsList.length <= 1"
            @click="removeStep(index)"
          >×</button>
        </div>
      </div>
    </div>

    <div class="form-field">
      <div class="form-label"><span>预期结果</span></div>
      <el-input v-model="modelValue.expected" type="textarea" :rows="2" class="form-textarea" />
    </div>

    <!-- 只读信息：详情页有，审批页不需要 -->
    <div v-if="metaLabel !== undefined" class="form-pair">
      <div class="form-field">
        <div class="form-label"><span>AI 服务</span></div>
        <div class="form-static">{{ metaLabel || '—' }}</div>
      </div>
      <div class="form-field">
        <div class="form-label"><span>创建时间</span></div>
        <div class="form-static">{{ metaTime || '—' }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Object, required: true },
  metaLabel: { type: String, default: undefined },
  metaTime: { type: String, default: undefined }
})

function addStep() {
  props.modelValue.stepsList.push('')
}

function removeStep(index) {
  if (props.modelValue.stepsList.length <= 1) return
  props.modelValue.stepsList.splice(index, 1)
}
</script>
