"""
测试Kimi-2.6是否支持Vision功能
"""
import requests
import base64
import json

def test_kimi_vision():
    api_key = "YOUR_API_KEY"  # 替换为你的实际API key
    url = "https://api.yygu.cn/v3/llm.chat/chat/completions"
    
    headers = {
        "Authorization": f"Bearer yg-85fb8bbbc7914cb4708b427a0374adae",
        "Content-Type": "application/json"
    }
    
    # 测试用例1：纯文本消息（应该成功）
    print("=" * 60)
    print("测试1：纯文本消息（不使用Vision）")
    print("=" * 60)
    
    payload_text = {
        "model": "kimi-k2.6",
        "messages": [
            {"role": "user", "content": "你好，请简短介绍一下你自己"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload_text, timeout=10)
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ 纯文本调用成功")
            print(f"响应: {result['choices'][0]['message']['content'][:100]}...")
        else:
            print(f"❌ 纯文本调用失败: {response.text}")
    except Exception as e:
        print(f"❌ 请求异常: {str(e)}")
    
    # 测试用例2：Vision消息（包含图片base64）
    print("\n" + "=" * 60)
    print("测试2：Vision消息（包含图片base64）")
    print("=" * 60)
    
    # 使用一个简单的测试图片或模拟base64
    # 你需要替换为实际的图片base64
    test_image_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="  # 这是一个1x1像素的测试图片
    
    payload_vision = {
        "model": "kimi-k2.6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片显示的是什么？"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{test_image_base64}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload_vision, timeout=10)
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ ✅✅ VISION功能支持！Kimi可以识别图片！")
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            print("\n🎉 Kimi-2.6支持Vision功能，可以继续使用图片上传方案！")
        elif response.status_code == 400:
            error_text = response.text
            print(f"❌ Vision调用失败（400错误）")
            print(f"错误详情: {error_text}")
            
            if "image_url" in error_text or "unknown variant" in error_text:
                print("\n⚠️ Kimi-2.6不支持Vision功能")
                print("需要采用纯文本描述方案")
            else:
                print(f"\n其他错误: {error_text}")
        else:
            print(f"❌ Vision调用失败: {response.status_code}")
            print(f"错误详情: {response.text}")
    except Exception as e:
        print(f"❌ 请求异常: {str(e)}")
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

def test_with_real_image(image_path):
    """使用真实图片测试"""
    print("=" * 60)
    print("使用真实图片测试Vision功能")
    print("=" * 60)
    
    try:
        with open(image_path, 'rb') as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')
        
        api_key = "YOUR_API_KEY"  # 替换为你的实际API key
        url = "https://api.yygu.cn/v3/llm.chat/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "kimi-k2.6",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述这个UI界面的功能和元素"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.7,
            "max_tokens": 200
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Vision功能完全支持！")
            print(f"响应内容: {result['choices'][0]['message']['content']}")
        else:
            print(f"❌ 失败: {response.text}")
    except FileNotFoundError:
        print(f"❌ 图片文件不存在: {image_path}")
    except Exception as e:
        print(f"❌ 异常: {str(e)}")

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Kimi-2.6 Vision功能测试")
    print("=" * 60)
    print("\n请将 YOUR_API_KEY 替换为实际的API密钥")
    print("\n")
    
    test_kimi_vision()
    
    # 如果有真实图片，可以测试：
    # test_with_real_image("path/to/your/ui_image.jpg")
    
    print("\n测试完成！")
    print("\n如果Vision测试成功（状态码200），说明Kimi支持图片识别")
    print("如果Vision测试失败（状态码400且包含'image_url'错误），说明需要改用纯文本方案")