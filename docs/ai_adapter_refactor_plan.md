# AI适配器改造计划 - 统一API调用方式

## 背景
当前代码使用 OpenAI SDK 进行API调用，但用户实际使用的是统一API地址 `https://api.yygu.cn/v3/llm.chat/chat/completions`，该API支持多种模型（deepseek-v4-flash、glm-5.1等）。

需要改造为直接使用 `requests` 库发送HTTP请求。

## 改造目标
1. 替换 OpenAI SDK 为 requests 库
2. 统一使用用户提供的API地址
3. 保持图片上传功能（base64编码）
4. 保持原有的错误处理和图像优化逻辑

## 涉及文件

### 需要修改的文件：
1. `backend/app/adapters/deepseek_adapter.py` - DeepSeek适配器
2. `backend/app/adapters/glm_adapter.py` - GLM适配器
3. `backend/app/services/case_generator.py` - 配置默认API地址和模型
4. `backend/requirements.txt` - 移除openai库依赖（可选），确认requests存在

### base_adapter.py 不需要修改
基础适配器类只定义接口协议，具体实现由子类完成。

---

## 详细修改方案

### 1. deepseek_adapter.py 改造

**当前方式（使用OpenAI SDK）：**
```python
from openai import OpenAI
self.client = OpenAI(api_key=api_key, base_url=api_base)
response = self.client.chat.completions.create(...)
```

**改造后（使用requests）：**

#### 导入部分修改
```python
import base64
import os
import json
import requests
from typing import List, Dict
from app.adapters.base_adapter import BaseAIAdapter
from PIL import Image
```

#### 构造函数修改
```python
def __init__(self, api_key: str, api_base: str = "https://api.yygu.cn/v3/llm.chat/chat/completions", 
             model: str = "deepseek-v4-flash", temperature: float = 0.7, max_tokens: int = 2000):
    super().__init__(api_key, api_base, model, temperature, max_tokens)
    # 不再需要创建OpenAI client
```

#### generate_cases方法修改
```python
def generate_cases(self, image_path: str, prompt: str) -> List[Dict]:
    optimized_path = self.optimize_image(image_path)
    
    try:
        base64_image = self.image_to_base64(optimized_path)
        
        # 构造请求
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": """你是一位专业的测试工程师...（保持原system prompt）"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        # 发送请求
        response = requests.post(
            self.api_base,
            headers=headers,
            json=payload,
            timeout=60  # 添加超时设置
        )
        
        # 检查响应状态
        if response.status_code != 200:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")
        
        # 解析响应
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # 解析JSON数组（保持原有逻辑）
        json_match = content[content.find('['):content.rfind(']')+1]
        testcases = json.loads(json_match)
        
        return testcases
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"DeepSeek API request error: {str(e)}")
    except Exception as e:
        raise Exception(f"DeepSeek API error: {str(e)}")
    finally:
        if os.path.exists(optimized_path):
            os.remove(optimized_path)
```

**改动要点：**
- 移除 `self.client = OpenAI(...)`
- 使用 `requests.post()` 直接发送HTTP请求
- 手动构造headers和payload
- 响应解析：`response.json()['choices'][0]['message']['content']`
- 添加超时参数（60秒）
- 添加响应状态检查（status_code != 200抛异常）
- 保持图像优化和base64编码逻辑不变

---

### 2. glm_adapter.py 改造

与deepseek_adapter.py改造方式完全相同，只需修改默认模型名：

**构造函数：**
```python
def __init__(self, api_key: str, api_base: str = "https://api.yygu.cn/v3/llm.chat/chat/completions",
             model: str = "glm-5.1", temperature: float = 0.7, max_tokens: int = 2000):
    super().__init__(api_key, api_base, model, temperature, max_tokens)
```

**generate_cases方法：**
与DeepSeekAdapter完全相同的实现（可考虑后续重构合并）

**错误消息：**
```python
raise Exception(f"GLM API request error: {str(e)}")
raise Exception(f"GLM API error: {str(e)}")
```

---

### 3. case_generator.py 改造

修改 `_get_adapter()` 方法中的默认值：

