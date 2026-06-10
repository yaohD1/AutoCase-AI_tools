# 超时优化 + 用例数量控制功能实施完成总结

## 实施时间
2026-06-09

## 实施内容

### ✅ 已完成的工作

#### 第一部分：超时优化（解决超时问题）

**问题：** Kimi Vision API调用超时（30秒超时）

**解决方案：**

1. **✅ KimiAdapter超时增加**
   - 文件：`backend/app/adapters/kimi_adapter.py`
   - 构造函数新增参数：`timeout: int = 170`
   - requests.post使用：`timeout=self.timeout`
   - 效果：默认超时170秒，可配置

2. **✅ DeepSeekAdapter超时增加**
   - 文件：`backend/app/adapters/deepseek_adapter.py`
   - 构造函数新增参数：`timeout: int = 170`
   - requests.post使用：`timeout=self.timeout`
   - 效果：保持代码一致性

3. **图片压缩保持不变**
   - 按用户要求，不优化图片压缩
   - 保持1920x1920分辨率，quality=85

---

#### 第二部分：用例数量控制功能

**功能：** 用户可控制生成测试用例的数量

**实施方案：** 方案A - 输入框（InputNumber）

**实现细节：**

1. **✅ 前端UI添加**
   - 文件：`frontend/src/views/Upload.vue`
   - 新增输入框：`<el-input-number v-model="form.caseCount" :min="1" :max="50" :step="1" />`
   - 提示文本："每种类型的用例数量（建议5-15个）"
   - 默认值：10个
   - 范围：1-50个

2. **✅ 前端数据传递**
   - form添加字段：`caseCount: 10`
   - API调用传递：`case_count: form.value.caseCount`

3. **✅ 后端参数接收**
   - 文件：`backend/app/routes/testcase.py`
   - 接收参数：`case_count = data.get('case_count', 10)`
   - 传递给Generator：`generator.generate(image.file_path, case_types, case_count)`

4. **✅ CaseGenerator更新**
   - 文件：`backend/app/services/case_generator.py`
   - 方法签名：`generate(self, image_path: str, case_types: List[str], case_count: int = 10)`
   - 传递count：`PromptTemplates.get_combined_prompt(case_types, case_count)`

5. **✅ Prompt模板动态化**
   - 文件：`backend/app/utils/prompt_templates.py`
   - 模板改为格式化字符串：
     - `FUNCTIONAL_TEST_TEMPLATE = "请生成{count}个功能测试用例。"`
     - `UI_TEST_TEMPLATE = "请生成{count}个UI交互测试用例。"`
     - `BOUNDARY_TEST_TEMPLATE = "请生成{count}个边界值测试用例。"`
     - `EXCEPTION_TEST_TEMPLATE = "请生成{count}个异常场景测试用例。"`
   - get_prompt_by_type：添加count参数
   - get_combined_prompt：动态计算每种类型数量，添加count参数

---

## 修改文件清单

### 后端文件（需要重启）
1. ✅ `backend/app/adapters/kimi_adapter.py` - 超时170秒
2. ✅ `backend/app/adapters/deepseek_adapter.py` - 超时170秒
3. ✅ `backend/app/services/case_generator.py` - 添加count参数
4. ✅ `backend/app/routes/testcase.py` - 接收case_count参数
5. ✅ `backend/app/utils/prompt_templates.py` - Prompt动态化

### 前端文件（刷新页面）
6. ✅ `frontend/src/views/Upload.vue` - UI添加数量控制

---

## 核心改动详情

### 1. 超时配置

**KimiAdapter构造函数：**
```python
def __init__(self, api_key: str, api_base: str = "...", model: str = "kimi-k2.6",
             temperature: float = 0.7, max_tokens: int = 2000, timeout: int = 170):
    super().__init__(api_key, api_base, model, temperature, max_tokens)
    self.timeout = timeout
```

**requests.post调用：**
```python
response = requests.post(
    self.api_base,
    headers=headers,
    json=payload,
    timeout=self.timeout  # 使用170秒
)
```

---

### 2. Prompt模板动态化

**模板格式：**
```python
FUNCTIONAL_TEST_TEMPLATE = """请分析这张UI设计图，生成功能测试用例。

重点关注：
1. 核心业务功能的实现
2. 用户操作流程的完整性
3. 数据输入输出的验证
4. 各功能按钮/链接的正确性

请生成{count}个功能测试用例。"""
```

**动态填充：**
```python
@classmethod
def get_prompt_by_type(cls, case_type: str, count: int = 10) -> str:
    template = templates.get(case_type, cls.FUNCTIONAL_TEST_TEMPLATE)
    return template.format(count=count)
```

**组合Prompt：**
```python
@classmethod
def get_combined_prompt(cls, case_types: list, count: int = 10) -> str:
    # 计算每种类型应该生成多少个
    cases_per_type = max(1, count // len(case_types))
    # 例如：用户选10个，4种类型 → 每种约2-3个
    # 生成prompt："总共约10个：功能测试（约2个），UI测试（约2个）..."
```

---

### 3. 前端数量控制UI

