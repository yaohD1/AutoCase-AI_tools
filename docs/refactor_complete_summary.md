# AI适配器改造完成总结

## 改造完成时间
2026-06-09

## 改造内容

### 1. 已修改文件

#### ✅ backend/requirements.txt
- 添加 `requests==2.31.0` 库
- 添加 `flask-sqlalchemy==3.1.1` （修复缺失的依赖）

#### ✅ backend/app/adapters/deepseek_adapter.py
**改动：**
- 从 OpenAI SDK 改为直接使用 requests 库
- 移除 `self.client = OpenAI(...)` 初始化
- 默认API地址：`https://api.yygu.cn/v3/llm.chat/chat/completions`
- 默认模型：`deepseek-v4-flash`

**新增方法逻辑：**
```python
headers = {
    "Authorization": f"Bearer {self.api_key}",
    "Content-Type": "application/json"
}

response = requests.post(self.api_base, headers=headers, json=payload, timeout=60)
```

#### ✅ backend/app/adapters/glm_adapter.py
**改动：**
- 从 OpenAI SDK 改为直接使用 requests 库
- 移除 `self.client = OpenAI(...)` 初始化
- 默认API地址：`https://api.yygu.cn/v3/llm.chat/chat/completions`
- 默认模型：`glm-5.1`

#### ✅ backend/app/services/case_generator.py
**改动：**
- 更新默认API地址为统一地址
- DeepSeek默认模型：`deepseek-v4-flash`
- GLM默认模型：`glm-5.1`

---

## 改造要点

### 请求方式变更
**原方式（OpenAI SDK）：**
```python
from openai import OpenAI
client = OpenAI(api_key=key, base_url=url)
response = client.chat.completions.create(...)
```

**新方式（requests库）：**
```python
import requests
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
response = requests.post(url, headers=headers, json=payload, timeout=60)
```

### 保持不变的功能
1. ✅ 图片优化逻辑（PIL.Image 处理）
2. ✅ Base64编码上传
3. ✅ System Prompt 内容
4. ✅ JSON数组解析逻辑
5. ✅ 错误处理机制

### 新增功能
1. ✅ 60秒超时设置
2. ✅ HTTP状态码检查（非200抛异常）
3. ✅ requests异常专门捕获
4. ✅ 更详细的错误信息

---

## 配置方式

### 方式1：前端界面配置（推荐）
访问 http://localhost:5173/settings 添加AI配置：
- 服务商：DeepSeek 或 GLM
- API Key：你的密钥
- API Base URL：可选（默认已配置）
- 模型：可选（默认已配置）

### 方式2：直接使用默认值
无需配置，直接使用代码中的默认值：
- API地址：`https://api.yygu.cn/v3/llm.chat/chat/completions`
- DeepSeek模型：`deepseek-v4-flash`
- GLM模型：`glm-5.1`

---

## 下一步操作

### 1. 重启服务
```bash
# 后端
cd backend
python run.py

# 前端
cd frontend
npm run dev
```

### 2. 配置AI服务
- 打开前端页面 → 点击右上角齿轮图标
- 在设置页面添加至少一个AI配置
- 或使用数据库默认配置（代码中的默认值）

### 3. 测试功能
- 创建项目
- 上传图片
- 选择AI服务商
- 生成测试用例
- 导出XMind

---

## 测试脚本
已创建 `backend/test_api.py` 用于验证API调用：
```python
python backend/test_api.py
```

需要替换脚本中的 `test_key` 为实际的API密钥。

---

## 注意事项

### 重要提醒
1. **确认API支持Vision功能**
   - 确保 `https://api.yygu.cn/v3/llm.chat/chat/completions` 支持图片上传（base64格式）
   - 如果不支持，需要改为纯文本prompt方式

2. **模型名称确认**
   - `deepseek-v4-flash` 和 `glm-5.1` 是否为正确的模型名
   - 可根据实际情况调整

3. **数据库覆盖**
   - 用户可通过前端Settings页面配置覆盖默认值
   - API Base URL可自定义
   - 模型名可自定义

---

## 改造完成验证

### 文件修改验证
- ✅ requirements.txt：添加requests库
- ✅ deepseek_adapter.py：使用requests，默认地址和模型已更新
- ✅ glm_adapter.py：使用requests，默认地址和模型已更新
- ✅ case_generator.py：默认配置已更新

### 代码完整性验证
- ✅ 导入正确（requests库）
- ✅ 构造函数正确（移除OpenAI client）
- ✅ 请求构造正确（headers + payload）
- ✅ 响应解析正确（response.json()['choices'][0]['message']['content'])
- ✅ 错误处理正确（捕获requests异常）
- ✅ 图像处理保持不变

---

## 改造成功！

所有改造步骤已完成，项目现在使用统一的API地址和requests库进行调用。

### 改造效果
- 统一API调用方式
- 更灵活的配置管理
- 更详细的错误提示
- 更好的超时控制

### 后续优化建议
1. 可考虑合并DeepSeekAdapter和GLMAdapter为UnifiedAdapter（减少重复代码）
2. 可添加API调用日志记录
3. 可添加网络失败自动重试机制
4. 可添加API调用统计（次数、成功率等）

---

## 问题诊断

如果使用时遇到问题：

### 问题1：点击生成按钮无反应
**原因：** 未配置AI服务
**解决：** 在Settings页面添加AI配置

### 问题2：API调用失败
**原因：** API Key错误或网络问题
**解决：** 检查API Key和网络连接，查看浏览器控制台错误信息

### 问题3：返回格式错误
**原因：** API不支持vision或返回格式不一致
**解决：** 确认API支持图片上传，调整解析逻辑

---

改造完成，请重启服务并测试！