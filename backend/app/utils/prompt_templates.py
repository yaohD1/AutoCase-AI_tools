class PromptTemplates:
    
    IMAGE_ANALYSIS_PROMPT = """你是一位资深的UI测试分析师。请分析这张UI设计图，按模块输出结构化描述。

要求：
1. 按照从上到下、从左到右的顺序，识别所有功能模块
2. 对每个模块描述：
   - 模块名称
   - 该模块包含的UI元素（按钮、输入框、下拉菜单等）
   - 该模块的功能说明
   - 用户的预期操作流程
   - 测试关注点

输出JSON格式，只输出JSON数组，不要加任何解释：
[
  {
    "module": "模块名称",
    "ui_elements": ["元素1", "元素2"],
    "function_description": "该模块的功能说明",
    "interaction_flow": "用户操作流程描述",
    "test_focus": ["测试关注点1", "测试关注点2"]
  }
]"""
    
    FUNCTIONAL_TEST_TEMPLATE = """请分析这张UI设计图，生成功能测试用例。

重点关注：
1. 核心业务功能的实现
2. 用户操作流程的完整性
3. 数据输入输出的验证
4. 各功能按钮/链接的正确性

请生成{count}个功能测试用例。"""

    UI_TEST_TEMPLATE = """请分析这张UI设计图，生成UI交互测试用例。

重点关注：
1. 界面元素显示是否正确
2. 交互操作是否流畅
3. 界面响应是否符合预期
4. 不同状态下的UI表现

请生成{count}个UI交互测试用例。"""

    BOUNDARY_TEST_TEMPLATE = """请分析这张UI设计图，生成边界值测试用例。

重点关注：
1. 输入框的边界值（最小值、最大值、临界值）
2. 数据长度的限制
3. 数值范围的边界
4. 特殊字符的处理

请生成{count}个边界值测试用例。"""

    EXCEPTION_TEST_TEMPLATE = """请分析这张UI设计图，生成异常场景测试用例。

重点关注：
1. 异常输入的处理
2. 网络异常情况
3. 权限不足的场景
4. 数据错误的情况
5. 并发操作

请生成{count}个异常场景测试用例。"""
    
    SMART_TEST_TEMPLATE = """请根据模块描述，全面分析后生成完整的测试用例。

每个模块都要生成用例，按模块自由发挥，不限制数量，确保覆盖：
1. 核心功能路径
2. 边界条件
3. 异常场景
4. UI交互细节

请生成全面覆盖的测试用例。"""
    
    @classmethod
    def get_prompt_by_type(cls, case_type: str, count: int = 10) -> str:
        templates = {
            'functional': cls.FUNCTIONAL_TEST_TEMPLATE,
            'ui': cls.UI_TEST_TEMPLATE,
            'boundary': cls.BOUNDARY_TEST_TEMPLATE,
            'exception': cls.EXCEPTION_TEST_TEMPLATE
        }
        template = templates.get(case_type, cls.FUNCTIONAL_TEST_TEMPLATE)
        return template.format(count=count)
    
    @classmethod
    def get_combined_prompt(cls, case_types: list, count: int = 10) -> str:
        prompt_parts = [f"请基于这张UI设计图，生成以下类型的测试用例，总共约{count}个：\n"]
        
        total_cases = count
        cases_per_type = max(1, count // len(case_types))
        
        for i, case_type in enumerate(case_types, 1):
            prompt_name = {
                'functional': '功能测试',
                'ui': 'UI交互测试',
                'boundary': '边界值测试',
                'exception': '异常场景测试'
            }.get(case_type, case_type)
            prompt_parts.append(f"{i}. {prompt_name}（约{cases_per_type}个）")
        
        prompt_parts.append("\n\n详细要求：")
        
        if 'functional' in case_types:
            prompt_parts.append("\n【功能测试】")
            prompt_parts.append(cls.get_prompt_by_type('functional', cases_per_type).split('\n\n')[1])
        
        if 'ui' in case_types:
            prompt_parts.append("\n【UI交互测试】")
            prompt_parts.append(cls.get_prompt_by_type('ui', cases_per_type).split('\n\n')[1])
        
        if 'boundary' in case_types:
            prompt_parts.append("\n【边界值测试】")
            prompt_parts.append(cls.get_prompt_by_type('boundary', cases_per_type).split('\n\n')[1])
        
        if 'exception' in case_types:
            prompt_parts.append("\n【异常场景测试】")
            prompt_parts.append(cls.get_prompt_by_type('exception', cases_per_type).split('\n\n')[1])
        
        return '\n'.join(prompt_parts)