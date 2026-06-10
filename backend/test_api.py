"""
测试API调用是否正常
"""
import requests
import json

def test_api_without_image():
    api_key = "test_key"  # 替换为实际API key
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
    
    print("测试无图片的API调用...")
    print(f"URL: {url}")
    print(f"Headers: {headers}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"\n状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n响应内容: {json.dumps(result, indent=2, ensure_ascii=False)}")
            print("✅ API调用成功！")
        else:
            print(f"❌ API调用失败: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ 请求异常: {str(e)}")

if __name__ == "__main__":
    test_api_without_image()