```python
def _get_adapter(self):
    if self.ai_config.provider == 'deepseek':
        return DeepSeekAdapter(
            api_key=self.ai_config.api_key,
            api_base=self.ai_config.api_base or "https://api.yygu.cn/v3/llm.chat/chat/completions",
            model=self.ai_config.model or "deepseek-v4-flash",
            temperature=self.ai_config.temperature,
            max_tokens=self.ai_config.max_tokens
        )
    elif self.ai_config.provider == 'glm':
        return GLMAdapter(
            api_key=self.ai_config.api_key,
            api_base=self.ai_config.api_base or "https://api.yygu.cn/v3/llm.chat/chat/completions",
            model=self.ai_config.model or "glm-5.1",
            temperature=self.ai_config.temperature,
            max_tokens=self.ai_config.max_tokens
        )
    else:
        raise ValueError(f"Unsupported AI provider: {self.ai_config.provider}")
```

**改动要点：**
- 统一API地址改为 `https://api.yygu.cn/v3/llm.chat/chat/completions`
- DeepSeek默认模型改为 `deepseek-v4-flash`（或根据实际情况调整）
- GLM默认模型改为 `glm-5.1`
- 保持 `or` 逻辑（用户可在数据库配置中覆盖默认值）

---

### 4. requirements.txt 改造

**当前依赖：**
```
openai==1.12.0
```

**改造后：**
```
# 移除或注释 openai（如果不再使用）
# openai==1.12.0
requests==2.31.0  # 添加requests库
```

**注意：** 如果项目中其他地方仍使用openai库，则保留；否则可以移除。

---

## 执行顺序

1. 修改 `backend/requirements.txt` 确保requests库存在
2. 修改 `backend/app/adapters/deepseek_adapter.py`
3. 修改 `backend/app/adapters/glm_adapter.py`
4. 修改 `backend/app/services/case_generator.py`
5. 重启后端服务测试
6. 在前端Settings页面配置AI服务
7. 测试图片上传和用例生成功能

---

## 测试要点

1. **基本API调用测试**
   - 配置DeepSeek，测试能否调用成功
   - 配置GLM，测试能否调用成功
   - 检查返回的用例数据格式正确

2. **错误处理测试**
   - API Key错误时是否抛出明确异常
   - 网络超时时是否能正确处理
   - API返回非200状态码时是否正确提示

3. **功能完整性测试**
   - 图片上传是否正常
   - 用例生成是否有完整步骤
   - 导出XMind是否正常

4. **数据库配置覆盖测试**
   - 在前端Settings页面配置自定义API地址
   - 配置自定义模型名
   - 测试能否覆盖默认配置

---

## 潜在问题和注意事项

1. **API兼容性问题**
   - 需确认 `https://api.yygu.cn/v3/llm.chat/chat/completions` 支持vision功能（图片base64上传）
   - 需确认返回格式与OpenAI格式一致（包含choices数组）
   - 如果不支持vision，需要改用纯文本prompt或其他方式

2. **模型名称**
   - 需确认 deepseek-v4-flash、glm-5.1 是正确的模型名
   - 用户可根据实际需求修改模型名

3. **超时设置**
   - 设置60秒超时（图像分析和用例生成需要较长时间）
   - 可根据实际情况调整

4. **后续优化建议**
   - DeepSeekAdapter和GLMAdapter代码高度重复，可考虑抽象成UnifiedAdapter
   - 可添加API调用日志记录
   - 可添加重试机制（网络失败自动重试）

---

## 验证方法

修改完成后，通过以下方式验证：

```python
# 测试代码示例（可直接在backend目录运行Python测试）
import requests

api_key = "你的key"
url = "https://api.yygu.cn/v3/llm.chat/chat/completions"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

payload = {
    "model": "deepseek-v4-flash",
    "messages": [
        {"role": "user", "content": "你好"}
    ],
    "temperature": 0.7,
    "max_tokens": 100
}

response = requests.post(url, headers=headers, json=payload)
print(response.status_code)
print(response.json())
```

如果能正常返回，说明API配置正确。