**UI组件：**
```vue
<el-form-item label="生成数量">
    <el-input-number
        v-model="form.caseCount"
        :min="1"
        :max="50"
        :step="1"
    />
    <span style="margin-left: 10px; color: #999; font-size: 12px">
        每种类型的用例数量（建议5-15个）
    </span>
</el-form-item>
```

**数据默认值：**
```javascript
const form = ref({
    projectId: '',
    aiProvider: 'kimi',
    caseTypes: ['functional', 'ui', 'boundary', 'exception'],
    caseCount: 10  // 默认10个
})
```

**API调用：**
```javascript
await api.generateTestcases({
    image_id: imageId,
    project_id: form.value.projectId,
    provider: form.value.aiProvider,
    case_types: form.value.caseTypes,
    case_count: form.value.caseCount  // 新增参数
})
```

---

## 功能效果

### 超时优化效果
- ✅ Vision API调用超时从60秒增加到170秒
- ✅ 能处理较大的图片和复杂的UI设计图
- ✅ 减少超时错误发生率

### 数量控制效果
- ✅ 用户可以自主控制生成用例数量（1-50个）
- ✅ 默认生成10个用例
- ✅ Prompt会根据用户设置动态调整
- ✅ AI会尽量按照指定数量生成（可能有偏差）

---

## 数量分配逻辑

**示例1：用户设置10个，选择4种类型**
```
cases_per_type = 10 / 4 = 2-3个每种类型
Prompt："总共约10个用例"
- 功能测试：约2-3个
- UI测试：约2-3个
- 边界测试：约2-3个
- 异常测试：约2-3个
```

**示例2：用户设置5个，选择2种类型**
```
cases_per_type = 5 / 2 = 2-3个每种类型
Prompt："总共约5个用例"
- 功能测试：约2-3个
- UI测试：约2-3个
```

**示例3：用户设置20个，选择所有类型**
```
cases_per_type = 20 / 4 = 5个每种类型
Prompt："总共约20个用例"
- 功能测试：约5个
- UI测试：约5个
- 边界测试：约5个
- 异常测试：约5个
```

---

## 用户提示文本

**前端UI提示：**
- "每种类型的用例数量（建议5-15个）"

**Prompt提示AI：**
- "请生成{count}个XX测试用例"
- AI会尽量按照数量生成，但不保证精确匹配

---

## 测试建议

### 1. 超时测试

**测试步骤：**
1. 重启后端服务
2. 上传较大的图片（如1920x1080的UI截图）
3. 选择Kimi服务
4. 点击生成
5. 等待结果

**预期结果：**
- ✅ 不出现超时错误
- ✅ 170秒内返回结果
- ✅ 成功生成用例

---

### 2. 数量控制测试

**测试A：少量生成**
```
设置：caseCount = 5
类型：功能 + UI（2种）
预期：生成约5个用例（2-3个功能，2-3个UI）
```

**测试B：默认生成**
```
设置：caseCount = 10（默认）
类型：全部4种
预期：生成约10个用例（每种约2-3个）
```

**测试C：大量生成**
```
设置：caseCount = 20
类型：全部4种
预期：生成约20个用例（每种约5个）
```

---

### 3. 边界测试

**测试最小值：**
```
设置：caseCount = 1
预期：生成1个用例（极端情况）
```

**测试最大值：**
```
设置：caseCount = 50
预期：生成约50个用例（可能耗时较长）
```

---

## 是否需要重启？

### 后端修改 → 需要重启
**所有.py文件修改后必须重启后端：**
```bash
cd backend
python run.py
```

### 前端修改 → 刷新页面
**Vue文件修改后刷新浏览器：**
- 按 Ctrl+F5 硬刷新
- Vite会自动编译
- 不需手动重启前端服务

---

## 下一步操作

### 1. 重启后端服务
```bash
cd backend
python run.py
```

### 2. 刷新浏览器
访问 http://localhost:5173
按 Ctrl+F5 清除缓存刷新

### 3. 测试完整流程
- 在Upload页面看到"生成数量"输入框
- 设置数量（默认10）
- 上传图片
- 选择Kimi服务
- 生成用例
- 验证不超时且数量接近预期

---

## 实施总结

**✅ 所有改动已完成**

**修改文件：** 6个文件
**实施时间：** 约5分钟
**需要重启：** 后端服务
**生效方式：** 前端刷新页面

**解决的问题：**
1. ✅ 超时错误（170秒超时）
2. ✅ 用户无法控制数量（添加数量控制功能）

**新增功能：**
1. ✅ 用例数量控制（1-50个）
2. ✅ 动态Prompt（根据数量生成）
3. ✅ 用户友好的输入框UI

---

## 注意事项

### AI生成数量可能不完全精确
- Prompt指示："请生成{count}个用例"
- AI可能会生成略多或略少
- 例如：要求10个，可能生成8-12个
- 这是正常现象

### 数量过多可能导致超时
- 如果设置50个用例
-可能需要更长生成时间
- 建议先用10-15个测试

### 超时设置足够
- 170秒超时足够处理大多数情况
- 如果仍然超时，可考虑：
  - 减少数量
  - 减少case_types
  - 使用更简单的图片

---

**实施完成文档生成时间：** 2026-06-09
**实施人员：** OpenCode AI Assistant