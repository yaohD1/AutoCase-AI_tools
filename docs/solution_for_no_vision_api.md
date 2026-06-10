# 解决API不支持Vision功能的修复方案

## 问题分析

### 错误信息
```
Failed to deserialize the JSON body into the target type: 
messages[1]: unknown variant `image_url`, expected `text`
```

### 问题原因
你的API `https://api.yygu.cn/v3/llm.chat/chat/completions` **不支持Vision功能**，无法接受图片base64上传。只接受纯文本格式的消息内容。

### 当前代码问题位置
`backend/app/adapters/deepseek_adapter.py:66-77`
```python
{
    "role": "user",
    "content": [  # <-- 这是数组格式（OpenAI Vision格式）
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {...}}  # <-- API不支持这个
    ]
}
```

---

## 解决方案

### 方案1：改为纯文本Prompt（推荐）

**改动思路：**
1. 移除图片base64上传功能
2. 用户在前端上传图片后，提示用户**手动输入UI功能的文字描述**
3. 基于文字描述生成测试用例

**优点：**
- 不需要Vision功能支持
- 可以立即解决问题
- 成本更低（不消耗图片处理的token）

**缺点：**
- 需要用户手动描述UI功能
- 失去了自动识别UI设计图的能力

---

### 实施步骤（方案1）

#### 1. 修改后端适配器
**文件：** `deepseek_adapter.py`, `glm_adapter.py`

**改动：** 移除图片处理，改为纯文本content

```python
def generate_cases(self, description: str, prompt: str) -> List[Dict]:
    # 删除 optimized_path 和 base64_image 相关代码
    
    headers = {
        "Authorization": f"Bearer {self.api_key}",
        "Content-Type": "application/json"
    }
    
    # 构造纯文本消息
    full_prompt = f"{prompt}\n\nUI功能描述：\n{description}"
    
    payload = {
        "model": self.model,
        "messages": [
            {
                "role": "system",
                "content": """你是一位专业的测试工程师，擅长根据功能描述生成详细的测试用例。
请严格按照以下格式输出（JSON数组格式）：

[
  {
    "module": "模块名",
    "test_point": "测试点",
    "title": "用例：用例名 #优先级",
    "priority": "P0/P1/P2",
    "preconditions": "前置条件：...",
    "steps": ["步骤：步骤1", "步骤：步骤2", "..."],
    "expected": "预期：预期结果",
    "case_type": "functional/ui/boundary/exception"
  }
]

注意：
1. 每个测试点下可生成多个测试用例
2. 模块、测试点默认以"模块："、"测试点："开头
3. 用例格式为："用例：用例名 #优先级"，优先级为P0/P1/P2
4. 前置条件、步骤、预期都需带前缀
5. 返回有效的JSON数组"""
            },
            {
                "role": "user",
                "content": full_prompt  # <-- 纯文本，不是数组
            }
        ],
        "temperature": self.temperature,
        "max_tokens": self.max_tokens
    }
    
    response = requests.post(self.api_base, headers=headers, json=payload, timeout=60)
    
    if response.status_code != 200:
        raise Exception(f"API request failed: {response.status_code} - {response.text}")
    
    result = response.json()
    content = result['choices'][0]['message']['content']
    
    json_match = content[content.find('['):content.rfind(']')+1]
    testcases = json.loads(json_match)
    
    return testcases
```

**关键改动：**
- ✅ `content` 从数组改为字符串：`"content": full_prompt`
- ✅ 移除所有图片处理代码（optimize_image, image_to_base64）
- ✅ System prompt改为基于文字描述生成用例
- ✅ 接收 `description` 参数而不是 `image_path`

---

#### 2. 修改BaseAdapter接口
**文件：** `backend/app/adapters/base_adapter.py`

**改动：** 方法签名修改

```python
@abstractmethod
def generate_cases(self, description: str, prompt: str) -> List[Dict]:
    # 从 image_path 改为 description
    pass
```

---

#### 3. 修改路由逻辑
**文件：** `backend/app/routes/testcase.py:36-53`

**当前逻辑：**
```python
data.get('image_id')  # 获取图片ID
image = Image.query.get(image_id)  # 查询图片
generator.generate(image.file_path, case_types)  # 传入图片路径
```

