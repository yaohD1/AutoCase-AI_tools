import base64
import os
import json
import requests
from typing import List, Dict
from app.adapters.base_adapter import BaseAIAdapter
from PIL import Image

class KimiAdapter(BaseAIAdapter):
    def __init__(self, api_key: str, api_base: str = "https://api.yygu.cn/v3/llm.chat/chat/completions", model: str = "kimi-k2.6", temperature: float = 0.7, max_tokens: int = 2000, timeout: int = 170):
        super().__init__(api_key, api_base, model, temperature, max_tokens)
        self.timeout = timeout
    
    def optimize_image(self, image_path: str) -> str:
        img = Image.open(image_path)
        if img.width > 2048 or img.height > 2048:
            img.thumbnail((2048, 2048))
        temp_path = image_path.rsplit('.', 1)[0] + '_optimized.jpg'
        img.convert('RGB').save(temp_path, quality=85, optimize=True)
        return temp_path
    
    def image_to_base64(self, image_path: str) -> str:
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def generate_cases(self, image_path: str, prompt: str) -> List[Dict]:
        optimized_path = self.optimize_image(image_path)
        
        try:
            base64_image = self.image_to_base64(optimized_path)
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": """你是一位专业的测试工程师，擅长从UI设计图中识别测试点并生成详细的测试用例。
请严格按照以下格式输出（JSON数组格式）：

[
  {
    "module": "模块名",
    "test_point": "测试点",
    "title": "用例:用例名 #优先级",
    "priority": "0/1/2",
    "preconditions": "前置条件:...",
    "steps": ["步骤:步骤1", "步骤:步骤2", "..."],
    "expected": "预期:预期结果",
    "case_type": "functional/ui/boundary/exception"
  }
]

注意：
1. 每个测试点下可生成多个测试用例
2. 模块、测试点默认以"模块："、"测试点："开头
3. 用例格式为："用例：用例名 #优先级"，优先级为0/1/2
4. 前置条件、步骤、预期都需带前缀（前置步骤:/步骤:/预期:）
5. 返回有效的JSON数组"""
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
            
            response = requests.post(
                self.api_base,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                raise Exception(f"API request failed: {response.status_code} - {response.text}")
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            json_match = content[content.find('['):content.rfind(']')+1]
            testcases = json.loads(json_match)
            
            return testcases
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Kimi API request error: {str(e)}")
        except Exception as e:
            raise Exception(f"Kimi API error: {str(e)}")
        finally:
            if os.path.exists(optimized_path):
                os.remove(optimized_path)

    def generate_from_text(self, prompt: str) -> List[Dict]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": """你是一位专业的测试工程师，擅长分析文档/接口文档并生成详细的测试用例。
请严格按照以下格式输出（JSON数组格式）：

[
  {
    "module": "模块名",
    "test_point": "测试点",
    "title": "用例:用例名 #优先级",
    "priority": "0/1/2",
    "preconditions": "前置条件:...",
    "steps": ["步骤:步骤1", "步骤:步骤2", "..."],
    "expected": "预期:预期结果",
    "case_type": "functional/ui/boundary/exception"
  }
]

注意：
1. 基于提供的文档内容分析测试点
2. 模块、测试点默认以"模块："、"测试点："开头
3. 用例格式为："用例：用例名 #优先级"，优先级为0/1/2
4. 前置条件、步骤、预期都需带前缀（前置步骤:/步骤:/预期:）
5. 返回有效的JSON数组"""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        response = requests.post(
            self.api_base,
            headers=headers,
            json=payload,
            timeout=self.timeout
        )

        if response.status_code != 200:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")

        result = response.json()
        content = result['choices'][0]['message']['content']

        json_match = content[content.find('['):content.rfind(']')+1]
        return json.loads(json_match)

    def analyze_image(self, image_path: str, prompt: str) -> List[Dict]:
        return self._analyze_images([image_path], prompt)
    
    def analyze_images(self, image_paths: List[str], prompt: str) -> List[Dict]:
        optimized_paths = []
        try:
            for p in image_paths:
                optimized_paths.append(self.optimize_image(p))
            
            content_parts = [{"type": "text", "text": prompt}]
            for op in optimized_paths:
                base64_image = self.image_to_base64(op)
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                })
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一位资深的UI测试分析师，擅长分析UI设计图并输出结构化模块描述。只输出JSON数组，不要加任何解释。"
                    },
                    {
                        "role": "user",
                        "content": content_parts
                    }
                ],
                "temperature": 0.3,
                "max_tokens": max(self.max_tokens, len(image_paths) * 2000)
            }
            
            response = requests.post(
                self.api_base,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                raise Exception(f"API request failed: {response.status_code} - {response.text}")
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            json_match = content[content.find('['):content.rfind(']')+1]
            return json.loads(json_match)
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Kimi API request error: {str(e)}")
        except Exception as e:
            raise Exception(f"Kimi API error: {str(e)}")
        finally:
            for op in optimized_paths:
                if os.path.exists(op):
                    os.remove(op)