**改造逻辑：**
```python
description = data.get('description')  # 获取用户输入的UI描述文本

if not description:
    return jsonify({'error': 'UI description required'}), 400

try:
    generator = CaseGenerator(ai_config)
    testcases = generator.generate(description, case_types)  # 传入描述文本
    
    for case_data in testcases:
        testcase = TestCase(
            project_id=project_id,
            module=case_data.get('module', ''),
            test_point=case_data.get('test_point', ''),
            title=case_data.get('title', ''),
            priority=case_data.get('priority', 'P2'),
            preconditions=case_data.get('preconditions', ''),
            steps=json.dumps(case_data.get('steps', []), ensure_ascii=False),
            expected=case_data.get('expected', ''),
            case_type=case_data.get('case_type', 'functional'),
            # image_source=image.filename,  # <-- 移除这行
            ai_provider=provider
        )
        db.session.add(testcase)
    
    db.session.commit()
```

---

#### 4. 修改CaseGenerator
**文件：** `backend/app/services/case_generator.py`

**改动：** 方法逻辑简化

```python
def generate(self, description: str, case_types: List[str]) -> List[Dict]:
    all_testcases = []
    
    prompt = PromptTemplates.get_combined_prompt(case_types)
    
    try:
        # 直接传入描述文本
        testcases = self.adapter.generate_cases(description, prompt)
        
        for case in testcases:
            priority = case.get('priority', 'P2')
            if not priority.startswith('P'):
                priority = 'P' + priority
            
            case['priority'] = priority
            
            if 'case_type' not in case:
                case['case_type'] = 'functional'
        
        all_testcases.extend(testcases)
        
    except Exception as e:
        raise Exception(f"Failed to generate test cases: {str(e)}")
    
    return all_testcases
```

**改动：**
- ✅ 参数从 `image_path` 改为 `description`
- ✅ 移除图片相关逻辑

---

#### 5. 修改Prompt模板
**文件：** `backend/app/utils/prompt_templates.py`

**改动：** 基于描述生成（可选，当前模板可以直接使用）

当前的prompt模板已经足够好，因为用户描述会被附加到prompt中。

---

#### 6. 修改前端界面
**文件：** `frontend/src/views/Upload.vue`

**改动1：移除图片上传区域，改为文本输入框**

```vue
<el-form-item label="UI功能描述">
    <el-input
        v-model="form.description"
        type="textarea"
        :rows="5"
        placeholder="请描述UI功能，例如：这是一个登录页面，包含用户名输入框、密码输入框、登录按钮、找回密码链接..."
    />
    <div class="description-tips">
        <el-tag size="small">示例：登录页面 - 用户名、密码输入框、登录按钮、记住我选项</el-tag>
    </div>
</el-form-item>
```

**改动2：修改表单数据**

```javascript
const form = ref({
    projectId: '',
    aiProvider: 'deepseek',
    caseTypes: ['functional', 'ui', 'boundary', 'exception'],
    description: ''  // <-- 新增描述字段
})
```

**改动3：修改生成按钮逻辑**

```javascript
async function generateCases() {
    if (!canGenerate.value) {
        return
    }
    
    generating.value = true
    
    try {
        // 不再上传图片，直接发送描述
        await api.generateTestcases({
            description: form.value.description,  // <-- 改为发送描述文本
            project_id: form.value.projectId,
            provider: form.value.aiProvider,
            case_types: form.value.caseTypes
        })
        
        hasGenerated.value = true
        ElMessage.success('测试用例生成成功！')
        
    } catch (error) {
        ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
    } finally {
        generating.value = false
    }
}
```

**改动4：修改canGenerate计算属性**

```javascript
const canGenerate = computed(() => {
    return form.value.projectId && form.value.description  // <-- 改为检查描述
})
```

**改动5：删除相关代码**

删除：
- `fileList` ref
- `handleFileChange` 函数
- `handleFileRemove` 函数
- `<el-upload>` 组件及相关逻辑

---

#### 7. 修改API定义
**文件：** `frontend/src/api/index.js`

**改动：** generateTestcases接口保持不变，但传递的参数变化

```javascript
generateTestcases(data) {
    return api.post('/generate', data)
    // data现在包含 {description, project_id, provider, case_types}
    // 不再包含 {image_id}
}
```

可选：可以移除uploadFile相关接口，或保留用于其他用途（如保存截图参考）。

---

### 方案2：使用其他支持Vision的API（备选）

如果确实需要自动识别UI设计图，可以：

**选项A：使用官方DeepSeek Vision API**
- API地址：`https://api.deepseek.com`
- 需要：DeepSeek官方API Key，且支持Vision

**选项B：使用OpenAI GPT-4 Vision**
- API地址：`https://api.openai.com/v1/chat/completions`
- 模型：`gpt-4-vision-preview`
- 需要：OpenAI API Key

**优点：**
- 可以自动识别UI设计图
- 保持原有的自动化流程

**缺点：**
- 需要额外获取支持Vision的API Key
- 成本更高
- 可能需要切换到不同的服务商

---

### 方案3：混合方案（推荐长期）

**短期：** 使用方案1（纯文本描述）快速解决问题

**长期：** 如果有支持Vision的API，可以提供双模式：
- 模式1：图片上传自动识别（需要Vision API）
- 模式2：文本描述生成用例（不需要Vision）

---

## 推荐选择

### 立即采用：方案1（纯文本Prompt）

**理由：**
1. ✅ 立即可用，无需额外API配置
2. ✅ 你的API明确不支持Vision
3. ✅ 成本更低
4. ✅ 用户可以更精确地描述需求

**用户体验改进建议：**
- 在前端提供描述模板/示例
- 提供常用UI功能的描述选项（下拉选择）
- 可考虑添加简单的图片标注功能（上传图片后手动标注关键点）

---

## 关键改动对比

### API请求格式对比

**❌ 错误格式（Vision格式 - 你的API不支持）：**
```python
payload = {
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "提示词"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        ]}
    ]
}
```

**✅ 正确格式（纯文本格式）：**
```python
payload = {
    "messages": [
        {"role": "user", "content": "提示词 + 用户描述文本"}  # <-- 字符串，不是数组
    ]
}
```

---

## 代码示例（完整）

### deepseek_adapter.py完整改造代码

```python
import requests
import json
from typing import List, Dict
from app.adapters.base_adapter import BaseAIAdapter

class DeepSeekAdapter(BaseAIAdapter):
    def __init__(self, api_key: str, api_base: str = "https://api.yygu.cn/v3/llm.chat/chat/completions", 
                 model: str = "deepseek-v4-flash", temperature: float = 0.7, max_tokens: int = 2000):
        super().__init__(api_key, api_base, model, temperature, max_tokens)
    
    def generate_cases(self, description: str, prompt: str) -> List[Dict]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        full_prompt = f"{prompt}\n\nUI功能描述：\n{description}"
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": """你是一位专业的测试工程师，擅长根据功能描述生成详细的测试用例。
请严格按照以下格式输出（JSON数组格式）：

[
  {
    "module": "模块名",
    "test_point": "测试点",
    "title": "用例：用例名 #优先级",
    "priority": "P0/P1/P2",
    "preconditions": "前置条件：...",
    "steps": ["步骤：步骤1", "步骤：步骤2", "..."],
    "expected": "预期：预期结果",
    "case_type": "functional/ui/boundary/exception"
  }
]

注意：
1. 每个测试点下可生成多个测试用例
2. 模块、测试点默认以"模块："、"测试点："开头
3. 用例格式为："用例：用例名 #优先级"，优先级为P0/P1/P2
4. 前置条件、步骤、预期都需带前缀
5. 返回有效的JSON数组"""
                },
                {
                    "role": "user",
                    "content": full_prompt
                }
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        response = requests.post(self.api_base, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")
        
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        json_match = content[content.find '['):content.rfind(']')+1]
        testcases = json.loads(json_match)
        
        return testcases
```

---

## 测试清单

修改完成后需要测试：

1. ✅ 前端文本输入框正常工作
2. ✅ 用户可以输入UI描述
3. ✅ API调用不发送图片（只有文本）
4. ✅ 描述文本能成功生成用例
5. ✅ 用例JSON格式正确
6. ✅ 数据库能正确保存用例
7. ✅ 导出XMind功能正常
8. ✅ 错误提示友好（缺少描述时提示用户）

---

## 下一步行动

**请确认是否采用方案1（纯文本描述方式），我将立即实施修改。**

如果选择方案1，我将按照上述步骤依次修改：
1. 后端适配器（移除Vision功能）
2. 后端路由和CaseGenerator
3. 前端界面（移除图片上传，改为文本输入）
4. 完整测试

预计修改时间：10-15分钟